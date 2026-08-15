"""Add effective-dated team aliases and membership identity.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "team_aliases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("team_id", sa.String(64), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date()),
        sa.UniqueConstraint(
            "team_id",
            "alias",
            "starts_on",
            name="uq_team_alias_effective",
        ),
    )
    op.create_index(
        "uq_team_membership_effective",
        "team_memberships",
        ["team_id", "person_id", "starts_on", "is_primary"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_team_membership_effective", table_name="team_memberships")
    op.drop_table("team_aliases")
