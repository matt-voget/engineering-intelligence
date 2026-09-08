"""Add indexes for snapshot report query paths.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_jira_relationship_target_type_seen",
        "jira_relationships",
        ["target_issue_id", "relationship_type", "first_seen_at"],
    )
    op.create_index(
        "ix_board_membership_run_issue",
        "board_membership_observations",
        ["ingestion_run_id", "issue_id"],
    )
    op.create_index(
        "ix_github_review_pull_observed",
        "github_reviews",
        ["pull_request_id", "observed_at"],
    )
    op.create_index(
        "ix_jira_github_record",
        "jira_github_relationships",
        ["github_record_type", "github_record_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_jira_github_record", table_name="jira_github_relationships")
    op.drop_index("ix_github_review_pull_observed", table_name="github_reviews")
    op.drop_index("ix_board_membership_run_issue", table_name="board_membership_observations")
    op.drop_index(
        "ix_jira_relationship_target_type_seen",
        table_name="jira_relationships",
    )
