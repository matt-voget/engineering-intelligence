"""Add GitHub delivery and Jira relationship evidence.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_repositories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("full_name", sa.String(255), nullable=False, unique=True),
        sa.Column("html_url", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.String(255)),
        sa.Column("private", sa.Boolean(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "github_pull_requests",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column(
            "repository_id",
            sa.Integer(),
            sa.ForeignKey("github_repositories.id"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("html_url", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_version_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "repository_id",
            "number",
            name="uq_github_pr_repository_number",
        ),
    )
    op.create_table(
        "github_pull_request_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "pull_request_id",
            sa.String(255),
            sa.ForeignKey("github_pull_requests.id"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version_hash", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("draft", sa.Boolean(), nullable=False),
        sa.Column("author_login", sa.String(255)),
        sa.Column("head_ref", sa.String(255), nullable=False),
        sa.Column("base_ref", sa.String(255), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("merged_at", sa.DateTime(timezone=True)),
        sa.Column("merge_commit_sha", sa.String(64)),
        sa.UniqueConstraint(
            "pull_request_id",
            "version_hash",
            name="uq_github_pr_version",
        ),
    )
    op.create_table(
        "github_commits",
        sa.Column("sha", sa.String(64), primary_key=True),
        sa.Column(
            "repository_id",
            sa.Integer(),
            sa.ForeignKey("github_repositories.id"),
            nullable=False,
        ),
        sa.Column("html_url", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("author_login", sa.String(255)),
        sa.Column("author_name", sa.String(255)),
        sa.Column("authored_at", sa.DateTime(timezone=True)),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "github_pull_request_commits",
        sa.Column(
            "pull_request_id",
            sa.String(255),
            sa.ForeignKey("github_pull_requests.id"),
            primary_key=True,
        ),
        sa.Column(
            "commit_sha",
            sa.String(64),
            sa.ForeignKey("github_commits.sha"),
            primary_key=True,
        ),
    )
    op.create_table(
        "github_reviews",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "pull_request_id",
            sa.String(255),
            sa.ForeignKey("github_pull_requests.id"),
            nullable=False,
        ),
        sa.Column("html_url", sa.Text()),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("author_login", sa.String(255)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "jira_github_relationships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "jira_issue_id",
            sa.String(64),
            sa.ForeignKey("jira_issues.id"),
            nullable=False,
        ),
        sa.Column("github_record_type", sa.String(32), nullable=False),
        sa.Column("github_record_id", sa.String(255), nullable=False),
        sa.Column("relationship_type", sa.String(64), nullable=False),
        sa.Column("confidence", sa.String(32), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("evidence_url", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "jira_issue_id",
            "github_record_type",
            "github_record_id",
            "relationship_type",
            name="uq_jira_github_relationship",
        ),
    )


def downgrade() -> None:
    op.drop_table("jira_github_relationships")
    op.drop_table("github_reviews")
    op.drop_table("github_pull_request_commits")
    op.drop_table("github_commits")
    op.drop_table("github_pull_request_versions")
    op.drop_table("github_pull_requests")
    op.drop_table("github_repositories")
