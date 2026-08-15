"""Represent unknown primary-team status without guessing.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_team_membership_effective", table_name="team_memberships")
    with op.batch_alter_table("team_memberships") as batch:
        batch.alter_column(
            "is_primary",
            existing_type=sa.Boolean(),
            nullable=True,
            existing_server_default=sa.text("1"),
        )
    op.create_index(
        "uq_team_membership_effective",
        "team_memberships",
        ["team_id", "person_id", "starts_on"],
        unique=True,
    )


def downgrade() -> None:
    op.execute(
        sa.text("UPDATE team_memberships SET is_primary = 0 WHERE is_primary IS NULL")
    )
    op.drop_index("uq_team_membership_effective", table_name="team_memberships")
    with op.batch_alter_table("team_memberships") as batch:
        batch.alter_column(
            "is_primary",
            existing_type=sa.Boolean(),
            nullable=False,
            existing_server_default=sa.text("1"),
        )
    op.create_index(
        "uq_team_membership_effective",
        "team_memberships",
        ["team_id", "person_id", "starts_on", "is_primary"],
        unique=True,
    )
