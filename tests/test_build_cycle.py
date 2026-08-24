from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from engineering_intelligence.queries.build_cycle import (
    _cycle,
    _eligible_issue,
    workflow_cycle_metrics,
)


def transition(at: datetime, from_status: str, to_status: str):
    return SimpleNamespace(
        changed_at=at,
        from_status_name=from_status,
        to_status_name=to_status,
    )


def timeline(*transitions):
    return SimpleNamespace(transitions=list(transitions))


def test_cycle_uses_first_in_progress_to_first_subsequent_done() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    result = _cycle(
        timeline(
            transition(started - timedelta(days=2), "In Progress", "Done"),
            transition(started, "Done", "In Progress"),
            transition(started + timedelta(days=2), "In Progress", "In Code Review"),
            transition(started + timedelta(days=5), "In Code Review", "Done"),
        )
    )

    assert result is not None
    cycle_started, cycle_ended, days, durations = result
    assert cycle_started == started
    assert cycle_ended == started + timedelta(days=5)
    assert days == 5.0
    assert [(item.status, item.days) for item in durations] == [
        ("In Code Review", 3.0),
        ("In Progress", 2.0),
    ]


def test_cycle_requires_a_done_transition_after_in_progress() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)

    assert _cycle(timeline(transition(started, "To Do", "In Progress"))) is None


def test_cycle_can_identify_a_zero_day_transition_for_population_filtering() -> None:
    at = datetime(2026, 1, 1, tzinfo=UTC)
    result = _cycle(
        timeline(
            transition(at, "To Do", "In Progress"),
            transition(at, "In Progress", "Done"),
        )
    )

    assert result is not None
    assert result[2] == 0.0


def test_non_ibr_accepts_all_issue_types_but_ibr_is_parent_only() -> None:
    assert _eligible_issue("non_ibr", "Bug") is True
    assert _eligible_issue("non_ibr", "Story") is True
    assert _eligible_issue("ibr_linked", "Epic") is True
    assert _eligible_issue("ibr_linked", "Feature Request") is True
    assert _eligible_issue("ibr_linked", "FDI Request") is True
    assert _eligible_issue("ibr_linked", "Bug") is False


def test_workflow_cycle_metrics_breaks_out_requested_phases() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    result = workflow_cycle_metrics(
        timeline(
            transition(started, "To Do", "In Progress"),
            transition(started + timedelta(days=2), "In Progress", "In Code Review"),
            transition(started + timedelta(days=3), "In Code Review", "Ready for Test"),
            transition(started + timedelta(days=4), "Ready for Test", "In Testing"),
            transition(started + timedelta(days=5), "In Testing", "Ready for Docs"),
            transition(started + timedelta(days=6), "Ready for Docs", "Done"),
        ),
        started + timedelta(days=10),
    )

    assert result is not None
    assert result.total_days == 6.0
    assert result.in_progress_days == 2.0
    assert result.in_review_days == 1.0
    assert result.in_test_days == 2.0
    assert result.skipped_phases == []


def test_workflow_cycle_metrics_is_running_and_identifies_skipped_steps() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    result = workflow_cycle_metrics(
        timeline(
            transition(started, "To Do", "In Progress"),
            transition(started + timedelta(days=2), "In Progress", "In Testing"),
        ),
        started + timedelta(days=5),
    )

    assert result is not None
    assert result.total_days == 5.0
    assert result.in_progress_days == 2.0
    assert result.in_review_days == 0.0
    assert result.in_test_days == 3.0
    assert result.skipped_phases == ["In Code Review", "Ready for Test"]
