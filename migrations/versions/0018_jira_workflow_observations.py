"""Add snapshot-safe Jira workflow observations.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jira_workflow_observations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("ingestion_run_id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column("issue_type_id", sa.String(length=64), nullable=False),
        sa.Column("issue_type_name", sa.String(length=255), nullable=False),
        sa.Column("statuses", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ingestion_run_id",
            "project_key",
            "issue_type_id",
            name="uq_jira_workflow_run_project_type",
        ),
    )


def downgrade() -> None:
    op.drop_table("jira_workflow_observations")
