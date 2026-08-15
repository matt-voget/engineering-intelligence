"""Preserve Jira relationship target evidence.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jira_relationships", sa.Column("target_issue_key", sa.String(64)))
    op.add_column("jira_relationships", sa.Column("target_summary", sa.Text()))
    op.add_column("jira_relationships", sa.Column("target_status", sa.String(255)))
    op.add_column("jira_relationships", sa.Column("target_url", sa.Text()))


def downgrade() -> None:
    op.drop_column("jira_relationships", "target_url")
    op.drop_column("jira_relationships", "target_status")
    op.drop_column("jira_relationships", "target_summary")
    op.drop_column("jira_relationships", "target_issue_key")
