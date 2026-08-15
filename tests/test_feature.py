import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from engineering_intelligence.ingestion.archive import RawPayloadArchive
from engineering_intelligence.ingestion.jira.service import JiraIngestionService
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
    upgrade_database,
)
from engineering_intelligence.queries.feature import FeatureQuery
from engineering_intelligence.renderers.feature_markdown import render_feature_markdown
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


def test_feature_query_renders_complete_snapshot_hierarchy(tmp_path: Path) -> None:
    database = tmp_path / "engintel.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    observed_at = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    JiraIngestionService(
        sessions,
        RawPayloadArchive(tmp_path / "raw"),
        HierarchyClient(),  # type: ignore[arg-type]
        base_url="https://gravitee.atlassian.net",
        team_field_id="customfield_12345",
        gravitee_customers_field_id="customfield_10607",
    ).ingest_board(2168, observed_at=observed_at)
    snapshot = SnapshotService(sessions).create(
        [2168],
        name="feature-fixture",
        created_at=observed_at,
    )

    feature = FeatureQuery(sessions).get(snapshot.id, "IDN-1")

    assert feature.feature_key == "IDN-1"
    assert feature.original_issue_type == "Epic"
    assert feature.summary.total_issues == 3
    assert feature.summary.descendant_issues == 2
    assert feature.hierarchy.children[0].jira_key == "IDN-2"
    assert feature.hierarchy.children[0].children[0].jira_key == "IDN-3"
    assert feature.hierarchy.children[0].relationship_from_parent == "child"
    assert feature.hierarchy.children[0].children[0].relationship_from_parent == "subtask"
    assert feature.hierarchy.source_updated_at == datetime(
        2026, 7, 28, 15, 0, tzinfo=UTC
    )
    assert feature.hierarchy.gravitee_customers == ["Acme Corp", "Globex"]
    assert {person.display_name for person in feature.contributors} == {
        "Jordan Lee",
        "Alex Kim",
    }
    assert feature.summary.blocking_links == 1
    assert feature.jira_links[0].target_issue_key == "BX-10"
    assert feature.jira_links[0].target_url == "https://gravitee.atlassian.net/browse/BX-10"
    markdown = render_feature_markdown(feature)
    assert "# Feature IDN-1" in markdown
    assert "[IDN-3](https://gravitee.atlassian.net/browse/IDN-3)" in markdown
    assert "rolled up to Feature" in markdown


def test_non_ibr_issue_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "engintel.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    observed_at = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    JiraIngestionService(
        sessions,
        RawPayloadArchive(tmp_path / "raw"),
        HierarchyClient(),  # type: ignore[arg-type]
        base_url="https://gravitee.atlassian.net",
    ).ingest_board(2168, observed_at=observed_at)
    SnapshotService(sessions).create([2168], name="feature-fixture")

    with pytest.raises(ValueError, match="not an IBR item"):
        FeatureQuery(sessions).get("feature-fixture", "IDN-2")


def test_feature_uses_issue_version_valid_at_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "engintel.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    client = HierarchyClient()
    first_at = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    service = JiraIngestionService(
        sessions,
        RawPayloadArchive(tmp_path / "raw"),
        client,  # type: ignore[arg-type]
        base_url="https://gravitee.atlassian.net",
    )
    service.ingest_board(2168, observed_at=first_at)
    SnapshotService(sessions).create(
        [2168],
        name="before-change",
        created_at=first_at,
    )
    client.iter_board_issues = lambda *_args, **_kwargs: [
        {
            **fixture("issue_idn_1.json"),
            "fields": {
                **fixture("issue_idn_1.json")["fields"],
                "summary": "Changed after first snapshot",
                "updated": "2026-07-28T17:00:00.000+0000",
            },
        }
    ]
    second_at = datetime(2026, 7, 28, 17, 0, tzinfo=UTC)
    service.ingest_board(2168, observed_at=second_at)
    SnapshotService(sessions).create(
        [2168],
        name="after-change",
        created_at=second_at,
    )

    before = FeatureQuery(sessions).get("before-change", "IDN-1")
    after = FeatureQuery(sessions).get("after-change", "IDN-1")

    assert before.title == "Ship agent-ready team dashboard"
    assert after.title == "Changed after first snapshot"
    assert max(event.occurred_at for event in before.timeline) < second_at
