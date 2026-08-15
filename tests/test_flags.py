from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from engineering_intelligence.flags import FlagService
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
    upgrade_database,
)
from engineering_intelligence.persistence.models import (
    FlagEvent,
    FlagOccurrence,
    FlagUserState,
    JiraIssue,
    JiraIssueVersion,
    JiraStatusTransition,
    LogicalFlag,
    SignalDefinition,
    SignalEvaluation,
    Snapshot,
    SnapshotSourceState,
)
from engineering_intelligence.presentations.attention import AttentionCollection
from engineering_intelligence.presentations.dashboard import (
    Dashboard,
    EvidenceLink,
    HealthCoverage,
    HealthCoverageState,
    HealthFlag,
    HealthState,
    Severity,
    TeamDashboardRow,
    WorkItem,
)
from engineering_intelligence.queries.attention import AttentionQuery
from engineering_intelligence.queries.dashboard import DashboardQuery
from engineering_intelligence.renderers.attention_markdown import (
    render_attention_flag_markdown,
    render_attention_markdown,
)


def dashboard(snapshot_id: str, created_at: datetime, with_flag: bool = True) -> Dashboard:
    flags = (
        [
            HealthFlag(
                fingerprint="a2a:work-in-flight:none",
                area="work_in_flight",
                severity=Severity.watch,
                title="No IBR items in progress",
                explanation="No items.",
                raised_at=created_at,
                evidence=[
                    EvidenceLink(
                        label="Open A2A work in Jira",
                        url="https://example.test/issues/?jql=team-a2a",
                    )
                ],
            )
        ]
        if with_flag
        else []
    )
    visible_work = WorkItem(
        jira_id="100001",
        jira_key="IDN-1",
        title="Visible work",
        status="Ready for Build",
        url="https://example.test/browse/IDN-1",
    )
    return Dashboard(
        snapshot_id=snapshot_id,
        snapshot_name=snapshot_id,
        snapshot_created_at=created_at,
        source_freshness=[],
        teams=[
            TeamDashboardRow(
                team_id="a2a",
                team_name="A2A",
                health=HealthState.watch if flags else HealthState.healthy,
                health_coverage=reliable_coverage(created_at),
                flags=flags,
                most_recently_completed=None,
                in_progress=[] if with_flag else [visible_work.model_copy(update={"status": "In Progress"})],
                ready_for_build=[visible_work],
            )
        ],
    )


def reliable_coverage(observed_at: datetime) -> HealthCoverage:
    return HealthCoverage(
        state=HealthCoverageState.reliable,
        reasons=[],
        required_source="jira",
        required_scope="board:2168",
        observed_at=observed_at,
        maximum_age_seconds=86400,
        age_at_snapshot_seconds=0,
        board_record_count=1,
        team_record_count=1,
    )


def add_snapshot(sessions, snapshot_id: str, created_at: datetime) -> None:
    with sessions.begin() as session:
        session.add(
            Snapshot(
                id=snapshot_id,
                name=snapshot_id,
                created_at=created_at,
                schema_version="1.0",
                description=None,
            )
        )


@pytest.mark.parametrize(
    ("severities", "expected"),
    [
        ([], HealthState.healthy),
        ([Severity.info], HealthState.healthy),
        ([Severity.info, Severity.watch], HealthState.watch),
        ([Severity.watch, Severity.concern], HealthState.concern),
        ([Severity.concern, Severity.critical], HealthState.critical),
    ],
)
def test_health_uses_highest_actionable_severity(
    severities: list[Severity],
    expected: HealthState,
) -> None:
    flags = [
        HealthFlag(
            fingerprint=f"flag:{severity}",
            area="test",
            severity=severity,
            title=f"{severity} flag",
            explanation="Test evidence.",
            raised_at=datetime(2026, 7, 29, tzinfo=UTC),
        )
        for severity in severities
    ]

    assert DashboardQuery._health_state(
        flags,
        reliable_coverage(datetime(2026, 7, 29, tzinfo=UTC)),
    ) == expected


