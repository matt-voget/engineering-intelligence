"""Command-line entry point for Engineering Intelligence."""

import json
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path
from typing import Annotated, cast

import typer

from engineering_intelligence import __version__
from engineering_intelligence.backups import BackupService
from engineering_intelligence.config import SourceConfig, TeamsConfig, load_yaml_model
from engineering_intelligence.flags import FlagService
from engineering_intelligence.individual_cache import (
    cache_individual,
    load_cached_individual,
)
from engineering_intelligence.ingestion.archive import RawPayloadArchive
from engineering_intelligence.ingestion.github import GitHubClient, GitHubIngestionService
from engineering_intelligence.ingestion.jira import JiraClient, JiraIngestionService
from engineering_intelligence.lifecycle import (
    AgentName,
    LifecycleService,
    default_config_dir,
    repository_root,
)
from engineering_intelligence.organization import OrganizationService
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
    upgrade_database,
)
from engineering_intelligence.persistence.models import IngestionRun
from engineering_intelligence.portability import PortabilityService
from engineering_intelligence.presentations.attention import AttentionCollection
from engineering_intelligence.presentations.team_brief import build_team_brief
from engineering_intelligence.queries.attention import AttentionQuery
from engineering_intelligence.queries.dashboard import DashboardQuery
from engineering_intelligence.queries.feature import FeatureQuery
from engineering_intelligence.queries.individual import IndividualQuery
from engineering_intelligence.queries.metrics import MetricsQuery, resolve_metric_team
from engineering_intelligence.queries.people import PeopleQuery
from engineering_intelligence.queries.team import TeamQuery
from engineering_intelligence.queries.team_work import TeamWorkQuery
from engineering_intelligence.refresh import RefreshProgressEvent, RefreshService
from engineering_intelligence.renderers.attention_markdown import (
    render_attention_flag_markdown,
    render_attention_markdown,
)
from engineering_intelligence.renderers.dashboard_markdown import render_dashboard_markdown
from engineering_intelligence.renderers.feature_markdown import render_feature_markdown
from engineering_intelligence.renderers.metrics_markdown import render_metrics_markdown
from engineering_intelligence.renderers.people_markdown import (
    render_individual_markdown,
    render_people_markdown,
)
from engineering_intelligence.renderers.team_brief_markdown import (
    render_team_brief_markdown,
)
from engineering_intelligence.renderers.team_markdown import render_team_markdown
from engineering_intelligence.runtime import github_token, jira_credentials, runtime_paths
from engineering_intelligence.scheduler import SchedulerService
from engineering_intelligence.snapshot_selection import latest_snapshot
from engineering_intelligence.snapshots import SnapshotService

app = typer.Typer(
    name="engintel",
    help="Collect, inspect, and export local engineering intelligence.",
    no_args_is_help=True,
)
database_app = typer.Typer(help="Initialize and inspect local persistence.")
organization_app = typer.Typer(help="Load explicit teams, people, and membership history.")
jira_app = typer.Typer(help="Refresh read-only Jira source data.")
github_app = typer.Typer(help="Refresh configured read-only GitHub delivery data.")
snapshot_app = typer.Typer(help="Create and inspect reproducible source snapshots.")
dashboard_app = typer.Typer(help="Get deterministic team-health Dashboard data.")
feature_app = typer.Typer(help="Get deterministic IBR Feature details.")
team_app = typer.Typer(help="Get deterministic Team details.")
people_app = typer.Typer(help="Get the deterministic People directory.")
individual_app = typer.Typer(help="Get evidence-backed Individual work context.")
flag_app = typer.Typer(help="Manage local health-flag user state.")
attention_app = typer.Typer(help="Investigate durable health flags and history.")
metrics_app = typer.Typer(help="Calculate reproducible Ideate and Build metrics.")
backup_app = typer.Typer(help="Create and verify encrypted runtime backups.")
refresh_app = typer.Typer(help="Refresh all sources, snapshot, evaluate, and optionally back up.")
schedule_app = typer.Typer(help="Install or remove an optional recurring local refresh.")
app.add_typer(database_app, name="database")
app.add_typer(organization_app, name="organization")
app.add_typer(jira_app, name="jira")
app.add_typer(github_app, name="github")
app.add_typer(snapshot_app, name="snapshot")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(feature_app, name="feature")
app.add_typer(team_app, name="team")
app.add_typer(people_app, name="people")
app.add_typer(individual_app, name="individual")
app.add_typer(flag_app, name="flag")
app.add_typer(attention_app, name="attention")
app.add_typer(metrics_app, name="metrics")
app.add_typer(backup_app, name="backup")
app.add_typer(refresh_app, name="refresh")
app.add_typer(schedule_app, name="schedule")

DataDir = Annotated[
    Path | None,
    typer.Option(
        "--data-dir",
        help="Runtime data directory; defaults to ENGINTEL_DATA_DIR or the user data directory.",
    ),
]


@app.command()
def doctor() -> None:
    """Check that the foundational CLI is available."""
    typer.echo("Engineering Intelligence is ready.")
    typer.echo(f"Version: {__version__}")


