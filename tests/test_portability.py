import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from engineering_intelligence.config import TeamsConfig
from engineering_intelligence.organization import OrganizationService
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
    upgrade_database,
)
from engineering_intelligence.persistence.models import (
    SignalDefinition,
    SignalEvaluation,
    Snapshot,
    Team,
    TeamMembership,
)
from engineering_intelligence.portability import PortabilityService
from engineering_intelligence.runtime import runtime_paths


def test_export_import_round_trip_and_duplicate_idempotency(tmp_path: Path) -> None:
    source = runtime_paths(tmp_path / "source")
    upgrade_database(source.database)
    source_sessions = session_factory(create_sqlite_engine(source.database))
    OrganizationService(source_sessions).apply(
        TeamsConfig.model_validate(
            {
                "schema_version": "1",
                "teams": [
                    {
                        "id": "a2a",
                        "name": "A2A",
                        "aliases": ["Agent to Agent"],
                        "members": [
                            {
                                "id": "alex",
                                "name": "Alex Kim",
                                "starts_on": "2026-01-01",
                            }
                        ],
                    }
                ],
            }
        )
    )
    evaluated_at = datetime(2026, 7, 28, 19, 0, tzinfo=UTC)
    with source_sessions.begin() as session:
        session.add(
            Snapshot(
                id="signal-snapshot",
                name="signal-snapshot",
                created_at=evaluated_at,
                schema_version="1.0",
                description=None,
            )
        )
        session.add(
            SignalDefinition(
                id="signal-definition",
                definition_key="team-no-work-in-progress",
                version="1.0.0",
                category="flow_and_delivery",
                scope_type="team",
                area="work_in_flight",
                title="No work in progress",
                description="Fixture definition",
                comparison_basis="absolute_rule",
                parameters={"operator": "equals", "value": 0},
                severity_policy={"when_triggered": "watch"},
                confidence_policy={"level": "high"},
                definition_hash="a" * 64,
                effective_from=evaluated_at,
                created_at=evaluated_at,
            )
        )
        session.flush()
        session.add(
            SignalEvaluation(
                id="signal-evaluation",
                signal_definition_id="signal-definition",
                snapshot_id="signal-snapshot",
                scope_type="team",
                scope_id="a2a",
                subject_id="",
                dimension="ibr_in_progress_count",
                evaluated_at=evaluated_at,
                condition_met=False,
                severity=None,
                confidence="high",
                current_value={"count": 1},
                baseline=None,
                sample_size=1,
                flag_fingerprint=None,
                details={"comparison_basis": "absolute_rule"},
            )
        )
    raw_file = source.raw_archive / "jira/aa/payload.json.gz"
    raw_file.parent.mkdir(parents=True)
    raw_file.write_bytes(b"raw-fixture")
    bundle = tmp_path / "export.zip"
    config_root = Path(__file__).resolve().parents[1] / "config"
    service = PortabilityService()
    manifest = service.export_bundle(
        source,
        bundle,
        source_config=config_root / "sources.example.yaml",
        teams_config=config_root / "teams.example.yaml",
        include_raw=True,
        created_at=datetime(2026, 7, 28, 20, 0, tzinfo=UTC),
    )

    target = runtime_paths(tmp_path / "target")
    upgrade_database(target.database)
    plan = service.plan_import(target.database, bundle)
    applied = service.import_bundle(target, bundle)
    duplicate = service.plan_import(target.database, bundle)

    assert manifest.raw_payload_count == 1
    assert plan.valid and plan.total_inserts > 0
    assert applied.total_inserts == plan.total_inserts
    assert duplicate.total_inserts == 0
    assert duplicate.total_conflicts == 0
    assert target.raw_archive.joinpath("jira/aa/payload.json.gz").read_bytes() == b"raw-fixture"
    target_sessions = session_factory(create_sqlite_engine(target.database))
    with target_sessions() as session:
        assert session.scalar(select(func.count()).select_from(Team)) == 1
        assert session.scalar(select(func.count()).select_from(TeamMembership)) == 1
        assert session.scalar(select(func.count()).select_from(SignalDefinition)) == 1
        assert session.scalar(select(func.count()).select_from(SignalEvaluation)) == 1
    report = target.root / f"import-reports/{manifest.export_id}/import-report.md"
    assert report.exists()
    assert "Import report" in report.read_text()


def test_export_checksum_tampering_is_rejected(tmp_path: Path) -> None:
    source = runtime_paths(tmp_path / "source")
    upgrade_database(source.database)
    config_root = Path(__file__).resolve().parents[1] / "config"
    bundle = tmp_path / "export.zip"
    service = PortabilityService()
    service.export_bundle(
        source,
        bundle,
        source_config=config_root / "sources.example.yaml",
        teams_config=config_root / "teams.example.yaml",
    )
    with zipfile.ZipFile(bundle) as original:
        files = {name: original.read(name) for name in original.namelist()}
    files["data/teams.ndjson"] = b'{"id":"tampered"}\n'
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as tampered:
        for name, payload in files.items():
            tampered.writestr(name, payload)

    with pytest.raises(ValueError, match="checksum"):
        service.verify_bundle(bundle)
