"""Compact, deterministic Team meeting-brief presentation schema."""

from collections import Counter
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from engineering_intelligence.presentations.dashboard import (
    HealthCoverage,
    HealthFlag,
    HealthState,
    SourceFreshness,
    WorkItem,
)
from engineering_intelligence.presentations.feature import JiraLinkEvidence
from engineering_intelligence.presentations.team import TeamAvailability, TeamDetail


class TeamRosterSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member_count: int
    complete_identity_count: int
    incomplete_identity_count: int


class GitHubDeliverySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    message: str
    total_records: int
    repositories_with_linked_records: list[str] = Field(default_factory=list)
    record_type_counts: dict[str, int] = Field(default_factory=dict)
    state_counts: dict[str, int] = Field(default_factory=dict)


class TeamBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.1"
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
    most_recently_completed: WorkItem | None
    in_progress: list[WorkItem]
    ready_for_build: list[WorkItem]
    roster: TeamRosterSummary
    github: GitHubDeliverySummary
    blocked_work: list[JiraLinkEvidence]
    metrics_availability: TeamAvailability
    data_quality_notes: list[str]


def build_team_brief(team: TeamDetail) -> TeamBrief:
    """Project complete Team detail into a bounded meeting-preparation contract."""
    workflow = {column.name: column for column in team.workflow}
    in_progress = workflow.get("In Progress")
    ready_for_build = workflow.get("Ready for Build")
    complete_identities = sum(
        member.identity_mapping_state == "complete" for member in team.roster
    )
    repositories = sorted({record.repository for record in team.github_delivery})
    record_type_counts = dict(
        sorted(Counter(record.record_type for record in team.github_delivery).items())
    )
    state_counts = dict(
        sorted(Counter(record.state for record in team.github_delivery).items())
    )
    return TeamBrief(
        snapshot_id=team.snapshot_id,
        snapshot_name=team.snapshot_name,
        snapshot_created_at=team.snapshot_created_at,
        organization_config_hash=team.organization_config_hash,
        source_config_hash=team.source_config_hash,
        source_freshness=team.source_freshness,
        team_id=team.team_id,
        team_name=team.team_name,
        health=team.health,
        health_coverage=team.health_coverage,
        flags=team.flags,
        most_recently_completed=team.most_recently_completed,
        in_progress=in_progress.items if in_progress else [],
        ready_for_build=ready_for_build.items if ready_for_build else [],
        roster=TeamRosterSummary(
            member_count=len(team.roster),
            complete_identity_count=complete_identities,
            incomplete_identity_count=len(team.roster) - complete_identities,
        ),
        github=GitHubDeliverySummary(
            available=team.github_availability.available,
            message=team.github_availability.message,
            total_records=len(team.github_delivery),
            repositories_with_linked_records=repositories,
            record_type_counts=record_type_counts,
            state_counts=state_counts,
        ),
        blocked_work=team.blocked_work,
        metrics_availability=team.metrics_availability,
        data_quality_notes=team.data_quality_notes,
    )