@app.command("setup")
def setup(
    config_dir: Annotated[
        Path | None,
        typer.Option("--config-dir", help="Private directory for editable configuration."),
    ] = None,
    data_dir: DataDir = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace existing generated configuration files."),
    ] = False,
) -> None:
    """Create private starter configuration and initialize local storage."""
    config_root = (config_dir or default_config_dir()).expanduser().resolve()
    data_root = runtime_paths(data_dir).root
    source_target = config_root / "sources.yaml"
    teams_target = config_root / "teams.yaml"
    templates = {
        source_target: repository_root() / "config" / "sources.example.yaml",
        teams_target: repository_root() / "config" / "teams.example.yaml",
    }
    existing = [str(path) for path in templates if path.exists()]
    if existing and not overwrite:
        raise typer.BadParameter(
            "Configuration already exists; use --overwrite only if replacement is intended: "
            + ", ".join(existing),
            param_hint="--config-dir",
        )
    config_root.mkdir(parents=True, exist_ok=True)
    for target, template in templates.items():
        shutil.copyfile(template, target)
    upgrade_database(runtime_paths(data_root).database)
    typer.echo(json.dumps({
        "status": "ready_for_configuration",
        "source_config": str(source_target),
        "teams_config": str(teams_target),
        "data_dir": str(data_root),
        "next": [
            "Export the Jira and GitHub credential variables named in sources.yaml.",
            (
                "Export ATLASSIAN_HOST or set jira.base_url, then let an agent run "
                "the guided onboarding in "
                "docs/onboarding.md to discover boards, custom fields, repositories, "
                "and team rosters — or edit both YAML files by hand, replacing every "
                "CHANGE_ME value."
            ),
            "Run engintel install with these config paths, then invoke team-status-prep.",
        ],
    }, indent=2))


@app.command()
def version(
    short: Annotated[
        bool,
        typer.Option("--short", help="Print only the semantic version."),
    ] = False,
) -> None:
    """Print version information."""
    if short:
        typer.echo(__version__)
        return
    typer.echo(f"Engineering Intelligence {__version__}")


@database_app.command("init")
def database_init(data_dir: DataDir = None) -> None:
    """Create or upgrade the local database."""
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    typer.echo(json.dumps({"database": str(paths.database), "status": "ready"}))


@organization_app.command("apply")
def organization_apply(
    teams_config_path: Annotated[
        Path,
        typer.Option("--teams-config", exists=True, dir_okay=False),
    ] = Path("config/teams.example.yaml"),
    data_dir: DataDir = None,
) -> None:
    """Validate and idempotently apply team and people configuration."""
    config = load_yaml_model(teams_config_path, TeamsConfig)
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    result = OrganizationService(sessions).apply(config)
    typer.echo(result.model_dump_json())


@jira_app.command("sync")
def jira_sync(
    config_path: Annotated[
        Path,
        typer.Option(
            "--config",
            exists=True,
            dir_okay=False,
            help="Versioned Jira source configuration.",
        ),
    ] = Path("config/sources.example.yaml"),
    board_ids: Annotated[
        list[int] | None,
        typer.Option("--board", help="Board ID to refresh; repeat for multiple boards."),
    ] = None,
    query_ids: Annotated[
        list[str] | None,
        typer.Option("--query", help="Named Jira query ID; repeat for multiple queries."),
    ] = None,
    data_dir: DataDir = None,
) -> None:
    """Archive and normalize configured Jira boards and named queries."""
    source_config = load_yaml_model(config_path, SourceConfig)
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    email, token = jira_credentials(source_config.jira)
    configured_ids = [board.id for board in source_config.jira.boards]
    configured_queries = {
        query.id: query for query in source_config.jira.queries if query.enabled
    }
    explicit_selection = board_ids is not None or query_ids is not None
    selected_ids = board_ids or ([] if explicit_selection else configured_ids)
    selected_query_ids = query_ids or (
        [] if explicit_selection else list(configured_queries)
    )
    unknown = sorted(set(selected_ids) - set(configured_ids))
    if unknown:
        raise typer.BadParameter(f"Boards are not configured: {unknown}", param_hint="--board")
    unknown_queries = sorted(set(selected_query_ids) - set(configured_queries))
    if unknown_queries:
        raise typer.BadParameter(
            f"Queries are not configured or enabled: {unknown_queries}",
            param_hint="--query",
        )

    engine = create_sqlite_engine(paths.database)
    sessions = session_factory(engine)
    results: list[dict[str, object]] = []
    with JiraClient(
        str(source_config.jira.base_url),
        email,
        token,
    ) as client:
        service = JiraIngestionService(
            sessions,
            RawPayloadArchive(paths.raw_archive),
            client,
            base_url=str(source_config.jira.base_url),
            team_field_id=source_config.jira.team_field_id,
            target_date_field_id=source_config.jira.target_date_field_id,
            gravitee_customers_field_id=source_config.jira.gravitee_customers_field_id,
            hierarchy_max_depth=source_config.jira.hierarchy_max_depth,
            hierarchy_batch_size=source_config.jira.hierarchy_batch_size,
        )
        for board_id in selected_ids:
            run_id = service.ingest_board(board_id)
            with sessions() as session:
                run = session.get(IngestionRun, run_id)
                assert run is not None
                results.append(
                    {
                        "board_id": board_id,
                        "run_id": run_id,
                        "status": run.status,
                        "records_seen": run.records_seen,
                        "records_changed": run.records_changed,
                    }
                )
        for query_id in selected_query_ids:
            query = configured_queries[query_id]
            run_id = service.ingest_query(query.id, query.jql)
            with sessions() as session:
                run = session.get(IngestionRun, run_id)
                assert run is not None
                results.append(
                    {
                        "query_id": query.id,
                        "run_id": run_id,
                        "status": run.status,
                        "records_seen": run.records_seen,
                        "records_changed": run.records_changed,
                    }
                )
    typer.echo(
        json.dumps(
            {
                "database": str(paths.database),
                "raw_archive": str(paths.raw_archive),
                "runs": results,
            },
            sort_keys=True,
        )
    )


