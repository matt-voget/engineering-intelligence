"""Versioned local configuration models."""

import os
from datetime import date
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

ModelT = TypeVar("ModelT", bound=BaseModel)

IBR_BOARD_ROLE = "ibr"
# Accepted for configurations and pinned snapshots written before the rename.
LEGACY_IBR_BOARD_ROLE = "portfolio"


class JiraBoardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    role: str = "team"
    url: HttpUrl | None = None

    @field_validator("role")
    @classmethod
    def _normalize_role(cls, value: str) -> str:
        if value == LEGACY_IBR_BOARD_ROLE:
            return IBR_BOARD_ROLE
        return value


class JiraQueryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    name: str
    purpose: str
    jql: str = Field(min_length=1)
    enabled: bool = True


class JiraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: HttpUrl
    host_env: str = "ATLASSIAN_HOST"
    email: str | None = None
    email_env: str = "ATLASSIAN_EMAIL"
    token_env: str = "ATLASSIAN_API_TOKEN"
    team_field_id: str | None = None
    target_date_field_id: str | None = None
    gravitee_customers_field_id: str | None = None
    hierarchy_max_depth: int = Field(default=10, ge=0, le=25)
    hierarchy_batch_size: int = Field(default=40, ge=1, le=100)
    collect_accountable_work: bool = False
    boards: list[JiraBoardConfig]
    queries: list[JiraQueryConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _apply_host_environment(self) -> "JiraConfig":
        configured_host = os.environ.get(self.host_env)
        if not configured_host:
            return self
        value = configured_host.strip().rstrip("/")
        if "://" not in value:
            value = f"https://{value}"
        self.base_url = HttpUrl(value)
        return self


class GitHubRepositoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")
    team_ids: list[str] = Field(default_factory=list)


class GitHubConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_url: HttpUrl = HttpUrl("https://api.github.com")
    token_env: str = "GITHUB_PAT"
    initial_lookback_days: int = Field(default=90, ge=1, le=3650)
    max_pull_requests_per_repository: int = Field(default=500, ge=1, le=5000)
    # Every run re-verifies at least this many days of pull-request history per
    # repository; the per-repository cap only limits records older than this window.
    min_refresh_window_days: int = Field(default=31, ge=1, le=365)
    repositories: list[GitHubRepositoryConfig] = Field(default_factory=list)


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    jira: JiraConfig
    github: GitHubConfig = Field(default_factory=GitHubConfig)


class TeamMemberConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    preferred_name: str | None = None
    role: str | None = None
    manager_person_id: str | None = None
    jira_account_id: str | None = None
    github_login: str | None = None
    starts_on: date
    ends_on: date | None = None
    is_primary: bool | None = None
    active: bool = True


class RosterSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str
    source: str | None = None
    source_team_id: str | None = None
    observed_on: date | None = None
    starts_on_basis: str | None = None
    notes: list[str] = Field(default_factory=list)


class TeamConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    members: list[TeamMemberConfig] = Field(default_factory=list)
    roster_source: RosterSourceConfig = Field(
        default_factory=lambda: RosterSourceConfig(state="unverified")
    )


class TeamsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    teams: list[TeamConfig]


def load_yaml_model(path: Path, model: type[ModelT]) -> ModelT:
    """Load and validate a YAML configuration file."""
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    return model.model_validate(data)
