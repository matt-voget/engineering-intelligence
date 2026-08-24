"""Snapshot-pinned organization-wide GitHub Finder schema."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GitHubFinderRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_key: str
    record_type: str
    repository: str
    identifier: str
    title: str
    url: str
    state: str | None = None
    draft: bool | None = None
    author_login: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    merged_at: datetime | None = None
    authored_at: datetime | None = None
    committed_at: datetime | None = None
    head_ref: str | None = None
    base_ref: str | None = None
    commit_count: int | None = None
    review_count: int | None = None
    reviewers: list[str] = Field(default_factory=list)
    first_reviewed_at: datetime | None = None
    pickup_hours: float | None = None
    review_hours: float | None = None
    pull_requests: list[str] = Field(default_factory=list)
    jira_keys: list[str] = Field(default_factory=list)
    jira_urls: dict[str, str] = Field(default_factory=dict)


class GitHubFinderView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    snapshot_id: str
    snapshot_name: str
    snapshot_created_at: datetime
    source_freshness: dict[str, datetime]
    records: list[GitHubFinderRecord] = Field(default_factory=list)
    data_quality_notes: list[str] = Field(default_factory=list)
