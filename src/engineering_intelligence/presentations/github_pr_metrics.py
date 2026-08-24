"""Snapshot-backed GitHub pull-request metric schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from engineering_intelligence.presentations.rag import RagAssessment


class GitHubPersonRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str
    display_name: str | None = None


class PullRequestMetricContribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str
    number: int
    title: str
    url: str
    author: GitHubPersonRef | None
    reviewers: list[GitHubPersonRef] = Field(default_factory=list)
    created_at: datetime
    first_reviewed_at: datetime
    merged_at: datetime
    pickup_hours: float
    review_hours: float
    pickup_rag: RagAssessment | None = None
    review_rag: RagAssessment | None = None


class GitHubPullRequestMetricsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    snapshot_id: str
    snapshot_name: str
    snapshot_created_at: datetime
    team_id: str
    team_name: str
    repositories: list[str]
    author_logins: list[str]
    contributions: list[PullRequestMetricContribution] = Field(default_factory=list)
    data_quality_notes: list[str] = Field(default_factory=list)
