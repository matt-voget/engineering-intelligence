"""Store normalized Gravitee Customers option labels.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jira_issue_versions", sa.Column("gravitee_customers", sa.JSON()))


def downgrade() -> None:
    op.drop_column("jira_issue_versions", "gravitee_customers")
