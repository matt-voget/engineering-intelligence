"""Versioned local configuration models."""

from datetime import date
from pathlib import Path
from typing import Literal, TypeVar

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

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
    email: str | None = None
    email_env: str = "ATLAS_ATLASSIAN_EMAIL"
    token_env: str = "JIRA_API_TOKEN"
    team_field_id: str | None = None
    target_date_field_id: str | None = None
    gravitee_customers_field_id: str | None = None
    hierarchy_max_depth: int = Field(default=10, ge=0, le=25)
    hierarchy_batch_size: int = Field(default=40, ge=1, le=100)
    collect_accountable_work: bool = False
    boards: list[JiraBoardConfig]
    queries: list[JiraQueryConfig] = Field(default_factory=list)


class GitHubRepositoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(pattern=r"^[^/\s]+/[^/\s]+$")

    @model_validator(mode="before")
    @classmethod
    def _discard_legacy_team_ids(cls, value):
        """Load older files/snapshots while removing repository-team semantics."""
        if isinstance(value, dict) and "team_ids" in value:
            value = {key: item for key, item in value.items() if key != "team_ids"}
        return value


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


class RagRuleConfig(BaseModel):
    """Threshold rule for a report metric instance."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    label: str
    section: Literal["build_cycle_time", "github_pr_metrics"]
    metric: Literal["cycle_days", "pickup_hours", "review_hours"]
    amber_at: float = Field(ge=0)
    red_at: float = Field(ge=0)
    classification: Literal["ibr_linked", "non_ibr"] | None = None
    team_ids: list[str] = Field(default_factory=list)
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_thresholds_and_scope(self):
        if self.red_at <= self.amber_at:
            raise ValueError("red_at must be greater than amber_at")
        if self.section == "build_cycle_time" and self.metric != "cycle_days":
            raise ValueError("build_cycle_time rules require metric: cycle_days")
        if self.section == "github_pr_metrics" and self.metric == "cycle_days":
            raise ValueError("github_pr_metrics rules require an hour metric")
        if self.classification and self.section != "build_cycle_time":
            raise ValueError("classification is only valid for build_cycle_time")
        return self


class RagConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    green_symbol: str = "✓"
    amber_symbol: str = "▲"
    red_symbol: str = "!"
    rules: list[RagRuleConfig] = Field(default_factory=list)

    @field_validator("rules")
    @classmethod
    def _unique_rule_ids(cls, rules: list[RagRuleConfig]) -> list[RagRuleConfig]:
        ids = [rule.id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("RAG rule IDs must be unique")
        return rules


class TeamsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    rag: RagConfig = Field(default_factory=RagConfig)
    teams: list[TeamConfig]


def load_yaml_model(path: Path, model: type[ModelT]) -> ModelT:
    """Load and validate a YAML configuration file."""
    with path.open(encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    return model.model_validate(data)
