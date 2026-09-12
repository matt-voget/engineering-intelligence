"""Calculate snapshot-safe GitHub pull-request pickup and review time."""

import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import SourceConfig, TeamsConfig
from engineering_intelligence.persistence.models import (
    GitHubPullRequest,
    GitHubPullRequestVersion,
    GitHubRepository,
    GitHubReview,
    JiraGitHubRelationship,
    JiraIssue,
    JiraIssueVersion,
    SnapshotSourceState,
)
from engineering_intelligence.presentations.github_pr_metrics import (
    GitHubPersonRef,
    GitHubPullRequestMetricsView,
    PullRequestMetricContribution,
)
from engineering_intelligence.presentations.rag import assess_rag
from engineering_intelligence.queries.dashboard import DashboardQuery, _as_utc
from engineering_intelligence.queries.team import _team_config
from engineering_intelligence.snapshots.organization import (
    organization_config_for_snapshot,
    source_config_for_snapshot,
)


class GitHubPullRequestMetricsQuery:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def get(
        self,
        snapshot_identifier: str,
        team_identifier: str,
        source_config: SourceConfig,
        teams_config: TeamsConfig,
        attribution: str = "author",
    ) -> GitHubPullRequestMetricsView:
        """Pickup/review metrics for one team.

        ``attribution`` is a per-query choice, not configuration, so it never changes a
        snapshot's pinned config hash: ``author`` (default, the report contract) credits
        PRs authored by configured team members; ``jira-team`` credits a PR to the Team
        field of the first Jira key in its title, then branch name, falling back to the
        author only when no such key names a team; ``jira-team-strict`` is the same but
        drops PRs that name no Jira team (the Operations Portal's "Unassigned" rule).
        """
        if attribution not in ATTRIBUTION_MODES:
            raise ValueError(f"Unknown attribution mode: {attribution}")
        with self.sessions() as session:
            snapshot = DashboardQuery._snapshot(session, snapshot_identifier)
            source_config = source_config_for_snapshot(snapshot, source_config)
            teams_config = organization_config_for_snapshot(snapshot, teams_config)
            team = _team_config(teams_config, team_identifier)
            snapshot_date = _as_utc(snapshot.created_at).date()
            author_logins = {
                member.github_login.casefold()
                for member in team.members
                if member.github_login
                and member.active
                and member.starts_on <= snapshot_date
                and (member.ends_on is None or member.ends_on >= snapshot_date)
            }
            repository_names = sorted(
                repository.full_name
                for repository in source_config.github.repositories
            )
            all_states = session.scalars(
                select(SnapshotSourceState).where(
                    SnapshotSourceState.snapshot_id == snapshot.id
                )
            ).all()
            states = {
                state.scope.removeprefix("repository:"): state
                for state in all_states
                if state.source == "github" and state.scope.startswith("repository:")
            }
            jira_high_water = _jira_high_water(all_states, team.id)
            identity = {
                member.github_login.casefold(): member.preferred_name or member.name
                for configured_team in teams_config.teams
                for member in configured_team.members
                if member.github_login
            }
            contributions: list[PullRequestMetricContribution] = []
            missing_states = []
            repositories = {
                repository.full_name: repository
                for repository in session.scalars(
                    select(GitHubRepository).where(
                        GitHubRepository.full_name.in_(repository_names)
                    )
                )
            }
            repository_context = {}
            for repository_name in repository_names:
                state = states.get(repository_name)
                if state is None:
                    missing_states.append(repository_name)
                    continue
                repository = repositories.get(repository_name)
                if repository is None:
                    missing_states.append(repository_name)
                    continue
                repository_context[repository.id] = (repository_name, state)

            # Select only pull requests that have ever been attributed to a current
            # team author, then validate their latest pinned version below. The old
            # path rebuilt a latest-version subquery and scanned every pull request
            # separately for every configured repository and team.
            candidate_pull_ids = set(
                session.scalars(
                    select(GitHubPullRequestVersion.pull_request_id).where(
                        func.lower(GitHubPullRequestVersion.author_login).in_(
                            sorted(author_logins)
                        )
                    )
                )
            )
            team_names = {team.name.casefold(), *(alias.casefold() for alias in team.aliases)}
            issue_team_cache: dict[str, str | None] = {}
            if attribution != "author" and jira_high_water is not None:
                candidate_pull_ids |= _pulls_linked_to_team(
                    session, team_names, jira_high_water
                )
            pulls = (
                session.scalars(
                    select(GitHubPullRequest).where(
                        GitHubPullRequest.id.in_(sorted(candidate_pull_ids))
                    )
                ).all()
                if candidate_pull_ids
                else []
            )
            for pull in pulls:
                context = repository_context.get(pull.repository_id)
                if context is None:
                    continue
                repository_name, state = context
                high_water = _as_utc(state.high_water_mark)
                if pull.first_seen_at > state.high_water_mark:
                    continue
                version = session.scalar(
                    select(GitHubPullRequestVersion)
                    .where(
                        GitHubPullRequestVersion.pull_request_id == pull.id,
                        GitHubPullRequestVersion.observed_at <= state.high_water_mark,
                    )
                    .order_by(GitHubPullRequestVersion.observed_at.desc())
                    .limit(1)
                )
                if version is None:
                    continue
                jira_team = (
                    _jira_team_of_pull(session, version, jira_high_water, issue_team_cache)
                    if attribution != "author" and jira_high_water is not None
                    else None
                )
                if not _attributed(
                    attribution,
                    _author_in_scope(version.author_login, author_logins),
                    jira_team,
                    team_names,
                ):
                    continue
                reviews = list(
                    session.scalars(
                        select(GitHubReview)
                        .where(
                            GitHubReview.pull_request_id == pull.id,
                            GitHubReview.observed_at <= high_water,
                            GitHubReview.submitted_at <= high_water,
                        )
                        .order_by(GitHubReview.submitted_at, GitHubReview.id)
                    ).all()
                )
                measurement = _measure(version, reviews)
                if measurement is None:
                    continue
                first_reviewed_at, pickup_hours, review_hours, reviewer_logins = (
                    measurement
                )
                contributions.append(
                    PullRequestMetricContribution(
                        repository=repository_name,
                        number=pull.number,
                        title=version.title,
                        url=pull.html_url,
                        author=_person(version.author_login, identity),
                        reviewers=[
                            _person(login, identity)
                            for login in reviewer_logins
                            if _person(login, identity) is not None
                        ],
                        created_at=_as_utc(version.source_created_at),
                        first_reviewed_at=first_reviewed_at,
                        merged_at=_as_utc(version.merged_at),
                        pickup_hours=pickup_hours,
                        review_hours=review_hours,
                        pickup_rag=assess_rag(
                            teams_config.rag,
                            team_id=team.id,
                            section="github_pr_metrics",
                            metric="pickup_hours",
                            value=pickup_hours,
                            record_key=f"{repository_name}-{pull.number}",
                        ),
                        review_rag=assess_rag(
                            teams_config.rag,
                            team_id=team.id,
                            section="github_pr_metrics",
                            metric="review_hours",
                            value=review_hours,
                            record_key=f"{repository_name}-{pull.number}",
                        ),
                    )
                )
            contributions.sort(
                key=lambda item: (-item.pickup_hours, item.repository, item.number)
            )
            notes = [
                (
                    "Pickup time is elapsed time from PR creation to the first "
                    "non-author, non-bot submitted review."
                ),
                "Review time is elapsed time from that first review to merge.",
                "The report date filter selects PRs by merge date.",
                ATTRIBUTION_NOTES[attribution],
                (
                    "All configured repositories are searched; repository-to-team "
                    "mapping is not used."
                ),
                (
                    "Draft PRs, unmerged PRs, PRs without a qualifying review, and "
                    "records with inconsistent timestamps are excluded."
                ),
            ]
            if missing_states:
                notes.append(
                    "Pinned GitHub source state is missing for: "
                    + ", ".join(missing_states)
                    + "."
                )
            return GitHubPullRequestMetricsView(
                snapshot_id=snapshot.id,
                snapshot_name=snapshot.name,
                snapshot_created_at=_as_utc(snapshot.created_at),
                team_id=team.id,
                team_name=team.name,
                repositories=repository_names,
                author_logins=sorted(author_logins)
                if attribution == "author"
                else sorted(
                    {item.author.login for item in contributions if item.author},
                    key=str.casefold,
                ),
                contributions=contributions,
                data_quality_notes=notes,
            )


