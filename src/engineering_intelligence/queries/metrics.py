"""Calculate reproducible Ideate and Build metrics from Jira transitions."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from statistics import fmean

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from engineering_intelligence.config import TeamsConfig
from engineering_intelligence.persistence.models import (
    JiraIssue,
    JiraIssueVersion,
    JiraScopeObservation,
    JiraStatusTransition,
    Snapshot,
    SnapshotSourceState,
)
from engineering_intelligence.presentations.dashboard import SourceFreshness
from engineering_intelligence.presentations.metrics import (
    MetricContribution,
    MetricDefinition,
    MetricHealth,
    MetricPhase,
    MetricResult,
    MetricsView,
    MetricTrendPoint,
)

DEFINITION_SET_VERSION = "1.0.0"
LEADERSHIP_REPORT = (
    "https://datastudio.google.com/reporting/"
    "c8a3fb1f-16ff-45d3-a263-82a2ebda5724"
)
IDEATE_REPORT = f"{LEADERSHIP_REPORT}/page/p_fou2l2yj5d"
BUILD_REPORT = f"{LEADERSHIP_REPORT}/page/p_oxhipu7i5d"


@dataclass(frozen=True)
class _Definition:
    metric_id: str
    label: str
    phase: MetricPhase
    start_status: str
    end_status: str | None
    leadership_label: str
    issue_kind: str = "all"


DEFINITIONS = (
    _Definition(
        "ideation_days",
        "Ideation",
        MetricPhase.ideate,
        "ideation",
        None,
        "Ideation days",
    ),
    _Definition(
        "product_review_days",
        "Product review",
        MetricPhase.ideate,
        "product review",
        None,
        "Product review",
    ),
    _Definition(
        "ready_for_build_days",
        "Ready for build",
        MetricPhase.ideate,
        "ready for build",
        None,
        "Ready for build",
    ),
    _Definition(
        "ideate_cycle_days",
        "Ideate cycle",
        MetricPhase.ideate,
        "ideation",
        "in progress",
        "Ideate cycle",
    ),
    _Definition(
        "in_progress_days",
        "In progress",
        MetricPhase.build,
        "in progress",
        None,
        "In progress",
    ),
    _Definition(
        "review_days",
        "Review",
        MetricPhase.build,
        "in code review",
        None,
        "Review",
    ),
    _Definition(
        "test_wait_days",
        "Test wait",
        MetricPhase.build,
        "ready for test",
        None,
        "Test wait",
    ),
    _Definition(
        "in_test_days",
        "In test",
        MetricPhase.build,
        "in testing",
        None,
        "In test",
    ),
    _Definition(
        "docs_days",
        "Docs",
        MetricPhase.build,
        "ready for docs",
        None,
        "Docs",
    ),
    _Definition(
        "build_cycle_days",
        "Build cycle",
        MetricPhase.build,
        "in progress",
        "done",
        "Build cycle",
    ),
    _Definition(
        "feature_cycle_days",
        "Feature cycle time",
        MetricPhase.build,
        "in progress",
        "done",
        "Feature cycle time",
        "feature",
    ),
    _Definition(
        "bug_cycle_days",
        "Bug cycle time",
        MetricPhase.build,
        "in progress",
        "done",
        "Bug cycle time",
        "bug",
    ),
)


@dataclass
class _IssueTimeline:
    issue: JiraIssue
    version: JiraIssueVersion
    transitions: list[JiraStatusTransition]


class MetricsQuery:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def get(
        self,
        snapshot_identifier: str,
        *,
        team: str | None = None,
        team_aliases: list[str] | None = None,
        scope: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> MetricsView:
        with self.sessions() as session:
            snapshot = self._snapshot(session, snapshot_identifier)
            source_states = session.scalars(
                select(SnapshotSourceState)
                .where(SnapshotSourceState.snapshot_id == snapshot.id)
                .order_by(SnapshotSourceState.source, SnapshotSourceState.scope)
            ).all()
            jira_states = [state for state in source_states if state.source == "jira"]
            if not jira_states:
                raise ValueError(f"Snapshot {snapshot_identifier} has no Jira source")
            selected_states = (
                [state for state in jira_states if state.scope == scope]
                if scope
                else jira_states
            )
            if not selected_states:
                raise ValueError(f"Snapshot has no Jira source scope: {scope}")
            high_water = max(_as_utc(state.high_water_mark) for state in selected_states)
            end_date = date_to or _as_utc(snapshot.created_at).date()
            start_date = date_from or end_date - timedelta(days=29)
            if start_date > end_date:
                raise ValueError("date_from must be on or before date_to")
            issue_ids = None
            if scope and scope.startswith("query:"):
                state = selected_states[0]
                issue_ids = set(
                    session.scalars(
                        select(JiraScopeObservation.issue_id).where(
                            JiraScopeObservation.ingestion_run_id
                            == state.ingestion_run_id
                        )
                    ).all()
                )
            timelines = self._timelines(
                session,
                high_water,
                {team, *(team_aliases or [])} if team else None,
                issue_ids,
            )
            results = [
                self._result(definition, timelines, start_date, end_date, high_water)
                for definition in DEFINITIONS
            ]
            return MetricsView(
                snapshot_id=snapshot.id,
                snapshot_name=snapshot.name,
                snapshot_created_at=_as_utc(snapshot.created_at),
                source_freshness=[
                    SourceFreshness(
                        source=state.source,
                        scope=state.scope,
                        observed_at=_as_utc(state.high_water_mark),
                        ingestion_run_id=state.ingestion_run_id,
                    )
                    for state in source_states
                ],
                team=team,
                date_from=start_date,
                date_to=end_date,
                definition_set_version=DEFINITION_SET_VERSION,
                local_source_scopes=[state.scope for state in jira_states],
                selected_source_scope=scope,
                leadership_comparison_status="unreconciled_source_scope",
                metrics=results,
                data_quality_notes=[
                    "Durations use calendar days, matching the leadership report labels.",
                    (
                        "Leadership labels were confirmed from Looker Studio; exact source "
                        "formulas and filters remain external, so local comparisons are "
                        "explicitly directional until reconciled."
                    ),
                    "Thresholds are not configured; metric health is not yet evaluated.",
                ],
            )

    @staticmethod
    def _snapshot(session: Session, identifier: str) -> Snapshot:
        snapshot = session.get(Snapshot, identifier)
        if snapshot is None:
            snapshot = session.scalar(select(Snapshot).where(Snapshot.name == identifier))
        if snapshot is None:
            raise ValueError(f"Snapshot not found: {identifier}")
        return snapshot

    @staticmethod
    def _timelines(
        session: Session,
        high_water: datetime,
        team_names: set[str] | None,
        issue_ids: set[str] | None,
    ) -> list[_IssueTimeline]:
        issues = session.scalars(select(JiraIssue).where(JiraIssue.is_deleted.is_(False))).all()
        timelines = []
        for issue in issues:
            if issue_ids is not None and issue.id not in issue_ids:
                continue
            version = session.scalar(
                select(JiraIssueVersion)
                .where(
                    JiraIssueVersion.issue_id == issue.id,
                    JiraIssueVersion.observed_at <= high_water,
                )
                .order_by(JiraIssueVersion.observed_at.desc())
                .limit(1)
            )
            if version is None:
                continue
            if team_names and (version.team_name or "").casefold() not in {
                name.casefold() for name in team_names
            }:
                continue
            transitions = session.scalars(
                select(JiraStatusTransition)
                .where(
                    JiraStatusTransition.issue_id == issue.id,
                    JiraStatusTransition.first_seen_at <= high_water,
                    JiraStatusTransition.changed_at <= high_water,
                )
                .order_by(
                    JiraStatusTransition.changed_at,
                    JiraStatusTransition.changelog_id,
                    JiraStatusTransition.item_index,
                )
            ).all()
            timelines.append(_IssueTimeline(issue, version, list(transitions)))
        return timelines

    def _result(
        self,
        definition: _Definition,
        timelines: list[_IssueTimeline],
        start_date: date,
        end_date: date,
        high_water: datetime,
    ) -> MetricResult:
        window_start = datetime.combine(start_date, time.min, UTC)
        window_end = min(
            datetime.combine(end_date + timedelta(days=1), time.min, UTC),
            high_water + timedelta(microseconds=1),
        )
        baseline_start = window_start - timedelta(days=90)
        current, exclusions = self._contributions(
            definition, timelines, window_start, window_end, high_water
        )
        baseline, _baseline_exclusions = self._contributions(
            definition, timelines, baseline_start, window_start, high_water
        )
        current_value = _mean(item.value for item in current)
        baseline_value = _mean(item.value for item in baseline)
        return MetricResult(
            definition=_presentation_definition(definition),
            current_value=current_value,
            sample_size=len(current),
            baseline_90_day_value=baseline_value,
            change_from_baseline=(
                round(current_value - baseline_value, 2)
                if current_value is not None and baseline_value is not None
                else None
            ),
            threshold=None,
            health=(
                MetricHealth.not_configured if current else MetricHealth.unavailable
            ),
            candidate_issue_count=len(timelines),
            excluded_issue_type_count=exclusions["issue_type"],
            excluded_missing_completion_count=exclusions["missing_completion"],
            excluded_outside_period_count=exclusions["outside_period"],
            excluded_incomplete_transition_count=exclusions["incomplete_transition"],
            trend=_weekly_trend(current, start_date, end_date),
            contributions=sorted(
                current,
                key=lambda item: (-item.value, item.jira_key),
            ),
            data_quality_warnings=(
                []
                if current
                else [
                    "No qualifying issues have complete transition evidence in this period."
                ]
            ),
        )

    @staticmethod
    def _contributions(
        definition: _Definition,
        timelines: list[_IssueTimeline],
        window_start: datetime,
        window_end: datetime,
        high_water: datetime,
    ) -> tuple[list[MetricContribution], dict[str, int]]:
        results = []
        exclusions = {
            "issue_type": 0,
            "missing_completion": 0,
            "outside_period": 0,
            "incomplete_transition": 0,
        }
        for timeline in timelines:
            if not _issue_kind_matches(definition.issue_kind, timeline.version.issue_type_name):
                exclusions["issue_type"] += 1
                continue
            completion_status = (
                "in progress" if definition.phase == MetricPhase.ideate else "done"
            )
            completion = _first_entry(timeline, completion_status)
            if completion is None:
                exclusions["missing_completion"] += 1
                continue
            if not (window_start <= completion < window_end):
                exclusions["outside_period"] += 1
                continue
            if definition.end_status:
                started = _first_entry(timeline, definition.start_status)
                ended = _first_entry(timeline, definition.end_status)
                if started is None or ended is None or ended < started:
                    exclusions["incomplete_transition"] += 1
                    continue
                value = (ended - started).total_seconds() / 86400
                period_end = ended
            else:
                intervals = _status_intervals(timeline, high_water)
                matching = [
                    (started, ended)
                    for status, started, ended in intervals
                    if _canonical_status(status) == definition.start_status
                    and ended <= completion
                ]
                if not matching:
                    exclusions["incomplete_transition"] += 1
                    continue
                started = matching[0][0]
                period_end = matching[-1][1]
                value = sum(
                    (ended - interval_start).total_seconds() / 86400
                    for interval_start, ended in matching
                )
            results.append(
                MetricContribution(
                    jira_id=timeline.issue.id,
                    jira_key=timeline.issue.issue_key,
                    title=timeline.version.summary,
                    url=timeline.issue.web_url,
                    team_name=timeline.version.team_name,
                    issue_type=timeline.version.issue_type_name,
                    value=round(value, 2),
                    unit="calendar_days",
                    period_started_at=started,
                    period_ended_at=period_end,
                    warnings=[],
                )
            )
        return results, exclusions


def _presentation_definition(definition: _Definition) -> MetricDefinition:
    is_cycle = definition.end_status is not None
    completion = "first entry to In Progress" if definition.phase == MetricPhase.ideate else (
        "first entry to Done"
    )
    return MetricDefinition(
        metric_id=definition.metric_id,
        definition_version=DEFINITION_SET_VERSION,
        label=definition.label,
        phase=definition.phase,
        unit="calendar_days",
        aggregation="arithmetic mean of qualifying issue values",
        formula=(
            f"calendar days from first entry to {definition.start_status.title()} "
            f"until first entry to {definition.end_status.title()}"
            if is_cycle and definition.end_status
            else (
                f"sum of calendar time in {definition.start_status.title()} before "
                f"{completion}"
            )
        ),
        inclusion_rule=(
            f"{definition.issue_kind} issues with complete transition evidence whose "
            f"{completion} occurs in the selected date range"
        ),
        completion_event=completion,
        leadership_label=definition.leadership_label,
        leadership_report_url=(
            IDEATE_REPORT if definition.phase == MetricPhase.ideate else BUILD_REPORT
        ),
    )


def _status_intervals(
    timeline: _IssueTimeline,
    high_water: datetime,
) -> list[tuple[str, datetime, datetime]]:
    if not timeline.transitions:
        return []
    first = timeline.transitions[0]
    status = first.from_status_name
    started = _as_utc(timeline.version.source_created_at or timeline.issue.created_at)
    intervals = []
    for transition in timeline.transitions:
        changed_at = _as_utc(transition.changed_at)
        if status and changed_at >= started:
            intervals.append((status, started, changed_at))
        status = transition.to_status_name
        started = changed_at
    if status and started < high_water:
        intervals.append((status, started, high_water))
    return intervals


def _first_entry(timeline: _IssueTimeline, canonical_status: str) -> datetime | None:
    if timeline.transitions:
        first = timeline.transitions[0]
        if _canonical_status(first.from_status_name) == canonical_status:
            return _as_utc(timeline.version.source_created_at or timeline.issue.created_at)
    for transition in timeline.transitions:
        if _canonical_status(transition.to_status_name) == canonical_status:
            return _as_utc(transition.changed_at)
    return None


def _canonical_status(status: str | None) -> str:
    value = (status or "").strip().casefold()
    return "ready for docs" if value == "ready for doc" else value


def _issue_kind_matches(kind: str, issue_type: str | None) -> bool:
    is_bug = "bug" in (issue_type or "").casefold()
    return kind == "all" or (kind == "bug" and is_bug) or (kind == "feature" and not is_bug)


def _mean(values) -> float | None:
    materialized = list(values)
    return round(fmean(materialized), 2) if materialized else None


def _weekly_trend(
    contributions: list[MetricContribution],
    start_date: date,
    end_date: date,
) -> list[MetricTrendPoint]:
    buckets: dict[date, list[float]] = defaultdict(list)
    for item in contributions:
        day = item.period_ended_at.date()
        week = day - timedelta(days=day.weekday())
        buckets[week].append(item.value)
    first_week = start_date - timedelta(days=start_date.weekday())
    last_week = end_date - timedelta(days=end_date.weekday())
    points = []
    current = first_week
    while current <= last_week:
        values = buckets.get(current, [])
        points.append(
            MetricTrendPoint(
                period_start=current,
                value=_mean(values),
                sample_size=len(values),
            )
        )
        current += timedelta(days=7)
    return points


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def resolve_metric_team(
    identifier: str | None,
    teams_config: TeamsConfig,
) -> tuple[str | None, list[str]]:
    if identifier is None:
        return None, []
    normalized = identifier.casefold()
    matches = [
        team
        for team in teams_config.teams
        if normalized
        in {
            team.id.casefold(),
            team.name.casefold(),
            *(alias.casefold() for alias in team.aliases),
        }
    ]
    if len(matches) != 1:
        raise ValueError(f"Team not found or ambiguous: {identifier}")
    match = matches[0]
    return match.name, match.aliases
