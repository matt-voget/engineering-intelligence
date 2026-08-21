"""Build a deterministic Team detail from existing Dashboard and Feature contracts."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import TeamConfig, TeamsConfig
from engineering_intelligence.flags import FlagService
from engineering_intelligence.persistence.models import SnapshotSourceState
from engineering_intelligence.presentations.dashboard import WorkItem
from engineering_intelligence.presentations.feature import GitHubDeliveryRecord, JiraLinkEvidence
from engineering_intelligence.presentations.team import (
    TeamAvailability,
    TeamDetail,
    TeamRosterMember,
    WorkflowColumn,
)
from engineering_intelligence.queries.dashboard import DashboardQuery
from engineering_intelligence.queries.feature import FeatureQuery
from engineering_intelligence.queries.metrics import MetricsQuery
from engineering_intelligence.snapshots.organization import (
    ibr_board_id_for_snapshot,
    organization_config_for_snapshot,
)

WORKFLOW_COLUMNS = [
    "Backlog",
    "Idea",
    "Ideation",
    "Product Review",
    "Ready for Build",
    "In Progress",
    "In Code Review",
    "Ready for Test",
    "In Testing",
    "Ready for Docs",
    "Done",
]
ATTENTION_EMPTY = {
    "Ready for Build": "No high-level IBR work is ready to start.",
    "In Progress": "No high-level IBR work is currently in active delivery.",
}


class TeamQuery:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        jira_base_url: str,
        max_feature_nodes: int = 250,
    ) -> None:
        self.sessions = sessions
        self.dashboard_query = DashboardQuery(sessions, jira_base_url=jira_base_url)
        self.feature_query = FeatureQuery(sessions, max_nodes=max_feature_nodes)

    def get(
        self,
        snapshot_identifier: str,
        team_identifier: str,
        teams_config: TeamsConfig,
    ) -> TeamDetail:
        with self.sessions() as session:
            snapshot = self.dashboard_query._snapshot(session, snapshot_identifier)
            teams_config = organization_config_for_snapshot(snapshot, teams_config)
            ibr_scope = f"board:{ibr_board_id_for_snapshot(snapshot)}"
        team = _team_config(teams_config, team_identifier)
        dashboard = FlagService(self.sessions).record_dashboard(
            self.dashboard_query.get(snapshot_identifier, teams_config)
        )
        row = next(item for item in dashboard.teams if item.team_id == team.id)
        with self.sessions() as session:
            snapshot = self.dashboard_query._snapshot(session, snapshot_identifier)
            ibr_state = next(
                (state for state in dashboard.source_freshness if state.scope == ibr_scope),
                None,
            )
            if ibr_state is None:
                raise ValueError("Snapshot has no configured IBR board source")
            source_state = session.scalar(
                select(SnapshotSourceState).where(
                    SnapshotSourceState.snapshot_id == snapshot.id,
                    SnapshotSourceState.scope == ibr_scope,
                )
            )
            assert source_state is not None
            records = self.dashboard_query._ibr_versions(session, source_state)
        aliases = {team.name.casefold(), *(alias.casefold() for alias in team.aliases)}
        team_records = [
            (issue, version)
            for issue, version in records
            if version.team_name and version.team_name.casefold() in aliases
        ]
        work_items = [
            self.dashboard_query._work_item(issue, version) for issue, version in team_records
        ]
        workflow = _workflow(work_items)
        github_records: dict[tuple[str, str, str], GitHubDeliveryRecord] = {}
        team_logins = {
            member.github_login.casefold()
            for member in team.members
            if member.active and member.github_login
        }
        blocked: dict[tuple[str, str, str | None], JiraLinkEvidence] = {}
        github_available = False
        for issue, _version in team_records:
            feature = self.feature_query.get(snapshot_identifier, issue.issue_key)
            github_available = github_available or feature.github_delivery.available
            for delivery in feature.github_delivery.records:
                if not delivery.actor_login or delivery.actor_login.casefold() not in team_logins:
                    continue
                github_records[
                    (delivery.record_type, delivery.record_id, delivery.direct_jira_key)
                ] = delivery
            for link in feature.jira_links:
                if link.is_blocking_relationship:
                    blocked[(link.source_issue_key, link.relationship, link.target_issue_key)] = (
                        link
                    )
        roster = _roster(team, dashboard.snapshot_created_at.date())
        data_quality = []
        if not roster:
            data_quality.append("No roster members are configured for this team.")
        if team.roster_source.state == "unverified":
            data_quality.append("The configured roster has no authoritative membership source.")
        elif team.roster_source.starts_on_basis == "first_verified_observation":
            data_quality.append(
                "Roster start dates represent first verified observation, not confirmed "
                "team join dates."
            )
        data_quality.extend(team.roster_source.notes)
        if not github_available:
            data_quality.append("This snapshot has no configured GitHub source state.")
        data_quality.append(
            "Workflow reflects high-level items observed directly on the configured IBR board."
        )
        metrics = MetricsQuery(self.sessions).get(
            snapshot_identifier,
            team=team.name,
            team_aliases=team.aliases,
        )
        metrics_available = any(metric.sample_size for metric in metrics.metrics)
        return TeamDetail(
            snapshot_id=dashboard.snapshot_id,
            snapshot_name=dashboard.snapshot_name,
            snapshot_created_at=dashboard.snapshot_created_at,
            organization_config_hash=dashboard.organization_config_hash,
            source_config_hash=dashboard.source_config_hash,
            source_freshness=dashboard.source_freshness,
            team_id=team.id,
            team_name=team.name,
            health=row.health,
            health_coverage=row.health_coverage,
            flags=row.flags,
            workflow=workflow,
            most_recently_completed=row.most_recently_completed,
            roster=roster,
            github_delivery=sorted(
                github_records.values(),
                key=lambda item: (
                    item.occurred_at or dashboard.snapshot_created_at,
                    item.record_type,
                    item.record_id,
                ),
                reverse=True,
            ),
            github_availability=TeamAvailability(
                available=github_available,
                message=(
                    "GitHub evidence is available from configured repositories."
                    if github_available
                    else "No GitHub repository state is present in this snapshot."
                ),
            ),
            blocked_work=sorted(
                blocked.values(),
                key=lambda item: (
                    item.source_issue_key,
                    item.relationship,
                    item.target_issue_key or "",
                ),
            ),
            metrics_availability=TeamAvailability(
                available=metrics_available,
                message=(
                    "Transition-based team metrics are available through get_metrics."
                    if metrics_available
                    else "No qualifying transition-based team metrics are available."
                ),
            ),
            data_quality_notes=data_quality,
        )


def _team_config(teams: TeamsConfig, identifier: str) -> TeamConfig:
    normalized = identifier.casefold()
    matches = [
        team
        for team in teams.teams
        if normalized
        in {
            team.id.casefold(),
            team.name.casefold(),
            *(alias.casefold() for alias in team.aliases),
        }
    ]
    if len(matches) != 1:
        raise ValueError(f"Team not found or ambiguous: {identifier}")
    return matches[0]


def _workflow(items: list[WorkItem]) -> list[WorkflowColumn]:
    grouped = {
        name: sorted(
            (item for item in items if item.status.casefold() == name.casefold()),
            key=lambda item: item.jira_key,
        )
        for name in WORKFLOW_COLUMNS
    }
    unknown = sorted(
        (
            item
            for item in items
            if item.status.casefold() not in {name.casefold() for name in WORKFLOW_COLUMNS}
        ),
        key=lambda item: (item.status, item.jira_key),
    )
    columns = [
        WorkflowColumn(
            name=name,
            count=len(grouped[name]),
            items=grouped[name],
            empty=not grouped[name],
            attention_signal=not grouped[name] and name in ATTENTION_EMPTY,
            attention_explanation=ATTENTION_EMPTY.get(name) if not grouped[name] else None,
        )
        for name in WORKFLOW_COLUMNS
    ]
    if unknown:
        columns.append(
            WorkflowColumn(
                name="Unmapped status",
                count=len(unknown),
                items=unknown,
                empty=False,
                attention_signal=False,
                attention_explanation=(
                    "These Jira statuses are not mapped to the approved Team workflow."
                ),
            )
        )
    return columns


def _roster(team: TeamConfig, on_date: date) -> list[TeamRosterMember]:
    return sorted(
        (
            TeamRosterMember(
                person_id=member.id,
                display_name=member.name,
                preferred_name=member.preferred_name,
                role=member.role,
                starts_on=member.starts_on,
                ends_on=member.ends_on,
                is_primary=member.is_primary,
                jira_account_id=member.jira_account_id,
                github_login=member.github_login,
                identity_mapping_state=(
                    "complete"
                    if member.jira_account_id and member.github_login
                    else "partial"
                    if member.jira_account_id or member.github_login
                    else "unmapped"
                ),
            )
            for member in team.members
            if member.starts_on <= on_date
            and (member.ends_on is None or member.ends_on >= on_date)
            and member.active
        ),
        key=lambda item: item.display_name.casefold(),
    )
