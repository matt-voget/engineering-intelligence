"""Versioned human- and machine-readable export/import bundles."""

import csv
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from engineering_intelligence import __version__
from engineering_intelligence.runtime import RuntimePaths

TABLES = [
    "ingestion_runs",
    "raw_payloads",
    "boards",
    "board_columns",
    "jira_issues",
    "jira_issue_versions",
    "jira_status_transitions",
    "jira_relationships",
    "board_membership_observations",
    "jira_scope_observations",
    "sync_cursors",
    "teams",
    "team_aliases",
    "people",
    "team_memberships",
    "github_repositories",
    "github_pull_requests",
    "github_pull_request_versions",
    "github_commits",
    "github_pull_request_commits",
    "github_reviews",
    "jira_github_relationships",
    "snapshots",
    "snapshot_source_states",
    "signal_definitions",
    "signal_evaluations",
    "logical_flags",
    "flag_occurrences",
    "flag_events",
    "flag_evidence",
    "flag_user_states",
]


class ExportManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: str = "1"
    export_id: str
    created_at: datetime
    application_version: str
    database_schema_revision: str
    included_tables: dict[str, int]
    included_raw_payloads: bool
    raw_payload_count: int
    encrypted: bool = False
    files: dict[str, str]


class ImportPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    export_id: str
    source_application_version: str
    source_schema_revision: str
    inserts: dict[str, int]
    duplicates: dict[str, int]
    conflicts: dict[str, int]
    raw_files_to_copy: int
    valid: bool

    @property
    def total_inserts(self) -> int:
        return sum(self.inserts.values())

    @property
    def total_conflicts(self) -> int:
        return sum(self.conflicts.values())


