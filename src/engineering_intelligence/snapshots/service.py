"""Create and resolve reproducible named snapshots."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import SourceConfig, TeamsConfig
from engineering_intelligence.persistence.models import (
    IngestionRun,
    Snapshot,
    SnapshotSourceState,
)
from engineering_intelligence.snapshots.organization import (
    canonical_organization_config,
    canonical_source_config,
)


class SnapshotService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def create(
        self,
        board_ids: list[int],
        *,
        jira_queries: list[str] | None = None,
        github_repositories: list[str] | None = None,
        name: str | None = None,
        description: str | None = None,
        created_at: datetime | None = None,
        teams_config: TeamsConfig | None = None,
        source_config: SourceConfig | None = None,
    ) -> Snapshot:
        created_at = created_at or datetime.now(UTC)
        with self.sessions.begin() as session:
            if name and session.scalar(select(Snapshot.id).where(Snapshot.name == name)):
                raise ValueError(f"Snapshot name already exists: {name}")
            source_runs = [
                self._latest_successful_run(session, board_id) for board_id in board_ids
            ]
            query_runs = [
                self._latest_successful_query_run(session, query_id)
                for query_id in (jira_queries or [])
            ]
            github_runs = [
                self._latest_successful_github_run(session, full_name)
                for full_name in (github_repositories or [])
            ]
            organization_config = None
            organization_config_hash = None
            if teams_config is not None:
                organization_config, organization_config_hash = (
                    canonical_organization_config(teams_config)
                )
            pinned_source_config = None
            source_config_hash = None
            if source_config is not None:
                pinned_source_config, source_config_hash = canonical_source_config(
                    source_config
                )
            snapshot = Snapshot(
                id=str(uuid4()),
                name=name,
                created_at=created_at,
                schema_version="1.1",
                description=description,
                organization_config=organization_config,
                organization_config_hash=organization_config_hash,
                source_config=pinned_source_config,
                source_config_hash=source_config_hash,
            )
            session.add(snapshot)
            session.flush()
            for board_id, run in zip(board_ids, source_runs, strict=True):
                session.add(
                    SnapshotSourceState(
                        id=str(uuid4()),
                        snapshot_id=snapshot.id,
                        source="jira",
                        scope=f"board:{board_id}",
                        high_water_mark=run.started_at,
                        ingestion_run_id=run.id,
                    )
                )
            for full_name, run in zip(
                github_repositories or [],
                github_runs,
                strict=True,
            ):
                session.add(
                    SnapshotSourceState(
                        id=str(uuid4()),
                        snapshot_id=snapshot.id,
                        source="github",
                        scope=f"repository:{full_name}",
                        high_water_mark=run.started_at,
                        ingestion_run_id=run.id,
                    )
                )
            for query_id, run in zip(jira_queries or [], query_runs, strict=True):
                session.add(
                    SnapshotSourceState(
                        id=str(uuid4()),
                        snapshot_id=snapshot.id,
                        source="jira",
                        scope=f"query:{query_id}",
                        high_water_mark=run.started_at,
                        ingestion_run_id=run.id,
                    )
                )
            return snapshot

    def resolve(self, identifier: str) -> Snapshot:
        with self.sessions() as session:
            snapshot = session.get(Snapshot, identifier)
            if snapshot is None:
                snapshot = session.scalar(select(Snapshot).where(Snapshot.name == identifier))
            if snapshot is None:
                raise ValueError(f"Snapshot not found: {identifier}")
            session.expunge(snapshot)
            return snapshot

    @staticmethod
    def _latest_successful_run(session: Session, board_id: int) -> IngestionRun:
        run = session.scalar(
            select(IngestionRun)
            .where(
                IngestionRun.source == "jira",
                IngestionRun.status == "completed",
                IngestionRun.request_context["board_id"].as_integer() == board_id,
            )
            .order_by(IngestionRun.started_at.desc())
            .limit(1)
        )
        if run is None:
            raise ValueError(f"No successful Jira ingestion exists for board {board_id}")
        return run

    @staticmethod
    def _latest_successful_github_run(
        session: Session,
        full_name: str,
    ) -> IngestionRun:
        run = session.scalar(
            select(IngestionRun)
            .where(
                IngestionRun.source == "github",
                IngestionRun.status == "completed",
                IngestionRun.request_context["repository"].as_string() == full_name,
            )
            .order_by(IngestionRun.started_at.desc())
            .limit(1)
        )
        if run is None:
            raise ValueError(f"No successful GitHub ingestion exists for {full_name}")
        return run

    @staticmethod
    def _latest_successful_query_run(session: Session, query_id: str) -> IngestionRun:
        run = session.scalar(
            select(IngestionRun)
            .where(
                IngestionRun.source == "jira",
                IngestionRun.status == "completed",
                IngestionRun.request_context["query_id"].as_string() == query_id,
            )
            .order_by(IngestionRun.started_at.desc())
            .limit(1)
        )
        if run is None:
            raise ValueError(f"No successful Jira ingestion exists for query {query_id}")
        return run
