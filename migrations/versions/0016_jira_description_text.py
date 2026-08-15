"""Store normalized Jira description text.

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jira_issue_versions", sa.Column("description_text", sa.Text()))


def downgrade() -> None:
    op.drop_column("jira_issue_versions", "description_text")
