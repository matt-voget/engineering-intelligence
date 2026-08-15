"""Add named Jira query-scope observations.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jira_scope_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope_id", sa.String(64), nullable=False),
        sa.Column(
            "issue_id",
            sa.String(64),
            sa.ForeignKey("jira_issues.id"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingestion_run_id",
            sa.String(36),
            sa.ForeignKey("ingestion_runs.id"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "scope_id",
            "issue_id",
            "ingestion_run_id",
            name="uq_jira_scope_issue_run",
        ),
    )
    op.create_index(
        "ix_jira_scope_observation_run",
        "jira_scope_observations",
        ["ingestion_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_jira_scope_observation_run",
        table_name="jira_scope_observations",
    )
    op.drop_table("jira_scope_observations")
