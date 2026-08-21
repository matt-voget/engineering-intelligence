"""Calculate snapshot-safe Build Cycle Time for IBR and non-IBR parent work."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import TeamsConfig
from engineering_intelligence.persistence.models import (
    BoardMembershipObservation,
    JiraIssue,
    JiraIssueVersion,
    JiraScopeObservation,
    JiraStatusTransition,
    SnapshotSourceState,
)
from engineering_intelligence.presentations.build_cycle import (
    BuildCycleContribution,
    BuildCycleGroup,
    BuildCycleTimeView,
    ChildCycleTime,
    StatusDuration,
)
from engineering_intelligence.presentations.rag import assess_rag
from engineering_intelligence.queries.dashboard import DashboardQuery, _as_utc
from engineering_intelligence.queries.team import _team_config
from engineering_intelligence.snapshots.organization import (
    ibr_board_id_for_snapshot,
    organization_config_for_snapshot,
)

PARENT_ISSUE_TYPES = {"epic", "feature request", "fdi request"}
MAX_ANCESTOR_DEPTH = 10


@dataclass
class _Timeline:
    issue: JiraIssue
    version: JiraIssueVersion
    transitions: list[JiraStatusTransition]


class BuildCycleTimeQuery:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def get(
        self,
        snapshot_identifier: str,
        team_identifier: str,
        teams_config: TeamsConfig,
    ) -> BuildCycleTimeView:
        with self.sessions() as session:
            snapshot = DashboardQuery._snapshot(session, snapshot_identifier)
            teams_config = organization_config_for_snapshot(snapshot, teams_config)
            team = _team_config(teams_config, team_identifier)
            states = session.scalars(
                select(SnapshotSourceState).where(
                    SnapshotSourceState.snapshot_id == snapshot.id
                )
            ).all()
            team_scope = f"query:team-field-{team.id}"
            ibr_scope = f"board:{ibr_board_id_for_snapshot(snapshot)}"
            team_state = next((state for state in states if state.scope == team_scope), None)
            board_state = next((state for state in states if state.scope == ibr_scope), None)
            if team_state is None or team_state.ingestion_run_id is None:
                raise ValueError(f"Snapshot has no pinned Jira source scope: {team_scope}")
            high_water = _as_utc(team_state.high_water_mark)
            team_issue_ids = set(
                session.scalars(
                    select(JiraScopeObservation.issue_id).where(
                        JiraScopeObservation.ingestion_run_id
                        == team_state.ingestion_run_id
                    )
                )
            )
            board_issue_ids: set[str] = set()
            notes: list[str] = []
            if board_state is not None and board_state.ingestion_run_id is not None:
                board_issue_ids = set(
                    session.scalars(
                        select(BoardMembershipObservation.issue_id).where(
                            BoardMembershipObservation.ingestion_run_id
                            == board_state.ingestion_run_id
                        )
                    )
                )
            else:
                notes.append(
                    "The snapshot has no pinned IBR-board state; all qualifying parents "
                    "are classified as non-IBR."
                )

            visible_ids = team_issue_ids | board_issue_ids
            timelines = {
                issue_id: timeline
                for issue_id in visible_ids
                if (timeline := _timeline(session, issue_id, high_water)) is not None
            }
            children_by_parent: dict[str, list[str]] = defaultdict(list)
            for issue_id, timeline in timelines.items():
                if timeline.version.parent_issue_id:
                    children_by_parent[timeline.version.parent_issue_id].append(issue_id)

            def classification(issue_id: str) -> str:
                current = issue_id
                seen = {issue_id}
                for _ in range(MAX_ANCESTOR_DEPTH + 1):
                    if current in board_issue_ids:
                        return "ibr_linked"
                    timeline = timelines.get(current)
                    parent = timeline.version.parent_issue_id if timeline else None
                    if not parent or parent in seen:
                        break
                    seen.add(parent)
                    current = parent
                return "non_ibr"

            grouped: dict[str, list[BuildCycleContribution]] = {
                "ibr_linked": [],
                "non_ibr": [],
            }
            for issue_id in sorted(team_issue_ids):
                timeline = timelines.get(issue_id)
                if timeline is None:
                    continue
                group = classification(issue_id)
                if not _eligible_issue(group, timeline.version.issue_type_name):
                    continue
                cycle = _cycle(timeline)
                if cycle is None:
                    continue
                started, ended, days, durations = cycle
                if days <= 0:
                    continue
                grouped[group].append(
                    BuildCycleContribution(
                        jira_key=timeline.issue.issue_key,
                        title=timeline.version.summary or "",
                        url=timeline.issue.web_url,
                        issue_type=timeline.version.issue_type_name or "Unknown",
                        classification=group,
                        cycle_days=days,
                        period_started_at=started,
                        period_ended_at=ended,
                        top_status=_top_status(durations),
                        status_durations=durations,
                        children=_children(
                            issue_id, timelines, children_by_parent, depth=1
                        ),
                        rag=assess_rag(
                            teams_config.rag,
                            team_id=team.id,
                            section="build_cycle_time",
                            metric="cycle_days",
                            value=days,
                            record_key=timeline.issue.issue_key,
                            classification=group,
                        ),
                    )
                )
            for contributions in grouped.values():
                contributions.sort(key=lambda item: (-item.cycle_days, item.jira_key))
            return BuildCycleTimeView(
                snapshot_id=snapshot.id,
                snapshot_name=snapshot.name,
                snapshot_created_at=_as_utc(snapshot.created_at),
                team_id=team.id,
                team_name=team.name,
                scope=team_scope,
                ibr_parent_issue_types=["Epic", "Feature Request", "FDI Request"],
                groups=[
                    BuildCycleGroup(
                        classification=classification_name,
                        contributions=grouped[classification_name],
                    )
                    for classification_name in ("ibr_linked", "non_ibr")
                ],
                data_quality_notes=notes
                + [
                    (
                        "Calendar time is measured from first entry into In Progress "
                        "to the first subsequent entry into Done."
                    ),
                    "The report date filter selects issues by their Done transition.",
                    "Issues with a zero-day cycle are excluded.",
                ],
            )


def _timeline(
    session: Session, issue_id: str, high_water: datetime
) -> _Timeline | None:
    issue = session.get(JiraIssue, issue_id)
    if issue is None or issue.is_deleted:
        return None
    version = session.scalar(
        select(JiraIssueVersion)
        .where(
            JiraIssueVersion.issue_id == issue_id,
            JiraIssueVersion.observed_at <= high_water,
        )
        .order_by(JiraIssueVersion.observed_at.desc())
        .limit(1)
    )
    if version is None:
        return None
    transitions = list(
        session.scalars(
            select(JiraStatusTransition)
            .where(
                JiraStatusTransition.issue_id == issue_id,
                JiraStatusTransition.first_seen_at <= high_water,
                JiraStatusTransition.changed_at <= high_water,
            )
            .order_by(
                JiraStatusTransition.changed_at,
                JiraStatusTransition.changelog_id,
                JiraStatusTransition.item_index,
            )
        ).all()
    )
    return _Timeline(issue=issue, version=version, transitions=transitions)


def _eligible_issue(classification: str, issue_type: str | None) -> bool:
    return classification == "non_ibr" or (
        (issue_type or "").strip().casefold() in PARENT_ISSUE_TYPES
    )


def _cycle(
    timeline: _Timeline,
) -> tuple[datetime, datetime, float, list[StatusDuration]] | None:
    started = next(
        (
            _as_utc(transition.changed_at)
            for transition in timeline.transitions
            if _canonical(transition.to_status_name) == "in progress"
        ),
        None,
    )
    if started is None:
        return None
    ended = next(
        (
            _as_utc(transition.changed_at)
            for transition in timeline.transitions
            if _canonical(transition.to_status_name) == "done"
            and _as_utc(transition.changed_at) >= started
        ),
        None,
    )
    if ended is None:
        return None
    totals: dict[str, float] = defaultdict(float)
    status = "In Progress"
    cursor = started
    for transition in timeline.transitions:
        changed_at = _as_utc(transition.changed_at)
        if changed_at <= started:
            if changed_at == started:
                status = transition.to_status_name or status
            continue
        interval_end = min(changed_at, ended)
        if interval_end > cursor:
            totals[status] += (interval_end - cursor).total_seconds() / 86400
        if changed_at >= ended:
            break
        status = transition.to_status_name or "Unknown"
        cursor = changed_at
    durations = [
        StatusDuration(status=status_name, days=round(days, 2))
        for status_name, days in sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    ]
    return started, ended, round((ended - started).total_seconds() / 86400, 2), durations


def _children(
    parent_id: str,
    timelines: dict[str, _Timeline],
    children_by_parent: dict[str, list[str]],
    *,
    depth: int,
) -> list[ChildCycleTime]:
    results: list[ChildCycleTime] = []
    for child_id in sorted(
        children_by_parent.get(parent_id, []),
        key=lambda item: timelines[item].issue.issue_key,
    ):
        timeline = timelines[child_id]
        cycle = _cycle(timeline)
        if cycle is None:
            started = ended = None
            days = None
            durations: list[StatusDuration] = []
            warning = "No complete In Progress-to-Done transition in this snapshot."
        else:
            started, ended, days, durations = cycle
            warning = None
        if days is None or days > 0:
            results.append(
                ChildCycleTime(
                    jira_key=timeline.issue.issue_key,
                    title=timeline.version.summary or "",
                    url=timeline.issue.web_url,
                    issue_type=timeline.version.issue_type_name,
                    status=timeline.version.status_name,
                    depth=depth,
                    cycle_days=days,
                    period_started_at=started,
                    period_ended_at=ended,
                    top_status=_top_status(durations),
                    status_durations=durations,
                    warning=warning,
                )
            )
        results.extend(
            _children(
                child_id,
                timelines,
                children_by_parent,
                depth=depth + 1,
            )
        )
    return results


def _top_status(durations: list[StatusDuration]) -> str | None:
    return durations[0].status if durations else None


def _canonical(status: str | None) -> str:
    return (status or "").strip().casefold()
