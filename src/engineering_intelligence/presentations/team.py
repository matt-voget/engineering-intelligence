"""Versioned Team-detail presentation schema."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from engineering_intelligence.presentations.dashboard import (
    HealthCoverage,
    HealthFlag,
    HealthState,
    SourceFreshness,
    WorkItem,
)
from engineering_intelligence.presentations.feature import GitHubDeliveryRecord, JiraLinkEvidence


class WorkflowColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    count: int
    items: list[WorkItem] = Field(default_factory=list)
    empty: bool
    attention_signal: bool
    attention_explanation: str | None = None


class TeamRosterMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: str
    display_name: str
    preferred_name: str | None
    role: str | None
    starts_on: date
    ends_on: date | None
    is_primary: bool | None
    jira_account_id: str | None
    github_login: str | None
    identity_mapping_state: str


class TeamAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    message: str


class TeamDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.3"
    snapshot_id: str
    snapshot_name: str | None
    snapshot_created_at: datetime
    organization_config_hash: str | None = None
    source_config_hash: str | None = None
    source_freshness: list[SourceFreshness]
    team_id: str
    team_name: str
    health: HealthState
    health_coverage: HealthCoverage
    flags: list[HealthFlag]
    workflow: list[WorkflowColumn]
    most_recently_completed: WorkItem | None
    roster: list[TeamRosterMember]
    github_delivery: list[GitHubDeliveryRecord]
    github_availability: TeamAvailability
    blocked_work: list[JiraLinkEvidence]
    metrics_availability: TeamAvailability
    data_quality_notes: list[str]