@github_app.command("sync")
def github_sync(
    config_path: Annotated[
        Path,
        typer.Option("--config", exists=True, dir_okay=False),
    ] = Path("config/sources.example.yaml"),
    repositories: Annotated[
        list[str] | None,
        typer.Option(
            "--repository",
            help="Configured owner/repository to refresh; repeat as needed.",
        ),
    ] = None,
    data_dir: DataDir = None,
) -> None:
    """Archive GitHub pull requests, commits, reviews, and explicit Jira-key links."""
    source_config = load_yaml_model(config_path, SourceConfig)
    configured = {
        repository.full_name: repository
        for repository in source_config.github.repositories
    }
    selected = repositories or list(configured)
    unknown = sorted(set(selected) - set(configured))
    if unknown:
        raise typer.BadParameter(
            f"Repositories are not configured: {unknown}",
            param_hint="--repository",
        )
    if not selected:
        raise typer.BadParameter(
            "No GitHub repositories are configured",
            param_hint="--config",
        )
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    results: list[dict[str, object]] = []
    with GitHubClient(
        str(source_config.github.api_url),
        github_token(source_config.github),
    ) as client:
        service = GitHubIngestionService(
            sessions,
            RawPayloadArchive(paths.raw_archive),
            client,
            initial_lookback_days=source_config.github.initial_lookback_days,
            max_pull_requests=source_config.github.max_pull_requests_per_repository,
            min_refresh_window_days=source_config.github.min_refresh_window_days,
        )
        for full_name in selected:
            run_id = service.ingest_repository(full_name)
            with sessions() as session:
                run = session.get(IngestionRun, run_id)
                assert run is not None
                results.append(
                    {
                        "repository": full_name,
                        "run_id": run_id,
                        "status": run.status,
                        "records_seen": run.records_seen,
                        "records_changed": run.records_changed,
                    }
                )
    typer.echo(
        json.dumps(
            {
                "database": str(paths.database),
                "raw_archive": str(paths.raw_archive),
                "runs": results,
            },
            sort_keys=True,
        )
    )


@snapshot_app.command("create")
def snapshot_create(
    name: Annotated[str, typer.Option("--name", help="Stable human-readable snapshot name.")],
    config_path: Annotated[
        Path,
        typer.Option("--config", exists=True, dir_okay=False),
    ] = Path("config/sources.example.yaml"),
    teams_config_path: Annotated[
        Path,
        typer.Option("--teams-config", exists=True, dir_okay=False),
    ] = Path("config/teams.example.yaml"),
    data_dir: DataDir = None,
) -> None:
    """Create a named snapshot from the latest successful configured ingestions."""
    source_config = load_yaml_model(config_path, SourceConfig)
    teams_config = load_yaml_model(teams_config_path, TeamsConfig)
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    snapshot = SnapshotService(sessions).create(
        [board.id for board in source_config.jira.boards],
        jira_queries=[
            query.id for query in source_config.jira.queries if query.enabled
        ],
        github_repositories=[
            repository.full_name for repository in source_config.github.repositories
        ],
        name=name,
        teams_config=teams_config,
        source_config=source_config,
    )
    typer.echo(
        json.dumps(
            {
                "snapshot_id": snapshot.id,
                "snapshot_name": snapshot.name,
                "created_at": snapshot.created_at.isoformat(),
                "organization_config_hash": snapshot.organization_config_hash,
                "source_config_hash": snapshot.source_config_hash,
            },
            sort_keys=True,
        )
    )


@refresh_app.command("run")
def refresh_run(
    snapshot_name: Annotated[
        str | None,
        typer.Option("--snapshot-name", help="Optional stable snapshot name."),
    ] = None,
    source_config_path: Annotated[
        Path,
        typer.Option("--source-config", exists=True, dir_okay=False),
    ] = Path("config/sources.example.yaml"),
    teams_config_path: Annotated[
        Path,
        typer.Option("--teams-config", exists=True, dir_okay=False),
    ] = Path("config/teams.example.yaml"),
    backup_dir: Annotated[
        Path | None,
        typer.Option(
            "--backup-dir",
            file_okay=False,
            help="Optional directory for a verified encrypted backup.",
        ),
    ] = None,
    backup_retention: Annotated[
        int,
        typer.Option("--backup-retention", min=1, help="Number of managed backups to retain."),
    ] = 7,
    data_dir: DataDir = None,
) -> None:
    """Run the complete deterministic refresh workflow and save a receipt."""
    source_config = load_yaml_model(source_config_path, SourceConfig)
    teams_config = load_yaml_model(teams_config_path, TeamsConfig)
    passphrase = None
    if backup_dir is not None:
        passphrase = os.environ.get("ENGINTEL_BACKUP_PASSPHRASE")
        if passphrase is None:
            passphrase = typer.prompt("Backup passphrase", hide_input=True)
    receipt = RefreshService().run(
        runtime_paths(data_dir),
        source_config,
        teams_config,
        snapshot_name=snapshot_name,
        backup_dir=backup_dir,
        backup_passphrase=passphrase,
        backup_retention=backup_retention,
        progress_callback=_echo_refresh_progress,
    )
    typer.echo(receipt.model_dump_json(indent=2))
    if receipt.status != "completed":
        raise typer.Exit(1)


@refresh_app.command("latest")
def refresh_latest(data_dir: DataDir = None) -> None:
    """Print the most recent refresh receipt."""
    receipt = runtime_paths(data_dir).root / "receipts" / "refresh" / "latest.json"
    if not receipt.exists():
        raise typer.BadParameter("No refresh receipt exists", param_hint="--data-dir")
    typer.echo(receipt.read_text().rstrip())


