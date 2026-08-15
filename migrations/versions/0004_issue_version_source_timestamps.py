"""Version Jira source created and updated timestamps.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jira_issue_versions",
        sa.Column("source_created_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "jira_issue_versions",
        sa.Column("source_updated_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("jira_issue_versions", "source_updated_at")
    op.drop_column("jira_issue_versions", "source_created_at")
