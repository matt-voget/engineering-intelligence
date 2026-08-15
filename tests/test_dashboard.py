import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engineering_intelligence.config import SourceConfig, TeamsConfig, load_yaml_model
from engineering_intelligence.ingestion.archive import RawPayloadArchive
from engineering_intelligence.ingestion.jira.service import JiraIngestionService
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
    upgrade_database,
)
from engineering_intelligence.persistence.models import Snapshot
from engineering_intelligence.queries.dashboard import DashboardQuery
from engineering_intelligence.renderers.dashboard_markdown import render_dashboard_markdown
from engineering_intelligence.snapshots import SnapshotService

FIXTURES = Path(__file__).parent / "fixtures/jira"
ROOT = Path(__file__).resolve().parents[1]


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
        parent_keys: list[str],
        *,
        fields: list[str],
    ) -> list[dict[str, Any]]:
        return []

    def iter_issue_changelogs(self, _issue_ids_or_keys, *, field_ids=None):
        return []


def test_named_snapshot_dashboard_is_reproducible(tmp_path: Path) -> None:
    database = tmp_path / "engintel.db"
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
    teams = TeamsConfig.model_validate({"teams": [{
        "id": "a2a", "name": "A2A", "members": [],
        "roster_source": {"state": "configured"},
    }]})
    sources = SourceConfig.model_validate({
        "jira": {"base_url": "https://gravitee.atlassian.net", "boards": [
            {"id": 2168, "name": "Portfolio", "role": "portfolio"}
        ]}
    })
    snapshot = SnapshotService(sessions).create(
        [2168],
        name="fixture-snapshot",
        created_at=observed_at,
        teams_config=teams,
        source_config=sources,
    )
    current_fallback = teams.model_copy(deep=True)
    current_fallback.teams = [
        team for team in current_fallback.teams if team.id != "a2a"
    ]

    first = DashboardQuery(
        sessions,
        jira_base_url="https://gravitee.atlassian.net",
    ).get(snapshot.id, current_fallback)
    second = DashboardQuery(
        sessions,
        jira_base_url="https://gravitee.atlassian.net",
    ).get("fixture-snapshot", current_fallback)

    assert first == second
    assert snapshot.organization_config_hash is not None
    assert snapshot.source_config_hash is not None
    a2a = next(team for team in first.teams if team.team_id == "a2a")
    assert [item.jira_key for item in a2a.in_progress] == ["IDN-1"]
    assert a2a.health.value == "watch"
    assert a2a.flags[0].fingerprint == "a2a:near-term-pipeline:none"
    markdown = render_dashboard_markdown(first)
    assert "[IDN-1](https://gravitee.atlassian.net/browse/IDN-1)" in markdown
    assert "Target Date: 2026-08-15" in markdown
    assert "Snapshot ID:" in markdown


def test_snapshot_without_ibr_source_returns_unknown_dashboard(tmp_path: Path) -> None:
    database = tmp_path / "engintel.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    created_at = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)
    with sessions.begin() as session:
        session.add(
            Snapshot(
                id="missing-ibr",
                name="missing-ibr",
                created_at=created_at,
                schema_version="1.0",
                description=None,
            )
        )
    teams = load_yaml_model(ROOT / "config/teams.example.yaml", TeamsConfig)

    dashboard = DashboardQuery(
        sessions,
        jira_base_url="https://gravitee.atlassian.net",
    ).get("missing-ibr", teams)

    assert {team.health.value for team in dashboard.teams} == {"unknown"}
    assert all(not team.flags for team in dashboard.teams)
    assert {
        team.health_coverage.reasons[0]
        for team in dashboard.teams
    } == {"The snapshot has no successful IBR board ingestion."}
