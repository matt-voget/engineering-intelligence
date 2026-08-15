"""Select a compatible saved snapshot for cached and smart reads."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import SourceConfig, TeamsConfig
from engineering_intelligence.persistence.models import Snapshot
from engineering_intelligence.snapshots.organization import (
    canonical_organization_config,
    canonical_source_config,
)


@dataclass(frozen=True)
class SelectedSnapshot:
    id: str
    name: str | None


@dataclass(frozen=True)
class SnapshotSelection:
    snapshot: SelectedSnapshot | None
    age_seconds: float | None
    compatible: bool
    fresh: bool


def latest_snapshot(
    sessions: sessionmaker[Session],
    source_config: SourceConfig,
    teams_config: TeamsConfig,
    *,
    max_age_seconds: float,
    now: datetime | None = None,
) -> SnapshotSelection:
    now = now or datetime.now(UTC)
    _, source_hash = canonical_source_config(source_config)
    _, organization_hash = canonical_organization_config(teams_config)
    with sessions() as session:
        snapshot = session.scalar(select(Snapshot).order_by(Snapshot.created_at.desc()))
        if snapshot is None:
            return SnapshotSelection(None, None, False, False)
        created_at = snapshot.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        age_seconds = max(0.0, (now - created_at).total_seconds())
        compatible = (
            snapshot.source_config_hash == source_hash
            and snapshot.organization_config_hash == organization_hash
        )
        return SnapshotSelection(
            snapshot=SelectedSnapshot(id=snapshot.id, name=snapshot.name),
            age_seconds=age_seconds,
            compatible=compatible,
            fresh=compatible and age_seconds <= max_age_seconds,
        )
