from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from engineering_intelligence.queries.github_finder import (
    _coding_boundary,
    _coding_hours,
    _commit_boundary,
)
from engineering_intelligence.queries.github_pr_metrics import _author_in_scope, _measure


def test_measure_uses_first_non_author_non_bot_review() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    merged = created + timedelta(hours=10)
    version = SimpleNamespace(
        draft=False,
        merged_at=merged,
        source_created_at=created,
        author_login="author",
    )
    reviews = [
        SimpleNamespace(submitted_at=created + timedelta(hours=1), author_login="author"),
        SimpleNamespace(submitted_at=created + timedelta(hours=2), author_login="review-bot[bot]"),
        SimpleNamespace(submitted_at=created + timedelta(hours=3), author_login="reviewer"),
        SimpleNamespace(submitted_at=created + timedelta(hours=5), author_login="second"),
    ]

    assert _measure(version, reviews) == (
        created + timedelta(hours=3),
        3.0,
        7.0,
        ["reviewer", "second"],
    )


def test_measure_requires_merged_non_draft_pr_with_review() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    assert (
        _measure(
            SimpleNamespace(
                draft=True,
                merged_at=created + timedelta(hours=1),
                source_created_at=created,
                author_login="author",
            ),
            [],
        )
        is None
    )


def test_author_scope_is_case_insensitive_and_requires_team_identity() -> None:
    assert _author_in_scope("TeamMember", {"teammember"}) is True
    assert _author_in_scope("outsider", {"teammember"}) is False
    assert _author_in_scope(None, {"teammember"}) is False


def test_coding_hours_is_first_commit_to_pr_creation() -> None:
    created = datetime(2026, 1, 2, tzinfo=UTC)
    assert _coding_hours(created, created - timedelta(hours=30)) == 30.0
    assert _coding_hours(created, created + timedelta(hours=1)) == 0.0
    assert _coding_hours(created, None) is None


def test_coding_boundary_uses_commit_timestamp_not_authored_timestamp() -> None:
    old_authored = datetime(2025, 1, 1, tzinfo=UTC)
    committed = datetime(2026, 1, 1, tzinfo=UTC)
    assert (
        _commit_boundary(SimpleNamespace(authored_at=old_authored, committed_at=committed))
        == committed
    )


def test_coding_boundary_prefers_primary_jira_commit_and_falls_back() -> None:
    old = datetime(2026, 1, 1, tzinfo=UTC)
    primary = datetime(2026, 1, 3, tzinfo=UTC)
    version = SimpleNamespace(
        title="Deliver fleet",
        head_ref="feature/edge-121",
        body="",
    )
    commits = [
        SimpleNamespace(message="Unrelated work", committed_at=old),
        SimpleNamespace(message="Finish EDGE-121", committed_at=primary),
    ]
    assert _coding_boundary(version, commits, {"EDGE-121"}) == (
        primary,
        "primary_jira_key",
        "EDGE-121",
    )
    assert _coding_boundary(version, commits[:1], {"EDGE-121"}) == (
        old,
        "fallback_no_matching_jira_commit",
        "EDGE-121",
    )