@refresh_app.command("progress")
def refresh_progress(data_dir: DataDir = None) -> None:
    """Print durable progress for the latest current or completed refresh."""
    progress = (
        runtime_paths(data_dir).root
        / "receipts"
        / "refresh"
        / "progress"
        / "latest.json"
    )
    if not progress.exists():
        raise typer.BadParameter("No refresh progress exists", param_hint="--data-dir")
    typer.echo(progress.read_text().rstrip())


def _echo_refresh_progress(event: RefreshProgressEvent) -> None:
    typer.echo(
        json.dumps(event.model_dump(mode="json"), sort_keys=True),
        err=True,
    )


@schedule_app.command("install")
def schedule_install(
    hour: Annotated[
        int,
        typer.Option("--hour", min=0, max=23, help="Local hour for the daily refresh."),
    ] = 7,
    minute: Annotated[
        int,
        typer.Option("--minute", min=0, max=59, help="Local minute for the daily refresh."),
    ] = 0,
    backup_dir: Annotated[
        Path | None,
        typer.Option("--backup-dir", file_okay=False),
    ] = None,
    backup_retention: Annotated[
        int,
        typer.Option("--backup-retention", min=1),
    ] = 7,
    keychain_service: Annotated[
        str | None,
        typer.Option("--keychain-service", help="macOS Keychain service for backup."),
    ] = None,
    keychain_account: Annotated[
        str | None,
        typer.Option("--keychain-account", help="macOS Keychain account for backup."),
    ] = None,
    github_keychain_service: Annotated[
        str | None,
        typer.Option(
            "--github-keychain-service",
            help="macOS Keychain service containing the GitHub PAT.",
        ),
    ] = None,
    github_keychain_account: Annotated[
        str | None,
        typer.Option(
            "--github-keychain-account",
            help="macOS Keychain account containing the GitHub PAT.",
        ),
    ] = None,
    jira_keychain_service: Annotated[
        str | None,
        typer.Option(
            "--jira-keychain-service",
            help="macOS Keychain service containing the Jira API token.",
        ),
    ] = None,
    jira_keychain_account: Annotated[
        str | None,
        typer.Option(
            "--jira-keychain-account",
            help="macOS Keychain account containing the Jira API token.",
        ),
    ] = None,
    config_dir: Annotated[
        Path | None,
        typer.Option("--config-dir", help="Directory containing installation state."),
    ] = None,
) -> None:
    """Install an owned daily launchd or systemd-user refresh schedule."""
    state_root = config_dir or default_config_dir()
    lifecycle = LifecycleService(repository_root(), state_root)
    manifest = lifecycle.load()
    if manifest is None:
        raise typer.BadParameter(
            "Install Engineering Intelligence before enabling a schedule"
        )
    source_config = load_yaml_model(Path(manifest.source_config), SourceConfig)
    state = SchedulerService(repository_root(), state_root).install(
        Path(manifest.data_dir),
        source_config=Path(manifest.source_config),
        teams_config=Path(manifest.teams_config),
        hour=hour,
        minute=minute,
        backup_dir=backup_dir,
        backup_retention=backup_retention,
        keychain_service=keychain_service,
        keychain_account=keychain_account,
        github_keychain_service=github_keychain_service,
        github_keychain_account=github_keychain_account,
        jira_keychain_service=jira_keychain_service,
        jira_keychain_account=jira_keychain_account,
        jira_token_env=source_config.jira.token_env,
    )
    typer.echo(state.model_dump_json(indent=2))


@schedule_app.command("status")
def schedule_status(
    config_dir: Annotated[
        Path | None,
        typer.Option("--config-dir", help="Directory containing installation state."),
    ] = None,
) -> None:
    """Report the owned recurring-refresh schedule."""
    status = SchedulerService(
        repository_root(),
        config_dir or default_config_dir(),
    ).status()
    typer.echo(json.dumps(status, indent=2, sort_keys=True))


@schedule_app.command("uninstall")
def schedule_uninstall(
    config_dir: Annotated[
        Path | None,
        typer.Option("--config-dir", help="Directory containing installation state."),
    ] = None,
) -> None:
    """Disable the managed schedule and remove only its owned files."""
    removed = SchedulerService(
        repository_root(),
        config_dir or default_config_dir(),
    ).uninstall()
    typer.echo(json.dumps({"removed": removed}, sort_keys=True))


@dashboard_app.command("get")
def dashboard_get(
    snapshot: Annotated[str, typer.Argument(help="Snapshot ID or unique snapshot name.")],
    teams_config_path: Annotated[
        Path,
        typer.Option("--teams-config", exists=True, dir_okay=False),
    ] = Path("config/teams.example.yaml"),
    source_config_path: Annotated[
        Path,
        typer.Option("--source-config", exists=True, dir_okay=False),
    ] = Path("config/sources.example.yaml"),
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: json or markdown."),
    ] = "json",
    output: Annotated[
        Path | None,
        typer.Option("--output", dir_okay=False, help="Write output to this file."),
    ] = None,
    data_dir: DataDir = None,
) -> None:
    """Render the team-health Dashboard for an existing snapshot."""
    if output_format not in {"json", "markdown"}:
        raise typer.BadParameter("Expected json or markdown", param_hint="--format")
    teams_config = load_yaml_model(teams_config_path, TeamsConfig)
    source_config = load_yaml_model(source_config_path, SourceConfig)
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    dashboard = DashboardQuery(
        sessions,
        jira_base_url=str(source_config.jira.base_url),
    ).get(snapshot, teams_config, github_config=source_config.github)
    dashboard = FlagService(sessions).record_dashboard(dashboard)
    rendered = (
        dashboard.model_dump_json(indent=2)
        if output_format == "json"
        else render_dashboard_markdown(dashboard)
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + ("" if rendered.endswith("\n") else "\n"))
        typer.echo(
            json.dumps(
                {
                    "format": output_format,
                    "output": str(output.resolve()),
                    "snapshot_id": dashboard.snapshot_id,
                },
                sort_keys=True,
            )
        )
    else:
        typer.echo(rendered)


