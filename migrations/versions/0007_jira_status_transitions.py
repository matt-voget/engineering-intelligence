"""Add immutable Jira status-transition history.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jira_status_transitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "issue_id",
            sa.String(64),
            sa.ForeignKey("jira_issues.id"),
            nullable=False,
        ),
        sa.Column("changelog_id", sa.String(64), nullable=False),
        sa.Column("item_index", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("author_account_id", sa.String(255)),
        sa.Column("author_display_name", sa.String(255)),
        sa.Column("from_status_id", sa.String(64)),
        sa.Column("from_status_name", sa.String(255)),
        sa.Column("to_status_id", sa.String(64)),
        sa.Column("to_status_name", sa.String(255)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "issue_id",
            "changelog_id",
            "item_index",
            name="uq_jira_status_transition_item",
        ),
    )
    op.create_index(
        "ix_jira_status_transition_issue_changed",
        "jira_status_transitions",
        ["issue_id", "changed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_jira_status_transition_issue_changed",
        table_name="jira_status_transitions",
    )
    op.drop_table("jira_status_transitions")
