import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from engineering_intelligence.ingestion.archive import RawPayloadArchive
from engineering_intelligence.ingestion.jira.normalization import adf_to_text, parse_date
from engineering_intelligence.ingestion.jira.service import (
    JiraIngestionService,
    _changelog_datetime,
)
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
    upgrade_database,
)
from engineering_intelligence.persistence.models import (
    BoardColumn,
    BoardMembershipObservation,
    IngestionRun,
    JiraIssue,
    JiraIssueVersion,
    JiraRelationship,
    JiraScopeObservation,
    JiraStatusTransition,
    RawPayload,
)
from engineering_intelligence.queries.metrics import MetricsQuery
from engineering_intelligence.renderers.metrics_markdown import render_metrics_markdown
from engineering_intelligence.snapshots import SnapshotService

FIXTURES = Path(__file__).parent / "fixtures/jira"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def test_changelog_datetime_accepts_epoch_seconds_and_milliseconds() -> None:
    expected = datetime(2026, 7, 29, tzinfo=UTC)
    seconds = expected.timestamp()
    assert _changelog_datetime(seconds) == expected
    assert _changelog_datetime(seconds * 1000) == expected


def test_target_date_parser_preserves_only_unambiguous_calendar_dates() -> None:
    assert parse_date("2026-08-15") == date(2026, 8, 15)
    assert parse_date("21 July 2026") == date(2026, 7, 21)
    assert parse_date("2026-08") is None


def test_adf_description_is_normalized_to_plain_text() -> None:
    description = fixture("issue_idn_1.json")["fields"]["description"]
    assert adf_to_text(description) == (
        "Build an evidence-linked dashboard for engineering leaders.\n"
        "- Show team health and active delivery work."
    )
    assert adf_to_text(" Plain description ") == "Plain description"