def _measure(version, reviews) -> tuple[datetime, float, float, list[str]] | None:
    if version.draft or version.merged_at is None:
        return None
    created = _as_utc(version.source_created_at)
    merged = _as_utc(version.merged_at)
    author = (version.author_login or "").casefold()
    eligible = [
        review
        for review in reviews
        if review.submitted_at is not None
        and review.author_login
        and review.author_login.casefold() != author
        and not review.author_login.casefold().endswith("[bot]")
        and created <= _as_utc(review.submitted_at) <= merged
    ]
    if not eligible:
        return None
    first_review = min(_as_utc(review.submitted_at) for review in eligible)
    reviewers = sorted({review.author_login for review in eligible}, key=str.casefold)
    return (
        first_review,
        round((first_review - created).total_seconds() / 3600, 2),
        round((merged - first_review).total_seconds() / 3600, 2),
        reviewers,
    )


def _person(login: str | None, identity: dict[str, str]) -> GitHubPersonRef | None:
    if not login:
        return None
    return GitHubPersonRef(login=login, display_name=identity.get(login.casefold()))


def _author_in_scope(login: str | None, author_logins: set[str]) -> bool:
    return bool(login and login.casefold() in author_logins)


ATTRIBUTION_MODES = ("author", "jira-team", "jira-team-strict")
ATTRIBUTION_NOTES = {
    "author": (
        "PR authors must match an active configured GitHub identity for "
        "the selected team at the snapshot date."
    ),
    "jira-team": (
        "Attribution mode jira-team: a PR is credited to the Team field of the first "
        "Jira key in its title, then its branch name, as recorded in the pinned Jira "
        "snapshot; PRs naming no Jira team fall back to the author's configured team, "
        "and PRs whose Jira team is another team are excluded even when a team member "
        "authored them. author_logins lists the contributing authors."
    ),
    "jira-team-strict": (
        "Attribution mode jira-team-strict: a PR is credited only to the Team field of "
        "the first Jira key in its title, then its branch name; PRs naming no Jira team "
        "are excluded, matching the Operations Portal's Unassigned bucket. author_logins "
        "lists the contributing authors."
    ),
}
_JIRA_KEY = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def _attributed(
    mode: str,
    author_in_scope: bool,
    jira_team: str | None,
    team_names: set[str],
) -> bool:
    """Decide whether a pull request counts for the selected team.

    ``author`` credits the author's configured team only. ``jira-team`` credits the
    single team named by the PR's first Jira key when one names a team, and falls
    back to the author otherwise. ``jira-team-strict`` never falls back.
    """
    if mode == "jira-team-strict":
        return jira_team is not None and jira_team in team_names
    if mode != "jira-team":
        return author_in_scope
    if jira_team is not None:
        return jira_team in team_names
    return author_in_scope


