"""Classify a team's pinned Jira and GitHub work as IBR-linked or not.

The Jira population is the snapshot's ``query:team-field-<team.id>`` scope —
every issue whose Jira Team field names the team. An issue is IBR-linked when
it was observed on the configured IBR board in the same snapshot, or when its parent
chain reaches such an item. GitHub records enter through explicit Jira-key
links, through their pull request's links (commits and reviews inherit the
pull request's keys as a derived association), or through a configured team
author identity when no Jira link exists at all.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import TeamsConfig
from engineering_intelligence.persistence.models import (
    BoardMembershipObservation,
    GitHubCommit,
    GitHubPullRequest,
    GitHubPullRequestCommit,
    GitHubPullRequestVersion,
    GitHubRepository,
    GitHubReview,
    JiraGitHubRelationship,
    JiraIssue,
    JiraIssueVersion,
    JiraScopeObservation,
    SnapshotSourceState,
)
from engineering_intelligence.presentations.team_work import (
    ClassifiedGitHubRecord,
    ClassifiedJiraIssue,
    LinkedPullRequest,
    TeamWorkClassification,
    WorkSplit,
)
from engineering_intelligence.queries.dashboard import DashboardQuery, _as_utc
from engineering_intelligence.queries.team import _team_config
from engineering_intelligence.snapshots.organization import (
    ibr_board_id_for_snapshot,
    organization_config_for_snapshot,
)

LIST_WINDOW_DAYS = 92
SPLIT_WINDOW_DAYS = 31
MAX_ANCESTOR_DEPTH = 10


class TeamWorkQuery:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def get(
        self,
        snapshot_identifier: str,
        team_identifier: str,
        teams_config: TeamsConfig,
    ) -> TeamWorkClassification:
        with self.sessions() as session:
            snapshot = DashboardQuery._snapshot(session, snapshot_identifier)
            ibr_scope = f"board:{ibr_board_id_for_snapshot(snapshot)}"
            teams_config = organization_config_for_snapshot(snapshot, teams_config)
            team = _team_config(teams_config, team_identifier)
            states = session.scalars(
                select(SnapshotSourceState).where(SnapshotSourceState.snapshot_id == snapshot.id)
            ).all()
            scope = f"query:team-field-{team.id}"
            team_state = next((s for s in states if s.scope == scope), None)
            board_state = next((s for s in states if s.scope == ibr_scope), None)
            snapshot_at = _as_utc(snapshot.created_at)
            notes: list[str] = []

            board_issue_ids: set[str] = set()
            if board_state is not None and board_state.ingestion_run_id is not None:
                board_issue_ids = set(
                    session.scalars(
                        select(BoardMembershipObservation.issue_id).where(
                            BoardMembershipObservation.ingestion_run_id
                            == board_state.ingestion_run_id
                        )
                    )
                )
            else:
                notes.append(
                    "This snapshot has no pinned IBR-board state; no issue "
                    "can be classified as IBR-linked."
                )

            version_cache: dict[str, JiraIssueVersion | None] = {}

            def latest_version(issue_id: str, high_water_mark: datetime) -> JiraIssueVersion | None:
                if issue_id not in version_cache:
                    version_cache[issue_id] = session.scalar(
                        select(JiraIssueVersion)
                        .where(
                            JiraIssueVersion.issue_id == issue_id,
                            JiraIssueVersion.observed_at <= high_water_mark,
                        )
                        .order_by(JiraIssueVersion.observed_at.desc())
                        .limit(1)
                    )
                return version_cache[issue_id]

            classify_cache: dict[str, tuple[str, str | None, str | None]] = {}

            def classify_issue(
                issue_id: str, high_water_mark: datetime
            ) -> tuple[str, str | None, str | None]:
                """Return (classification, link_basis, board ancestor issue id)."""
                if issue_id in classify_cache:
                    return classify_cache[issue_id]
                result: tuple[str, str | None, str | None]
                if issue_id in board_issue_ids:
                    result = ("ibr_linked", "on_ibr_board", issue_id)
                else:
                    result = ("non_ibr", None, None)
                    current = issue_id
                    seen = {issue_id}
                    for _ in range(MAX_ANCESTOR_DEPTH):
                        version = latest_version(current, high_water_mark)
                        parent = version.parent_issue_id if version else None
                        if not parent or parent in seen:
                            break
                        if parent in board_issue_ids:
                            result = ("ibr_linked", "descendant_of_ibr_item", parent)
                            break
                        seen.add(parent)
                        current = parent
                classify_cache[issue_id] = result
                return result

            github_high_water = max(
                (_as_utc(state.high_water_mark) for state in states if state.source == "github"),
                default=None,
            )

            def linked_pull_requests(issue_id: str) -> list[LinkedPullRequest]:
                if github_high_water is None:
                    return []
                links = session.scalars(
                    select(JiraGitHubRelationship)
                    .where(
                        JiraGitHubRelationship.jira_issue_id == issue_id,
                        JiraGitHubRelationship.github_record_type == "pull_request",
                        JiraGitHubRelationship.first_seen_at <= github_high_water,
                    )
                    .order_by(JiraGitHubRelationship.github_record_id)
                ).all()
                results = []
                for link in links:
                    pull = session.get(GitHubPullRequest, link.github_record_id)
                    results.append(
                        LinkedPullRequest(
                            record_id=link.github_record_id,
                            url=pull.html_url if pull else None,
                        )
                    )
                return results

            jira_issues: list[ClassifiedJiraIssue] = []
            jira_split = WorkSplit()
            population: list[str] = []
            jira_available = team_state is not None and team_state.ingestion_run_id is not None
            if jira_available:
                high_water = _as_utc(team_state.high_water_mark)
                list_floor = snapshot_at - timedelta(days=LIST_WINDOW_DAYS)
                population = list(
                    session.scalars(
                        select(JiraScopeObservation.issue_id).where(
                            JiraScopeObservation.ingestion_run_id == team_state.ingestion_run_id
                        )
                    )
                )
                for issue_id in population:
                    issue = session.get(JiraIssue, issue_id)
                    version = latest_version(issue_id, high_water)
                    if issue is None or version is None:
                        continue
                    active = (version.status_category or "").casefold() != "done"
                    updated = (
                        _as_utc(version.source_updated_at) if version.source_updated_at else None
                    )
                    if not active and (updated is None or updated < list_floor):
                        continue
                    classification, basis, ancestor_id = classify_issue(issue_id, high_water)
                    ancestor = session.get(JiraIssue, ancestor_id) if ancestor_id else None
                    jira_issues.append(
                        ClassifiedJiraIssue(
                            jira_key=issue.issue_key,
                            title=version.summary or "",
                            status=version.status_name or "Unknown",
                            status_category=version.status_category,
                            issue_type=version.issue_type_name,
                            assignee_display_name=version.assignee_display_name,
                            url=issue.web_url,
                            source_updated_at=updated,
                            linked_pull_requests=linked_pull_requests(issue_id),
                            classification=classification,
                            link_basis=basis,
                            ibr_parent_key=ancestor.issue_key if ancestor else None,
                            ibr_parent_url=ancestor.web_url if ancestor else None,
                            active=active,
                        )
                    )
                    if active:
                        if classification == "ibr_linked":
                            jira_split.ibr_linked += 1
                        else:
                            jira_split.non_ibr += 1
                jira_issues.sort(
                    key=lambda item: (
                        item.source_updated_at or snapshot_at,
                        item.jira_key,
                    ),
                    reverse=True,
                )
                jira_message = f"{len(population)} issues carry this team in the Jira Team field."
            else:
                jira_message = (
                    f"This snapshot has no pinned {scope} source; run a refresh with "
                    "the team-field queries configured."
                )
                notes.append(jira_message)

            github_records, github_split, github_available, github_message = self._github(
                session,
                states,
                team,
                snapshot_at,
                classify_issue,
                set(population) if jira_available else set(),
                notes,
            )

            notes.append(
                "Issue priority is not captured in the pinned snapshot; P1 status "
                "cannot be labelled deterministically."
            )
            return TeamWorkClassification(
                snapshot_id=snapshot.id,
                snapshot_name=snapshot.name,
                snapshot_created_at=snapshot_at,
                team_id=team.id,
                team_name=team.name,
                scope=scope,
                jira_available=jira_available,
                jira_message=jira_message,
                github_available=github_available,
                github_message=github_message,
                list_window_days=LIST_WINDOW_DAYS,
                split_window_days=SPLIT_WINDOW_DAYS,
                jira_issues=jira_issues,
                jira_split=jira_split,
                github_records=github_records,
                github_split=github_split,
                data_quality_notes=notes,
            )

    def _github(
        self,
        session: Session,
        states: list[SnapshotSourceState],
        team,
        snapshot_at: datetime,
        classify_issue,
        population_ids: set[str],
        notes: list[str],
    ) -> tuple[list[ClassifiedGitHubRecord], WorkSplit, bool, str]:
        github_states = {
            state.scope.removeprefix("repository:"): state
            for state in states
            if state.source == "github" and state.scope.startswith("repository:")
        }
        if not github_states:
            message = "This snapshot contains no configured GitHub source state."
            notes.append(message)
            return [], WorkSplit(), False, message
        logins = {
            member.github_login.casefold()
            for member in team.members
            if member.github_login and member.active
        }
        list_floor = snapshot_at - timedelta(days=LIST_WINDOW_DAYS)
        split_floor = snapshot_at - timedelta(days=SPLIT_WINDOW_DAYS)

        relationship_cache: dict[tuple[str, str], list[JiraGitHubRelationship]] = {}

        def relationships_for(record_type: str, record_id: str):
            key = (record_type, record_id)
            if key not in relationship_cache:
                relationship_cache[key] = list(
                    session.scalars(
                        select(JiraGitHubRelationship).where(
                            JiraGitHubRelationship.github_record_type == record_type,
                            JiraGitHubRelationship.github_record_id == record_id,
                        )
                    )
                )
            return relationship_cache[key]

        def issue_key_of(issue_id: str) -> str | None:
            issue = session.get(JiraIssue, issue_id)
            return issue.issue_key if issue else None

        records: list[ClassifiedGitHubRecord] = []
        split = WorkSplit()
        seen_records: set[tuple[str, str]] = set()

        def classify_links(
            own_links: list[JiraGitHubRelationship],
            inherited_links: list[JiraGitHubRelationship],
            high_water_mark: datetime,
        ) -> tuple[str, str, list[str], bool]:
            links = [
                (link, "explicit_jira_key")
                for link in own_links
                if _as_utc(link.first_seen_at) <= high_water_mark
            ] + [
                (link, "via_pull_request")
                for link in inherited_links
                if _as_utc(link.first_seen_at) <= high_water_mark
            ]
            if not links:
                return "unlinked", "author_identity", [], False
            keys: list[str] = []
            classification = "non_ibr"
            # An explicit key on the record itself outranks keys inherited
            # from its pull request.
            basis = (
                "explicit_jira_key"
                if any(link_basis == "explicit_jira_key" for _, link_basis in links)
                else "via_pull_request"
            )
            in_population = False
            for link, _link_basis in links:
                key = issue_key_of(link.jira_issue_id)
                if key and key not in keys:
                    keys.append(key)
                if link.jira_issue_id in population_ids:
                    in_population = True
                issue_class, _, _ = classify_issue(link.jira_issue_id, high_water_mark)
                if issue_class == "ibr_linked":
                    classification = "ibr_linked"
            return classification, basis, sorted(keys), in_population

        def add_record(
            record_type: str,
            record_id: str,
            repository: str,
            title: str,
            url: str | None,
            actor_login: str | None,
            occurred_at: datetime | None,
            own_links: list[JiraGitHubRelationship],
            inherited_links: list[JiraGitHubRelationship],
            high_water_mark: datetime,
        ) -> None:
            if (record_type, record_id) in seen_records:
                return
            occurred = _as_utc(occurred_at) if occurred_at else None
            if occurred is None or occurred < list_floor or occurred > snapshot_at:
                return
            relevant_author = bool(actor_login and actor_login.casefold() in logins)
            classification, basis, keys, _in_population = classify_links(
                own_links, inherited_links, high_water_mark
            )
            # Team ownership is identity-based. Jira relationships classify the
            # member's work, but never assign another person's work to the team.
            if not relevant_author:
                return
            seen_records.add((record_type, record_id))
            records.append(
                ClassifiedGitHubRecord(
                    record_type=record_type,
                    record_id=record_id,
                    repository=repository,
                    title=title,
                    url=url,
                    actor_login=actor_login,
                    occurred_at=occurred,
                    jira_keys=keys,
                    classification=classification,
                    link_basis=basis,
                )
            )
            if occurred >= split_floor:
                if classification == "ibr_linked":
                    split.ibr_linked += 1
                elif classification == "non_ibr":
                    split.non_ibr += 1
                else:
                    split.unlinked += 1

        for full_name, state in github_states.items():
            high_water = _as_utc(state.high_water_mark)
            repository = session.scalar(
                select(GitHubRepository).where(GitHubRepository.full_name == full_name)
            )
            if repository is None:
                continue
            pulls = session.scalars(
                select(GitHubPullRequest).where(
                    GitHubPullRequest.repository_id == repository.id,
                    GitHubPullRequest.first_seen_at <= state.high_water_mark,
                )
            ).all()
            for pull in pulls:
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
                latest_activity = (
                    version.source_updated_at
                    or version.merged_at
                    or version.closed_at
                    or version.source_created_at
                )
                if latest_activity and _as_utc(latest_activity) < list_floor:
                    # Reviews and commits never postdate the pull request's own
                    # last activity, so the whole record family is out of window.
                    continue
                pull_links = relationships_for("pull_request", pull.id)
                add_record(
                    "pull_request",
                    pull.id,
                    full_name,
                    version.title,
                    pull.html_url,
                    version.author_login,
                    version.merged_at or version.closed_at or version.source_updated_at,
                    pull_links,
                    [],
                    high_water,
                )
                commit_shas = session.scalars(
                    select(GitHubPullRequestCommit.commit_sha).where(
                        GitHubPullRequestCommit.pull_request_id == pull.id
                    )
                ).all()
                for sha in commit_shas:
                    commit = session.get(GitHubCommit, sha)
                    if commit is None or commit.first_seen_at > state.high_water_mark:
                        continue
                    add_record(
                        "commit",
                        sha,
                        full_name,
                        (commit.message or "").splitlines()[0] if commit.message else "",
                        commit.html_url,
                        commit.author_login,
                        commit.committed_at or commit.authored_at,
                        relationships_for("commit", sha),
                        pull_links,
                        high_water,
                    )
                reviews = session.scalars(
                    select(GitHubReview).where(
                        GitHubReview.pull_request_id == pull.id,
                        GitHubReview.observed_at <= state.high_water_mark,
                    )
                ).all()
                for review in reviews:
                    add_record(
                        "review",
                        review.id,
                        full_name,
                        f"Review on PR #{pull.number}: {version.title}",
                        review.html_url or pull.html_url,
                        review.author_login,
                        review.submitted_at,
                        relationships_for("review", review.id),
                        pull_links,
                        high_water,
                    )
        records.sort(
            key=lambda item: (item.occurred_at or snapshot_at, item.record_id),
            reverse=True,
        )
        message = (
            "GitHub records within the list window are classified through explicit "
            "Jira keys, inherited pull-request keys, or configured author identity."
        )
        return records, split, True, message