@pytest.mark.parametrize(
    "coverage_state",
    [
        HealthCoverageState.stale,
        HealthCoverageState.incomplete,
        HealthCoverageState.insufficient,
    ],
)
def test_unreliable_coverage_overrides_flag_health(
    coverage_state: HealthCoverageState,
) -> None:
    coverage = reliable_coverage(datetime(2026, 7, 29, tzinfo=UTC)).model_copy(
        update={"state": coverage_state, "reasons": ["Coverage is not reliable."]}
    )
    flag = HealthFlag(
        fingerprint="critical:test",
        area="test",
        severity=Severity.critical,
        title="Critical flag",
        explanation="Test evidence.",
        raised_at=datetime(2026, 7, 29, tzinfo=UTC),
    )

    assert DashboardQuery._health_state([flag], coverage) == HealthState.unknown


@pytest.mark.parametrize(
    ("source_age", "board_count", "status", "expected"),
    [
        (timedelta(hours=1), 1, "In Progress", HealthCoverageState.reliable),
        (timedelta(hours=25), 1, "In Progress", HealthCoverageState.stale),
        (timedelta(hours=1), 0, None, HealthCoverageState.insufficient),
        (timedelta(hours=1), 1, None, HealthCoverageState.incomplete),
    ],
)
def test_health_coverage_is_snapshot_safe(
    source_age: timedelta,
    board_count: int,
    status: str | None,
    expected: HealthCoverageState,
) -> None:
    snapshot_at = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)
    source_state = SnapshotSourceState(
        snapshot_id="snapshot",
        source="jira",
        scope="board:2168",
        high_water_mark=snapshot_at - source_age,
        ingestion_run_id="run",
    )
    team_records = (
        [
            (
                JiraIssue(
                    id="issue",
                    issue_key="IDN-1",
                    self_url="https://example.test/rest/IDN-1",
                    web_url="https://example.test/browse/IDN-1",
                    project_key="IDN",
                    first_seen_at=snapshot_at,
                    last_seen_at=snapshot_at,
                    current_version_hash="hash",
                    is_deleted=False,
                ),
                JiraIssueVersion(
                    id="version",
                    issue_id="issue",
                    observed_at=snapshot_at,
                    version_hash="hash",
                    summary="Feature",
                    status_name=status,
                    labels=[],
                    components=[],
                    fix_versions=[],
                ),
            )
        ]
        if board_count
        else []
    )

    coverage = DashboardQuery._health_coverage(
        snapshot_created_at=snapshot_at,
        source_state=source_state,
        board_record_count=board_count,
        team_records=team_records,
    )

    assert coverage.state == expected
    assert coverage.age_at_snapshot_seconds == int(source_age.total_seconds())


def test_missing_ibr_source_returns_insufficient_coverage() -> None:
    coverage = DashboardQuery._health_coverage(
        snapshot_created_at=datetime(2026, 7, 29, 18, 0, tzinfo=UTC),
        source_state=None,
        board_record_count=0,
        team_records=[],
    )

    assert coverage.state == HealthCoverageState.insufficient
    assert coverage.observed_at is None
    assert coverage.age_at_snapshot_seconds is None
    assert coverage.reasons == ["The snapshot has no successful IBR board ingestion."]