class FixtureJiraClient:
    def __init__(self) -> None:
        self.board = fixture("board_2168.json")
        self.issues = [fixture("issue_idn_1.json")]

    def get_board_configuration(self, board_id: int) -> dict[str, Any]:
        assert board_id == 2168
        return self.board

    def iter_board_issues(
        self,
        board_id: int,
        *,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        assert board_id == 2168
        assert fields and "customfield_10019" in fields
        assert "description" in fields
        return self.issues

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

    def iter_issue_changelogs(
        self,
        issue_ids_or_keys: list[str],
        *,
        field_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        assert field_ids == ["status"]
        return [
            {
                "issueId": "100001",
                "changeHistories": [
                    {
                        "id": "history-1",
                        "created": "2026-07-20T09:00:00+00:00",
                        "author": {
                            "accountId": "person-1",
                            "displayName": "Jordan Example",
                        },
                        "items": [
                            {
                                "fieldId": "status",
                                "from": "1",
                                "fromString": "Ideation",
                                "to": "2",
                                "toString": "Product Review",
                            }
                        ],
                    },
                    {
                        "id": "history-2",
                        "created": "2026-07-22T09:00:00+00:00",
                        "author": {
                            "accountId": "person-2",
                            "displayName": "Alex Example",
                        },
                        "items": [
                            {
                                "fieldId": "status",
                                "from": "2",
                                "fromString": "Product Review",
                                "to": "3",
                                "toString": "Ready for Build",
                            }
                        ],
                    },
                    {
                        "id": "history-3",
                        "created": "2026-07-24T09:00:00+00:00",
                        "author": {
                            "accountId": "person-2",
                            "displayName": "Alex Example",
                        },
                        "items": [
                            {
                                "fieldId": "status",
                                "from": "3",
                                "fromString": "Ready for Build",
                                "to": "4",
                                "toString": "In Progress",
                            }
                        ],
                    },
                ],
            }
        ] if "100001" in issue_ids_or_keys else []

    def iter_jql_issues(
        self,
        jql: str,
        *,
        fields: list[str],
    ) -> list[dict[str, Any]]:
        assert jql == 'project = IDN AND type = "Epic"'
        assert "status" in fields
        return [fixture("issue_idn_1.json")]


def test_ingestion_is_historical_and_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "engintel.db"
    upgrade_database(database_path)
    sessions = session_factory(create_sqlite_engine(database_path))
    client = FixtureJiraClient()
    service = JiraIngestionService(
        sessions,
        RawPayloadArchive(tmp_path / "raw"),
        client,  # type: ignore[arg-type]
        base_url="https://gravitee.atlassian.net",
        team_field_id="customfield_12345",
        target_date_field_id="customfield_54321",
        gravitee_customers_field_id="customfield_10607",
    )
    first_at = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    service.ingest_board(2168, observed_at=first_at)
    service.ingest_board(2168, observed_at=first_at + timedelta(minutes=15))
    SnapshotService(sessions).create(
        [2168],
        name="metrics-fixture",
        created_at=first_at + timedelta(minutes=15),
    )

    with sessions() as session:
        issue = session.scalar(select(JiraIssue))
        version = session.scalar(select(JiraIssueVersion))
        assert issue is not None
        assert issue.issue_key == "IDN-1"
        assert issue.web_url.endswith("/browse/IDN-1")
        assert version is not None
        assert version.team_name == "A2A"
        assert version.description_text == (
            "Build an evidence-linked dashboard for engineering leaders.\n"
            "- Show team health and active delivery work."
        )
        assert version.target_date.isoformat() == "2026-08-15"
        assert version.target_date_value == "2026-08-15"
        assert version.gravitee_customers == ["Acme Corp", "Globex"]
        assert session.scalar(select(func.count()).select_from(JiraIssueVersion)) == 3
        assert session.scalar(select(func.count()).select_from(RawPayload)) == 5
        assert session.scalar(select(func.count()).select_from(IngestionRun)) == 2
        assert session.scalar(select(func.count()).select_from(BoardMembershipObservation)) == 2
        assert {
            observation.issue_id
            for observation in session.scalars(select(BoardMembershipObservation))
        } == {issue.id}
        assert session.scalar(select(func.count()).select_from(BoardColumn)) == 4
        assert session.scalar(select(func.count()).select_from(JiraRelationship)) == 5
        assert session.scalar(select(func.count()).select_from(JiraIssue)) == 3
        transitions = session.scalars(
            select(JiraStatusTransition).order_by(JiraStatusTransition.changed_at)
        ).all()
        assert [transition.to_status_name for transition in transitions] == [
            "Product Review",
            "Ready for Build",
            "In Progress",
        ]
        assert transitions[0].first_seen_at.replace(tzinfo=UTC) == first_at
        assert transitions[0].last_seen_at.replace(tzinfo=UTC) == (
            first_at + timedelta(minutes=15)
        )

    metrics = MetricsQuery(sessions).get(
        "metrics-fixture",
        date_from=first_at.date().replace(day=1),
        date_to=first_at.date(),
    )
    by_id = {metric.definition.metric_id: metric for metric in metrics.metrics}
    assert by_id["product_review_days"].current_value == 2.0
    assert by_id["ready_for_build_days"].current_value == 2.0
    assert by_id["ideate_cycle_days"].current_value == 22.96
    assert by_id["ideate_cycle_days"].sample_size == 1
    assert by_id["ideate_cycle_days"].candidate_issue_count == 3
    assert by_id["ideate_cycle_days"].excluded_missing_completion_count == 2
    assert by_id["ideate_cycle_days"].contributions[0].jira_key == "IDN-1"
    assert "IDN-1" in render_metrics_markdown(metrics)


def test_changed_issue_creates_a_new_version(tmp_path: Path) -> None:
    database_path = tmp_path / "engintel.db"
    upgrade_database(database_path)
    sessions = session_factory(create_sqlite_engine(database_path))
    client = FixtureJiraClient()
    service = JiraIngestionService(
        sessions,
        RawPayloadArchive(tmp_path / "raw"),
        client,  # type: ignore[arg-type]
        base_url="https://gravitee.atlassian.net",
    )
    service.ingest_board(2168)
    client.issues[0]["fields"]["summary"] = "Updated summary"
    service.ingest_board(2168)

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(JiraIssueVersion)) == 4
        runs = session.scalars(select(IngestionRun).order_by(IngestionRun.started_at)).all()
        assert [run.records_changed for run in runs] == [6, 1]


def test_named_jql_scope_is_snapshot_safe_and_metric_selectable(tmp_path: Path) -> None:
    database_path = tmp_path / "engintel.db"
    upgrade_database(database_path)
    sessions = session_factory(create_sqlite_engine(database_path))
    client = FixtureJiraClient()
    observed_at = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    service = JiraIngestionService(
        sessions,
        RawPayloadArchive(tmp_path / "raw"),
        client,  # type: ignore[arg-type]
        base_url="https://gravitee.atlassian.net",
    )
    run_id = service.ingest_query(
        "leadership-metrics",
        'project = IDN AND type = "Epic"',
        observed_at=observed_at,
    )
    SnapshotService(sessions).create(
        [],
        jira_queries=["leadership-metrics"],
        name="query-metrics",
        created_at=observed_at,
    )

    with sessions() as session:
        observations = session.scalars(select(JiraScopeObservation)).all()
        assert len(observations) == 1
        assert observations[0].ingestion_run_id == run_id
    metrics = MetricsQuery(sessions).get(
        "query-metrics",
        scope="query:leadership-metrics",
        date_from=datetime(2026, 7, 1, tzinfo=UTC).date(),
        date_to=datetime(2026, 7, 28, tzinfo=UTC).date(),
    )
    assert metrics.selected_source_scope == "query:leadership-metrics"
    assert metrics.metrics[0].candidate_issue_count == 1


def test_board_replay_reuses_version_previously_seen_before_query_scope(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "engintel.db"
    upgrade_database(database_path)
    sessions = session_factory(create_sqlite_engine(database_path))
    service = JiraIngestionService(
        sessions,
        RawPayloadArchive(tmp_path / "raw"),
        FixtureJiraClient(),  # type: ignore[arg-type]
        base_url="https://gravitee.atlassian.net",
    )
    observed_at = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)

    service.ingest_board(2168, observed_at=observed_at)
    service.ingest_query(
        "leadership-metrics",
        'project = IDN AND type = "Epic"',
        observed_at=observed_at + timedelta(minutes=1),
    )
    service.ingest_board(2168, observed_at=observed_at + timedelta(minutes=2))

    with sessions() as session:
        duplicate_versions = session.scalars(
            select(JiraIssueVersion).where(JiraIssueVersion.issue_id == "10001")
        ).all()
        assert len({version.version_hash for version in duplicate_versions}) == len(
            duplicate_versions
        )
