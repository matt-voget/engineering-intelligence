"""Preserve the exact Jira Target Date field value.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jira_issue_versions", sa.Column("target_date_value", sa.Text()))


def downgrade() -> None:
    op.drop_column("jira_issue_versions", "target_date_value")
