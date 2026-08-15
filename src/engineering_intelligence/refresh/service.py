"""Deterministic Jira/GitHub refresh, snapshot, flag, receipt, and backup workflow."""

import fcntl
import os
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from engineering_intelligence.backups import BackupService
from engineering_intelligence.config import SourceConfig, TeamsConfig
from engineering_intelligence.flags import FlagService
from engineering_intelligence.individual_cache import materialize_individuals
from engineering_intelligence.ingestion.archive import RawPayloadArchive
from engineering_intelligence.ingestion.github import GitHubClient, GitHubIngestionService
from engineering_intelligence.ingestion.jira import JiraClient, JiraIngestionService
from engineering_intelligence.organization import OrganizationService
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
    upgrade_database,
)
from engineering_intelligence.persistence.models import IngestionRun
from engineering_intelligence.queries.dashboard import DashboardQuery
from engineering_intelligence.runtime import (
    RuntimePaths,
    github_token,
    jira_credentials,
)
from engineering_intelligence.snapshots import SnapshotService
from engineering_intelligence.snapshots.organization import (
    canonical_organization_config,
    canonical_source_config,
)


class RefreshReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    refresh_id: str
    status: str
    started_at: datetime
    completed_at: datetime
    data_dir: str
    source_config_hash: str | None = None
    organization_config_hash: str | None = None
    organization: dict[str, Any] | None = None
    jira_runs: list[dict[str, Any]] = Field(default_factory=list)
    github_runs: list[dict[str, Any]] = Field(default_factory=list)
    snapshot_id: str | None = None
    snapshot_name: str | None = None
    flags_recorded: int | None = None
    individual_summaries_materialized: int | None = None
    backup: dict[str, Any] | None = None
    error: str | None = None


class RefreshProgressEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    elapsed_seconds: float
    stage: str
    status: str
    source: str | None = None
    completed_sources: int
    total_sources: int
    records_seen: int | None = None
    records_changed: int | None = None
    message: str


class RefreshProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    refresh_id: str
    status: str
    started_at: datetime
    updated_at: datetime
    completed_sources: int
    total_sources: int
    events: list[RefreshProgressEvent] = Field(default_factory=list)


