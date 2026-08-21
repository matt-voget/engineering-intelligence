"""Calculate snapshot-safe GitHub pull-request pickup and review time."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import SourceConfig, TeamsConfig
from engineering_intelligence.persistence.models import (
    GitHubPullRequest,
    GitHubPullRequestVersion,
    GitHubRepository,
    GitHubReview,
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
    ) -> GitHubPullRequestMetricsView:
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
            states = {
                state.scope.removeprefix("repository:"): state
                for state in session.scalars(
                    select(SnapshotSourceState).where(
                        SnapshotSourceState.snapshot_id == snapshot.id,
                        SnapshotSourceState.source == "github",
                    )
                ).all()
                if state.scope.startswith("repository:")
            }
            identity = {
                member.github_login.casefold(): member.preferred_name or member.name
                for configured_team in teams_config.teams
                for member in configured_team.members
                if member.github_login
            }
            contributions: list[PullRequestMetricContribution] = []
            missing_states = []
            for repository_name in repository_names:
                state = states.get(repository_name)
                if state is None:
                    missing_states.append(repository_name)
                    continue
                repository = session.scalar(
                    select(GitHubRepository).where(
                        GitHubRepository.full_name == repository_name
                    )
                )
                if repository is None:
                    missing_states.append(repository_name)
                    continue
                high_water = _as_utc(state.high_water_mark)
                latest = (
                    select(
                        GitHubPullRequestVersion.pull_request_id,
                        func.max(GitHubPullRequestVersion.observed_at).label("observed_at"),
                    )
                    .where(GitHubPullRequestVersion.observed_at <= high_water)
                    .group_by(GitHubPullRequestVersion.pull_request_id)
                    .subquery()
                )
                rows = session.execute(
                    select(GitHubPullRequest, GitHubPullRequestVersion)
                    .join(latest, latest.c.pull_request_id == GitHubPullRequest.id)
                    .join(
                        GitHubPullRequestVersion,
                        (
                            GitHubPullRequestVersion.pull_request_id
                            == GitHubPullRequest.id
                        )
                        & (
                            GitHubPullRequestVersion.observed_at
                            == latest.c.observed_at
                        ),
                    )
                    .where(GitHubPullRequest.repository_id == repository.id)
                ).all()
                for pull, version in rows:
                    if not _author_in_scope(version.author_login, author_logins):
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
                (
                    "PR authors must match an active configured GitHub identity for "
                    "the selected team at the snapshot date."
                ),
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
                author_logins=sorted(author_logins),
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
