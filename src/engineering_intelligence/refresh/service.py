"""Deterministic Jira/GitHub refresh, snapshot, flag, receipt, and backup workflow."""

import fcntl
import json
import os
import signal
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
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
from engineering_intelligence.ingestion.limiter import RequestLimiter
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
    records_new: int | None = None
    records_updated: int | None = None
    records_reused: int | None = None
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


class RefreshTaskState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    status: str = "planned"
    attempt: int = 0
    updated_at: datetime
    records_seen: int | None = None
    records_changed: int | None = None
    error: str | None = None


class RefreshRunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "2"
    refresh_id: str
    status: str
    started_at: datetime
    updated_at: datetime
    lease_expires_at: datetime
    source_config_hash: str
    organization_config_hash: str
    tasks: list[RefreshTaskState]
    error: str | None = None


class RefreshInterrupted(RuntimeError):
    """A refresh stopped because the process received an interrupt signal."""


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
        resume: bool = False,
        resume_refresh_id: str | None = None,
    ) -> RefreshReceipt:
        started_at = started_at or datetime.now(UTC)
        refresh_id = str(uuid4())
        _organization_payload, organization_hash = canonical_organization_config(teams_config)
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
        planned_sources = [
            *(f"jira:board:{board.id}" for board in source_config.jira.boards),
            *(f"jira:query:{query.id}" for query in source_config.jira.queries if query.enabled),
            *(["jira:query:accountable-active-work"] if collect_accountable_work else []),
            *(f"github:{repository.full_name}" for repository in source_config.github.repositories),
        ]
        if resume_refresh_id is not None:
            run_state = load_run_state(paths.root, resume_refresh_id)
            if run_state.source_config_hash != source_hash:
                raise ValueError("Cannot resume: source configuration has changed")
            if run_state.organization_config_hash != organization_hash:
                raise ValueError("Cannot resume: organization configuration has changed")
            if [task.source for task in run_state.tasks] != planned_sources:
                raise ValueError("Cannot resume: planned source manifest has changed")
            refresh_id = run_state.refresh_id
            started_at = run_state.started_at
            run_state.status = "planned"
            run_state.error = None
        else:
            run_state = RefreshRunState(
                refresh_id=refresh_id,
                status="planned",
                started_at=started_at,
                updated_at=started_at,
                lease_expires_at=started_at + timedelta(seconds=30),
                source_config_hash=source_hash,
                organization_config_hash=organization_hash,
                tasks=[
                    RefreshTaskState(source=source, updated_at=started_at)
                    for source in planned_sources
                ],
            )
        receipt.refresh_id = refresh_id
        receipt.started_at = started_at
        _write_run_state(paths.root, run_state)
        progress = RefreshProgress(
            refresh_id=refresh_id,
            status="running",
            started_at=started_at,
            updated_at=started_at,
            completed_sources=0,
            total_sources=total_sources,
        )
        completed_before_resume = (
            {task.source for task in run_state.tasks if task.status == "completed"}
            if resume_refresh_id is not None
            else (_completed_progress_sources(paths.root) if resume else set())
        )
        source_failures: list[str] = []
        state_lock = threading.Lock()
        publish_lock = threading.Lock()

        def synchronized(function: Callable[..., Any]) -> Callable[..., Any]:
            def locked(*args: Any, **kwargs: Any) -> Any:
                with publish_lock:
                    return function(*args, **kwargs)

            return locked

        @synchronized
        def publish(
            stage: str,
            status: str,
            message: str,
            *,
            source: str | None = None,
            records_seen: int | None = None,
            records_changed: int | None = None,
            records_new: int | None = None,
            records_updated: int | None = None,
            records_reused: int | None = None,
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
                records_new=records_new,
                records_updated=records_updated,
                records_reused=records_reused,
                message=message,
            )
            progress.status = status if status in {"completed", "failed"} else "running"
            progress.updated_at = observed_at
            progress.events.append(event)
            _write_progress(paths.root, progress)
            _append_event(paths.root, refresh_id, event)
            with state_lock:
                run_state.status = (
                    status if status in {"completed", "failed", "cancelled"} else "running"
                )
                run_state.updated_at = observed_at
                run_state.lease_expires_at = observed_at + timedelta(seconds=30)
                if status in {"failed", "cancelled"}:
                    run_state.error = message
                if source is not None:
                    task = next(task for task in run_state.tasks if task.source == source)
                    task.updated_at = observed_at
                    task.records_seen = records_seen
                    task.records_changed = records_changed
                    if status == "running":
                        task.status = "running"
                        task.attempt += 1
                    elif status == "completed_source":
                        task.status = "completed"
                    elif status == "failed_source":
                        task.status = "failed"
                        task.error = message
                _write_run_state(paths.root, run_state)
            if progress_callback is not None:
                progress_callback(event)

        publish("initialization", "running", "Refresh started")

        def skip_completed(source: str) -> bool:
            if source not in completed_before_resume:
                return False
            progress.completed_sources += 1
            publish(
                "resume",
                "completed_source",
                f"Reused completed source {source}",
                source=source,
            )
            return True

        if (backup_dir is None) != (backup_passphrase is None):
            raise ValueError("Backup requires both a destination directory and a passphrase")
        if backup_retention < 1:
            raise ValueError("Backup retention must be at least 1")
        paths.root.mkdir(parents=True, exist_ok=True)
        try:
            with (
                _interrupt_as_exception(),
                _refresh_lock(paths.root),
                _lease_heartbeat(paths.root, run_state, state_lock),
            ):
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
                                limiter=RequestLimiter(source_config.jira.request_concurrency),
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
                        if skip_completed(source):
                            continue
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
                            _jira_completion_message(f"Jira board {board.id}", run),
                            source=source,
                            records_seen=run["records_seen"],
                            records_changed=run["records_changed"],
                            records_new=run["counters"].get("new"),
                            records_updated=run["counters"].get("updated"),
                            records_reused=run["counters"].get("reused"),
                        )
                    for query in source_config.jira.queries:
                        if not query.enabled:
                            continue
                        source = f"jira:query:{query.id}"
                        if skip_completed(source):
                            continue
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
                            _jira_completion_message(f"Jira query {query.id}", run),
                            source=source,
                            records_seen=run["records_seen"],
                            records_changed=run["records_changed"],
                            records_new=run["counters"].get("new"),
                            records_updated=run["counters"].get("updated"),
                            records_reused=run["counters"].get("reused"),
                        )
                    derived_jira_queries: list[str] = []
                    if collect_accountable_work:
                        query_id = "accountable-active-work"
                        source = f"jira:query:{query_id}"
                        if not skip_completed(source):
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
                            progress.completed_sources += 1
                            publish(
                                "jira",
                                "completed_source",
                                _jira_completion_message(
                                    "Active Jira work for the accountable roster", run
                                ),
                                source=source,
                                records_seen=run["records_seen"],
                                records_changed=run["records_changed"],
                                records_new=run["counters"].get("new"),
                                records_updated=run["counters"].get("updated"),
                                records_reused=run["counters"].get("reused"),
                            )
                        derived_jira_queries.append(query_id)

                    repositories = source_config.github.repositories
                    if repositories:
                        active_github_client = github_client
                        if active_github_client is None:
                            active_github_client = stack.enter_context(
                                GitHubClient(
                                    str(source_config.github.api_url),
                                    github_token(source_config.github),
                                    event_callback=lambda event: publish(
                                        "github",
                                        str(event["kind"]),
                                        str(event["message"]),
                                    ),
                                    limiter=RequestLimiter(
                                        source_config.github.request_concurrency
                                    ),
                                )
                            )
                        github_service = GitHubIngestionService(
                            sessions,
                            RawPayloadArchive(paths.raw_archive),
                            active_github_client,
                            initial_lookback_days=(source_config.github.initial_lookback_days),
                            max_pull_requests=(
                                source_config.github.max_pull_requests_per_repository
                            ),
                            min_refresh_window_days=(source_config.github.min_refresh_window_days),
                        )
                        pending_repositories = []
                        for repository in repositories:
                            source = f"github:{repository.full_name}"
                            if skip_completed(source):
                                continue
                            publish(
                                "github",
                                "running",
                                f"Refreshing GitHub repository {repository.full_name}",
                                source=source,
                            )
                            pending_repositories.append(repository)
                        with ThreadPoolExecutor(
                            max_workers=source_config.github.repository_workers,
                            thread_name_prefix="github-refresh",
                        ) as executor:
                            futures = {
                                executor.submit(
                                    github_service.ingest_repository,
                                    repository.full_name,
                                ): repository
                                for repository in pending_repositories
                            }
                            for future in as_completed(futures):
                                repository = futures[future]
                                source = f"github:{repository.full_name}"
                                try:
                                    run_id = future.result()
                                except Exception as exc:  # noqa: BLE001 - aggregate failures
                                    source_failures.append(
                                        f"{source}: {type(exc).__name__}: {exc}"
                                    )
                                    publish(
                                        "github",
                                        "failed_source",
                                        source_failures[-1],
                                        source=source,
                                    )
                                    continue
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
                                    _delta_completion_message(
                                        f"GitHub repository {repository.full_name}", run
                                    ),
                                    source=source,
                                    records_seen=run["records_seen"],
                                    records_changed=run["records_changed"],
                                    records_new=run["counters"].get("new"),
                                    records_updated=run["counters"].get("updated"),
                                    records_reused=run["counters"].get("reused"),
                                )

                    if source_failures:
                        raise RuntimeError(
                            f"{len(source_failures)} source(s) failed; rerun with --resume: "
                            + "; ".join(source_failures)
                        )

                name = snapshot_name or started_at.strftime("refresh-%Y%m%dT%H%M%SZ")
                publish("snapshot", "running", f"Creating snapshot {name}")
                snapshot = SnapshotService(sessions).create(
                    [board.id for board in source_config.jira.boards],
                    jira_queries=[query.id for query in source_config.jira.queries if query.enabled]
                    + derived_jira_queries,
                    github_repositories=[
                        repository.full_name for repository in source_config.github.repositories
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
                receipt.flags_recorded = sum(len(team.flags) for team in dashboard.teams)
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
        except BaseException as exc:  # noqa: BLE001 - terminal state must always be durable
            interrupted = isinstance(exc, (KeyboardInterrupt, RefreshInterrupted))
            receipt.status = "cancelled" if interrupted else "failed"
            receipt.error = f"{type(exc).__name__}: {exc}"
            publish(receipt.status, receipt.status, receipt.error)
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
    quoted = ", ".join(
        f'"{account_id.replace(chr(34), chr(92) + chr(34))}"' for account_id in account_ids
    )
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
            "counters": (run.request_context or {}).get("counters", {}),
        }


def _jira_completion_message(label: str, run: dict[str, Any]) -> str:
    return _delta_completion_message(label, run, unit="issues")


def _delta_completion_message(
    label: str,
    run: dict[str, Any],
    *,
    unit: str = "pull requests",
) -> str:
    counters = run["counters"]
    return (
        f"{label} — {counters.get('checked', 0)} {unit} checked; "
        f"{counters.get('new', 0)} new, {counters.get('updated', 0)} updated, "
        f"{counters.get('reused', 0)} reused"
    )


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


@contextmanager
def _interrupt_as_exception() -> Iterator[None]:
    previous: dict[signal.Signals, Any] = {}

    def interrupt(signum: int, _frame: Any) -> None:
        raise RefreshInterrupted(f"received {signal.Signals(signum).name}")

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupt)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


