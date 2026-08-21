from pathlib import Path

import pytest

from engineering_intelligence.config import JiraConfig
from engineering_intelligence.runtime import jira_credentials, runtime_paths


def jira_config() -> JiraConfig:
    return JiraConfig(
        base_url="https://example.atlassian.net",
        email="owner@example.com",
        boards=[],
    )


def test_runtime_paths_are_contained(tmp_path: Path) -> None:
    paths = runtime_paths(tmp_path / "chosen")

    assert paths.database.parent == paths.root
    assert paths.raw_archive.parent == paths.root


def test_credentials_use_named_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "test-token")

    assert jira_credentials(jira_config()) == ("owner@example.com", "test-token")


def test_token_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLASSIAN_API_TOKEN", raising=False)

    with pytest.raises(ValueError, match="ATLASSIAN_API_TOKEN"):
        jira_credentials(jira_config())
