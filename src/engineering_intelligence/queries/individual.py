"""Snapshot-safe Individual work context from explicit Jira and GitHub evidence."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import TeamsConfig
from engineering_intelligence.persistence.models import (
    Board,
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
    JiraScopeObservation,
    Person,
    Snapshot,
    SnapshotSourceState,
    Team,
    TeamMembership,
)
from engineering_intelligence.presentations.dashboard import EvidenceLink, SourceFreshness
from engineering_intelligence.presentations.feature import GitHubDeliveryRecord, JiraLinkEvidence
from engineering_intelligence.presentations.people import (
    BoardEvidence,
    CondensedWorkItem,
    IndividualDetail,
    JiraWorkRelationship,
    MembershipEvidence,
    PerformanceSignal,
    SuppressedSignalRule,
)
from engineering_intelligence.queries.feature import FeatureQuery
from engineering_intelligence.snapshots.organization import (
    organization_config_for_snapshot,
    portfolio_board_id_for_snapshot,
)


class IndividualQuery:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        max_feature_nodes: int = 250,
        teams_config: TeamsConfig | None = None,
    ) -> None:
        self.sessions = sessions
        self.feature_query = FeatureQuery(sessions, max_nodes=max_feature_nodes)
        self.teams_config = teams_config

    def get(self, snapshot_identifier: str, person_identifier: str) -> IndividualDetail:
        with self.sessions() as session:
            snapshot = _snapshot(session, snapshot_identifier)
            if self.teams_config is not None:
                teams_config = organization_config_for_snapshot(
                    snapshot,
                    self.teams_config,
                )
            else:
                teams_config = None
            states = session.scalars(
                select(SnapshotSourceState)
                .where(SnapshotSourceState.snapshot_id == snapshot.id)
                .order_by(SnapshotSourceState.source, SnapshotSourceState.scope)
            ).all()
            portfolio_scope = f"board:{portfolio_board_id_for_snapshot(snapshot)}"
            ibr_state = next((state for state in states if state.scope == portfolio_scope), None)
            if ibr_state is None or ibr_state.ingestion_run_id is None:
                raise ValueError("Snapshot has no configured portfolio board source")
            accountable_state = next(
                (state for state in states if state.scope == "query:accountable-active-work"),
                None,
            )
            high_water_mark = max(
                state.high_water_mark
                for state in (ibr_state, accountable_state)
                if state is not None
            )
            person = _person(session, person_identifier)
            memberships = _memberships(session, person.id, snapshot.created_at)
            latest_versions = _latest_jira_versions(session, high_water_mark)
            roots = set(
                session.scalars(
                    select(BoardMembershipObservation.issue_id).where(
                        BoardMembershipObservation.ingestion_run_id
                        == ibr_state.ingestion_run_id
                    )
                ).all()
            )
            parents = {
                relationship.source_issue_id: relationship.target_issue_id
                for relationship in session.scalars(
                    select(JiraRelationship).where(
                        JiraRelationship.relationship_type == "parent",
                        JiraRelationship.first_seen_at <= high_water_mark,
                    )
                )
            }
            accountable_issue_ids = (
                set(
                    session.scalars(
                        select(JiraScopeObservation.issue_id).where(
                            JiraScopeObservation.ingestion_run_id
                            == accountable_state.ingestion_run_id
                        )
                    ).all()
                )
                if accountable_state is not None
                and accountable_state.ingestion_run_id is not None
                else set()
            )
            board_memberships = _board_memberships(session, states, snapshot)
            jira_work = _jira_work(
                session,
                person,
                latest_versions,
                roots,
                parents,
                accountable_issue_ids,
                board_memberships,
            )
            github = _github_contributions(
                session,
                person,
                states,
                latest_versions,
                roots,
                parents,
            )
        feature_keys = sorted(
            {
                relationship.feature_key
                for relationship in jira_work
                if relationship.feature_key is not None
            }
        )
        blockers: dict[tuple[str, str, str | None], JiraLinkEvidence] = {}
        for key in feature_keys:
            feature = self.feature_query.get(snapshot_identifier, key)
            for link in feature.jira_links:
                if link.is_blocking_relationship:
                    blockers[(link.source_issue_key, link.relationship, link.target_issue_key)] = (
                        link
                    )
        signals = _signals(jira_work, github, memberships)
        context_signals, suppressed_signal_rules = _context_signals(
            jira_work,
            github,
            memberships,
            person,
            _as_utc(snapshot.created_at),
            teams_config,
        )
        signals.extend(context_signals)
        notes = [
            (
                "Jira work reflects the assignee on the issue version valid at this "
                "snapshot; full assignment history is not collected yet."
            ),
            (
                "GitHub evidence is limited to configured repositories. Jira and IBR "
                "associations are shown when an explicit Jira key is present; unlinked "
                "activity remains visible."
            ),
            (
                "Signals are evidence-backed prompts or facts, not ratings, rankings, "
                "or performance conclusions."
            ),
        ]
        if not person.jira_account_id:
            notes.append("Jira identity is not mapped for this person.")
        if not person.github_login:
            notes.append("GitHub identity is not mapped for this person.")
        return IndividualDetail(
            snapshot_id=snapshot.id,
            snapshot_name=snapshot.name,
            snapshot_created_at=_as_utc(snapshot.created_at),
            organization_config_hash=snapshot.organization_config_hash,
            source_config_hash=snapshot.source_config_hash,
            source_freshness=[
                SourceFreshness(
                    source=state.source,
                    scope=state.scope,
                    observed_at=_as_utc(state.high_water_mark),
                    ingestion_run_id=state.ingestion_run_id,
                )
                for state in states
            ],
            person_id=person.id,
            display_name=person.display_name,
            preferred_name=person.preferred_name,
            role=person.role,
            manager_person_id=person.manager_person_id,
            active=person.active,
            jira_account_id=person.jira_account_id,
            github_login=person.github_login,
            identity_mapping_state=_identity_state(person),
            memberships=memberships,
            current_work=[
                CondensedWorkItem(
                    jira_key=item.direct_issue_key,
                    title=item.direct_issue_title,
                    url=item.direct_issue_url,
                    status=item.direct_issue_status,
                    issue_type=item.direct_issue_type,
                    target_date=item.target_date,
                    target_date_value=item.target_date_value,
                    boards=item.boards,
                    feature_key=item.feature_key,
                    feature_title=item.feature_title,
                    feature_url=item.feature_url,
                )
                for item in jira_work
                if item.in_flight
            ],
            jira_work=jira_work,
            github_contributions=github,
            blockers_and_dependencies=sorted(
                blockers.values(),
                key=lambda item: (
                    item.source_issue_key,
                    item.relationship,
                    item.target_issue_key or "",
                ),
            ),
            signals=signals,
            suppressed_signal_rules=suppressed_signal_rules,
            data_quality_notes=notes,
        )


def _snapshot(session: Session, identifier: str) -> Snapshot:
    snapshot = session.get(Snapshot, identifier)
    if snapshot is None:
        snapshot = session.scalar(select(Snapshot).where(Snapshot.name == identifier))
    if snapshot is None:
        raise ValueError(f"Snapshot not found: {identifier}")
    return snapshot


def _person(session: Session, identifier: str) -> Person:
    normalized = identifier.casefold()
    matches = session.scalars(
        select(Person).where(
            (func.lower(Person.id) == normalized)
            | (func.lower(Person.display_name) == normalized)
            | (func.lower(Person.preferred_name) == normalized)
            | (func.lower(Person.github_login) == normalized)
            | (func.lower(Person.jira_account_id) == normalized)
        )
    ).all()
    if len(matches) != 1:
        raise ValueError(f"Person not found or ambiguous: {identifier}")
    return matches[0]


def _memberships(
    session: Session,
    person_id: str,
    snapshot_at: datetime,
) -> list[MembershipEvidence]:
    on_date = snapshot_at.date()
    records = session.scalars(
        select(TeamMembership)
        .where(TeamMembership.person_id == person_id)
        .order_by(TeamMembership.starts_on, TeamMembership.team_id)
    ).all()
    results = []
    for record in records:
        team = session.get(Team, record.team_id)
        results.append(
            MembershipEvidence(
                team_id=record.team_id,
                team_name=team.name if team else record.team_id,
                starts_on=record.starts_on,
                ends_on=record.ends_on,
                is_primary=record.is_primary,
                current_at_snapshot=(
                    record.starts_on <= on_date
                    and (record.ends_on is None or record.ends_on >= on_date)
                ),
            )
        )
    return results


def _latest_jira_versions(
    session: Session,
    high_water_mark: datetime,
) -> dict[str, JiraIssueVersion]:
    records = session.scalars(
        select(JiraIssueVersion)
        .where(JiraIssueVersion.observed_at <= high_water_mark)
        .order_by(JiraIssueVersion.issue_id, JiraIssueVersion.observed_at.desc())
    ).all()
    latest: dict[str, JiraIssueVersion] = {}
    for record in records:
        latest.setdefault(record.issue_id, record)
    return latest


def _root_id(issue_id: str, roots: set[str], parents: dict[str, str]) -> str | None:
    visited = set()
    current = issue_id
    while current not in visited:
        if current in roots:
            return current
        visited.add(current)
        current = parents.get(current, "")
        if not current:
            return None
    return None


def _board_memberships(
    session: Session,
    states: list[SnapshotSourceState],
    snapshot: Snapshot,
) -> dict[str, list[BoardEvidence]]:
    configured_boards = {}
    if snapshot.source_config is not None:
        configured_boards = {
            int(board["id"]): board
            for board in snapshot.source_config.get("jira", {}).get("boards", [])
        }
    results: dict[str, list[BoardEvidence]] = {}
    for state in states:
        if (
            state.source != "jira"
            or not state.scope.startswith("board:")
            or state.ingestion_run_id is None
        ):
            continue
        board_id = int(state.scope.removeprefix("board:"))
        board = session.get(Board, board_id)
        configured = configured_boards.get(board_id, {})
        if board is None:
            continue
        evidence = BoardEvidence(
            board_id=board_id,
            board_name=board.name,
            board_url=str(configured.get("url") or board.source_url),
            role=configured.get("role"),
        )
        issue_ids = session.scalars(
            select(BoardMembershipObservation.issue_id).where(
                BoardMembershipObservation.ingestion_run_id == state.ingestion_run_id
            )
        ).all()
        for issue_id in issue_ids:
            results.setdefault(issue_id, []).append(evidence)
    for memberships in results.values():
        memberships.sort(key=lambda item: (item.board_name, item.board_id))
    return results


def _jira_work(
    session: Session,
    person: Person,
    versions: dict[str, JiraIssueVersion],
    roots: set[str],
    parents: dict[str, str],
    accountable_issue_ids: set[str],
    board_memberships: dict[str, list[BoardEvidence]],
) -> list[JiraWorkRelationship]:
    if not person.jira_account_id:
        return []
    results = []
    for issue_id, version in versions.items():
        if version.assignee_account_id != person.jira_account_id:
            continue
        root_id = _root_id(issue_id, roots, parents)
        if root_id is None and issue_id not in accountable_issue_ids:
            continue
        issue = session.get(JiraIssue, issue_id)
        root = session.get(JiraIssue, root_id) if root_id else None
        root_version = versions.get(root_id) if root_id else None
        if issue is None or (root_id and (root is None or root_version is None)):
            continue
        rolled_up = root_id is not None and issue_id != root_id
        issue_type = version.issue_type_name or "Unknown"
        relationship_type = (
            "active_assignment_outside_ibr"
            if root_id is None
            else "high_level_assignee"
            if not rolled_up
            else "subtask_assignee"
            if issue_type.casefold() in {"sub-task", "subtask"}
            else "child_issue_assignee"
        )
        results.append(
            JiraWorkRelationship(
                feature_key=root.issue_key if root else None,
                feature_title=root_version.summary if root_version else None,
                feature_description_text=root_version.description_text if root_version else None,
                feature_url=root.web_url if root else None,
                feature_status=(root_version.status_name or "Unknown") if root_version else None,
                direct_issue_key=issue.issue_key,
                direct_issue_title=version.summary,
                direct_issue_description_text=version.description_text,
                direct_issue_url=issue.web_url,
                direct_issue_status=version.status_name or "Unknown",
                direct_issue_type=issue_type,
                direct_issue_updated_at=_as_utc(version.source_updated_at),
                target_date=version.target_date,
                target_date_value=version.target_date_value,
                boards=board_memberships.get(issue_id, []),
                relationship_type=relationship_type,
                rolled_up_to_feature=rolled_up,
                active=(version.status_category or "").casefold() != "done",
                in_flight=(version.status_category or "").casefold() == "indeterminate",
                evidence=[
                    EvidenceLink(
                        label=f"{issue.issue_key}: {version.summary}",
                        url=issue.web_url,
                        jira_key=issue.issue_key,
                        title=version.summary,
                    )
                ],
            )
        )
    return sorted(results, key=lambda item: (item.feature_key or "", item.direct_issue_key))


def _github_contributions(
    session: Session,
    person: Person,
    states: list[SnapshotSourceState],
    versions: dict[str, JiraIssueVersion],
    roots: set[str],
    parents: dict[str, str],
) -> list[GitHubDeliveryRecord]:
    if not person.github_login:
        return []
    github_states = {
        state.scope.removeprefix("repository:"): state
        for state in states
        if state.source == "github" and state.scope.startswith("repository:")
    }
    if not github_states:
        return []
    records: list[GitHubDeliveryRecord] = []
    relationships = session.scalars(select(JiraGitHubRelationship)).all()
    by_record: dict[tuple[str, str], list[JiraGitHubRelationship]] = {}
    for relationship in relationships:
        by_record.setdefault(
            (relationship.github_record_type, relationship.github_record_id),
            [],
        ).append(relationship)
    for pull in session.scalars(select(GitHubPullRequest)).all():
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
        if version and (version.author_login or "").casefold() == person.github_login.casefold():
            records.extend(
                _github_records_for_relationships(
                    session,
                    by_record.get(("pull_request", pull.id), []),
                    versions,
                    roots,
                    parents,
                    "pull_request",
                    pull.id,
                    repository.full_name,
                    version.title,
                    "merged" if version.merged_at else version.state,
                    pull.html_url,
                    version.author_login,
                    version.merged_at or version.closed_at or version.source_updated_at,
                )
            )
        for review in session.scalars(
            select(GitHubReview).where(
                GitHubReview.pull_request_id == pull.id,
                GitHubReview.observed_at <= state.high_water_mark,
            )
        ):
            if (review.author_login or "").casefold() != person.github_login.casefold():
                continue
            records.extend(
                _github_records_for_relationships(
                    session,
                    by_record.get(("pull_request", pull.id), []),
                    versions,
                    roots,
                    parents,
                    "review",
                    review.id,
                    repository.full_name,
                    f"Review on PR #{pull.number}: {version.title if version else pull.id}",
                    review.state,
                    review.html_url or pull.html_url,
                    review.author_login,
                    review.submitted_at,
                )
            )
    for commit in session.scalars(select(GitHubCommit)).all():
        repository = session.get(GitHubRepository, commit.repository_id)
        state = github_states.get(repository.full_name if repository else "")
        actor = commit.author_login or commit.author_name
        if (
            repository is None
            or state is None
            or commit.first_seen_at > state.high_water_mark
            or (actor or "").casefold() != person.github_login.casefold()
        ):
            continue
        records.extend(
            _github_records_for_relationships(
                session,
                by_record.get(("commit", commit.sha), []),
                versions,
                roots,
                parents,
                "commit",
                commit.sha,
                repository.full_name,
                commit.message.splitlines()[0],
                "committed",
                commit.html_url,
                actor,
                commit.committed_at or commit.authored_at,
            )
        )
    unique = {
        (record.record_type, record.record_id, record.direct_jira_key): record
        for record in records
    }
    return sorted(
        unique.values(),
        key=lambda item: (
            item.occurred_at or datetime.min.replace(tzinfo=UTC),
            item.record_type,
            item.record_id,
        ),
        reverse=True,
    )


def _github_records_for_relationships(
    session: Session,
    relationships: list[JiraGitHubRelationship],
    versions: dict[str, JiraIssueVersion],
    roots: set[str],
    parents: dict[str, str],
    record_type: str,
    record_id: str,
    repository: str,
    title: str,
    state: str,
    url: str,
    actor: str | None,
    occurred_at: datetime | None,
) -> list[GitHubDeliveryRecord]:
    results = []
    for relationship in relationships:
        issue = session.get(JiraIssue, relationship.jira_issue_id)
        root_id = _root_id(relationship.jira_issue_id, roots, parents)
        root = session.get(JiraIssue, root_id) if root_id else None
        if issue is None:
            continue
        results.append(
            GitHubDeliveryRecord(
                record_type=record_type,
                record_id=record_id,
                repository=repository,
                title=title,
                state=state,
                url=url,
                actor_login=actor,
                occurred_at=_as_utc(occurred_at) if occurred_at else None,
                direct_jira_key=issue.issue_key,
                direct_jira_url=issue.web_url,
                rolled_up_to_feature=root is not None and issue.id != root.id,
                relationship_type=relationship.relationship_type,
                confidence=relationship.confidence,
                evidence=relationship.evidence,
            )
        )
    if not results:
        results.append(
            GitHubDeliveryRecord(
                record_type=record_type,
                record_id=record_id,
                repository=repository,
                title=title,
                state=state,
                url=url,
                actor_login=actor,
                occurred_at=_as_utc(occurred_at) if occurred_at else None,
                direct_jira_key=None,
                direct_jira_url=None,
                rolled_up_to_feature=False,
                relationship_type="unlinked_activity",
                confidence="confirmed",
                evidence="GitHub actor identity matches the configured person.",
            )
        )
    return results


def _signals(
    jira_work: list[JiraWorkRelationship],
    github: list[GitHubDeliveryRecord],
    memberships: list[MembershipEvidence],
) -> list[PerformanceSignal]:
    signals = []
    active = [item for item in jira_work if item.active]
    if active:
        signals.append(
            PerformanceSignal(
                signal_type="verified_fact",
                title="Current Jira work context observed",
                explanation=(
                    "The snapshot shows current Jira assignments. This is context, "
                    "not a conclusion about output or ownership."
                ),
                confidence="confirmed",
                evidence=[evidence for item in active for evidence in item.evidence],
            )
        )
    reviews = [item for item in github if item.record_type == "review"]
    if reviews:
        signals.append(
            PerformanceSignal(
                signal_type="verified_fact",
                title="Code-review collaboration observed",
                explanation=(
                    "Configured GitHub evidence contains review activity."
                ),
                confidence="confirmed",
                evidence=[
                    EvidenceLink(label=item.title, url=item.url) for item in reviews
                ],
            )
        )
    current_primary = {
        item.team_name
        for item in memberships
        if item.current_at_snapshot and item.is_primary
    }
    if len(current_primary) > 1:
        signals.append(
            PerformanceSignal(
                signal_type="external_context",
                title="Multiple primary memberships overlap",
                explanation=(
                    "The snapshot contains overlapping primary team memberships; "
                    "interpret work context cautiously."
                ),
                confidence="confirmed",
                evidence=[],
            )
        )
    return signals


def _context_signals(
    jira_work: list[JiraWorkRelationship],
    github: list[GitHubDeliveryRecord],
    memberships: list[MembershipEvidence],
    person: Person,
    evaluated_at: datetime,
    teams_config: TeamsConfig | None,
) -> tuple[list[PerformanceSignal], list[SuppressedSignalRule]]:
    """Return neutral context prompts only inside a verified evidence boundary."""
    rules = (
        (
            "individual-broad-concurrent-context",
            "Broad concurrent work context",
            ["verified current roster", "Jira identity"],
        ),
        (
            "individual-cross-source-visibility-gap",
            "Cross-source visibility gap",
            ["verified current roster", "Jira identity", "GitHub identity"],
        ),
        (
            "individual-linked-support-context",
            "Linked GitHub context without a current Jira assignment",
            ["verified current roster", "Jira identity", "GitHub identity"],
        ),
    )
    current_memberships = [
        membership for membership in memberships if membership.current_at_snapshot
    ]
    verified_team_ids = set()
    if teams_config is not None:
        verified_team_ids = {
            team.id
            for team in teams_config.teams
            if team.roster_source.state == "current_observation"
            and team.roster_source.observed_on is not None
            and team.roster_source.observed_on <= evaluated_at.date()
        }
    verified_roster = bool(
        {membership.team_id for membership in current_memberships} & verified_team_ids
    )
    suppressed: list[SuppressedSignalRule] = []
    signals: list[PerformanceSignal] = []

    def suppress(
        rule_key: str,
        title: str,
        required_evidence: list[str],
        reason: str,
    ) -> None:
        suppressed.append(
            SuppressedSignalRule(
                rule_key=rule_key,
                title=title,
                reason=reason,
                required_evidence=required_evidence,
            )
        )

    if not verified_roster:
        for rule_key, title, required in rules:
            suppress(
                rule_key,
                title,
                required,
                "No current membership has a roster source verified by this snapshot.",
            )
        return signals, suppressed

    active = [item for item in jira_work if item.active]
    feature_count = len({item.feature_key for item in active if item.feature_key})
    if not person.jira_account_id:
        suppress(rules[0][0], rules[0][1], rules[0][2], "Jira identity is not mapped.")
    elif len(active) >= 5 and feature_count >= 3:
        signals.append(
            PerformanceSignal(
                signal_type="investigation_prompt",
                rule_key=rules[0][0],
                title=rules[0][1],
                explanation=(
                    f"The snapshot links {len(active)} active Jira issues across "
                    f"{feature_count} Features to this person. Probe workload and context "
                    "switching; do not interpret the count as output or performance."
                ),
                confidence="confirmed",
                evaluated_at=evaluated_at,
                current_value={
                    "active_issue_count": len(active),
                    "active_feature_count": feature_count,
                },
                threshold={"minimum_issues": 5, "minimum_features": 3},
                evidence=[evidence for item in active for evidence in item.evidence],
            )
        )

    complete_identity = bool(person.jira_account_id and person.github_login)
    recent_github = [
        item
        for item in github
        if item.occurred_at is not None
        and _as_utc(item.occurred_at) >= evaluated_at - timedelta(days=30)
    ]
    if not complete_identity:
        for rule_key, title, required in rules[1:]:
            suppress(
                rule_key,
                title,
                required,
                "Both Jira and GitHub identities are required for cross-source comparison.",
            )
    if complete_identity and active and not recent_github:
        signals.append(
            PerformanceSignal(
                signal_type="investigation_prompt",
                rule_key=rules[1][0],
                title=rules[1][1],
                explanation=(
                    "Current Jira assignments are present, but no GitHub activity "
                    "is visible in configured repositories during the last 30 "
                    "days. Probe non-code work, repository coverage, or missing Jira keys; "
                    "this is not an inactivity conclusion."
                ),
                confidence="limited",
                evaluated_at=evaluated_at,
                current_value={
                    "active_jira_issue_count": len(active),
                    "github_record_count_30d": 0,
                },
                threshold={"window_days": 30, "github_record_count": 0},
                evidence=[evidence for item in active for evidence in item.evidence],
            )
        )
    if complete_identity and not active and recent_github:
        signals.append(
            PerformanceSignal(
                signal_type="investigation_prompt",
                rule_key=rules[2][0],
                title=rules[2][1],
                explanation=(
                    f"The snapshot contains {len(recent_github)} GitHub "
                    "records in the last 30 days but no current Jira assignment. Probe "
                    "support, review, or unplanned-work context; this is not a performance "
                    "conclusion."
                ),
                confidence="limited",
                evaluated_at=evaluated_at,
                current_value={
                    "active_jira_issue_count": 0,
                    "linked_github_record_count_30d": len(recent_github),
                },
                threshold={"window_days": 30, "minimum_github_records": 1},
                evidence=[
                    EvidenceLink(label=item.title, url=item.url)
                    for item in recent_github
                ],
            )
        )
    return signals, suppressed


def _identity_state(person: Person) -> str:
    return (
        "complete"
        if person.jira_account_id and person.github_login
        else "partial"
        if person.jira_account_id or person.github_login
        else "unmapped"
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
