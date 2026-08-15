"""People-directory and Individual work-context presentation schemas."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from engineering_intelligence.presentations.dashboard import EvidenceLink, SourceFreshness
from engineering_intelligence.presentations.feature import GitHubDeliveryRecord, JiraLinkEvidence


class MembershipEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: str
    team_name: str
    starts_on: date
    ends_on: date | None
    is_primary: bool | None
    current_at_snapshot: bool


class BoardEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    board_id: int
    board_name: str
    board_url: str
    role: str | None = None


class JiraWorkRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_key: str | None
    feature_title: str | None
    feature_description_text: str | None = None
    feature_url: str | None
    feature_status: str | None
    direct_issue_key: str
    direct_issue_title: str
    direct_issue_description_text: str | None = None
    direct_issue_url: str
    direct_issue_status: str
    direct_issue_type: str
    direct_issue_updated_at: datetime | None = None
    target_date: date | None = None
    target_date_value: str | None = None
    boards: list[BoardEvidence] = Field(default_factory=list)
    relationship_type: str
    rolled_up_to_feature: bool
    active: bool
    in_flight: bool
    evidence: list[EvidenceLink]


class CondensedWorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jira_key: str
    title: str
    url: str
    status: str
    issue_type: str
    target_date: date | None = None
    target_date_value: str | None = None
    boards: list[BoardEvidence] = Field(default_factory=list)
    feature_key: str | None = None
    feature_title: str | None = None
    feature_url: str | None = None


class PerformanceSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_type: str
    rule_key: str | None = None
    title: str
    explanation: str
    confidence: str
    evaluated_at: datetime | None = None
    current_value: dict[str, Any] = Field(default_factory=dict)
    threshold: dict[str, Any] | None = None
    evidence: list[EvidenceLink]


class SuppressedSignalRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_key: str
    title: str
    reason: str
    required_evidence: list[str]


class PersonDirectoryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: str
    display_name: str
    preferred_name: str | None
    role: str | None
    current_teams: list[str]
    current_features: list[str]
    active_context: list[str]
    identity_mapping_state: str
    jira_account_id: str | None
    github_login: str | None


class PeopleDirectory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.1"
    snapshot_id: str
    snapshot_name: str | None
    snapshot_created_at: datetime
    organization_config_hash: str | None = None
    source_config_hash: str | None = None
    source_freshness: list[SourceFreshness]
    people: list[PersonDirectoryRow]
    data_quality_notes: list[str]


class IndividualDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.7"
    snapshot_id: str
    snapshot_name: str | None
    snapshot_created_at: datetime
    organization_config_hash: str | None = None
    source_config_hash: str | None = None
    source_freshness: list[SourceFreshness]
    person_id: str
    display_name: str
    preferred_name: str | None
    role: str | None
    manager_person_id: str | None
    active: bool
    jira_account_id: str | None
    github_login: str | None
    identity_mapping_state: str
    memberships: list[MembershipEvidence]
    current_work: list[CondensedWorkItem]
    jira_work: list[JiraWorkRelationship]
    github_contributions: list[GitHubDeliveryRecord]
    blockers_and_dependencies: list[JiraLinkEvidence]
    signals: list[PerformanceSignal]
    suppressed_signal_rules: list[SuppressedSignalRule] = Field(default_factory=list)
    data_quality_notes: list[str] = Field(default_factory=list)
