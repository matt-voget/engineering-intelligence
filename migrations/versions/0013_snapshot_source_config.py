"""Pin source configuration to snapshots.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("snapshots", sa.Column("source_config", sa.JSON()))
    op.add_column("snapshots", sa.Column("source_config_hash", sa.String(64)))


def downgrade() -> None:
    op.drop_column("snapshots", "source_config_hash")
    op.drop_column("snapshots", "source_config")
