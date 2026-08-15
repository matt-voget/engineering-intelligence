"""Deterministic Attention inbox presentation schemas."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AttentionCollection(StrEnum):
    active = "active"
    snoozed = "snoozed"
    understood = "understood"
    resolved = "resolved"
    all = "all"


class AttentionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    url: str
    jira_key: str | None = None
    title: str | None = None
    snapshot_id: str


class AttentionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    severity: str
    occurred_at: datetime
    snapshot_id: str
    explanation: str | None = None


class AttentionOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurrence_id: str
    opened_at: datetime
    resolved_at: datetime | None
    latest_snapshot_id: str | None
    events: list[AttentionEvent] = Field(default_factory=list)


class AttentionFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str
    occurrence_id: str
    collection: AttentionCollection
    unread: bool
    title: str
    team_id: str
    team_name: str
    health_area: str
    severity: str
    signal_definition_key: str | None = None
    signal_definition_version: str | None = None
    signal_evaluation_id: str | None = None
    explanation: str | None
    condition_started_at: datetime
    first_detected_at: datetime
    last_observed_at: datetime
    last_updated_at: datetime
    active_duration_seconds: int
    evidence: list[AttentionEvidence] = Field(default_factory=list)
    evidence_count: int
    affected_entities: list[str] = Field(default_factory=list)
    confidence: str
    investigation_questions: list[str] = Field(default_factory=list)
    viewed_at: datetime | None = None
    understood_at: datetime | None = None
    snoozed_until: datetime | None = None
    severity_history: list[AttentionEvent] = Field(default_factory=list)
    occurrences: list[AttentionOccurrence] = Field(default_factory=list)


class AttentionInbox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    generated_at: datetime
    collection: AttentionCollection
    unread_only: bool
    team: str | None
    counts: dict[str, int]
    flags: list[AttentionFlag]
    data_quality_notes: list[str] = Field(default_factory=list)