def _jira_high_water(states, team_id: str) -> datetime | None:
    """The Jira high-water mark to pin issue versions to: the team scope's, else the latest."""
    jira = [state for state in states if state.source == "jira"]
    scoped = next(
        (state for state in jira if state.scope == f"query:team-field-{team_id}"), None
    )
    if scoped is not None:
        return _as_utc(scoped.high_water_mark)
    marks = [_as_utc(state.high_water_mark) for state in jira if state.high_water_mark]
    return max(marks) if marks else None


def _jira_keys_in_order(version) -> list[str]:
    """Jira keys named by a pull request, title first then branch, in text order, deduplicated."""
    seen: list[str] = []
    for text in (version.title or "", version.head_ref or ""):
        for key in _JIRA_KEY.findall(text.upper()):
            if key not in seen:
                seen.append(key)
    return seen


def _jira_team_of_pull(
    session: Session,
    version,
    jira_high_water: datetime,
    cache: dict[str, str | None],
) -> str | None:
    """Casefolded Team-field name of the first Jira key on the PR that names a team, pinned to the snapshot."""
    for key in _jira_keys_in_order(version):
        if key not in cache:
            issue = session.scalar(select(JiraIssue).where(JiraIssue.issue_key == key))
            team_name = None
            if issue is not None:
                team_name = session.scalar(
                    select(JiraIssueVersion.team_name)
                    .where(
                        JiraIssueVersion.issue_id == issue.id,
                        JiraIssueVersion.observed_at <= jira_high_water,
                    )
                    .order_by(JiraIssueVersion.observed_at.desc())
                    .limit(1)
                )
            cache[key] = team_name.casefold() if team_name else None
        if cache[key]:
            return cache[key]
    return None


def _pulls_linked_to_team(
    session: Session, team_names: set[str], jira_high_water: datetime
) -> set[str]:
    """Candidate pull request ids: any PR linked to an issue whose pinned Team field names this team."""
    issue_ids = {
        issue_id
        for issue_id, team_name in session.execute(
            select(JiraIssueVersion.issue_id, JiraIssueVersion.team_name).where(
                JiraIssueVersion.observed_at <= jira_high_water,
                JiraIssueVersion.team_name.is_not(None),
            )
        )
        if team_name and team_name.casefold() in team_names
    }
    if not issue_ids:
        return set()
    return set(
        session.scalars(
            select(JiraGitHubRelationship.github_record_id).where(
                JiraGitHubRelationship.github_record_type == "pull_request",
                JiraGitHubRelationship.jira_issue_id.in_(sorted(issue_ids)),
            )
        )
    )
