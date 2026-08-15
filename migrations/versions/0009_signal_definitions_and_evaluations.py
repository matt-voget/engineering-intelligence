"""Add versioned signal definitions and immutable evaluations.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signal_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("definition_key", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("scope_type", sa.String(32), nullable=False),
        sa.Column("area", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("comparison_basis", sa.String(64), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("severity_policy", sa.JSON(), nullable=False),
        sa.Column("confidence_policy", sa.JSON(), nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "definition_key",
            "version",
            name="uq_signal_definition_key_version",
        ),
        sa.UniqueConstraint("definition_hash", name="uq_signal_definition_hash"),
    )
    op.create_table(
        "signal_evaluations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "signal_definition_id",
            sa.String(36),
            sa.ForeignKey("signal_definitions.id"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.String(36),
            sa.ForeignKey("snapshots.id"),
            nullable=False,
        ),
        sa.Column("scope_type", sa.String(32), nullable=False),
        sa.Column("scope_id", sa.String(128), nullable=False),
        sa.Column("subject_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("dimension", sa.String(128), nullable=False, server_default=""),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("condition_met", sa.Boolean(), nullable=False),
        sa.Column("severity", sa.String(24)),
        sa.Column("confidence", sa.String(24), nullable=False),
        sa.Column("current_value", sa.JSON(), nullable=False),
        sa.Column("baseline", sa.JSON()),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("flag_fingerprint", sa.String(255)),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "snapshot_id",
            "signal_definition_id",
            "scope_type",
            "scope_id",
            "subject_id",
            "dimension",
            name="uq_signal_evaluation_identity",
        ),
    )
    op.create_index(
        "ix_signal_evaluation_snapshot",
        "signal_evaluations",
        ["snapshot_id"],
    )
    op.create_index(
        "ix_signal_evaluation_scope",
        "signal_evaluations",
        ["scope_type", "scope_id", "evaluated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_signal_evaluation_scope", table_name="signal_evaluations")
    op.drop_index("ix_signal_evaluation_snapshot", table_name="signal_evaluations")
    op.drop_table("signal_evaluations")
    op.drop_table("signal_definitions")
