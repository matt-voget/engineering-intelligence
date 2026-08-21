"""Snapshot-backed Build Cycle Time presentation schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from engineering_intelligence.presentations.rag import RagAssessment


class StatusDuration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    days: float


class ChildCycleTime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jira_key: str
    title: str
    url: str
    issue_type: str | None
    status: str | None
    depth: int
    cycle_days: float | None
    period_started_at: datetime | None
    period_ended_at: datetime | None
    top_status: str | None
    status_durations: list[StatusDuration] = Field(default_factory=list)
    warning: str | None = None


class BuildCycleContribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jira_key: str
    title: str
    url: str
    issue_type: str
    classification: str
    cycle_days: float
    period_started_at: datetime
    period_ended_at: datetime
    top_status: str | None
    status_durations: list[StatusDuration] = Field(default_factory=list)
    children: list[ChildCycleTime] = Field(default_factory=list)
    rag: RagAssessment | None = None


class BuildCycleGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: str
    contributions: list[BuildCycleContribution] = Field(default_factory=list)


class BuildCycleTimeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    snapshot_id: str
    snapshot_name: str
    snapshot_created_at: datetime
    team_id: str
    team_name: str
    scope: str
    ibr_parent_issue_types: list[str]
    groups: list[BuildCycleGroup]
    data_quality_notes: list[str] = Field(default_factory=list)
