"""Build a snapshot-safe Jira Feature hierarchy for an IBR item."""

from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.persistence.models import (
    BoardMembershipObservation,
    GitHubCommit,
    GitHubPullRequest,
    GitHubPullRequestVersion,
    GitHubRepository,
    GitHubReview,
    JiraGitHubRelationship,
    JiraIssue,
    JiraIssueVersion,
    JiraRelationship,
    Person,
    Snapshot,
    SnapshotSourceState,
)
from engineering_intelligence.presentations.dashboard import SourceFreshness
from engineering_intelligence.presentations.feature import (
    DeliveryAvailability,
    FeatureDetail,
    FeatureHierarchyNode,
    FeatureSummary,
    FeatureTimelineEvent,
    GitHubDeliveryRecord,
    JiraLinkEvidence,
    PersonEvidence,
)
from engineering_intelligence.snapshots.organization import portfolio_board_id_for_snapshot


class FeatureQuery:
    def __init__(self, sessions: sessionmaker[Session], *, max_nodes: int = 1000) -> None:
        self.sessions = sessions
        self.max_nodes = max_nodes

    def get(
        self,
        snapshot_identifier: str,
        issue_key: str,
        *,
        ibr_board_id: int | None = None,
    ) -> FeatureDetail:
        with self.sessions() as session:
            snapshot = _snapshot(session, snapshot_identifier)
            ibr_board_id = ibr_board_id or portfolio_board_id_for_snapshot(snapshot)
            states = session.scalars(
                select(SnapshotSourceState)
                .where(SnapshotSourceState.snapshot_id == snapshot.id)
                .order_by(SnapshotSourceState.source, SnapshotSourceState.scope)
            ).all()
            state = next(
                (item for item in states if item.scope == f"board:{ibr_board_id}"),
                None,
            )
            if state is None or state.ingestion_run_id is None:
                raise ValueError(f"Snapshot has no IBR board {ibr_board_id} source")
            root = session.scalar(select(JiraIssue).where(JiraIssue.issue_key == issue_key))
            if root is None or not session.scalar(
                select(BoardMembershipObservation.id).where(
                    BoardMembershipObservation.ingestion_run_id == state.ingestion_run_id,
                    BoardMembershipObservation.issue_id == root.id,
                )
            ):
                raise ValueError(
                    f"Jira issue {issue_key} is not an IBR item in snapshot {snapshot_identifier}"
                )
            versions: dict[str, JiraIssueVersion] = {}
            issues: dict[str, JiraIssue] = {}
            contributors: list[PersonEvidence] = []
            links: list[JiraLinkEvidence] = []
            timeline: list[FeatureTimelineEvent] = []
            hierarchy = self._node(
                session,
                root,
                state.high_water_mark,
                0,
                "feature",
                set(),
                issues,
                versions,
                contributors,
                links,
                timeline,
            )
            status_counts = dict(
                sorted(Counter(version.status_name or "Unknown" for version in versions.values()).items())
            )
            blocking_links = sum(item.is_blocking_relationship for item in links)
            root_version = versions[root.id]
            github_delivery = self._github_delivery(
                session,
                states,
                root.id,
                issues,
            )
            contributors.extend(
                PersonEvidence(
                    jira_account_id=None,
                    github_login=record.actor_login,
                    display_name=record.actor_login,
                    relationship_type={
                        "pull_request": "pull_request_author",
                        "commit": "commit_author",
                        "review": "reviewer",
                    }[record.record_type],
                    direct_issue_key=record.direct_jira_key,
                    direct_issue_url=record.direct_jira_url,
                    source_url=record.url,
                    rolled_up_to_feature=record.rolled_up_to_feature,
                )
                for record in github_delivery.records
                if record.actor_login
            )
            people_keys = {
                _person_identity_key(session, item)
                for item in contributors
            }
            return FeatureDetail(
                snapshot_id=snapshot.id,
                snapshot_name=snapshot.name,
                snapshot_created_at=_as_utc(snapshot.created_at),
                source_freshness=[
                    SourceFreshness(
                        source=item.source,
                        scope=item.scope,
                        observed_at=_as_utc(item.high_water_mark),
                        ingestion_run_id=item.ingestion_run_id,
                    )
                    for item in states
                ],
                feature_key=root.issue_key,
                feature_url=root.web_url,
                original_issue_type=root_version.issue_type_name or "Unknown",
                title=root_version.summary,
                status=root_version.status_name or "Unknown",
                team_name=root_version.team_name,
                hierarchy=hierarchy,
                summary=FeatureSummary(
                    total_issues=len(issues),
                    descendant_issues=max(len(issues) - 1, 0),
                    related_people=len(people_keys),
                    jira_links=len(links),
                    blocking_links=blocking_links,
                    linked_delivery_records=len(github_delivery.records),
                    status_counts=status_counts,
                ),
                contributors=sorted(
                    contributors,
                    key=lambda item: (item.display_name.casefold(), item.direct_issue_key),
                ),
                jira_links=sorted(
                    links,
                    key=lambda item: (
                        item.source_issue_key,
                        item.relationship,
                        item.target_issue_key or "",
                    ),
                ),
                timeline=sorted(
                    timeline,
                    key=lambda item: (item.occurred_at, item.issue_key, item.event_type),
                ),
                github_delivery=github_delivery,
                data_quality_notes=[
                    (
                        "Jira contributors reflect current assignees observed in this "
                        "snapshot; assignment changelog ingestion is not yet available."
                    ),
                    (
                        "Timeline events contain created, last-updated, and resolved facts; "
                        "full Jira status history is not yet available."
                    ),
                    (
                        "Jira link direction and target metadata are first-observed "
                        "evidence; relationship-change history is not yet available."
                    ),
                ],
            )

    @staticmethod
    def _github_delivery(
        session: Session,
        states: list[SnapshotSourceState],
        root_issue_id: str,
        issues: dict[str, JiraIssue],
    ) -> DeliveryAvailability:
        github_states = {
            state.scope.removeprefix("repository:"): state
            for state in states
            if state.source == "github" and state.scope.startswith("repository:")
        }
        if not github_states:
            return DeliveryAvailability(
                available=False,
                message="This snapshot contains no configured GitHub source state.",
            )
        max_high_water = max(state.high_water_mark for state in github_states.values())
        relationships = session.scalars(
            select(JiraGitHubRelationship)
            .where(
                JiraGitHubRelationship.jira_issue_id.in_(issues),
                JiraGitHubRelationship.first_seen_at <= max_high_water,
            )
            .order_by(
                JiraGitHubRelationship.github_record_type,
                JiraGitHubRelationship.github_record_id,
                JiraGitHubRelationship.jira_issue_id,
            )
        ).all()
        records: list[GitHubDeliveryRecord] = []
        for relationship in relationships:
            issue = issues[relationship.jira_issue_id]
            if relationship.github_record_type == "pull_request":
                pull = session.get(GitHubPullRequest, relationship.github_record_id)
                if pull is None:
                    continue
                repository = session.get(GitHubRepository, pull.repository_id)
                state = github_states.get(repository.full_name if repository else "")
                if repository is None or state is None:
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
                records.append(
                    _delivery_record(
                        "pull_request",
                        pull.id,
                        repository.full_name,
                        version.title,
                        "merged" if version.merged_at else version.state,
                        pull.html_url,
                        version.author_login,
                        version.merged_at or version.closed_at or version.source_updated_at,
                        issue,
                        root_issue_id,
                        relationship,
                    )
                )
                reviews = session.scalars(
                    select(GitHubReview)
                    .where(
                        GitHubReview.pull_request_id == pull.id,
                        GitHubReview.observed_at <= state.high_water_mark,
                    )
                    .order_by(GitHubReview.submitted_at, GitHubReview.id)
                ).all()
                records.extend(
                    _delivery_record(
                        "review",
                        review.id,
                        repository.full_name,
                        f"Review on PR #{pull.number}: {version.title}",
                        review.state,
                        review.html_url or pull.html_url,
                        review.author_login,
                        review.submitted_at,
                        issue,
                        root_issue_id,
                        relationship,
                    )
                    for review in reviews
                )
            elif relationship.github_record_type == "commit":
                commit = session.get(GitHubCommit, relationship.github_record_id)
                if commit is None:
                    continue
                repository = session.get(GitHubRepository, commit.repository_id)
                state = github_states.get(repository.full_name if repository else "")
                if (
                    repository is None
                    or state is None
                    or commit.first_seen_at > state.high_water_mark
                ):
                    continue
                records.append(
                    _delivery_record(
                        "commit",
                        commit.sha,
                        repository.full_name,
                        commit.message.splitlines()[0],
                        "committed",
                        commit.html_url,
                        commit.author_login or commit.author_name,
                        commit.committed_at or commit.authored_at,
                        issue,
                        root_issue_id,
                        relationship,
                    )
                )
        records.sort(
            key=lambda item: (
                item.occurred_at or datetime.min.replace(tzinfo=UTC),
                item.record_type,
                item.record_id,
                item.direct_jira_key,
            )
        )
        return DeliveryAvailability(
            available=True,
            message=(
                "GitHub delivery evidence collected from configured repositories; "
                "explicit Jira-key relationships are confirmed."
            ),
            records=records,
        )

    def _node(
        self,
        session: Session,
        issue: JiraIssue,
        high_water_mark: datetime,
        depth: int,
        relationship: str,
        ancestors: set[str],
        issues: dict[str, JiraIssue],
        versions: dict[str, JiraIssueVersion],
        contributors: list[PersonEvidence],
        links: list[JiraLinkEvidence],
        timeline: list[FeatureTimelineEvent],
    ) -> FeatureHierarchyNode:
        if issue.id in ancestors:
            raise ValueError(f"Jira hierarchy cycle detected at {issue.issue_key}")
        if len(issues) >= self.max_nodes:
            raise ValueError(f"Feature hierarchy exceeds the {self.max_nodes}-node safety limit")
        version = _version_as_of(session, issue.id, high_water_mark)
        if version is None:
            raise ValueError(
                f"No issue version for {issue.issue_key} at snapshot high-water mark"
            )
        issues[issue.id] = issue
        versions[issue.id] = version
        direct_assignee = None
        if version.assignee_display_name:
            direct_assignee = PersonEvidence(
                jira_account_id=version.assignee_account_id,
                display_name=version.assignee_display_name,
                direct_issue_key=issue.issue_key,
                direct_issue_url=issue.web_url,
                rolled_up_to_feature=depth > 0,
            )
            contributors.append(direct_assignee)
        self._timeline(issue, version, timeline)
        self._links(session, issue, high_water_mark, links)

        child_records = session.scalars(
            select(JiraRelationship)
            .where(
                JiraRelationship.relationship_type == "parent",
                JiraRelationship.target_issue_id == issue.id,
                JiraRelationship.first_seen_at <= high_water_mark,
            )
            .order_by(JiraRelationship.source_issue_id)
        ).all()
        children = []
        next_ancestors = ancestors | {issue.id}
        for child_record in child_records:
            child = session.get(JiraIssue, child_record.source_issue_id)
            if child is None or _version_as_of(session, child.id, high_water_mark) is None:
                continue
            child_version = _version_as_of(session, child.id, high_water_mark)
            assert child_version is not None
            child_relationship = (
                "subtask"
                if (child_version.issue_type_name or "").casefold() in {"sub-task", "subtask"}
                else "child"
            )
            children.append(
                self._node(
                    session,
                    child,
                    high_water_mark,
                    depth + 1,
                    child_relationship,
                    next_ancestors,
                    issues,
                    versions,
                    contributors,
                    links,
                    timeline,
                )
            )
        children.sort(key=lambda item: item.jira_key)
        people = {
            item.jira_account_id or item.display_name.casefold()
            for item in contributors
            if item.direct_issue_key
            in {issue.issue_key, *(node.jira_key for node in _flatten(children))}
        }
        return FeatureHierarchyNode(
            jira_id=issue.id,
            jira_key=issue.issue_key,
            title=version.summary,
            issue_type=version.issue_type_name or "Unknown",
            status=version.status_name or "Unknown",
            status_category=version.status_category,
            team_name=version.team_name,
            description_text=version.description_text,
            source_updated_at=(
                _as_utc(version.source_updated_at) if version.source_updated_at else None
            ),
            gravitee_customers=version.gravitee_customers or [],
            url=issue.web_url,
            relationship_from_parent=relationship,
            depth=depth,
            direct_assignee=direct_assignee,
            rollup_people_count=len(people),
            children=children,
        )

    @staticmethod
    def _timeline(
        issue: JiraIssue,
        version: JiraIssueVersion,
        timeline: list[FeatureTimelineEvent],
    ) -> None:
        if version.source_created_at:
            timeline.append(
                FeatureTimelineEvent(
                    occurred_at=_as_utc(version.source_created_at),
                    event_type="created",
                    issue_key=issue.issue_key,
                    issue_url=issue.web_url,
                    description=f"{version.issue_type_name or 'Issue'} created",
                )
            )
        if version.source_updated_at:
            timeline.append(
                FeatureTimelineEvent(
                    occurred_at=_as_utc(version.source_updated_at),
                    event_type="last_updated",
                    issue_key=issue.issue_key,
                    issue_url=issue.web_url,
                    description=f"Last observed update; status {version.status_name or 'Unknown'}",
                )
            )
        if version.resolved_at:
            timeline.append(
                FeatureTimelineEvent(
                    occurred_at=_as_utc(version.resolved_at),
                    event_type="resolved",
                    issue_key=issue.issue_key,
                    issue_url=issue.web_url,
                    description="Issue resolved",
                )
            )

    @staticmethod
    def _links(
        session: Session,
        issue: JiraIssue,
        high_water_mark: datetime,
        links: list[JiraLinkEvidence],
    ) -> None:
        records = session.scalars(
            select(JiraRelationship).where(
                JiraRelationship.source_issue_id == issue.id,
                JiraRelationship.relationship_type == "issue_link",
                JiraRelationship.first_seen_at <= high_water_mark,
            )
        ).all()
        for record in records:
            target = session.get(JiraIssue, record.target_issue_id)
            target_version = (
                _version_as_of(session, target.id, high_water_mark) if target else None
            )
            description = record.source_description or "linked to"
            links.append(
                JiraLinkEvidence(
                    source_issue_key=issue.issue_key,
                    relationship=description,
                    target_issue_key=(
                        target.issue_key if target else record.target_issue_key
                    ),
                    target_title=(
                        target_version.summary
                        if target_version
                        else record.target_summary
                    ),
                    target_status=(
                        target_version.status_name
                        if target_version
                        else record.target_status
                    ),
                    target_url=target.web_url if target else record.target_url,
                    is_blocking_relationship="block" in description.casefold(),
                )
            )