@feature_app.command("get")
def feature_get(
    issue_key: Annotated[str, typer.Argument(help="IBR Jira issue key.")],
    snapshot: Annotated[
        str,
        typer.Option("--snapshot", help="Snapshot ID or unique snapshot name."),
    ],
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: json or markdown."),
    ] = "json",
    output: Annotated[
        Path | None,
        typer.Option("--output", dir_okay=False, help="Write output to this file."),
    ] = None,
    data_dir: DataDir = None,
) -> None:
    """Render hierarchy-aware detail for an IBR Feature."""
    if output_format not in {"json", "markdown"}:
        raise typer.BadParameter("Expected json or markdown", param_hint="--format")
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    feature = FeatureQuery(sessions).get(snapshot, issue_key.upper())
    rendered = (
        feature.model_dump_json(indent=2)
        if output_format == "json"
        else render_feature_markdown(feature)
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + ("" if rendered.endswith("\n") else "\n"))
        typer.echo(
            json.dumps(
                {
                    "feature_key": feature.feature_key,
                    "format": output_format,
                    "output": str(output.resolve()),
                    "snapshot_id": feature.snapshot_id,
                },
                sort_keys=True,
            )
        )
    else:
        typer.echo(rendered)


@team_app.command("brief")
def team_brief(
    team_identifier: Annotated[str, typer.Argument(help="Team ID, name, or alias.")],
    snapshot: Annotated[
        str,
        typer.Option("--snapshot", help="Snapshot ID or unique snapshot name."),
    ],
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: json or markdown."),
    ] = "json",
    teams_config_path: Annotated[
        Path,
        typer.Option("--teams-config", exists=True, dir_okay=False),
    ] = Path("config/teams.example.yaml"),
    source_config_path: Annotated[
        Path,
        typer.Option("--source-config", exists=True, dir_okay=False),
    ] = Path("config/sources.example.yaml"),
    data_dir: DataDir = None,
) -> None:
    """Render a compact, deterministic meeting brief for one team."""
    if output_format not in {"json", "markdown"}:
        raise typer.BadParameter("Expected json or markdown", param_hint="--format")
    teams_config = load_yaml_model(teams_config_path, TeamsConfig)
    source_config = load_yaml_model(source_config_path, SourceConfig)
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    detail = TeamQuery(
        sessions,
        jira_base_url=str(source_config.jira.base_url),
    ).get(snapshot, team_identifier, teams_config)
    brief = build_team_brief(detail)
    typer.echo(
        brief.model_dump_json(indent=2)
        if output_format == "json"
        else render_team_brief_markdown(brief)
    )


@team_app.command("get")
def team_get(
    team_identifier: Annotated[str, typer.Argument(help="Team ID, name, or alias.")],
    snapshot: Annotated[
        str,
        typer.Option("--snapshot", help="Snapshot ID or unique snapshot name."),
    ],
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: json or markdown."),
    ] = "json",
    output: Annotated[
        Path | None,
        typer.Option("--output", dir_okay=False, help="Write output to this file."),
    ] = None,
    teams_config_path: Annotated[
        Path,
        typer.Option("--teams-config", exists=True, dir_okay=False),
    ] = Path("config/teams.example.yaml"),
    source_config_path: Annotated[
        Path,
        typer.Option("--source-config", exists=True, dir_okay=False),
    ] = Path("config/sources.example.yaml"),
    data_dir: DataDir = None,
) -> None:
    """Render one team's snapshot-safe workflow, people, and evidence."""
    if output_format not in {"json", "markdown"}:
        raise typer.BadParameter("Expected json or markdown", param_hint="--format")
    teams_config = load_yaml_model(teams_config_path, TeamsConfig)
    source_config = load_yaml_model(source_config_path, SourceConfig)
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    team = TeamQuery(
        sessions,
        jira_base_url=str(source_config.jira.base_url),
    ).get(snapshot, team_identifier, teams_config)
    rendered = (
        team.model_dump_json(indent=2)
        if output_format == "json"
        else render_team_markdown(team)
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + ("" if rendered.endswith("\n") else "\n"))
        typer.echo(
            json.dumps(
                {
                    "team_id": team.team_id,
                    "format": output_format,
                    "output": str(output.resolve()),
                    "snapshot_id": team.snapshot_id,
                },
                sort_keys=True,
            )
        )
    else:
        typer.echo(rendered)


@team_app.command("work")
def team_work(
    team_identifier: Annotated[str, typer.Argument(help="Team ID, name, or alias.")],
    snapshot: Annotated[
        str,
        typer.Option("--snapshot", help="Snapshot ID or unique snapshot name."),
    ],
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: json."),
    ] = "json",
    teams_config_path: Annotated[
        Path,
        typer.Option("--teams-config", exists=True, dir_okay=False),
    ] = Path("config/teams.example.yaml"),
    data_dir: DataDir = None,
) -> None:
    """Classify the team's pinned Jira and GitHub work as IBR-linked or not."""
    if output_format != "json":
        raise typer.BadParameter("Expected json", param_hint="--format")
    teams_config = load_yaml_model(teams_config_path, TeamsConfig)
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    classification = TeamWorkQuery(sessions).get(snapshot, team_identifier, teams_config)
    typer.echo(classification.model_dump_json(indent=2))


