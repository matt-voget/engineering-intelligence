"""Dashboard presentation schema."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthState(StrEnum):
    healthy = "healthy"
    watch = "watch"
    concern = "concern"
    critical = "critical"
    unknown = "unknown"


class Severity(StrEnum):
    info = "info"
    watch = "watch"
    concern = "concern"
    critical = "critical"


class HealthCoverageState(StrEnum):
    reliable = "reliable"
    stale = "stale"
    incomplete = "incomplete"
    insufficient = "insufficient"


class HealthCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: HealthCoverageState
    reasons: list[str] = Field(default_factory=list)
    required_source: str
    required_scope: str
    observed_at: datetime | None
    maximum_age_seconds: int
    age_at_snapshot_seconds: int | None
    board_record_count: int
    team_record_count: int


class EvidenceLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    url: str
    jira_key: str | None = None
    title: str | None = None


class HealthFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    area: str
    severity: Severity
    title: str
    explanation: str
    raised_at: datetime
    occurrence_id: str | None = None
    unread: bool = True
    signal_definition_key: str | None = None
    signal_definition_version: str | None = None
    signal_evaluation_id: str | None = None
    confidence: str | None = None
    evidence: list[EvidenceLink] = Field(default_factory=list)


class WorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jira_id: str
    jira_key: str
    title: str
    status: str
    url: str
    completed_at: datetime | None = None
    assignee_account_id: str | None = None
    source_created_at: datetime | None = None
    source_updated_at: datetime | None = None
    target_date: date | None = None
    target_date_value: str | None = None


class SignalEvaluationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_key: str
    definition_version: str
    scope_type: str
    scope_id: str
    subject_id: str = ""
    dimension: str = ""
    condition_met: bool
    severity: str | None = None
    confidence: str
    current_value: dict[str, Any]
    baseline: dict[str, Any] | None = None
    sample_size: int
    flag_fingerprint: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class TeamDashboardRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: str
    team_name: str
    health: HealthState
    health_coverage: HealthCoverage
    flags: list[HealthFlag]
    most_recently_completed: WorkItem | None
    in_progress: list[WorkItem]
    ready_for_build: list[WorkItem]
    signal_evaluation_inputs: list[SignalEvaluationInput] = Field(
        default_factory=list,
        exclude=True,
    )


class SourceFreshness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    scope: str
    observed_at: datetime
    ingestion_run_id: str | None


class Dashboard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.4"
    snapshot_id: str
    snapshot_name: str | None
    snapshot_created_at: datetime
    organization_config_hash: str | None = None
    source_config_hash: str | None = None
    source_freshness: list[SourceFreshness]
    teams: list[TeamDashboardRow]
