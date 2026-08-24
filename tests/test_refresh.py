import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engineering_intelligence.config import SourceConfig, TeamsConfig
from engineering_intelligence.individual_cache import load_cached_individual
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
)
from engineering_intelligence.persistence.models import Snapshot
from engineering_intelligence.refresh import RefreshService
from engineering_intelligence.refresh.service import (
    _accountable_jira_ids,
    _accountable_work_jql,
    _completed_progress_sources,
    load_run_state,
)
from engineering_intelligence.runtime import runtime_paths
from engineering_intelligence.snapshot_selection import latest_snapshot

FIXTURES = Path(__file__).parent / "fixtures/jira"


class FixtureClient:
    def get_board_configuration(self, board_id: int) -> dict[str, Any]:
        payload = json.loads((FIXTURES / "board_2168.json").read_text())
        payload["id"] = board_id
        return payload

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

    def iter_jql_issues(self, _jql: str, *, fields=None):
        return []


class FailingClient(FixtureClient):
    def get_board_configuration(self, _board_id: int) -> dict[str, Any]:
        raise RuntimeError("fixture source unavailable")


class InterruptedClient(FixtureClient):
    def get_board_configuration(self, _board_id: int) -> dict[str, Any]:
        raise KeyboardInterrupt


class InterruptSecondClient(FixtureClient):
    def __init__(self) -> None:
        self.calls = 0

    def get_board_configuration(self, _board_id: int) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 2:
            raise KeyboardInterrupt
        return super().get_board_configuration(_board_id)


def _source_config() -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "jira": {
                "base_url": "https://gravitee.atlassian.net",
                "email": "fixture@example.com",
                "token_env": "FIXTURE_JIRA_TOKEN",
                "team_field_id": "customfield_12345",
                "boards": [
                    {"id": 2168, "name": "IBR", "role": "portfolio"}
                ],  # legacy alias for ibr
            },
            "github": {
                "api_url": "https://api.github.com",
                "repositories": [],
            },
        }
    )


def _teams_config() -> TeamsConfig:
    return TeamsConfig.model_validate(
        {
            "teams": [
                {
                    "id": "a2a",
                    "name": "A2A",
                    "members": [
                        {
                            "id": "alex",
                            "name": "Alex Example",
                            "starts_on": "2026-01-01",
                        }
                    ],
                    "roster_source": {"state": "unverified"},
                }
            ]
        }
    )


def test_accountable_work_scope_uses_active_deduplicated_jira_identities() -> None:
    teams = TeamsConfig.model_validate(
        {
            "teams": [
                {
                    "id": "devex",
                    "name": "Developer Experience",
                    "members": [
                        {
                            "id": "tenshin",
                            "name": "Tenshin",
                            "jira_account_id": "account:tenshin",
                            "starts_on": "2026-01-01",
                        },
                        {
                            "id": "former",
                            "name": "Former",
                            "jira_account_id": "account:former",
                            "starts_on": "2025-01-01",
                            "ends_on": "2026-01-31",
                        },
                    ],
                },
                {
                    "id": "shared",
                    "name": "Shared",
                    "members": [
                        {
                            "id": "tenshin",
                            "name": "Tenshin",
                            "jira_account_id": "account:tenshin",
                            "starts_on": "2026-01-01",
                        }
                    ],
                },
            ]
        }
    )

    ids = _accountable_jira_ids(teams, datetime(2026, 7, 31, tzinfo=UTC))

    assert ids == ["account:tenshin"]
    assert _accountable_work_jql(ids) == (
        'assignee in ("account:tenshin") AND statusCategory != "Done"'
    )


def test_refresh_creates_pinned_snapshot_flags_receipt_and_backup(
    tmp_path: Path,
) -> None:
    paths = runtime_paths(tmp_path / "data")
    receipt = RefreshService().run(
        paths,
        _source_config(),
        _teams_config(),
        snapshot_name="refresh-fixture",
        backup_dir=tmp_path / "backups",
        backup_passphrase="fixture-passphrase",
        jira_client=FixtureClient(),
        started_at=datetime(2026, 7, 29, 20, 0, tzinfo=UTC),
    )

    assert receipt.status == "completed"
    assert receipt.snapshot_name == "refresh-fixture"
    assert receipt.organization_config_hash
    assert receipt.source_config_hash
    assert receipt.flags_recorded == 3
    assert receipt.individual_summaries_materialized == 1
    assert receipt.backup and receipt.backup["verified"] is True
    assert Path(receipt.backup["path"]).exists()
    cached = load_cached_individual(paths.root, receipt.snapshot_id or "", "Alex Example")
    assert cached is not None
    assert cached.person_id == "alex"
    latest = paths.root / "receipts" / "refresh" / "latest.json"
    assert latest.exists()
    assert json.loads(latest.read_text())["refresh_id"] == receipt.refresh_id
    progress = json.loads(
        (
            paths.root / "receipts" / "refresh" / "progress" / f"{receipt.refresh_id}.json"
        ).read_text()
    )
    assert progress["status"] == "completed"
    assert progress["completed_sources"] == 1
    assert progress["total_sources"] == 1
    assert [event["stage"] for event in progress["events"]] == [
        "initialization",
        "organization",
        "jira",
        "jira",
        "snapshot",
        "snapshot",
        "flags",
        "flags",
        "backup",
        "backup",
        "complete",
    ]
    completed_source = next(
        event for event in progress["events"] if event["status"] == "completed_source"
    )
    assert completed_source["source"] == "jira:board:2168"
    assert completed_source["records_seen"] == 1
    assert completed_source["records_new"] == 1
    assert completed_source["records_updated"] == 0
    assert completed_source["records_reused"] == 0
    assert "1 issues checked" in completed_source["message"]
    run_root = paths.root / "receipts" / "refresh" / "runs" / receipt.refresh_id
    state = json.loads((run_root / "state.json").read_text())
    assert state["status"] == "completed"
    assert state["tasks"][0]["status"] == "completed"
    events = [json.loads(line) for line in (run_root / "events.jsonl").read_text().splitlines()]
    assert events[-1]["status"] == "completed"

    sessions = session_factory(create_sqlite_engine(paths.database))
    with sessions() as session:
        snapshot = session.get(Snapshot, receipt.snapshot_id)
        assert snapshot is not None
        selected = latest_snapshot(
            sessions,
            _source_config(),
            _teams_config(),
            max_age_seconds=3600,
            now=snapshot.created_at.replace(tzinfo=UTC),
        )
    assert selected.snapshot is not None
    assert selected.snapshot.id == receipt.snapshot_id
    assert selected.compatible is True
    assert selected.fresh is True