@people_app.command("list")
def people_list(
    snapshot: Annotated[
        str,
        typer.Option("--snapshot", help="Snapshot ID or unique snapshot name."),
    ],
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: json or markdown."),
    ] = "json",
    data_dir: DataDir = None,
) -> None:
    """Render the configured People directory and current work context."""
    if output_format not in {"json", "markdown"}:
        raise typer.BadParameter("Expected json or markdown", param_hint="--format")
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    directory = PeopleQuery(sessions).get(snapshot)
    typer.echo(
        directory.model_dump_json(indent=2)
        if output_format == "json"
        else render_people_markdown(directory)
    )


@individual_app.command("get")
def individual_get(
    person_identifier: Annotated[
        str,
        typer.Argument(help="Person ID, name, Jira account, or GitHub login."),
    ],
    snapshot: Annotated[
        str | None,
        typer.Option("--snapshot", help="Exact snapshot ID or unique snapshot name."),
    ] = None,
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help="Snapshot policy when --snapshot is omitted: cached, smart, or fresh.",
        ),
    ] = "smart",
    max_age_hours: Annotated[
        float,
        typer.Option(
            "--max-age-hours",
            min=0,
            help="Maximum compatible snapshot age used by smart mode.",
        ),
    ] = 4.0,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: json or markdown."),
    ] = "json",
    teams_config_path: Annotated[
        Path,
        typer.Option("--teams-config", exists=True, dir_okay=False),
    ] = Path("config/teams.example.yaml"),
    source_config_path: Annotated[
        Path,
        typer.Option("--source-config", exists=True, dir_okay=False),
    ] = Path("config/sources.example.yaml"),
    data_dir: DataDir = None,
) -> None:
    """Render evidence-backed work context without scoring the individual."""
    if output_format not in {"json", "markdown"}:
        raise typer.BadParameter("Expected json or markdown", param_hint="--format")
    if mode not in {"cached", "smart", "fresh"}:
        raise typer.BadParameter(
            "Expected cached, smart, or fresh", param_hint="--mode"
        )
    if snapshot is not None and mode == "fresh":
        raise typer.BadParameter(
            "--snapshot cannot be combined with --mode fresh", param_hint="--mode"
        )
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    teams_config = load_yaml_model(teams_config_path, TeamsConfig)
    source_config = load_yaml_model(source_config_path, SourceConfig)
    selected_snapshot = snapshot
    if selected_snapshot is None:
        selection = latest_snapshot(
            sessions,
            source_config,
            teams_config,
            max_age_seconds=max_age_hours * 3600,
        )
        needs_refresh = mode == "fresh" or (mode == "smart" and not selection.fresh)
        if needs_refresh:
            receipt = RefreshService().run(
                paths,
                source_config,
                teams_config,
                progress_callback=_echo_refresh_progress,
            )
            if receipt.status != "completed" or receipt.snapshot_id is None:
                raise typer.BadParameter(
                    f"Refresh failed: {receipt.error or 'no snapshot created'}",
                    param_hint="--mode",
                )
            selected_snapshot = receipt.snapshot_id
            typer.echo(
                f"Fresh snapshot created: {receipt.snapshot_name or receipt.snapshot_id}",
                err=True,
            )
        elif selection.snapshot is not None:
            selected_snapshot = selection.snapshot.id
            age_hours = (selection.age_seconds or 0.0) / 3600
            typer.echo(
                f"Using compatible saved snapshot {selection.snapshot.name or selection.snapshot.id} "
                f"({age_hours:.1f}h old)",
                err=True,
            )
        else:
            raise typer.BadParameter(
                "No saved snapshot is available; use --mode smart or --mode fresh",
                param_hint="--mode",
            )
    individual = load_cached_individual(
        paths.root,
        selected_snapshot,
        person_identifier,
    )
    if individual is None:
        individual = IndividualQuery(sessions, teams_config=teams_config).get(
            selected_snapshot,
            person_identifier,
        )
        cache_individual(paths.root, individual)
    typer.echo(
        individual.model_dump_json(indent=2)
        if output_format == "json"
        else render_individual_markdown(individual)
    )


@flag_app.command("mark-viewed")
def flag_mark_viewed(
    fingerprint: Annotated[str, typer.Argument(help="Stable logical flag fingerprint.")],
    data_dir: DataDir = None,
) -> None:
    """Mark a logical flag as viewed without changing Jira or GitHub."""
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    FlagService(sessions).mark_viewed(fingerprint)
    typer.echo(
        json.dumps(
            {"fingerprint": fingerprint, "state": "viewed"},
            sort_keys=True,
        )
    )


@attention_app.command("list")
def attention_list(
    collection: Annotated[
        AttentionCollection,
        typer.Option("--collection", help="active, snoozed, understood, resolved, or all."),
    ] = AttentionCollection.active,
    unread_only: Annotated[
        bool,
        typer.Option("--unread", help="Return only unread flags."),
    ] = False,
    team: Annotated[
        str | None,
        typer.Option("--team", help="Filter by exact team ID or name."),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: json or markdown."),
    ] = "json",
    data_dir: DataDir = None,
) -> None:
    """List flags from the durable local Attention inbox."""
    if output_format not in {"json", "markdown"}:
        raise typer.BadParameter("Expected json or markdown", param_hint="--format")
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    inbox = AttentionQuery(sessions).list(
        collection=collection,
        unread_only=unread_only,
        team=team,
    )
    typer.echo(
        inbox.model_dump_json(indent=2)
        if output_format == "json"
        else render_attention_markdown(inbox)
    )


