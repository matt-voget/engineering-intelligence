from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from engineering_intelligence.backups import BackupService
from engineering_intelligence.persistence.database import upgrade_database
from engineering_intelligence.runtime import runtime_paths


def test_encrypted_backup_round_trip(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path / "data")
    upgrade_database(paths.database)
    raw = paths.raw_archive / "jira/ab"
    raw.mkdir(parents=True)
    (raw / "payload.json.gz").write_bytes(b"fixture")
    destination = tmp_path / "backups/phase1.engintel-backup"
    created_at = datetime(2026, 7, 28, 18, 0, tzinfo=UTC)

    manifest = BackupService().create(
        paths,
        destination,
        "correct horse battery staple",
        created_at=created_at,
    )
    verified = BackupService().verify(destination, "correct horse battery staple")
    restored = tmp_path / "restored"
    restored_manifest = BackupService().restore(
        destination,
        restored,
        "correct horse battery staple",
    )

    assert destination.read_bytes().startswith(b"ENGINTEL1")
    assert verified == manifest
    assert restored_manifest == manifest
    assert verified.raw_file_count == 1
    assert (restored / "engineering-intelligence.db").exists()
    assert (restored / "raw/jira/ab/payload.json.gz").read_bytes() == b"fixture"


def test_wrong_passphrase_fails(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path / "data")
    upgrade_database(paths.database)
    destination = tmp_path / "backup.engintel-backup"
    BackupService().create(paths, destination, "right passphrase")

    with pytest.raises(InvalidTag):
        BackupService().verify(destination, "wrong passphrase")


def test_backup_must_be_separate_from_primary_data(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path / "data")
    upgrade_database(paths.database)

    with pytest.raises(ValueError, match="outside"):
        BackupService().create(
            paths,
            paths.root / "backups/unsafe.engintel-backup",
            "passphrase",
        )


def test_restore_refuses_to_overwrite_existing_destination(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path / "data")
    upgrade_database(paths.database)
    backup = tmp_path / "backup.engintel-backup"
    BackupService().create(paths, backup, "passphrase")
    destination = tmp_path / "existing"
    destination.mkdir()

    with pytest.raises(ValueError, match="already exists"):
        BackupService().restore(backup, destination, "passphrase")