def test_refresh_failure_is_saved_as_receipt(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path / "data")
    receipt = RefreshService().run(
        paths,
        _source_config(),
        _teams_config(),
        jira_client=FailingClient(),
    )

    assert receipt.status == "failed"
    assert "fixture source unavailable" in (receipt.error or "")
    assert receipt.organization_config_hash
    assert receipt.source_config_hash
    latest = paths.root / "receipts" / "refresh" / "latest.json"
    assert json.loads(latest.read_text())["status"] == "failed"
    progress = json.loads(
        (paths.root / "receipts" / "refresh" / "progress" / "latest.json").read_text()
    )
    assert progress["status"] == "failed"
    assert progress["events"][-1]["stage"] == "failed"
    assert "fixture source unavailable" in progress["events"][-1]["message"]


def test_completed_progress_sources_supports_interrupted_resume(tmp_path: Path) -> None:
    progress_dir = tmp_path / "receipts" / "refresh" / "progress"
    progress_dir.mkdir(parents=True)
    (progress_dir / "latest.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "status": "completed_source",
                        "source": "jira:board:2168",
                    },
                    {"status": "running", "source": "github:org/current"},
                    {
                        "status": "failed_source",
                        "source": "github:org/failed",
                    },
                    {
                        "status": "completed_source",
                        "source": "github:org/done",
                    },
                ]
            }
        )
    )

    assert _completed_progress_sources(tmp_path) == {
        "jira:board:2168",
        "github:org/done",
    }


def test_refresh_interruption_is_saved_as_cancelled(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path / "data")

    receipt = RefreshService().run(
        paths,
        _source_config(),
        _teams_config(),
        jira_client=InterruptedClient(),
    )

    assert receipt.status == "cancelled"
    state = json.loads(
        (
            paths.root
            / "receipts"
            / "refresh"
            / "runs"
            / receipt.refresh_id
            / "state.json"
        ).read_text()
    )
    assert state["status"] == "cancelled"
    assert "KeyboardInterrupt" in state["error"]


def test_run_specific_resume_reuses_completed_manifest_tasks(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path / "data")
    source_config = _source_config().model_copy(deep=True)
    source_config.jira.boards.append(
        source_config.jira.boards[0].model_copy(update={"id": 2169, "name": "Second"})
    )
    first = RefreshService().run(
        paths,
        source_config,
        _teams_config(),
        jira_client=InterruptSecondClient(),
    )
    assert first.status == "cancelled"

    resumed = RefreshService().run(
        paths,
        source_config,
        _teams_config(),
        jira_client=FixtureClient(),
        resume_refresh_id=first.refresh_id,
    )

    assert resumed.status == "completed", resumed.error
    assert resumed.refresh_id == first.refresh_id
    state = load_run_state(paths.root, first.refresh_id)
    assert state.status == "completed"
    assert [task.status for task in state.tasks] == ["completed", "completed"]
    assert [task.attempt for task in state.tasks] == [1, 2]


def test_status_reconciles_an_expired_running_lease(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path / "data")
    receipt = RefreshService().run(
        paths,
        _source_config(),
        _teams_config(),
        jira_client=FixtureClient(),
    )
    state_path = (
        paths.root
        / "receipts"
        / "refresh"
        / "runs"
        / receipt.refresh_id
        / "state.json"
    )
    payload = json.loads(state_path.read_text())
    payload["status"] = "running"
    payload["lease_expires_at"] = "2026-01-01T00:00:00Z"
    state_path.write_text(json.dumps(payload))

    state = load_run_state(
        paths.root,
        receipt.refresh_id,
        observed_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert state.status == "stale"
    assert "lease expired" in (state.error or "")