@contextmanager
def _lease_heartbeat(
    data_root: Path,
    state: RefreshRunState,
    state_lock: threading.Lock,
) -> Iterator[None]:
    stopped = threading.Event()

    def renew() -> None:
        while not stopped.wait(15):
            observed_at = datetime.now(UTC)
            with state_lock:
                if state.status not in {"planned", "running"}:
                    return
                state.updated_at = observed_at
                state.lease_expires_at = observed_at + timedelta(seconds=30)
                _write_run_state(data_root, state)

    thread = threading.Thread(target=renew, name="refresh-lease", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=2)


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


def _run_root(data_root: Path, refresh_id: str) -> Path:
    return data_root / "receipts" / "refresh" / "runs" / refresh_id


def _write_run_state(data_root: Path, state: RefreshRunState) -> None:
    root = _run_root(data_root, state.refresh_id)
    root.mkdir(parents=True, exist_ok=True)
    payload = state.model_dump_json(indent=2) + "\n"
    destination = root / "state.json"
    temporary = root / f".state.{os.getpid()}.tmp"
    temporary.write_text(payload)
    os.replace(temporary, destination)


def load_run_state(
    data_root: Path,
    refresh_id: str,
    *,
    reconcile_stale: bool = True,
    observed_at: datetime | None = None,
) -> RefreshRunState:
    destination = _run_root(data_root, refresh_id) / "state.json"
    if not destination.exists():
        raise ValueError(f"Refresh run does not exist: {refresh_id}")
    state = RefreshRunState.model_validate_json(destination.read_text())
    now = observed_at or datetime.now(UTC)
    if reconcile_stale and state.status == "running" and state.lease_expires_at < now:
        state.status = "stale"
        state.updated_at = now
        state.error = "Refresh worker lease expired before a terminal state was recorded"
        _write_run_state(data_root, state)
    return state


def _append_event(data_root: Path, refresh_id: str, event: RefreshProgressEvent) -> None:
    root = _run_root(data_root, refresh_id)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "events.jsonl").open("a") as stream:
        stream.write(event.model_dump_json() + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _completed_progress_sources(data_root: Path) -> set[str]:
    latest = data_root / "receipts" / "refresh" / "progress" / "latest.json"
    if not latest.exists():
        return set()
    payload = json.loads(latest.read_text())
    return {
        event["source"]
        for event in payload.get("events", [])
        if event.get("status") == "completed_source" and event.get("source")
    }