class RefreshService:
    def run(
        self,
        paths: RuntimePaths,
        source_config: SourceConfig,
        teams_config: TeamsConfig,
        *,
        snapshot_name: str | None = None,
        backup_dir: Path | None = None,
        backup_passphrase: str | None = None,
        backup_retention: int = 7,
        jira_client: Any | None = None,
        github_client: Any | None = None,
        started_at: datetime | None = None,
        progress_callback: Callable[[RefreshProgressEvent], None] | None = None,
    ) -> RefreshReceipt:
        started_at = started_at or datetime.now(UTC)
        refresh_id = str(uuid4())
        _organization_payload, organization_hash = canonical_organization_config(
            teams_config
        )
        _source_payload, source_hash = canonical_source_config(source_config)
        receipt = RefreshReceipt(
            refresh_id=refresh_id,
            status="running",
            started_at=started_at,
            completed_at=started_at,
            data_dir=str(paths.root),
            source_config_hash=source_hash,
            organization_config_hash=organization_hash,
        )
        accountable_jira_ids = _accountable_jira_ids(teams_config, started_at)
        collect_accountable_work = bool(
            source_config.jira.collect_accountable_work and accountable_jira_ids
        )
        total_sources = (
            len(source_config.jira.boards)
            + sum(1 for query in source_config.jira.queries if query.enabled)
            + int(collect_accountable_work)
            + len(source_config.github.repositories)
        )
        progress = RefreshProgress(
            refresh_id=refresh_id,
            status="running",
            started_at=started_at,
            updated_at=started_at,
            completed_sources=0,
            total_sources=total_sources,
        )

        def publish(
            stage: str,
            status: str,
            message: str,
            *,
            source: str | None = None,
            records_seen: int | None = None,
            records_changed: int | None = None,
        ) -> None:
            observed_at = datetime.now(UTC)
            event = RefreshProgressEvent(
                observed_at=observed_at,
                elapsed_seconds=max(
                    0.0,
                    round((observed_at - started_at).total_seconds(), 3),
                ),
                stage=stage,
                status=status,
                source=source,
                completed_sources=progress.completed_sources,
                total_sources=progress.total_sources,
                records_seen=records_seen,
                records_changed=records_changed,
                message=message,
            )
            progress.status = status if status in {"completed", "failed"} else "running"
            progress.updated_at = observed_at
            progress.events.append(event)
            _write_progress(paths.root, progress)
            if progress_callback is not None:
                progress_callback(event)

        publish("initialization", "running", "Refresh started")
        if (backup_dir is None) != (backup_passphrase is None):
            raise ValueError(
                "Backup requires both a destination directory and a passphrase"
            )
        if backup_retention < 1:
            raise ValueError("Backup retention must be at least 1")
        paths.root.mkdir(parents=True, exist_ok=True)
        try:
            with _refresh_lock(paths.root):
                upgrade_database(paths.database)
                engine = create_sqlite_engine(paths.database)
                sessions = session_factory(engine)
                organization = OrganizationService(sessions).apply(teams_config)
                receipt.organization = organization.model_dump(mode="json")
                publish(
                    "organization",
                    "completed_stage",
                    "Organization configuration applied",
                )
                with ExitStack() as stack:
                    active_jira_client = jira_client
                    if active_jira_client is None:
                        email, token = jira_credentials(source_config.jira)
                        active_jira_client = stack.enter_context(
                            JiraClient(
                                str(source_config.jira.base_url),
                                email,
                                token,
                            )
                        )
                    jira_service = JiraIngestionService(
                        sessions,
                        RawPayloadArchive(paths.raw_archive),
                        active_jira_client,
                        base_url=str(source_config.jira.base_url),
                        team_field_id=source_config.jira.team_field_id,
                        target_date_field_id=source_config.jira.target_date_field_id,
                        gravitee_customers_field_id=(
                            source_config.jira.gravitee_customers_field_id
                        ),
                        hierarchy_max_depth=source_config.jira.hierarchy_max_depth,
                        hierarchy_batch_size=source_config.jira.hierarchy_batch_size,
                    )
                    for board in source_config.jira.boards:
                        source = f"jira:board:{board.id}"
                        publish(
                            "jira",
                            "running",
                            f"Refreshing Jira board {board.id}",
                            source=source,
                        )
                        run_id = jira_service.ingest_board(board.id)
                        run = _run_receipt(sessions, run_id, {"board_id": board.id})
                        receipt.jira_runs.append(run)
                        progress.completed_sources += 1
                        publish(
                            "jira",
                            "completed_source",
                            f"Completed Jira board {board.id}",
                            source=source,
                            records_seen=run["records_seen"],
                            records_changed=run["records_changed"],
                        )
                    for query in source_config.jira.queries:
                        if not query.enabled:
                            continue
                        source = f"jira:query:{query.id}"
                        publish(
                            "jira",
                            "running",
                            f"Refreshing Jira query {query.id}",
                            source=source,
                        )
                        run_id = jira_service.ingest_query(query.id, query.jql)
                        run = _run_receipt(sessions, run_id, {"query_id": query.id})
                        receipt.jira_runs.append(run)
                        progress.completed_sources += 1
                        publish(
                            "jira",
                            "completed_source",
                            f"Completed Jira query {query.id}",
                            source=source,
                            records_seen=run["records_seen"],
                            records_changed=run["records_changed"],
                        )
                    derived_jira_queries: list[str] = []
                    if collect_accountable_work:
                        query_id = "accountable-active-work"
                        source = f"jira:query:{query_id}"
                        publish(
                            "jira",
                            "running",
                            "Refreshing active Jira work for the accountable roster",
                            source=source,
                        )
                        run_id = jira_service.ingest_query(
                            query_id,
                            _accountable_work_jql(accountable_jira_ids),
                        )
                        run = _run_receipt(sessions, run_id, {"query_id": query_id})
                        receipt.jira_runs.append(run)
                        derived_jira_queries.append(query_id)
                        progress.completed_sources += 1
                        publish(
                            "jira",
                            "completed_source",
                            "Completed active Jira work for the accountable roster",
                            source=source,
                            records_seen=run["records_seen"],
                            records_changed=run["records_changed"],
                        )

                    repositories = source_config.github.repositories
                    if repositories:
                        active_github_client = github_client
                        if active_github_client is None:
                            active_github_client = stack.enter_context(
                                GitHubClient(
                                    str(source_config.github.api_url),
                                    github_token(source_config.github),
                                )
                            )
                        github_service = GitHubIngestionService(
                            sessions,
                            RawPayloadArchive(paths.raw_archive),
                            active_github_client,
                            initial_lookback_days=(
                                source_config.github.initial_lookback_days
                            ),
                            max_pull_requests=(
                                source_config.github.max_pull_requests_per_repository
                            ),
                            min_refresh_window_days=(
                                source_config.github.min_refresh_window_days
                            ),
                        )
                        for repository in repositories:
                            source = f"github:{repository.full_name}"
                            publish(
                                "github",
                                "running",
                                f"Refreshing GitHub repository {repository.full_name}",
                                source=source,
                            )
                            run_id = github_service.ingest_repository(
                                repository.full_name
                            )
                            run = _run_receipt(
                                sessions,
                                run_id,
                                {"repository": repository.full_name},
                            )
                            receipt.github_runs.append(run)
                            progress.completed_sources += 1
                            publish(
                                "github",
                                "completed_source",
                                f"Completed GitHub repository {repository.full_name}",
                                source=source,
                                records_seen=run["records_seen"],
                                records_changed=run["records_changed"],
                            )

                name = snapshot_name or started_at.strftime("refresh-%Y%m%dT%H%M%SZ")
                publish("snapshot", "running", f"Creating snapshot {name}")
                snapshot = SnapshotService(sessions).create(
                    [board.id for board in source_config.jira.boards],
                    jira_queries=[
                        query.id
                        for query in source_config.jira.queries
                        if query.enabled
                    ]
                    + derived_jira_queries,
                    github_repositories=[
                        repository.full_name
                        for repository in source_config.github.repositories
                    ],
                    name=name,
                    created_at=datetime.now(UTC),
                    teams_config=teams_config,
                    source_config=source_config,
                )
                receipt.snapshot_id = snapshot.id
                receipt.snapshot_name = snapshot.name
                receipt.organization_config_hash = snapshot.organization_config_hash
                receipt.source_config_hash = snapshot.source_config_hash
                publish("snapshot", "completed_stage", f"Created snapshot {name}")
                publish("flags", "running", "Evaluating health flags")
                dashboard = FlagService(sessions).record_dashboard(
                    DashboardQuery(
                        sessions,
                        jira_base_url=str(source_config.jira.base_url),
                    ).get(
                        snapshot.id,
                        teams_config,
                        github_config=source_config.github,
                    )
                )
                receipt.flags_recorded = sum(
                    len(team.flags) for team in dashboard.teams
                )
                publish(
                    "flags",
                    "completed_stage",
                    f"Recorded {receipt.flags_recorded} active flags",
                )
                receipt.individual_summaries_materialized = materialize_individuals(
                    paths.root,
                    snapshot.id,
                    sessions,
                    teams_config,
                )
                if backup_dir is not None and backup_passphrase is not None:
                    publish("backup", "running", "Creating encrypted backup")
                    backup_path = _backup_path(backup_dir, started_at)
                    manifest = BackupService().create(
                        paths,
                        backup_path,
                        backup_passphrase,
                    )
                    removed = _enforce_backup_retention(
                        backup_path.parent,
                        backup_retention,
                    )
                    receipt.backup = {
                        "path": str(backup_path),
                        "database_sha256": manifest.database_sha256,
                        "raw_file_count": manifest.raw_file_count,
                        "verified": True,
                        "retention_removed": removed,
                    }
                    publish(
                        "backup",
                        "completed_stage",
                        f"Verified encrypted backup {backup_path.name}",
                    )
                receipt.status = "completed"
                publish("complete", "completed", "Refresh completed")
        except Exception as exc:  # noqa: BLE001 - every failure must produce a receipt
            receipt.status = "failed"
            receipt.error = f"{type(exc).__name__}: {exc}"
            publish("failed", "failed", receipt.error)
        receipt.completed_at = datetime.now(UTC)
        _write_receipt(paths.root, receipt)
        return receipt


