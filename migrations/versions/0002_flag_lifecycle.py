"""Persist health flag lifecycle and user state.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "logical_flags",
        sa.Column("fingerprint", sa.String(255), primary_key=True),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("area", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("current_severity", sa.String(24), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "flag_occurrences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "logical_flag_fingerprint",
            sa.String(255),
            sa.ForeignKey("logical_flags.fingerprint"),
            nullable=False,
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("latest_snapshot_id", sa.String(36), sa.ForeignKey("snapshots.id")),
    )
    op.create_index(
        "ix_flag_occurrence_logical_opened",
        "flag_occurrences",
        ["logical_flag_fingerprint", "opened_at"],
    )
    op.create_table(
        "flag_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "logical_flag_fingerprint",
            sa.String(255),
            sa.ForeignKey("logical_flags.fingerprint"),
            nullable=False,
        ),
        sa.Column(
            "occurrence_id",
            sa.String(36),
            sa.ForeignKey("flag_occurrences.id"),
            nullable=False,
        ),
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("snapshots.id"), nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "logical_flag_fingerprint",
            "snapshot_id",
            "event_type",
            name="uq_flag_event_snapshot_type",
        ),
    )
    op.create_table(
        "flag_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "occurrence_id",
            sa.String(36),
            sa.ForeignKey("flag_occurrences.id"),
            nullable=False,
        ),
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("snapshots.id"), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("jira_key", sa.String(64)),
        sa.Column("title", sa.Text()),
        sa.UniqueConstraint(
            "occurrence_id",
            "snapshot_id",
            "url",
            name="uq_flag_evidence_snapshot_url",
        ),
    )
    op.create_table(
        "flag_user_states",
        sa.Column(
            "logical_flag_fingerprint",
            sa.String(255),
            sa.ForeignKey("logical_flags.fingerprint"),
            primary_key=True,
        ),
        sa.Column("unread_since", sa.DateTime(timezone=True)),
        sa.Column("viewed_at", sa.DateTime(timezone=True)),
        sa.Column("understood_at", sa.DateTime(timezone=True)),
        sa.Column("snoozed_until", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("flag_user_states")
    op.drop_table("flag_evidence")
    op.drop_table("flag_events")
    op.drop_table("flag_occurrences")
    op.drop_table("logical_flags")
