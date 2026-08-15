"""SQLAlchemy models for the Phase 1 historical data foundation."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24))
    request_context: Mapped[dict[str, Any]] = mapped_column(JSON)
    records_seen: Mapped[int] = mapped_column(Integer, default=0)
    records_changed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


class RawPayload(Base):
    __tablename__ = "raw_payloads"
    __table_args__ = (
        UniqueConstraint("source", "content_hash", name="uq_raw_payload_source_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ingestion_run_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_runs.id"))
    source: Mapped[str] = mapped_column(String(32))
    record_type: Mapped[str] = mapped_column(String(64))
    source_record_id: Mapped[str] = mapped_column(String(255))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    object_path: Mapped[str] = mapped_column(Text)
    api_version: Mapped[str | None] = mapped_column(String(32))
    request_context: Mapped[dict[str, Any]] = mapped_column(JSON)


class Board(Base):
    __tablename__ = "boards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    board_type: Mapped[str] = mapped_column(String(32))
    filter_id: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BoardColumn(Base):
    __tablename__ = "board_columns"
    __table_args__ = (
        UniqueConstraint("board_id", "position", "status_id", name="uq_board_column_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("boards.id"))
    name: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer)
    status_id: Mapped[str | None] = mapped_column(String(64))


class JiraIssue(Base):
    __tablename__ = "jira_issues"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    issue_key: Mapped[str] = mapped_column(String(64), unique=True)
    self_url: Mapped[str] = mapped_column(Text)
    web_url: Mapped[str] = mapped_column(Text)
    project_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_version_hash: Mapped[str] = mapped_column(String(64))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class JiraIssueVersion(Base):
    __tablename__ = "jira_issue_versions"
    __table_args__ = (
        UniqueConstraint("issue_id", "version_hash", name="uq_issue_version_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issue_id: Mapped[str] = mapped_column(ForeignKey("jira_issues.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version_hash: Mapped[str] = mapped_column(String(64))
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str] = mapped_column(Text)
    description_text: Mapped[str | None] = mapped_column(Text)
    issue_type_id: Mapped[str | None] = mapped_column(String(64))
    issue_type_name: Mapped[str | None] = mapped_column(String(255))
    status_id: Mapped[str | None] = mapped_column(String(64))
    status_name: Mapped[str | None] = mapped_column(String(255))
    status_category: Mapped[str | None] = mapped_column(String(64))
    assignee_account_id: Mapped[str | None] = mapped_column(String(255))
    assignee_display_name: Mapped[str | None] = mapped_column(String(255))
    team_id: Mapped[str | None] = mapped_column(String(255))
    team_name: Mapped[str | None] = mapped_column(String(255))
    parent_issue_id: Mapped[str | None] = mapped_column(String(64))
    rank_value: Mapped[str | None] = mapped_column(Text)
    target_date: Mapped[date | None] = mapped_column(Date)
    target_date_value: Mapped[str | None] = mapped_column(Text)
    gravitee_customers: Mapped[list[str] | None] = mapped_column(JSON)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    labels: Mapped[list[str]] = mapped_column(JSON)
    components: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    fix_versions: Mapped[list[dict[str, Any]]] = mapped_column(JSON)


class JiraStatusTransition(Base):
    __tablename__ = "jira_status_transitions"
    __table_args__ = (
        UniqueConstraint(
            "issue_id",
            "changelog_id",
            "item_index",
            name="uq_jira_status_transition_item",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issue_id: Mapped[str] = mapped_column(ForeignKey("jira_issues.id"), index=True)
    changelog_id: Mapped[str] = mapped_column(String(64))
    item_index: Mapped[int] = mapped_column(Integer)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    author_account_id: Mapped[str | None] = mapped_column(String(255))
    author_display_name: Mapped[str | None] = mapped_column(String(255))
    from_status_id: Mapped[str | None] = mapped_column(String(64))
    from_status_name: Mapped[str | None] = mapped_column(String(255))
    to_status_id: Mapped[str | None] = mapped_column(String(64))
    to_status_name: Mapped[str | None] = mapped_column(String(255))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JiraRelationship(Base):
    __tablename__ = "jira_relationships"
    __table_args__ = (
        UniqueConstraint(
            "source_issue_id",
            "target_issue_id",
            "relationship_type",
            name="uq_jira_relationship",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_issue_id: Mapped[str] = mapped_column(String(64))
    target_issue_id: Mapped[str] = mapped_column(String(64))
    target_issue_key: Mapped[str | None] = mapped_column(String(64))
    target_summary: Mapped[str | None] = mapped_column(Text)
    target_status: Mapped[str | None] = mapped_column(String(255))
    target_url: Mapped[str | None] = mapped_column(Text)
    relationship_type: Mapped[str] = mapped_column(String(64))
    source_description: Mapped[str | None] = mapped_column(String(255))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class BoardMembershipObservation(Base):
    __tablename__ = "board_membership_observations"
    __table_args__ = (
        UniqueConstraint("board_id", "issue_id", "observed_at", name="uq_board_observation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    board_id: Mapped[int] = mapped_column(ForeignKey("boards.id"))
    issue_id: Mapped[str] = mapped_column(ForeignKey("jira_issues.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingestion_run_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_runs.id"))


class JiraScopeObservation(Base):
    __tablename__ = "jira_scope_observations"
    __table_args__ = (
        UniqueConstraint(
            "scope_id",
            "issue_id",
            "ingestion_run_id",
            name="uq_jira_scope_issue_run",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(64))
    issue_id: Mapped[str] = mapped_column(ForeignKey("jira_issues.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingestion_run_id: Mapped[str] = mapped_column(ForeignKey("ingestion_runs.id"))


class SyncCursor(Base):
    __tablename__ = "sync_cursors"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    scope: Mapped[str] = mapped_column(String(255), primary_key=True)
    cursor_value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class TeamAlias(Base):
    __tablename__ = "team_aliases"
    __table_args__ = (
        UniqueConstraint("team_id", "alias", "starts_on", name="uq_team_alias_effective"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    alias: Mapped[str] = mapped_column(String(255))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)


class Person(Base):
    __tablename__ = "people"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    preferred_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str | None] = mapped_column(String(255))
    manager_person_id: Mapped[str | None] = mapped_column(String(64))
    jira_account_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    github_login: Mapped[str | None] = mapped_column(String(255), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class TeamMembership(Base):
    __tablename__ = "team_memberships"
    __table_args__ = (
        Index(
            "uq_team_membership_effective",
            "team_id",
            "person_id",
            "starts_on",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id"))
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    is_primary: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class GitHubRepository(Base):
    __tablename__ = "github_repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), unique=True)
    html_url: Mapped[str] = mapped_column(Text)
    default_branch: Mapped[str | None] = mapped_column(String(255))
    private: Mapped[bool] = mapped_column(Boolean)
    archived: Mapped[bool] = mapped_column(Boolean)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GitHubPullRequest(Base):
    __tablename__ = "github_pull_requests"
    __table_args__ = (
        UniqueConstraint("repository_id", "number", name="uq_github_pr_repository_number"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("github_repositories.id"))
    number: Mapped[int] = mapped_column(Integer)
    html_url: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_version_hash: Mapped[str] = mapped_column(String(64))


class GitHubPullRequestVersion(Base):
    __tablename__ = "github_pull_request_versions"
    __table_args__ = (
        UniqueConstraint("pull_request_id", "version_hash", name="uq_github_pr_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pull_request_id: Mapped[str] = mapped_column(ForeignKey("github_pull_requests.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32))
    draft: Mapped[bool] = mapped_column(Boolean)
    author_login: Mapped[str | None] = mapped_column(String(255))
    head_ref: Mapped[str] = mapped_column(String(255))
    base_ref: Mapped[str] = mapped_column(String(255))
    source_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merge_commit_sha: Mapped[str | None] = mapped_column(String(64))


class GitHubCommit(Base):
    __tablename__ = "github_commits"

    sha: Mapped[str] = mapped_column(String(64), primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("github_repositories.id"))
    html_url: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    author_login: Mapped[str | None] = mapped_column(String(255))
    author_name: Mapped[str | None] = mapped_column(String(255))
    authored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GitHubPullRequestCommit(Base):
    __tablename__ = "github_pull_request_commits"

    pull_request_id: Mapped[str] = mapped_column(
        ForeignKey("github_pull_requests.id"),
        primary_key=True,
    )
    commit_sha: Mapped[str] = mapped_column(ForeignKey("github_commits.sha"), primary_key=True)


class GitHubReview(Base):
    __tablename__ = "github_reviews"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pull_request_id: Mapped[str] = mapped_column(ForeignKey("github_pull_requests.id"))
    html_url: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32))
    author_login: Mapped[str | None] = mapped_column(String(255))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JiraGitHubRelationship(Base):
    __tablename__ = "jira_github_relationships"
    __table_args__ = (
        UniqueConstraint(
            "jira_issue_id",
            "github_record_type",
            "github_record_id",
            "relationship_type",
            name="uq_jira_github_relationship",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    jira_issue_id: Mapped[str] = mapped_column(ForeignKey("jira_issues.id"))
    github_record_type: Mapped[str] = mapped_column(String(32))
    github_record_id: Mapped[str] = mapped_column(String(255))
    relationship_type: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[str] = mapped_column(String(32))
    evidence: Mapped[str] = mapped_column(Text)
    evidence_url: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    schema_version: Mapped[str] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    organization_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    organization_config_hash: Mapped[str | None] = mapped_column(String(64))
    source_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_config_hash: Mapped[str | None] = mapped_column(String(64))


class SnapshotSourceState(Base):
    __tablename__ = "snapshot_source_states"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "source", "scope", name="uq_snapshot_source_scope"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id"))
    source: Mapped[str] = mapped_column(String(32))
    scope: Mapped[str] = mapped_column(String(255))
    high_water_mark: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingestion_run_id: Mapped[str | None] = mapped_column(ForeignKey("ingestion_runs.id"))


class SignalDefinition(Base):
    __tablename__ = "signal_definitions"
    __table_args__ = (
        UniqueConstraint(
            "definition_key",
            "version",
            name="uq_signal_definition_key_version",
        ),
        UniqueConstraint("definition_hash", name="uq_signal_definition_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    definition_key: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(32))
    category: Mapped[str] = mapped_column(String(64))
    scope_type: Mapped[str] = mapped_column(String(32))
    area: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    comparison_basis: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON)
    severity_policy: Mapped[dict[str, Any]] = mapped_column(JSON)
    confidence_policy: Mapped[dict[str, Any]] = mapped_column(JSON)
    definition_hash: Mapped[str] = mapped_column(String(64))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SignalEvaluation(Base):
    __tablename__ = "signal_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "signal_definition_id",
            "scope_type",
            "scope_id",
            "subject_id",
            "dimension",
            name="uq_signal_evaluation_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    signal_definition_id: Mapped[str] = mapped_column(
        ForeignKey("signal_definitions.id")
    )
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id"))
    scope_type: Mapped[str] = mapped_column(String(32))
    scope_id: Mapped[str] = mapped_column(String(128))
    subject_id: Mapped[str] = mapped_column(String(255), default="")
    dimension: Mapped[str] = mapped_column(String(128), default="")
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    condition_met: Mapped[bool] = mapped_column(Boolean)
    severity: Mapped[str | None] = mapped_column(String(24))
    confidence: Mapped[str] = mapped_column(String(24))
    current_value: Mapped[dict[str, Any]] = mapped_column(JSON)
    baseline: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    sample_size: Mapped[int] = mapped_column()
    flag_fingerprint: Mapped[str | None] = mapped_column(String(255))
    details: Mapped[dict[str, Any]] = mapped_column(JSON)


class LogicalFlag(Base):
    __tablename__ = "logical_flags"

    fingerprint: Mapped[str] = mapped_column(String(255), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(64))
    area: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean)
    current_severity: Mapped[str] = mapped_column(String(24))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FlagOccurrence(Base):
    __tablename__ = "flag_occurrences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    logical_flag_fingerprint: Mapped[str] = mapped_column(
        ForeignKey("logical_flags.fingerprint")
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshots.id"))


class FlagEvent(Base):
    __tablename__ = "flag_events"
    __table_args__ = (
        UniqueConstraint(
            "logical_flag_fingerprint",
            "snapshot_id",
            "event_type",
            name="uq_flag_event_snapshot_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    logical_flag_fingerprint: Mapped[str] = mapped_column(
        ForeignKey("logical_flags.fingerprint")
    )
    occurrence_id: Mapped[str] = mapped_column(ForeignKey("flag_occurrences.id"))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id"))
    event_type: Mapped[str] = mapped_column(String(24))
    severity: Mapped[str] = mapped_column(String(24))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSON)


class FlagEvidence(Base):
    __tablename__ = "flag_evidence"
    __table_args__ = (
        UniqueConstraint(
            "occurrence_id",
            "snapshot_id",
            "url",
            name="uq_flag_evidence_snapshot_url",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    occurrence_id: Mapped[str] = mapped_column(ForeignKey("flag_occurrences.id"))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("snapshots.id"))
    label: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    jira_key: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(Text)


class FlagUserState(Base):
    __tablename__ = "flag_user_states"

    logical_flag_fingerprint: Mapped[str] = mapped_column(
        ForeignKey("logical_flags.fingerprint"),
        primary_key=True,
    )
    unread_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    understood_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