def _accountable_jira_ids(
    teams_config: TeamsConfig,
    observed_at: datetime,
) -> list[str]:
    on_date = observed_at.date()
    return sorted(
        {
            member.jira_account_id
            for team in teams_config.teams
            for member in team.members
            if member.active
            and member.jira_account_id
            and member.starts_on <= on_date
            and (member.ends_on is None or member.ends_on >= on_date)
        }
    )


def _accountable_work_jql(account_ids: list[str]) -> str:
    quoted = ", ".join(f'"{account_id.replace(chr(34), chr(92) + chr(34))}"' for account_id in account_ids)
    return f'assignee in ({quoted}) AND statusCategory != "Done"'


def _run_receipt(
    sessions: Any,
    run_id: str,
    identity: dict[str, Any],
) -> dict[str, Any]:
    with sessions() as session:
        run = session.get(IngestionRun, run_id)
        if run is None:
            raise ValueError(f"Ingestion run disappeared: {run_id}")
        if run.status != "completed":
            raise ValueError(f"Ingestion run did not complete: {run_id} ({run.status})")
        return {
            **identity,
            "run_id": run_id,
            "status": run.status,
            "records_seen": run.records_seen,
            "records_changed": run.records_changed,
        }


@contextmanager
def _refresh_lock(data_root: Path) -> Iterator[None]:
    lock_path = data_root / ".refresh.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another Engineering Intelligence refresh is running") from exc
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _backup_path(directory: Path, started_at: datetime) -> Path:
    root = directory.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root / (
        "engineering-intelligence-"
        f"{started_at.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        ".engintel-backup"
    )


