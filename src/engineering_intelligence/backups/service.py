"""Create and verify encrypted, portable Phase 1 backups."""

import hashlib
import io
import os
import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from pydantic import BaseModel, ConfigDict

from engineering_intelligence import __version__
from engineering_intelligence.runtime import RuntimePaths

MAGIC = b"ENGINTEL1"
SALT_SIZE = 16
NONCE_SIZE = 12


class BackupManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_version: str = "1"
    application_version: str
    created_at: datetime
    database_sha256: str
    raw_file_count: int
    source_data_dir: str


class BackupService:
    def create(
        self,
        paths: RuntimePaths,
        destination: Path,
        passphrase: str,
        *,
        created_at: datetime | None = None,
    ) -> BackupManifest:
        if not passphrase:
            raise ValueError("Backup passphrase must not be empty")
        destination = destination.expanduser().resolve()
        if destination.is_relative_to(paths.root):
            raise ValueError("Backup destination must be outside the primary data directory")
        if not paths.database.exists():
            raise ValueError(f"Database does not exist: {paths.database}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        created_at = created_at or datetime.now(UTC)

        with tempfile.TemporaryDirectory(prefix="engintel-backup-") as temporary:
            temporary_root = Path(temporary)
            database_copy = temporary_root / "engineering-intelligence.db"
            self._copy_database(paths.database, database_copy)
            raw_files = (
                sorted(path for path in paths.raw_archive.rglob("*") if path.is_file())
                if paths.raw_archive.exists()
                else []
            )
            manifest = BackupManifest(
                application_version=__version__,
                created_at=created_at,
                database_sha256=_sha256_file(database_copy),
                raw_file_count=len(raw_files),
                source_data_dir=str(paths.root),
            )
            archive = self._archive(database_copy, paths.raw_archive, raw_files, manifest)
            encrypted = _encrypt(archive, passphrase)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encrypted)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_name, destination)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        self.verify(destination, passphrase)
        return manifest

    def verify(self, backup_path: Path, passphrase: str) -> BackupManifest:
        archive = _decrypt(backup_path.read_bytes(), passphrase)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            members = {member.name: member for member in bundle.getmembers()}
            if "manifest.json" not in members or "engineering-intelligence.db" not in members:
                raise ValueError("Backup is missing its manifest or database")
            manifest_stream = bundle.extractfile(members["manifest.json"])
            database_stream = bundle.extractfile(members["engineering-intelligence.db"])
            if manifest_stream is None or database_stream is None:
                raise ValueError("Backup members could not be read")
            manifest = BackupManifest.model_validate_json(manifest_stream.read())
            database_hash = hashlib.sha256(database_stream.read()).hexdigest()
            if database_hash != manifest.database_sha256:
                raise ValueError("Backup database checksum does not match its manifest")
            raw_count = sum(name.startswith("raw/") and not item.isdir() for name, item in members.items())
            if raw_count != manifest.raw_file_count:
                raise ValueError("Backup raw archive count does not match its manifest")
            return manifest

    def restore(
        self,
        backup_path: Path,
        destination: Path,
        passphrase: str,
    ) -> BackupManifest:
        """Restore a verified backup into a new, previously absent directory."""
        destination = destination.expanduser().resolve()
        if destination.exists():
            raise ValueError(f"Restore destination already exists: {destination}")
        archive = _decrypt(backup_path.expanduser().resolve().read_bytes(), passphrase)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with (
            tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle,
            tempfile.TemporaryDirectory(
                dir=destination.parent,
                prefix=f".{destination.name}.restore-",
            ) as temporary,
        ):
            members = {member.name: member for member in bundle.getmembers()}
            _validate_members(members)
            manifest_stream = bundle.extractfile(members["manifest.json"])
            database_stream = bundle.extractfile(members["engineering-intelligence.db"])
            if manifest_stream is None or database_stream is None:
                raise ValueError("Backup members could not be read")
            manifest = BackupManifest.model_validate_json(manifest_stream.read())
            staging = Path(temporary) / "runtime"
            staging.mkdir()
            database = staging / "engineering-intelligence.db"
            database.write_bytes(database_stream.read())
            if _sha256_file(database) != manifest.database_sha256:
                raise ValueError("Restored database checksum does not match its manifest")
            with sqlite3.connect(database) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                raise ValueError(f"Restored SQLite integrity failed: {integrity}")
            raw_count = 0
            for name, member in sorted(members.items()):
                if not name.startswith("raw/") or member.isdir():
                    continue
                stream = bundle.extractfile(member)
                if stream is None:
                    raise ValueError(f"Backup member could not be read: {name}")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(stream.read())
                raw_count += 1
            if raw_count != manifest.raw_file_count:
                raise ValueError("Restored raw archive count does not match its manifest")
            os.replace(staging, destination)
        return manifest

    @staticmethod
    def _copy_database(source: Path, destination: Path) -> None:
        with (
            sqlite3.connect(source) as source_connection,
            sqlite3.connect(destination) as destination_connection,
        ):
            source_connection.backup(destination_connection)
            integrity = destination_connection.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                raise ValueError(f"SQLite backup integrity failed: {integrity}")

    @staticmethod
    def _archive(
        database: Path,
        raw_root: Path,
        raw_files: list[Path],
        manifest: BackupManifest,
    ) -> bytes:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as bundle:
            bundle.add(database, arcname="engineering-intelligence.db")
            manifest_bytes = manifest.model_dump_json(indent=2).encode()
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.size = len(manifest_bytes)
            manifest_info.mtime = int(manifest.created_at.timestamp())
            bundle.addfile(manifest_info, io.BytesIO(manifest_bytes))
            for path in raw_files:
                bundle.add(path, arcname=str(Path("raw") / path.relative_to(raw_root)))
        return output.getvalue()


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(passphrase.encode())


def _encrypt(plaintext: bytes, passphrase: str) -> bytes:
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(_derive_key(passphrase, salt)).encrypt(nonce, plaintext, MAGIC)
    return MAGIC + salt + nonce + ciphertext


def _decrypt(payload: bytes, passphrase: str) -> bytes:
    if not payload.startswith(MAGIC):
        raise ValueError("Not an Engineering Intelligence encrypted backup")
    salt_start = len(MAGIC)
    nonce_start = salt_start + SALT_SIZE
    ciphertext_start = nonce_start + NONCE_SIZE
    salt = payload[salt_start:nonce_start]
    nonce = payload[nonce_start:ciphertext_start]
    return AESGCM(_derive_key(passphrase, salt)).decrypt(
        nonce,
        payload[ciphertext_start:],
        MAGIC,
    )


def _validate_members(members: dict[str, tarfile.TarInfo]) -> None:
    required = {"manifest.json", "engineering-intelligence.db"}
    if not required.issubset(members):
        raise ValueError("Backup is missing its manifest or database")
    for name, member in members.items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Backup contains an unsafe path: {name}")
        if name not in required and not name.startswith("raw/"):
            raise ValueError(f"Backup contains an unexpected member: {name}")
        if member.issym() or member.islnk():
            raise ValueError(f"Backup contains an unsupported link: {name}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
