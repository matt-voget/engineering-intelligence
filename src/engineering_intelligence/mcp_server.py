"""Read-only stdio MCP interface over deterministic presentation queries."""

import os
from datetime import date
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from engineering_intelligence.config import SourceConfig, TeamsConfig, load_yaml_model
from engineering_intelligence.flags import FlagService
from engineering_intelligence.lifecycle import repository_root
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
    upgrade_database,
)
from engineering_intelligence.presentations.attention import AttentionCollection
from engineering_intelligence.presentations.team_brief import build_team_brief
from engineering_intelligence.queries.attention import AttentionQuery
from engineering_intelligence.queries.build_cycle import BuildCycleTimeQuery
from engineering_intelligence.queries.dashboard import DashboardQuery
from engineering_intelligence.queries.feature import FeatureQuery
from engineering_intelligence.queries.github_pr_metrics import GitHubPullRequestMetricsQuery
from engineering_intelligence.queries.individual import IndividualQuery
from engineering_intelligence.queries.metrics import MetricsQuery, resolve_metric_team
from engineering_intelligence.queries.people import PeopleQuery
from engineering_intelligence.queries.team import TeamQuery
from engineering_intelligence.runtime import runtime_paths

SERVER_NAME = "engineering-intelligence"
MAX_FEATURE_NODES = 250
READ_ONLY_TOOL = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def create_server(
    data_dir: Path,
    source_config_path: Path | None = None,
    teams_config_path: Path | None = None,
) -> FastMCP:
    """Create an MCP server bound to one explicit local runtime directory."""
    paths = runtime_paths(data_dir)
    upgrade_database(paths.database)
    sessions = session_factory(create_sqlite_engine(paths.database))
    source_config = load_yaml_model(
        source_config_path or repository_root() / "config/sources.example.yaml",
        SourceConfig,
    )
    teams_config = load_yaml_model(
        teams_config_path or repository_root() / "config/teams.example.yaml",
        TeamsConfig,
    )
    server = FastMCP(
        SERVER_NAME,
        instructions=(
            "Read-only, deterministic access to saved Engineering Intelligence "
            "snapshots. Reuse one snapshot across follow-up calls."
        ),
        json_response=True,
    )

    @server.tool(
        name="get_metrics",
        title="Get engineering Metrics",
        annotations=READ_ONLY_TOOL,
        description=(
            "Calculate versioned Ideate and Build calendar-day metrics from persisted "
            "Jira status transitions for one saved snapshot and optional exact team/date "
            "filters. Returns formulas, inclusion rules, 90-day baselines, weekly trends, "
            "data-quality warnings, and linked contributing issues. Metrics are never "
            "combined into a team or individual performance score."
        ),
    )
    def get_metrics(
        snapshot: str,
        team: str | None = None,
        scope: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> dict[str, Any]:
        canonical_team, aliases = resolve_metric_team(team, teams_config)
        return MetricsQuery(sessions).get(
            snapshot,
            team=canonical_team,
            team_aliases=aliases,
            scope=scope,
            date_from=date.fromisoformat(date_from) if date_from else None,
            date_to=date.fromisoformat(date_to) if date_to else None,
        ).model_dump(mode="json")

    @server.tool(
        name="get_build_cycle_time",
        title="Get team Build Cycle Time",
        annotations=READ_ONLY_TOOL,
        description=(
            "Return snapshot-backed In Progress-to-Done calendar time for Epic, "
            "Feature Request, and FDI Request parents, separated into IBR-linked and "
            "non-IBR groups. Non-IBR includes every team-assigned issue type outside "
            "the IBR hierarchy. Includes status-duration attribution and child issue "
            "cycle evidence."
        ),
    )
    def get_build_cycle_time(snapshot: str, team: str) -> dict[str, Any]:
        return BuildCycleTimeQuery(sessions).get(
            snapshot,
            team,
            teams_config,
        ).model_dump(mode="json")

    @server.tool(
        name="get_github_pr_metrics",
        title="Get team GitHub PR metrics",
        annotations=READ_ONLY_TOOL,
        description=(
            "Return snapshot-backed pull-request pickup and review time for PRs authored "
            "by active members of the selected team across all configured repositories, with "
            "contributing PR links, authors, and reviewers."
        ),
    )
    def get_github_pr_metrics(snapshot: str, team: str) -> dict[str, Any]:
        return GitHubPullRequestMetricsQuery(sessions).get(
            snapshot,
            team,
            source_config,
            teams_config,
        ).model_dump(mode="json")

    @server.tool(
        name="list_attention",
        title="List the Attention inbox",
        annotations=READ_ONLY_TOOL,
        description=(
            "Return up to 250 durable health flags with read state, lifecycle timestamps, "
            "severity, evidence, affected team, confidence, and investigation questions. "
            "Collection may be active, snoozed, understood, resolved, or all. This reads "
            "local state and never modifies Jira or GitHub."
        ),
    )
    def list_attention(
        collection: str = "active",
        unread_only: bool = False,
        team: str | None = None,
    ) -> dict[str, Any]:
        try:
            selected = AttentionCollection(collection)
        except ValueError as error:
            raise ValueError(
                "collection must be active, snoozed, understood, resolved, or all"
            ) from error
        return AttentionQuery(sessions, max_flags=250).list(
            collection=selected,
            unread_only=unread_only,
            team=team,
        ).model_dump(mode="json")

    @server.tool(
        name="get_flag",
        title="Get Attention flag detail",
        annotations=READ_ONLY_TOOL,
        description=(
            "Return one durable flag with explanation, evidence, current read/user state, "
            "severity history, all retained occurrences, and immutable lifecycle events. "
            "The fingerprint must come from the Dashboard, Team view, or Attention inbox."
        ),
    )
    def get_flag(fingerprint: str) -> dict[str, Any]:
        return AttentionQuery(sessions).get(fingerprint).model_dump(mode="json")

    @server.tool(
        name="get_dashboard",
        title="Get engineering team Dashboard",
        annotations=READ_ONLY_TOOL,
        description=(
            "Deterministically return the complete team-health Dashboard for a required "
            "saved snapshot ID or unique name. The response is bounded to configured "
            "teams, includes source freshness and Jira evidence, and has no source-system "
            "side effects. Rendering may idempotently persist local flag evaluation state."
        ),
    )
    def get_dashboard(snapshot: str) -> dict[str, Any]:
        dashboard = DashboardQuery(
            sessions,
            jira_base_url=str(source_config.jira.base_url),
        ).get(
            snapshot,
            teams_config,
            github_config=source_config.github,
        )
        return FlagService(sessions).record_dashboard(dashboard).model_dump(mode="json")

    @server.tool(
        name="get_people",
        title="Get the People directory",
        annotations=READ_ONLY_TOOL,
        description=(
            "Deterministically return up to 250 configured active people with current "
            "team membership, Feature relationships, active Jira context, and identity "
            "mapping state for a required saved snapshot. It is evidence-only and has "
            "no source-system side effects."
        ),
    )
    def get_people(snapshot: str) -> dict[str, Any]:
        return PeopleQuery(
            sessions,
            max_people=250,
            max_feature_nodes=MAX_FEATURE_NODES,
        ).get(snapshot).model_dump(mode="json")

    @server.tool(
        name="get_individual",
        title="Get Individual work context",
        annotations=READ_ONLY_TOOL,
        description=(
            "Deterministically return evidence-backed Jira assignments, hierarchy "
            "roll-ups, GitHub authorship/reviews, memberships, blockers, and labeled "
            "signals for one configured person in a required saved snapshot. Person may "
            "be an ID, exact name, Jira account, or GitHub login. It never returns a "
            "rating, ranking, or productivity score; rules lacking verified roster or "
            "identity evidence are returned as explicitly suppressed. It has no "
            "source-system side effects."
        ),
    )
    def get_individual(snapshot: str, person: str) -> dict[str, Any]:
        return IndividualQuery(
            sessions,
            max_feature_nodes=MAX_FEATURE_NODES,
            teams_config=teams_config,
        ).get(snapshot, person).model_dump(mode="json")

    @server.tool(
        name="get_team_brief",
        title="Get compact engineering Team brief",
        annotations=READ_ONLY_TOOL,
        description=(
            "Deterministically return a compact meeting-preparation contract for one "
            "configured team in a required saved snapshot. Includes health, coverage, "
            "flags with evidence links, latest completed work, all in-progress and "
            "ready-for-build IBR work, blocker links, roster completeness, and aggregate "
            "GitHub evidence counts. Omits individual GitHub delivery records; call "
            "get_team only when detailed evidence is needed."
        ),
    )
    def get_team_brief(snapshot: str, team: str) -> dict[str, Any]:
        detail = TeamQuery(
            sessions,
            jira_base_url=str(source_config.jira.base_url),
            max_feature_nodes=MAX_FEATURE_NODES,
        ).get(snapshot, team, teams_config)
        return build_team_brief(detail).model_dump(mode="json")

    @server.tool(
        name="get_team",
        title="Get engineering Team detail",
        annotations=READ_ONLY_TOOL,
        description=(
            "Deterministically return one configured team's health, complete IBR "
            "workflow including empty columns, roster, GitHub evidence, blockers, and "
            "data-quality states for a required saved snapshot. Team may be an ID, name, "
            "or configured alias. It has no source-system side effects."
        ),
    )
    def get_team(snapshot: str, team: str) -> dict[str, Any]:
        return TeamQuery(
            sessions,
            jira_base_url=str(source_config.jira.base_url),
            max_feature_nodes=MAX_FEATURE_NODES,
        ).get(snapshot, team, teams_config).model_dump(mode="json")

    @server.tool(
        name="get_feature",
        title="Get an IBR Feature",
        annotations=READ_ONLY_TOOL,
        description=(
            "Deterministically return hierarchy-aware detail for one Jira issue observed "
            "on the configured IBR board in a required saved snapshot. Includes at most "
            f"{MAX_FEATURE_NODES} hierarchy nodes, source freshness, contributors, Jira "
            "links, timeline, and data-quality notes. It is read-only and has no side effects."
        ),
    )
    def get_feature(snapshot: str, issue_key: str) -> dict[str, Any]:
        return FeatureQuery(sessions, max_nodes=MAX_FEATURE_NODES).get(
            snapshot,
            issue_key.upper(),
        ).model_dump(mode="json")

    return server


def main() -> None:
    """Run the local MCP server over stdio."""
    configured = os.environ.get("ENGINTEL_DATA_DIR")
    if not configured:
        raise SystemExit("ENGINTEL_DATA_DIR must identify the installed runtime directory")
    source_config = os.environ.get("ENGINTEL_SOURCE_CONFIG")
    teams_config = os.environ.get("ENGINTEL_TEAMS_CONFIG")
    create_server(
        Path(configured),
        Path(source_config) if source_config else None,
        Path(teams_config) if teams_config else None,
    ).run(transport="stdio")


if __name__ == "__main__":
    main()