def _enforce_backup_retention(directory: Path, keep: int) -> list[str]:
    backups = sorted(
        directory.glob("engineering-intelligence-*.engintel-backup"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for path in backups[keep:]:
        path.unlink()
        removed.append(str(path))
    return removed


def _write_receipt(data_root: Path, receipt: RefreshReceipt) -> None:
    receipt_root = data_root / "receipts" / "refresh"
    receipt_root.mkdir(parents=True, exist_ok=True)
    payload = receipt.model_dump_json(indent=2) + "\n"
    destination = receipt_root / f"{receipt.refresh_id}.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(payload)
    os.replace(temporary, destination)
    latest = receipt_root / "latest.json"
    temporary_latest = receipt_root / ".latest.tmp"
    temporary_latest.write_text(payload)
    os.replace(temporary_latest, latest)


def _write_progress(data_root: Path, progress: RefreshProgress) -> None:
    progress_root = data_root / "receipts" / "refresh" / "progress"
    progress_root.mkdir(parents=True, exist_ok=True)
    payload = progress.model_dump_json(indent=2) + "\n"
    destination = progress_root / f"{progress.refresh_id}.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(payload)
    os.replace(temporary, destination)
    latest = progress_root / "latest.json"
    temporary_latest = progress_root / ".latest.tmp"
    temporary_latest.write_text(payload)
    os.replace(temporary_latest, latest)
