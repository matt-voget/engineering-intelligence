import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from engineering_intelligence.config import SourceConfig, TeamsConfig
from engineering_intelligence.ingestion.archive import RawPayloadArchive
from engineering_intelligence.ingestion.jira.service import JiraIngestionService
from engineering_intelligence.mcp_server import MAX_FEATURE_NODES, create_server
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
    upgrade_database,
)
from engineering_intelligence.snapshots import SnapshotService

FIXTURES = Path(__file__).parent / "fixtures/jira"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


class HierarchyClient:
    def get_board_configuration(self, _board_id: int) -> dict[str, Any]:
        return fixture("board_2168.json")

    def iter_board_issues(
        self,
        _board_id: int,
        *,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return [fixture("issue_idn_1.json")]

    def iter_child_issues(
        self,
        parent_keys: list[str],
        *,
        fields: list[str],
    ) -> list[dict[str, Any]]:
        children = {
            "IDN-1": fixture("issue_idn_2.json"),
            "IDN-2": fixture("issue_idn_3.json"),
        }
        return [children[key] for key in parent_keys if key in children]

    def iter_issue_changelogs(self, _issue_ids_or_keys, *, field_ids=None):
        return []


def populated_runtime(tmp_path: Path) -> Path:
    upgrade_database(tmp_path / "engineering-intelligence.db")
    sessions = session_factory(create_sqlite_engine(tmp_path / "engineering-intelligence.db"))
    observed_at = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    JiraIngestionService(
        sessions,
        RawPayloadArchive(tmp_path / "raw"),
        HierarchyClient(),  # type: ignore[arg-type]
        base_url="https://gravitee.atlassian.net",
        team_field_id="customfield_12345",
    ).ingest_board(2168, observed_at=observed_at)
    teams = TeamsConfig.model_validate({"teams": [{
        "id": "a2a", "name": "A2A", "members": [],
        "roster_source": {"state": "configured"},
    }]})
    sources = SourceConfig.model_validate({
        "jira": {"base_url": "https://gravitee.atlassian.net", "boards": [
            {"id": 2168, "name": "Portfolio", "role": "portfolio"}
        ]}
    })
    SnapshotService(sessions).create(
        [2168],
        name="mcp-fixture",
        created_at=observed_at,
        teams_config=teams,
        source_config=sources,
    )
    (tmp_path / "sources.yaml").write_text(sources.model_dump_json())
    (tmp_path / "teams.yaml").write_text(teams.model_dump_json())
    return tmp_path


def test_mcp_tools_use_deterministic_query_contracts(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(
            populated_runtime(tmp_path),
            tmp_path / "sources.yaml",
            tmp_path / "teams.yaml",
        )
        tools = {tool.name: tool for tool in await server.list_tools()}

        assert set(tools) == {
            "get_metrics",
            "list_attention",
            "get_flag",
            "get_dashboard",
            "get_people",
            "get_individual",
            "get_team_brief",
            "get_team",
            "get_feature",
        }
        assert all(tool.annotations.readOnlyHint is True for tool in tools.values())
        assert all(tool.annotations.destructiveHint is False for tool in tools.values())
        assert tools["get_feature"].inputSchema["required"] == ["snapshot", "issue_key"]
        assert str(MAX_FEATURE_NODES) in (tools["get_feature"].description or "")

        _, dashboard = await server.call_tool(
            "get_dashboard",
            {"snapshot": "mcp-fixture"},
        )
        _, feature = await server.call_tool(
            "get_feature",
            {"snapshot": "mcp-fixture", "issue_key": "idn-1"},
        )
        _, attention = await server.call_tool("list_attention", {})
        fingerprint = attention["flags"][0]["fingerprint"]
        _, flag = await server.call_tool("get_flag", {"fingerprint": fingerprint})
        _, metrics = await server.call_tool(
            "get_metrics",
            {"snapshot": "mcp-fixture"},
        )
        _, team_brief = await server.call_tool(
            "get_team_brief",
            {"snapshot": "mcp-fixture", "team": "a2a"},
        )

        assert dashboard["snapshot_name"] == "mcp-fixture"
        assert feature["feature_key"] == "IDN-1"
        assert feature["summary"]["total_issues"] == 3
        assert flag["fingerprint"] == fingerprint
        assert flag["evidence_count"] >= 1
        assert metrics["definition_set_version"] == "1.0.0"
        assert team_brief["team_id"] == "a2a"
        assert team_brief["github"]["total_records"] == 0
        assert "github_delivery" not in team_brief

    asyncio.run(exercise())


def test_stdio_protocol_lists_read_only_tools(tmp_path: Path) -> None:
    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "engineering_intelligence.mcp_server"],
            env={
                **os.environ,
                "ENGINTEL_DATA_DIR": str(tmp_path),
            },
        )
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert [tool.name for tool in tools.tools] == [
                "get_metrics",
                "list_attention",
                "get_flag",
                "get_dashboard",
                "get_people",
                "get_individual",
                "get_team_brief",
                "get_team",
                "get_feature",
            ]

    asyncio.run(exercise())