@attention_app.command("get")
def attention_get(
    fingerprint: Annotated[str, typer.Argument(help="Stable logical flag fingerprint.")],
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: json or markdown."),
    ] = "json",
    data_dir: DataDir = None,
) -> None:
    """Show one flag's evidence, timestamps, and complete occurrence history."""
    if output_format not in {"json", "markdown"}:
        raise typer.BadParameter("Expected json or markdown", param_hint="--format")
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    flag = AttentionQuery(sessions).get(fingerprint)
    typer.echo(
        flag.model_dump_json(indent=2)
        if output_format == "json"
        else render_attention_flag_markdown(flag)
    )


@metrics_app.command("get")
def metrics_get(
    snapshot: Annotated[
        str,
        typer.Option("--snapshot", help="Snapshot ID or unique snapshot name."),
    ],
    team: Annotated[
        str | None,
        typer.Option("--team", help="Exact Jira team name; omit for all source scope."),
    ] = None,
    scope: Annotated[
        str | None,
        typer.Option("--scope", help="Exact snapshot Jira scope, such as query:metrics."),
    ] = None,
    date_from: Annotated[
        str | None,
        typer.Option("--from", help="Inclusive completion date (YYYY-MM-DD)."),
    ] = None,
    date_to: Annotated[
        str | None,
        typer.Option("--to", help="Inclusive completion date (YYYY-MM-DD)."),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: json or markdown."),
    ] = "json",
    teams_config_path: Annotated[
        Path,
        typer.Option("--teams-config", exists=True, dir_okay=False),
    ] = Path("config/teams.example.yaml"),
    data_dir: DataDir = None,
) -> None:
    """Render transparent Jira transition-based engineering metrics."""
    if output_format not in {"json", "markdown"}:
        raise typer.BadParameter("Expected json or markdown", param_hint="--format")
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    canonical_team, aliases = resolve_metric_team(
        team,
        load_yaml_model(teams_config_path, TeamsConfig),
    )
    view = MetricsQuery(sessions).get(
        snapshot,
        team=canonical_team,
        team_aliases=aliases,
        scope=scope,
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
    )
    typer.echo(
        view.model_dump_json(indent=2)
        if output_format == "json"
        else render_metrics_markdown(view)
    )


@backup_app.command("create")
def backup_create(
    destination: Annotated[
        Path | None,
        typer.Option(
            "--destination",
            dir_okay=False,
            help="Encrypted backup file outside the primary data directory.",
        ),
    ] = None,
    data_dir: DataDir = None,
) -> None:
    """Create and immediately verify an encrypted backup."""
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    if destination is None:
        destination = Path(typer.prompt("Where should the encrypted backup be stored?"))
    passphrase = os.environ.get("ENGINTEL_BACKUP_PASSPHRASE") or typer.prompt(
        "Backup passphrase",
        hide_input=True,
        confirmation_prompt=True,
    )
    manifest = BackupService().create(paths, destination, passphrase)
    typer.echo(
        json.dumps(
            {
                "backup": str(destination.expanduser().resolve()),
                "created_at": manifest.created_at.isoformat(),
                "database_sha256": manifest.database_sha256,
                "raw_file_count": manifest.raw_file_count,
                "verified": True,
            },
            sort_keys=True,
        )
    )


@backup_app.command("verify")
def backup_verify(
    backup_path: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False),
    ],
) -> None:
    """Decrypt and verify an encrypted backup without restoring it."""
    passphrase = os.environ.get("ENGINTEL_BACKUP_PASSPHRASE") or typer.prompt(
        "Backup passphrase",
        hide_input=True,
    )
    manifest = BackupService().verify(backup_path, passphrase)
    typer.echo(
        json.dumps(
            {
                "backup": str(backup_path.expanduser().resolve()),
                "created_at": manifest.created_at.isoformat(),
                "database_sha256": manifest.database_sha256,
                "raw_file_count": manifest.raw_file_count,
                "verified": True,
            },
            sort_keys=True,
        )
    )


@backup_app.command("restore")
def backup_restore(
    backup_path: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False),
    ],
    destination: Annotated[
        Path | None,
        typer.Option("--destination", file_okay=False),
    ] = None,
) -> None:
    """Restore an encrypted backup into a new, non-existing directory."""
    if destination is None:
        destination = Path(typer.prompt("Where should the backup be restored?"))
    passphrase = os.environ.get("ENGINTEL_BACKUP_PASSPHRASE") or typer.prompt(
        "Backup passphrase",
        hide_input=True,
    )
    manifest = BackupService().restore(backup_path, destination, passphrase)
    typer.echo(
        json.dumps(
            {
                "backup": str(backup_path.expanduser().resolve()),
                "destination": str(destination.expanduser().resolve()),
                "database_sha256": manifest.database_sha256,
                "raw_file_count": manifest.raw_file_count,
                "restored": True,
            },
            sort_keys=True,
        )
    )


@app.command("install")
def install(
    agents: Annotated[
        list[str] | None,
        typer.Option("--agent", help="Agent adapter: codex or claude-code; repeat as needed."),
    ] = None,
    data_dir: DataDir = None,
    config_dir: Annotated[
        Path | None,
        typer.Option("--config-dir", help="Directory for the installation manifest."),
    ] = None,
    source_config: Annotated[
        Path | None,
        typer.Option("--source-config", exists=True, dir_okay=False),
    ] = None,
    teams_config: Annotated[
        Path | None,
        typer.Option("--teams-config", exists=True, dir_okay=False),
    ] = None,
) -> None:
    """Install agent skill links and record every local mutation."""
    if data_dir is None:
        data_dir = Path(typer.prompt("Where should persistent data be stored?"))
    selected = _agent_names(agents)
    state_root = (config_dir or default_config_dir()).expanduser().resolve()
    source_config = source_config or state_root / "sources.yaml"
    teams_config = teams_config or state_root / "teams.yaml"
    if not source_config.is_file() or not teams_config.is_file():
        raise typer.BadParameter(
            "Run `engintel setup` first or provide both configuration paths.",
            param_hint="--source-config",
        )
    # Validate before mutating agent configuration.
    load_yaml_model(source_config, SourceConfig)
    load_yaml_model(teams_config, TeamsConfig)
    manifest = LifecycleService(repository_root(), state_root).install(
        data_dir,
        selected,
        source_config=source_config,
        teams_config=teams_config,
    )
    typer.echo(manifest.model_dump_json(indent=2))


