"""Phase 1 persistence foundation.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("request_context", sa.JSON(), nullable=False),
        sa.Column("records_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
    )
    op.create_table(
        "raw_payloads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ingestion_run_id", sa.String(36), sa.ForeignKey("ingestion_runs.id")),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("record_type", sa.String(64), nullable=False),
        sa.Column("source_record_id", sa.String(255), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("object_path", sa.Text(), nullable=False),
        sa.Column("api_version", sa.String(32)),
        sa.Column("request_context", sa.JSON(), nullable=False),
        sa.UniqueConstraint("source", "content_hash", name="uq_raw_payload_source_hash"),
    )
    op.create_index(
        "ix_raw_payload_record",
        "raw_payloads",
        ["source", "record_type", "source_record_id", "retrieved_at"],
    )
    op.create_table(
        "boards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("board_type", sa.String(32), nullable=False),
        sa.Column("filter_id", sa.String(64)),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "board_columns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("board_id", sa.Integer(), sa.ForeignKey("boards.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status_id", sa.String(64)),
        sa.UniqueConstraint("board_id", "position", "status_id", name="uq_board_column_status"),
    )
    op.create_table(
        "jira_issues",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("issue_key", sa.String(64), nullable=False, unique=True),
        sa.Column("self_url", sa.Text(), nullable=False),
        sa.Column("web_url", sa.Text(), nullable=False),
        sa.Column("project_key", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_source_updated_at", sa.DateTime(timezone=True)),
        sa.Column("current_version_hash", sa.String(64), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "jira_issue_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("issue_id", sa.String(64), sa.ForeignKey("jira_issues.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version_hash", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("issue_type_id", sa.String(64)),
        sa.Column("issue_type_name", sa.String(255)),
        sa.Column("status_id", sa.String(64)),
        sa.Column("status_name", sa.String(255)),
        sa.Column("status_category", sa.String(64)),
        sa.Column("assignee_account_id", sa.String(255)),
        sa.Column("assignee_display_name", sa.String(255)),
        sa.Column("team_id", sa.String(255)),
        sa.Column("team_name", sa.String(255)),
        sa.Column("parent_issue_id", sa.String(64)),
        sa.Column("rank_value", sa.Text()),
        sa.Column("target_date", sa.Date()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("components", sa.JSON(), nullable=False),
        sa.Column("fix_versions", sa.JSON(), nullable=False),
        sa.UniqueConstraint("issue_id", "version_hash", name="uq_issue_version_hash"),
    )
    op.create_index(
        "ix_jira_issue_versions_issue_observed",
        "jira_issue_versions",
        ["issue_id", "observed_at"],
    )
    op.create_table(
        "jira_relationships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_issue_id", sa.String(64), nullable=False),
        sa.Column("target_issue_id", sa.String(64), nullable=False),
        sa.Column("relationship_type", sa.String(64), nullable=False),
        sa.Column("source_description", sa.String(255)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint(
            "source_issue_id",
            "target_issue_id",
            "relationship_type",
            name="uq_jira_relationship",
        ),
    )
    op.create_table(
        "board_membership_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("board_id", sa.Integer(), sa.ForeignKey("boards.id"), nullable=False),
        sa.Column("issue_id", sa.String(64), sa.ForeignKey("jira_issues.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingestion_run_id", sa.String(36), sa.ForeignKey("ingestion_runs.id")),
        sa.UniqueConstraint("board_id", "issue_id", "observed_at", name="uq_board_observation"),
    )
    op.create_table(
        "sync_cursors",
        sa.Column("source", sa.String(32), primary_key=True),
        sa.Column("scope", sa.String(255), primary_key=True),
        sa.Column("cursor_value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "teams",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "people",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("preferred_name", sa.String(255)),
        sa.Column("role", sa.String(255)),
        sa.Column("manager_person_id", sa.String(64)),
        sa.Column("jira_account_id", sa.String(255), unique=True),
        sa.Column("github_login", sa.String(255), unique=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "team_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("team_id", sa.String(64), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("person_id", sa.String(64), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date()),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("description", sa.Text()),
    )
    op.create_table(
        "snapshot_source_states",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("snapshots.id"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(255), nullable=False),
        sa.Column("high_water_mark", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingestion_run_id", sa.String(36), sa.ForeignKey("ingestion_runs.id")),
        sa.UniqueConstraint("snapshot_id", "source", "scope", name="uq_snapshot_source_scope"),
    )


def downgrade() -> None:
    for table in (
        "snapshot_source_states",
        "snapshots",
        "team_memberships",
        "people",
        "teams",
        "sync_cursors",
        "board_membership_observations",
        "jira_relationships",
        "jira_issue_versions",
        "jira_issues",
        "board_columns",
        "boards",
        "raw_payloads",
        "ingestion_runs",
    ):
        op.drop_table(table)
