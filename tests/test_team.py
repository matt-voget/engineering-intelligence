import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from engineering_intelligence.config import SourceConfig, TeamsConfig
from engineering_intelligence.ingestion.archive import RawPayloadArchive
from engineering_intelligence.ingestion.jira.service import JiraIngestionService
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
    upgrade_database,
)
from engineering_intelligence.persistence.models import Snapshot
from engineering_intelligence.presentations.team_brief import build_team_brief
from engineering_intelligence.queries.team import WORKFLOW_COLUMNS, TeamQuery
from engineering_intelligence.renderers.team_brief_markdown import (
    render_team_brief_markdown,
)
from engineering_intelligence.renderers.team_markdown import render_team_markdown
from engineering_intelligence.snapshots import SnapshotService

IBR_SOURCES = SourceConfig.model_validate({
    "jira": {"base_url": "https://gravitee.atlassian.net", "boards": [
        {"id": 2168, "name": "IBR", "role": "ibr"}
    ]}
})


FIXTURES = Path(__file__).parent / "fixtures/jira"


class FixtureClient:
    def get_board_configuration(self, _board_id: int) -> dict[str, Any]:
        return json.loads((FIXTURES / "board_2168.json").read_text())

    def iter_board_issues(
        self,
        _board_id: int,
        *,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return [json.loads((FIXTURES / "issue_idn_1.json").read_text())]

    def iter_child_issues(
        self,
        _parent_keys: list[str],
        *,
        fields: list[str],
    ) -> list[dict[str, Any]]:
        return []

    def iter_issue_changelogs(self, _issue_ids_or_keys, *, field_ids=None):
        return []


def test_team_detail_preserves_complete_workflow_and_roster(tmp_path: Path) -> None:
    database = tmp_path / "engineering-intelligence.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    observed_at = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    JiraIngestionService(
        sessions,
        RawPayloadArchive(tmp_path / "raw"),
        FixtureClient(),  # type: ignore[arg-type]
        base_url="https://gravitee.atlassian.net",
        team_field_id="customfield_12345",
        target_date_field_id="customfield_54321",
    ).ingest_board(2168, observed_at=observed_at)
    SnapshotService(sessions).create(
        [2168],
        name="team-fixture",
        created_at=observed_at,
        source_config=IBR_SOURCES,
    )
    teams = TeamsConfig.model_validate(
        {
            "teams": [
                {
                    "id": "a2a",
                    "name": "A2A",
                    "aliases": [],
                    "members": [
                        {
                            "id": "alex",
                            "name": "Alex Kim",
                            "role": "Engineer",
                            "jira_account_id": "jira-alex",
                            "github_login": "alex",
                            "starts_on": "2026-01-01",
                        }
                    ],
                }
            ]
        }
    )

    team = TeamQuery(
        sessions,
        jira_base_url="https://gravitee.atlassian.net",
    ).get("team-fixture", "a2a", teams)

    assert team.team_name == "A2A"
    assert [column.name for column in team.workflow] == WORKFLOW_COLUMNS
    assert next(column for column in team.workflow if column.name == "In Progress").count == 1
    ready = next(column for column in team.workflow if column.name == "Ready for Build")
    assert ready.count == 0
    assert ready.attention_signal is True
    assert team.roster[0].identity_mapping_state == "complete"
    assert team.github_availability.available is False
    assert team.metrics_availability.available is False
    markdown = render_team_markdown(team)
    assert "### Ready for Build (0) ⚑" in markdown
    assert "[IDN-1](https://gravitee.atlassian.net/browse/IDN-1)" in markdown
    assert "Target Date: 2026-08-15" in markdown

    brief = build_team_brief(team)
    assert brief.schema_version == "1.1"
    assert [item.jira_key for item in brief.in_progress] == ["IDN-1"]
    assert brief.ready_for_build == []
    assert brief.roster.member_count == 1
    assert brief.roster.complete_identity_count == 1
    assert brief.github.total_records == 0
    brief_markdown = render_team_brief_markdown(brief)
    assert "## In progress" in brief_markdown
    assert "[IDN-1](https://gravitee.atlassian.net/browse/IDN-1)" in brief_markdown
    assert "Target Date: 2026-08-15" in brief_markdown
    assert "## Ready for build\n\n- No items" in brief_markdown


def test_team_detail_rejects_unknown_team(tmp_path: Path) -> None:
    database = tmp_path / "engineering-intelligence.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    teams = TeamsConfig.model_validate(
        {"teams": [{"id": "a2a", "name": "A2A", "members": []}]}
    )
    with sessions.begin() as session:
        session.add(
            Snapshot(
                id="team-snapshot",
                name="team-snapshot",
                created_at=datetime(2026, 7, 29, tzinfo=UTC),
                schema_version="1.1",
                description=None,
                organization_config=teams.model_dump(mode="json"),
                organization_config_hash="test",
            )
        )

    with pytest.raises(ValueError, match="Team not found"):
        TeamQuery(
            sessions,
            jira_base_url="https://gravitee.atlassian.net",
        ).get("team-snapshot", "unknown", teams)