def test_flag_dedup_view_resolve_and_reflag(tmp_path: Path) -> None:
    database = tmp_path / "engintel.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    service = FlagService(sessions)
    first_at = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    add_snapshot(sessions, "snapshot-1", first_at)
    first = service.record_dashboard(dashboard("snapshot-1", first_at))
    service.record_dashboard(dashboard("snapshot-1", first_at))
    service.mark_viewed("a2a:work-in-flight:none", viewed_at=first_at + timedelta(minutes=1))
    viewed = service.record_dashboard(dashboard("snapshot-1", first_at))

    second_at = first_at + timedelta(hours=1)
    add_snapshot(sessions, "snapshot-2", second_at)
    service.record_dashboard(dashboard("snapshot-2", second_at, with_flag=False))

    third_at = second_at + timedelta(hours=1)
    add_snapshot(sessions, "snapshot-3", third_at)
    third = service.record_dashboard(dashboard("snapshot-3", third_at))

    with sessions() as session:
        logical = session.get(LogicalFlag, "a2a:work-in-flight:none")
        state = session.get(FlagUserState, "a2a:work-in-flight:none")
        assert logical is not None and logical.active is True
        assert state is not None and state.unread_since is not None
        assert session.scalar(select(func.count()).select_from(FlagOccurrence)) == 2
        assert session.scalar(select(func.count()).select_from(FlagEvent)) == 3
        assert session.scalar(select(func.count()).select_from(SignalDefinition)) == 13
        assert session.scalar(select(func.count()).select_from(SignalEvaluation)) == 6
        first_evaluations = session.scalars(
            select(SignalEvaluation).where(
                SignalEvaluation.snapshot_id == "snapshot-1"
            )
        ).all()
        assert {evaluation.condition_met for evaluation in first_evaluations} == {
            True,
            False,
        }
        assert {
            evaluation.severity for evaluation in first_evaluations
        } == {"watch", None}
        assert {evaluation.confidence for evaluation in first_evaluations} == {"high"}
    assert first.teams[0].flags[0].occurrence_id is not None
    assert first.teams[0].flags[0].signal_definition_key == "team-no-work-in-progress"
    assert first.teams[0].flags[0].signal_definition_version == "1.0.0"
    assert first.teams[0].flags[0].signal_evaluation_id is not None
    assert first.teams[0].flags[0].confidence == "high"
    assert viewed.teams[0].flags[0].unread is False
    assert third.teams[0].flags[0].unread is True

    inbox = AttentionQuery(sessions).list(
        collection=AttentionCollection.active,
        now=third_at + timedelta(minutes=5),
    )
    assert inbox.counts == {
        "active": 1,
        "snoozed": 0,
        "understood": 0,
        "resolved": 0,
    }
    assert len(inbox.flags) == 1
    attention_flag = inbox.flags[0]
    assert attention_flag.unread is True
    assert attention_flag.active_duration_seconds == 300
    assert attention_flag.evidence_count == 1
    assert attention_flag.signal_definition_key == "team-no-work-in-progress"
    assert attention_flag.signal_definition_version == "1.0.0"
    assert attention_flag.signal_evaluation_id is not None
    assert len(attention_flag.occurrences) == 2
    assert [event.event_type for event in attention_flag.occurrences[0].events] == [
        "opened"
    ]
    assert "No IBR items in progress" in render_attention_markdown(inbox)
    assert "Investigation questions" in render_attention_flag_markdown(attention_flag)

    resolved = AttentionQuery(sessions).list(
        collection=AttentionCollection.resolved,
        now=second_at + timedelta(minutes=1),
    )
    # Current collection state is durable and the condition has since reopened.
    assert resolved.flags == []


def test_unknown_coverage_does_not_resolve_existing_flags(tmp_path: Path) -> None:
    database = tmp_path / "engintel.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    service = FlagService(sessions)
    first_at = datetime(2026, 7, 29, 16, 0, tzinfo=UTC)
    add_snapshot(sessions, "snapshot-reliable", first_at)
    service.record_dashboard(dashboard("snapshot-reliable", first_at))

    unknown_at = first_at + timedelta(hours=25)
    add_snapshot(sessions, "snapshot-unknown", unknown_at)
    unknown = dashboard("snapshot-unknown", unknown_at, with_flag=False)
    unknown_team = unknown.teams[0]
    unknown_team.health = HealthState.unknown
    unknown_team.health_coverage = reliable_coverage(first_at).model_copy(
        update={
            "state": HealthCoverageState.stale,
            "reasons": ["The source is stale."],
            "age_at_snapshot_seconds": 90000,
        }
    )
    unknown_team.signal_evaluation_inputs = []
    service.record_dashboard(unknown)

    with sessions() as session:
        logical = session.get(LogicalFlag, "a2a:work-in-flight:none")
        assert logical is not None
        assert logical.active is True
        assert session.scalar(select(func.count()).select_from(FlagOccurrence)) == 1
        assert session.scalar(select(func.count()).select_from(FlagEvent)) == 1