def _snapshot(session: Session, identifier: str) -> Snapshot:
    snapshot = session.get(Snapshot, identifier)
    if snapshot is None:
        snapshot = session.scalar(select(Snapshot).where(Snapshot.name == identifier))
    if snapshot is None:
        raise ValueError(f"Snapshot not found: {identifier}")
    return snapshot


def _version_as_of(
    session: Session,
    issue_id: str,
    high_water_mark: datetime,
) -> JiraIssueVersion | None:
    return session.scalar(
        select(JiraIssueVersion)
        .where(
            JiraIssueVersion.issue_id == issue_id,
            JiraIssueVersion.observed_at <= high_water_mark,
        )
        .order_by(JiraIssueVersion.observed_at.desc())
        .limit(1)
    )


def _flatten(nodes: list[FeatureHierarchyNode]) -> list[FeatureHierarchyNode]:
    return [node for item in nodes for node in [item, *_flatten(item.children)]]


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _delivery_record(
    record_type: str,
    record_id: str,
    repository: str,
    title: str,
    state: str,
    url: str,
    actor_login: str | None,
    occurred_at: datetime | None,
    issue: JiraIssue,
    root_issue_id: str,
    relationship: JiraGitHubRelationship,
) -> GitHubDeliveryRecord:
    return GitHubDeliveryRecord(
        record_type=record_type,
        record_id=record_id,
        repository=repository,
        title=title,
        state=state,
        url=url,
        actor_login=actor_login,
        occurred_at=_as_utc(occurred_at) if occurred_at else None,
        direct_jira_key=issue.issue_key,
        direct_jira_url=issue.web_url,
        rolled_up_to_feature=issue.id != root_issue_id,
        relationship_type=relationship.relationship_type,
        confidence=relationship.confidence,
        evidence=relationship.evidence,
    )


def _person_identity_key(session: Session, evidence: PersonEvidence) -> str:
    person_id = None
    if evidence.jira_account_id:
        person_id = session.scalar(
            select(Person.id).where(Person.jira_account_id == evidence.jira_account_id)
        )
    if person_id is None and evidence.github_login:
        person_id = session.scalar(
            select(Person.id).where(
                func.lower(Person.github_login) == evidence.github_login.casefold()
            )
        )
    if person_id:
        return f"person:{person_id}"
    if evidence.jira_account_id:
        return f"jira:{evidence.jira_account_id}"
    if evidence.github_login:
        return f"github:{evidence.github_login.casefold()}"
    return f"display:{evidence.display_name.casefold()}"