@app.command("upgrade")
def upgrade(
    config_dir: Annotated[
        Path | None,
        typer.Option("--config-dir", help="Directory containing the installation manifest."),
    ] = None,
) -> None:
    """Apply migrations and refresh recorded agent adapters in place."""
    manifest = LifecycleService(repository_root(), config_dir or default_config_dir()).upgrade()
    typer.echo(manifest.model_dump_json(indent=2))


@app.command("uninstall")
def uninstall(
    agents: Annotated[
        list[str] | None,
        typer.Option("--agent", help="Only remove selected adapters."),
    ] = None,
    config_dir: Annotated[
        Path | None,
        typer.Option("--config-dir", help="Directory containing the installation manifest."),
    ] = None,
) -> None:
    """Remove only recorded agent links; preserve data, backups, and repository."""
    state_root = config_dir or default_config_dir()
    schedule_removed = (
        SchedulerService(repository_root(), state_root).uninstall()
        if agents is None
        else []
    )
    selected = _agent_names(agents) if agents else None
    removed = LifecycleService(
        repository_root(),
        state_root,
    ).uninstall(selected)
    typer.echo(
        json.dumps(
            {
                "removed": removed,
                "schedule_removed": schedule_removed,
                "persistent_data_preserved": True,
            }
        )
    )


@app.command("export")
def export_command(
    output: Annotated[
        Path | None,
        typer.Option("--output", dir_okay=False, help="Destination ZIP bundle."),
    ] = None,
    include_raw: Annotated[
        bool,
        typer.Option("--include-raw", help="Include content-addressed raw payload objects."),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Explicitly replace an existing destination."),
    ] = False,
    source_config: Annotated[
        Path,
        typer.Option("--source-config", exists=True, dir_okay=False),
    ] = Path("config/sources.example.yaml"),
    teams_config: Annotated[
        Path,
        typer.Option("--teams-config", exists=True, dir_okay=False),
    ] = Path("config/teams.example.yaml"),
    data_dir: DataDir = None,
) -> None:
    """Export normalized history, readable extracts, and non-secret configuration."""
    if output is None:
        output = Path(typer.prompt("Where should the export be stored?"))
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    manifest = PortabilityService().export_bundle(
        paths,
        output,
        source_config=source_config,
        teams_config=teams_config,
        include_raw=include_raw,
        overwrite=overwrite,
    )
    typer.echo(
        json.dumps(
            {
                "export": str(output.expanduser().resolve()),
                "export_id": manifest.export_id,
                "tables": manifest.included_tables,
                "raw_payload_count": manifest.raw_payload_count,
                "verified": True,
            },
            sort_keys=True,
        )
    )


@app.command("import")
def import_command(
    input_bundle: Annotated[
        Path | None,
        typer.Option("--input", exists=True, dir_okay=False, help="Source ZIP bundle."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate and report without changing data."),
    ] = False,
    backup_output: Annotated[
        Path | None,
        typer.Option(
            "--backup-output",
            dir_okay=False,
            help="Required encrypted pre-import backup for a non-empty destination.",
        ),
    ] = None,
    data_dir: DataDir = None,
) -> None:
    """Validate, preview, and idempotently import a portable bundle."""
    if input_bundle is None:
        input_bundle = Path(typer.prompt("Which export should be imported?"))
    paths = runtime_paths(data_dir)
    service = PortabilityService()
    if dry_run and not paths.database.exists():
        with tempfile.TemporaryDirectory(prefix="engintel-import-plan-") as temporary:
            planning_database = Path(temporary) / "planning.db"
            upgrade_database(planning_database)
            plan = service.plan_import(planning_database, input_bundle)
        typer.echo(plan.model_dump_json(indent=2))
        return
    if not dry_run:
        upgrade_database(paths.database)
    plan = service.plan_import(paths.database, input_bundle)
    if dry_run:
        typer.echo(plan.model_dump_json(indent=2))
        return
    existing_records = service.database_record_count(paths.database)
    if plan.total_inserts and existing_records:
        if backup_output is None:
            backup_output = Path(
                typer.prompt("Where should the encrypted pre-import backup be stored?")
            )
        passphrase = os.environ.get("ENGINTEL_BACKUP_PASSPHRASE") or typer.prompt(
            "Pre-import backup passphrase",
            hide_input=True,
            confirmation_prompt=True,
        )
        BackupService().create(paths, backup_output, passphrase)
    applied = service.import_bundle(paths, input_bundle)
    typer.echo(applied.model_dump_json(indent=2))


def _agent_names(values: list[str] | None) -> list[AgentName]:
    selected = values or ["codex", "claude-code"]
    unknown = sorted(set(selected) - {"codex", "claude-code"})
    if unknown:
        raise typer.BadParameter(f"Unknown agents: {unknown}", param_hint="--agent")
    return cast(list[AgentName], selected)


if __name__ == "__main__":
    app()