def test_signal_evaluation_is_immutable_per_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "engintel.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    service = FlagService(sessions)
    evaluated_at = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)
    add_snapshot(sessions, "snapshot-1", evaluated_at)
    service.record_dashboard(dashboard("snapshot-1", evaluated_at))

    changed = dashboard("snapshot-1", evaluated_at, with_flag=False)
    with pytest.raises(ValueError, match="immutable"):
        service.record_dashboard(changed)

    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SignalEvaluation)) == 2


def test_feature_risk_signals_persist_triggered_and_clear_results(tmp_path: Path) -> None:
    database = tmp_path / "engintel.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    evaluated_at = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)
    risky = WorkItem(
        jira_id="100001",
        jira_key="IDN-1",
        title="Risky Feature",
        status="In Progress",
        url="https://example.test/browse/IDN-1",
        assignee_account_id=None,
        source_updated_at=datetime(2026, 7, 1, tzinfo=UTC),
        target_date=date(2026, 7, 20),
    )
    clear = WorkItem(
        jira_id="100002",
        jira_key="IDN-2",
        title="Healthy Feature",
        status="In Progress",
        url="https://example.test/browse/IDN-2",
        assignee_account_id="person-1",
        source_updated_at=datetime(2026, 7, 28, tzinfo=UTC),
        target_date=date(2026, 8, 15),
    )
    flags, evaluations = DashboardQuery._feature_signals(
        "a2a",
        [risky, clear],
        evaluated_at,
    )
    assert {flag.area for flag in flags} == {
        "stalled_work",
        "target_risk",
        "ownership_gap",
    }
    assert len(evaluations) == 6
    assert sum(item.condition_met for item in evaluations) == 3

    add_snapshot(sessions, "feature-snapshot", evaluated_at)
    payload = Dashboard(
        snapshot_id="feature-snapshot",
        snapshot_name="feature-snapshot",
        snapshot_created_at=evaluated_at,
        source_freshness=[],
        teams=[
            TeamDashboardRow(
                team_id="a2a",
                team_name="A2A",
                health=HealthState.watch,
                health_coverage=reliable_coverage(evaluated_at),
                flags=flags,
                most_recently_completed=None,
                in_progress=[risky, clear],
                ready_for_build=[],
                signal_evaluation_inputs=evaluations,
            )
        ],
    )
    FlagService(sessions).record_dashboard(payload)

    with sessions() as session:
        persisted = session.scalars(
            select(SignalEvaluation).where(
                SignalEvaluation.snapshot_id == "feature-snapshot"
            )
        ).all()
        assert len(persisted) == 6
        assert sum(item.condition_met for item in persisted) == 3
        assert {item.scope_id for item in persisted} == {"IDN-1", "IDN-2"}
    inbox = AttentionQuery(sessions).list(now=evaluated_at)
    assert len(inbox.flags) == 3
    assert {
        flag.signal_definition_key for flag in inbox.flags
    } == {
        "feature-stalled-active-work",
        "feature-target-date-overdue",
        "feature-active-ownership-gap",
    }
    assert all("feature:IDN-1" in flag.affected_entities for flag in inbox.flags)


