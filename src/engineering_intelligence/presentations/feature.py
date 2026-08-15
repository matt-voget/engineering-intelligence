"""Versioned Feature-detail presentation schema."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from engineering_intelligence.presentations.dashboard import SourceFreshness


class PersonEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jira_account_id: str | None
    github_login: str | None = None
    display_name: str
    relationship_type: str = "jira_assignee"
    direct_issue_key: str
    direct_issue_url: str
    source_url: str | None = None
    rolled_up_to_feature: bool


class JiraLinkEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_issue_key: str
    relationship: str
    target_issue_key: str | None
    target_title: str | None
    target_status: str | None
    target_url: str | None
    is_blocking_relationship: bool


class FeatureTimelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime
    event_type: str
    issue_key: str
    issue_url: str
    description: str


class FeatureHierarchyNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jira_id: str
    jira_key: str
    title: str
    issue_type: str
    status: str
    status_category: str | None
    team_name: str | None
    description_text: str | None = None
    source_updated_at: datetime | None
    gravitee_customers: list[str] = Field(default_factory=list)
    url: str
    relationship_from_parent: str
    depth: int
    direct_assignee: PersonEvidence | None
    rollup_people_count: int
    linked_delivery_count: int = 0
    children: list["FeatureHierarchyNode"] = Field(default_factory=list)


class FeatureSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_issues: int
    descendant_issues: int
    related_people: int
    jira_links: int
    blocking_links: int
    linked_delivery_records: int
    status_counts: dict[str, int]


class DeliveryAvailability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    message: str
    records: list["GitHubDeliveryRecord"] = Field(default_factory=list)


class GitHubDeliveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_type: str
    record_id: str
    repository: str
    title: str
    state: str
    url: str
    actor_login: str | None
    occurred_at: datetime | None
    direct_jira_key: str | None
    direct_jira_url: str | None
    rolled_up_to_feature: bool
    relationship_type: str
    confidence: str
    evidence: str


class FeatureDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    snapshot_id: str
    snapshot_name: str | None
    snapshot_created_at: datetime
    source_freshness: list[SourceFreshness]
    feature_key: str
    feature_url: str
    original_issue_type: str
    title: str
    status: str
    team_name: str | None
    hierarchy: FeatureHierarchyNode
    summary: FeatureSummary
    contributors: list[PersonEvidence]
    jira_links: list[JiraLinkEvidence]
    timeline: list[FeatureTimelineEvent]
    github_delivery: DeliveryAvailability
    data_quality_notes: list[str]
