"""Align persisted flag severities with the approved vocabulary.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table, column in (
        ("logical_flags", "current_severity"),
        ("flag_events", "severity"),
        ("signal_evaluations", "severity"),
    ):
        op.execute(
            sa.text(
                f"UPDATE {table} SET {column} = 'watch' "
                f"WHERE {column} = 'warning'"
            )
        )
    op.execute(
        sa.text(
            "UPDATE signal_evaluations "
            "SET details = json_set(details, '$.presentation_severity', 'watch') "
            "WHERE json_extract(details, '$.presentation_severity') = 'warning'"
        )
    )


def downgrade() -> None:
    # Existing installations already used "watch" in signal evaluations, so a
    # downgrade cannot safely distinguish migrated values from original values.
    pass
