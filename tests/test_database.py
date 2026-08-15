import sqlite3
from pathlib import Path

from sqlalchemy import text

from engineering_intelligence.persistence.database import create_sqlite_engine, upgrade_database


def test_initial_migration_and_sqlite_safety_settings(tmp_path: Path) -> None:
    database_path = tmp_path / "engintel.db"
    upgrade_database(database_path)
    engine = create_sqlite_engine(database_path)

    with engine.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()

    assert {
        "raw_payloads",
        "jira_issues",
        "jira_issue_versions",
        "jira_relationships",
        "snapshots",
    } <= tables
    assert foreign_keys == 1
    assert journal_mode == "wal"

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