class PortabilityService:
    def export_bundle(
        self,
        paths: RuntimePaths,
        destination: Path,
        *,
        source_config: Path,
        teams_config: Path,
        include_raw: bool = False,
        overwrite: bool = False,
        created_at: datetime | None = None,
    ) -> ExportManifest:
        destination = destination.expanduser().resolve()
        if destination.exists() and not overwrite:
            raise ValueError(f"Export destination already exists: {destination}")
        if not paths.database.exists():
            raise ValueError(f"Database does not exist: {paths.database}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        created_at = created_at or datetime.now(UTC)
        export_id = str(uuid4())
        files: dict[str, bytes] = {
            "README.md": _bundle_readme(export_id).encode(),
            "config/sources.yaml": source_config.read_bytes(),
            "config/teams.yaml": teams_config.read_bytes(),
        }
        counts: dict[str, int] = {}
        with sqlite3.connect(paths.database) as connection:
            connection.row_factory = sqlite3.Row
            schema_revision = _schema_revision(connection)
            for table in TABLES:
                rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
                counts[table] = len(rows)
                files[f"data/{table}.ndjson"] = _ndjson(rows)
            files.update(_human_csvs(connection))

        raw_count = 0
        if include_raw and paths.raw_archive.exists():
            for path in sorted(item for item in paths.raw_archive.rglob("*") if item.is_file()):
                relative = PurePosixPath("raw") / PurePosixPath(
                    path.relative_to(paths.raw_archive).as_posix()
                )
                files[str(relative)] = path.read_bytes()
                raw_count += 1

        files["reports/export-summary.md"] = _export_summary(
            export_id,
            created_at,
            counts,
            include_raw,
            raw_count,
        ).encode()
        checksums = {name: _sha256(payload) for name, payload in files.items()}
        manifest = ExportManifest(
            export_id=export_id,
            created_at=created_at,
            application_version=__version__,
            database_schema_revision=schema_revision,
            included_tables=counts,
            included_raw_payloads=include_raw,
            raw_payload_count=raw_count,
            files=checksums,
        )
        files["manifest.json"] = manifest.model_dump_json(indent=2).encode()
        all_checksums = {name: _sha256(payload) for name, payload in files.items()}
        files["checksums.sha256"] = "".join(
            f"{digest}  {name}\n" for name, digest in sorted(all_checksums.items())
        ).encode()
        self._write_zip(destination, files)
        self.verify_bundle(destination)
        return manifest

    def verify_bundle(self, bundle: Path) -> ExportManifest:
        with zipfile.ZipFile(bundle) as archive:
            names = set(archive.namelist())
            if {"manifest.json", "checksums.sha256"} - names:
                raise ValueError("Export bundle is missing its manifest or checksums")
            self._validate_member_names(names)
            manifest = ExportManifest.model_validate_json(archive.read("manifest.json"))
            expected = _parse_checksums(archive.read("checksums.sha256").decode())
            for name, digest in expected.items():
                if name not in names or _sha256(archive.read(name)) != digest:
                    raise ValueError(f"Export checksum failed: {name}")
            for name, digest in manifest.files.items():
                if name not in names or _sha256(archive.read(name)) != digest:
                    raise ValueError(f"Manifest checksum failed: {name}")
            return manifest

    def plan_import(self, database: Path, bundle: Path) -> ImportPlan:
        manifest = self.verify_bundle(bundle)
        with zipfile.ZipFile(bundle) as archive, sqlite3.connect(database) as connection:
            inserts: dict[str, int] = {}
            duplicates: dict[str, int] = {}
            conflicts: dict[str, int] = {}
            for table in TABLES:
                rows = _read_ndjson(archive.read(f"data/{table}.ndjson"))
                primary_keys = _primary_keys(connection, table)
                insert_count = duplicate_count = conflict_count = 0
                for row in rows:
                    existing = _existing_row(connection, table, primary_keys, row)
                    if existing is None:
                        insert_count += 1
                    elif dict(existing) == row:
                        duplicate_count += 1
                    else:
                        conflict_count += 1
                inserts[table] = insert_count
                duplicates[table] = duplicate_count
                conflicts[table] = conflict_count
            raw_to_copy = sum(name.startswith("raw/") for name in archive.namelist())
        return ImportPlan(
            export_id=manifest.export_id,
            source_application_version=manifest.application_version,
            source_schema_revision=manifest.database_schema_revision,
            inserts=inserts,
            duplicates=duplicates,
            conflicts=conflicts,
            raw_files_to_copy=raw_to_copy,
            valid=sum(conflicts.values()) == 0,
        )

    def import_bundle(
        self,
        paths: RuntimePaths,
        bundle: Path,
        *,
        imported_config_dir: Path | None = None,
    ) -> ImportPlan:
        plan = self.plan_import(paths.database, bundle)
        if not plan.valid:
            raise ValueError(f"Import has {plan.total_conflicts} conflicting records")
        with zipfile.ZipFile(bundle) as archive, sqlite3.connect(paths.database) as connection:
            self._validate_raw(archive, paths.raw_archive)
            connection.execute("PRAGMA foreign_keys=ON")
            try:
                for table in TABLES:
                    rows = _read_ndjson(archive.read(f"data/{table}.ndjson"))
                    primary_keys = _primary_keys(connection, table)
                    for row in rows:
                        if _existing_row(connection, table, primary_keys, row) is None:
                            _insert_row(connection, table, row)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise ValueError("Imported database failed SQLite integrity validation")
            self._copy_raw(archive, paths.raw_archive)
            config_destination = imported_config_dir or (
                paths.root / "imported-config" / plan.export_id
            )
            self._copy_config(archive, config_destination)
            self._write_import_reports(paths.root, plan)
        return plan

    @staticmethod
    def database_record_count(database: Path) -> int:
        if not database.exists():
            return 0
        with sqlite3.connect(database) as connection:
            return sum(
                connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                for table in TABLES
                if _table_exists(connection, table)
            )

    @staticmethod
    def _write_zip(destination: Path, files: dict[str, bytes]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        os.close(descriptor)
        try:
            with zipfile.ZipFile(temporary_name, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, payload in sorted(files.items()):
                    archive.writestr(name, payload)
            os.replace(temporary_name, destination)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    @staticmethod
    def _copy_raw(archive: zipfile.ZipFile, destination: Path) -> None:
        for name in archive.namelist():
            if not name.startswith("raw/"):
                continue
            relative = Path(PurePosixPath(name).relative_to("raw"))
            target = destination / relative
            payload = archive.read(name)
            if target.exists() and _sha256(target.read_bytes()) != _sha256(payload):
                raise ValueError(f"Raw payload conflict: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(payload)

    @staticmethod
    def _validate_raw(archive: zipfile.ZipFile, destination: Path) -> None:
        for name in archive.namelist():
            if not name.startswith("raw/"):
                continue
            relative = Path(PurePosixPath(name).relative_to("raw"))
            target = destination / relative
            if target.exists() and _sha256(target.read_bytes()) != _sha256(archive.read(name)):
                raise ValueError(f"Raw payload conflict: {relative}")

    @staticmethod
    def _copy_config(archive: zipfile.ZipFile, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        for name in ("config/sources.yaml", "config/teams.yaml"):
            (destination / Path(name).name).write_bytes(archive.read(name))

    @staticmethod
    def _write_import_reports(root: Path, plan: ImportPlan) -> None:
        destination = root / "import-reports" / plan.export_id
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "import-report.json").write_text(plan.model_dump_json(indent=2) + "\n")
        (destination / "import-report.md").write_text(_import_summary(plan))

    @staticmethod
    def _validate_member_names(names: set[str]) -> None:
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Unsafe export member path: {name}")


def _schema_revision(connection: sqlite3.Connection) -> str:
    row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    if row is None:
        raise ValueError("Database has no Alembic schema revision")
    return str(row[0])


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _ndjson(rows: list[sqlite3.Row]) -> bytes:
    return "".join(
        json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n" for row in rows
    ).encode()


def _read_ndjson(payload: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in payload.decode().splitlines() if line]


def _primary_keys(connection: sqlite3.Connection, table: str) -> list[str]:
    keys = [
        (row[5], row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        if row[5]
    ]
    if not keys:
        raise ValueError(f"Import table has no primary key: {table}")
    return [name for _position, name in sorted(keys)]


def _existing_row(
    connection: sqlite3.Connection,
    table: str,
    primary_keys: list[str],
    row: dict[str, Any],
) -> sqlite3.Row | None:
    connection.row_factory = sqlite3.Row
    predicate = " AND ".join(f'"{key}" = ?' for key in primary_keys)
    return connection.execute(
        f'SELECT * FROM "{table}" WHERE {predicate}',
        [row[key] for key in primary_keys],
    ).fetchone()


def _insert_row(connection: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    columns = list(row)
    names = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join("?" for _column in columns)
    connection.execute(
        f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
        [row[column] for column in columns],
    )


def _human_csvs(connection: sqlite3.Connection) -> dict[str, bytes]:
    queries = {
        "tables/teams.csv": "SELECT id,name,active FROM teams ORDER BY name",
        "tables/people.csv": (
            "SELECT id,display_name,preferred_name,role,jira_account_id,github_login,active "
            "FROM people ORDER BY display_name"
        ),
        "tables/jira-issues.csv": (
            "SELECT i.issue_key,v.summary,v.issue_type_name,v.status_name,v.team_name,i.web_url "
            "FROM jira_issues i JOIN jira_issue_versions v "
            "ON v.issue_id=i.id AND v.version_hash=i.current_version_hash "
            "ORDER BY i.issue_key"
        ),
    }
    result = {}
    for name, query in queries.items():
        rows = connection.execute(query).fetchall()
        output = io.StringIO()
        writer = csv.writer(output)
        if rows:
            writer.writerow(rows[0].keys())
            writer.writerows(rows)
        result[name] = output.getvalue().encode()
    return result


def _parse_checksums(payload: str) -> dict[str, str]:
    result = {}
    for line in payload.splitlines():
        digest, name = line.split("  ", 1)
        result[name] = digest
    return result


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _bundle_readme(export_id: str) -> str:
    return (
        "# Engineering Intelligence export\n\n"
        f"Export ID: `{export_id}`\n\n"
        "This bundle contains normalized historical data, human-readable extracts, "
        "and non-secret configuration. Verify `checksums.sha256` before use. "
        "Credentials and machine-specific absolute paths are excluded.\n"
    )


def _export_summary(
    export_id: str,
    created_at: datetime,
    counts: dict[str, int],
    include_raw: bool,
    raw_count: int,
) -> str:
    lines = [
        "# Export summary",
        "",
        f"- Export ID: `{export_id}`",
        f"- Created: {created_at.isoformat()}",
        f"- Raw payloads included: {str(include_raw).lower()} ({raw_count})",
        "",
        "## Normalized tables",
        "",
    ]
    lines.extend(f"- `{table}`: {count}" for table, count in counts.items())
    return "\n".join(lines) + "\n"


def _import_summary(plan: ImportPlan) -> str:
    lines = [
        "# Import report",
        "",
        f"- Export ID: `{plan.export_id}`",
        f"- Valid: {str(plan.valid).lower()}",
        f"- Inserts: {plan.total_inserts}",
        f"- Conflicts: {plan.total_conflicts}",
        f"- Raw files copied or already present: {plan.raw_files_to_copy}",
        "",
        "## Tables",
        "",
    ]
    lines.extend(
        f"- `{table}`: {plan.inserts[table]} inserts, "
        f"{plan.duplicates[table]} duplicates, {plan.conflicts[table]} conflicts"
        for table in TABLES
    )
    return "\n".join(lines) + "\n"
