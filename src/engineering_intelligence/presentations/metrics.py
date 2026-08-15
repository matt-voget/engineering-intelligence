"""Versioned deterministic engineering Metrics presentation schemas."""

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from engineering_intelligence.presentations.dashboard import SourceFreshness


class MetricPhase(StrEnum):
    ideate = "ideate"
    build = "build"


class MetricHealth(StrEnum):
    not_configured = "not_configured"
    healthy = "healthy"
    watch = "watch"
    concern = "concern"
    critical = "critical"
    unavailable = "unavailable"


class MetricDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_id: str
    definition_version: str
    label: str
    phase: MetricPhase
    unit: str
    aggregation: str
    formula: str
    inclusion_rule: str
    completion_event: str
    leadership_label: str | None
    leadership_report_url: str


class MetricContribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jira_id: str
    jira_key: str
    title: str
    url: str
    team_name: str | None
    issue_type: str | None
    value: float
    unit: str
    period_started_at: datetime | None
    period_ended_at: datetime
    warnings: list[str] = Field(default_factory=list)


class MetricTrendPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: date
    value: float | None
    sample_size: int


class MetricResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: MetricDefinition
    current_value: float | None
    sample_size: int
    baseline_90_day_value: float | None
    change_from_baseline: float | None
    threshold: float | None
    health: MetricHealth
    candidate_issue_count: int
    excluded_issue_type_count: int
    excluded_missing_completion_count: int
    excluded_outside_period_count: int
    excluded_incomplete_transition_count: int
    trend: list[MetricTrendPoint] = Field(default_factory=list)
    contributions: list[MetricContribution] = Field(default_factory=list)
    data_quality_warnings: list[str] = Field(default_factory=list)


class MetricsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.1"
    snapshot_id: str
    snapshot_name: str | None
    snapshot_created_at: datetime
    source_freshness: list[SourceFreshness]
    team: str | None
    date_from: date
    date_to: date
    definition_set_version: str
    local_source_scopes: list[str]
    selected_source_scope: str | None
    leadership_comparison_status: str
    metrics: list[MetricResult]
    data_quality_notes: list[str] = Field(default_factory=list)
