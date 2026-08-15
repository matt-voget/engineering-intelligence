"""Compressed, content-addressed raw payload storage."""

import gzip
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArchivedPayload:
    content_hash: str
    path: Path
    created: bool


class RawPayloadArchive:
    """Store canonical JSON payloads once, addressed by SHA-256."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, source: str, payload: dict[str, Any]) -> ArchivedPayload:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        content_hash = hashlib.sha256(canonical).hexdigest()
        relative_path = Path(source) / content_hash[:2] / f"{content_hash}.json.gz"
        destination = self.root / relative_path
        if destination.exists():
            return ArchivedPayload(content_hash, relative_path, False)

        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{content_hash}.",
            suffix=".tmp",
        )
        try:
            with (
                os.fdopen(descriptor, "wb") as raw_stream,
                gzip.GzipFile(fileobj=raw_stream, mode="wb", mtime=0) as stream,
            ):
                stream.write(canonical)
            os.replace(temporary_name, destination)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return ArchivedPayload(content_hash, relative_path, True)

    def get(self, relative_path: Path) -> dict[str, Any]:
        with gzip.open(self.root / relative_path, "rt", encoding="utf-8") as stream:
            return json.load(stream)
