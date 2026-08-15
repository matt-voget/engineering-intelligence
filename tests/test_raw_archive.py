from pathlib import Path

from engineering_intelligence.ingestion.archive import RawPayloadArchive


def test_archive_is_canonical_and_deduplicated(tmp_path: Path) -> None:
    archive = RawPayloadArchive(tmp_path)

    first = archive.put("jira", {"key": "IDN-1", "fields": {"a": 1, "b": 2}})
    second = archive.put("jira", {"fields": {"b": 2, "a": 1}, "key": "IDN-1"})

    assert first.content_hash == second.content_hash
    assert first.created is True
    assert second.created is False
    assert archive.get(first.path)["key"] == "IDN-1"
