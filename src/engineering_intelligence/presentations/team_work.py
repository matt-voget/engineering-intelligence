"""Versioned IBR-versus-non-IBR team work classification schema."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

JIRA_CLASSIFICATIONS = ("ibr_linked", "non_ibr")
GITHUB_CLASSIFICATIONS = ("ibr_linked", "non_ibr", "unlinked")


class LinkedPullRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    url: str | None


class ClassifiedJiraIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jira_key: str
    title: str
    status: str
    status_category: str | None
    issue_type: str | None
    assignee_display_name: str | None
    url: str
    source_updated_at: datetime | None
    linked_pull_requests: list[LinkedPullRequest] = Field(default_factory=list)
    classification: str
    # "on_ibr_board" when the issue itself is an IBR board item; otherwise
    # "descendant_of_ibr_item" with the board ancestor recorded alongside.
    link_basis: str | None
    ibr_parent_key: str | None
    ibr_parent_url: str | None
    active: bool


class ClassifiedGitHubRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_type: str
    record_id: str
    repository: str
    title: str
    url: str | None
    actor_login: str | None
    occurred_at: datetime | None
    jira_keys: list[str] = Field(default_factory=list)
    classification: str
    # "explicit_jira_key" when the record's own text carries the key,
    # "via_pull_request" when a commit or review inherits its pull request's
    # keys, "author_identity" when only the configured author ties it to the team.
    link_basis: str


class WorkSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ibr_linked: int = 0
    non_ibr: int = 0
    unlinked: int = 0


class TeamWorkClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    snapshot_id: str
    snapshot_name: str
    snapshot_created_at: datetime
    team_id: str
    team_name: str
    scope: str
    jira_available: bool
    jira_message: str
    github_available: bool
    github_message: str
    # Issues listed: active (status category not done) or updated within
    # list_window_days of the snapshot. GitHub records listed: occurred within
    # list_window_days. Splits count active issues and records within
    # split_window_days respectively.
    list_window_days: int
    split_window_days: int
    jira_issues: list[ClassifiedJiraIssue] = Field(default_factory=list)
    jira_split: WorkSplit
    github_records: list[ClassifiedGitHubRecord] = Field(default_factory=list)
    github_split: WorkSplit
    data_quality_notes: list[str] = Field(default_factory=list)