def test_history_signals_detect_regression_cycling_and_baseline_aging() -> None:
    evaluated_at = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)

    def transition(
        identifier: str,
        issue_id: str,
        days_ago: int,
        from_status: str,
        to_status: str,
    ) -> JiraStatusTransition:
        return JiraStatusTransition(
            id=identifier,
            issue_id=issue_id,
            changelog_id=identifier,
            item_index=0,
            changed_at=evaluated_at - timedelta(days=days_ago),
            author_account_id=None,
            author_display_name=None,
            from_status_id=None,
            from_status_name=from_status,
            to_status_id=None,
            to_status_name=to_status,
            first_seen_at=evaluated_at,
            last_seen_at=evaluated_at,
        )

    risky = WorkItem(
        jira_id="risk",
        jira_key="IDN-1",
        title="Cycling Feature",
        status="In Code Review",
        url="https://example.test/browse/IDN-1",
        source_created_at=evaluated_at - timedelta(days=60),
    )
    risky_transitions = [
        transition("r1", "risk", 40, "In Progress", "In Code Review"),
        transition("r2", "risk", 35, "In Code Review", "In Progress"),
        transition("r3", "risk", 30, "In Progress", "In Code Review"),
        transition("r4", "risk", 25, "In Code Review", "In Progress"),
        transition("r5", "risk", 20, "In Progress", "In Code Review"),
    ]
    baseline_items = []
    transitions_by_issue = {"risk": risky_transitions}
    for index in range(5):
        issue_id = f"baseline-{index}"
        baseline_items.append(
            WorkItem(
                jira_id=issue_id,
                jira_key=f"IDN-{index + 2}",
                title="Baseline Feature",
                status="Ready for Test",
                url=f"https://example.test/browse/IDN-{index + 2}",
                source_created_at=evaluated_at - timedelta(days=30),
            )
        )
        transitions_by_issue[issue_id] = [
            transition(
                f"{issue_id}-1",
                issue_id,
                12 + index,
                "In Progress",
                "In Code Review",
            ),
            transition(
                f"{issue_id}-2",
                issue_id,
                10 + index,
                "In Code Review",
                "Ready for Test",
            ),
        ]

    flags, evaluations = DashboardQuery._history_signals(
        "a2a",
        [risky, *baseline_items],
        transitions_by_issue,
        evaluated_at,
    )

    assert {flag.area for flag in flags} == {
        "workflow_regression",
        "workflow_cycling",
        "stage_aging",
    }
    risky_evaluations = [
        item for item in evaluations if item.scope_id == "IDN-1"
    ]
    assert len(risky_evaluations) == 3
    assert all(item.condition_met for item in risky_evaluations)
    aging = next(
        item
        for item in risky_evaluations
        if item.definition_key == "feature-stage-aging-vs-team"
    )
    assert aging.sample_size == 5
    assert aging.baseline == {
        "median_days": 2.0,
        "window_days": 90,
        "multiplier": 1.5,
    }


def test_data_quality_signals_separate_missing_evidence_from_clear_results() -> None:
    evaluated_at = datetime(2026, 7, 29, 18, 0, tzinfo=UTC)
    missing = WorkItem(
        jira_id="missing",
        jira_key="IDN-10",
        title="Feature with missing evidence",
        status="In Progress",
        url="https://example.test/browse/IDN-10",
    )
    complete = WorkItem(
        jira_id="complete",
        jira_key="IDN-11",
        title="Feature with complete evidence",
        status="In Progress",
        url="https://example.test/browse/IDN-11",
        source_created_at=evaluated_at - timedelta(days=10),
        source_updated_at=evaluated_at - timedelta(days=1),
    )
    transition = JiraStatusTransition(
        id="transition",
        issue_id="complete",
        changelog_id="transition",
        item_index=0,
        changed_at=evaluated_at - timedelta(days=5),
        author_account_id=None,
        author_display_name=None,
        from_status_id=None,
        from_status_name="Ready for Build",
        to_status_id=None,
        to_status_name="In Progress",
        first_seen_at=evaluated_at,
        last_seen_at=evaluated_at,
    )

    flags, evaluations = DashboardQuery._data_quality_signals(
        "a2a",
        [missing, complete],
        {"complete": [transition]},
        evaluated_at,
    )

    assert {flag.area for flag in flags} == {
        "transition_evidence",
        "required_evidence",
    }
    assert all(flag.evidence[0].jira_key == "IDN-10" for flag in flags)
    assert len(evaluations) == 4
    missing_results = [
        evaluation
        for evaluation in evaluations
        if evaluation.scope_id == "IDN-10"
    ]
    complete_results = [
        evaluation
        for evaluation in evaluations
        if evaluation.scope_id == "IDN-11"
    ]
    assert all(evaluation.condition_met for evaluation in missing_results)
    assert all(not evaluation.condition_met for evaluation in complete_results)
    assert DashboardQuery._history_signals(
        "a2a",
        [missing],
        {},
        evaluated_at,
    ) == ([], [])
