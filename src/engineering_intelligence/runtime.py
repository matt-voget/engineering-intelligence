"""Runtime path resolution and credential loading."""

import os
from dataclasses import dataclass
from pathlib import Path

from engineering_intelligence.config import GitHubConfig, JiraConfig


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    database: Path
    raw_archive: Path


def default_data_dir() -> Path:
    configured = os.environ.get("ENGINTEL_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local/share"
    return base / "engineering-intelligence"


def runtime_paths(data_dir: Path | None = None) -> RuntimePaths:
    root = (data_dir or default_data_dir()).expanduser().resolve()
    return RuntimePaths(
        root=root,
        database=root / "engineering-intelligence.db",
        raw_archive=root / "raw",
    )


def jira_credentials(config: JiraConfig) -> tuple[str, str]:
    email = os.environ.get(config.email_env) or config.email
    token = os.environ.get(config.token_env)
    if not email:
        raise ValueError(
            f"Jira email is missing; set {config.email_env} or configure jira.email"
        )
    if not token:
        raise ValueError(f"Jira token is missing; set {config.token_env}")
    return email, token


def github_token(config: GitHubConfig) -> str:
    token = os.environ.get(config.token_env)
    if not token:
        raise ValueError(f"GitHub token is missing; set {config.token_env}")
    return token
