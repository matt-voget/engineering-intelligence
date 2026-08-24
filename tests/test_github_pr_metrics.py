from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

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
        SimpleNamespace(
            submitted_at=created + timedelta(hours=1), author_login="author"
        ),
        SimpleNamespace(
            submitted_at=created + timedelta(hours=2), author_login="review-bot[bot]"
        ),
        SimpleNamespace(
            submitted_at=created + timedelta(hours=3), author_login="reviewer"
        ),
        SimpleNamespace(
            submitted_at=created + timedelta(hours=5), author_login="second"
        ),
    ]

    assert _measure(version, reviews) == (
        created + timedelta(hours=3),
        3.0,
        7.0,
        ["reviewer", "second"],
    )


def test_measure_requires_merged_non_draft_pr_with_review() -> None:
    created = datetime(2026, 1, 1, tzinfo=UTC)
    assert _measure(
        SimpleNamespace(
            draft=True,
            merged_at=created + timedelta(hours=1),
            source_created_at=created,
            author_login="author",
        ),
        [],
    ) is None


def test_author_scope_is_case_insensitive_and_requires_team_identity() -> None:
    assert _author_in_scope("TeamMember", {"teammember"}) is True
    assert _author_in_scope("outsider", {"teammember"}) is False
    assert _author_in_scope(None, {"teammember"}) is False
