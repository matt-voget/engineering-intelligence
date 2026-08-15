"""Pin organization configuration to snapshots.

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("snapshots", sa.Column("organization_config", sa.JSON()))
    op.add_column(
        "snapshots",
        sa.Column("organization_config_hash", sa.String(64)),
    )


def downgrade() -> None:
    op.drop_column("snapshots", "organization_config_hash")
    op.drop_column("snapshots", "organization_config")
