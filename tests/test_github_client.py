from datetime import UTC, datetime, timedelta

import httpx

from engineering_intelligence.ingestion.github.client import GitHubClient

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _pull(number: int, updated_at: datetime) -> dict[str, object]:
    return {
        "id": number,
        "number": number,
        "updated_at": updated_at.isoformat(),
    }


def _client_with_pulls(pulls: list[dict[str, object]]) -> GitHubClient:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        per_page = int(request.url.params.get("per_page", "100"))
        start = (page - 1) * per_page
        return httpx.Response(200, json=pulls[start : start + per_page])

    return GitHubClient(
        "https://api.github.com",
        "token",
        transport=httpx.MockTransport(handler),
    )


def test_iter_pull_requests_stops_at_updated_since() -> None:
    pulls = [
        _pull(3, NOW - timedelta(days=1)),
        _pull(2, NOW - timedelta(days=10)),
        _pull(1, NOW - timedelta(days=120)),
    ]
    with _client_with_pulls(pulls) as client:
        numbers = [
            payload["number"]
            for payload in client.iter_pull_requests(
                "gravitee-io/example",
                updated_since=NOW - timedelta(days=90),
                max_records=500,
            )
        ]
    assert numbers == [3, 2]


def test_max_records_caps_records_older_than_guaranteed_window() -> None:
    pulls = [
        _pull(5, NOW - timedelta(days=2)),
        _pull(4, NOW - timedelta(days=40)),
        _pull(3, NOW - timedelta(days=50)),
        _pull(2, NOW - timedelta(days=60)),
        _pull(1, NOW - timedelta(days=70)),
    ]
    with _client_with_pulls(pulls) as client:
        numbers = [
            payload["number"]
            for payload in client.iter_pull_requests(
                "gravitee-io/example",
                updated_since=NOW - timedelta(days=90),
                max_records=2,
                min_updated_since=NOW - timedelta(days=31),
            )
        ]
    assert numbers == [5, 4]


def test_guaranteed_window_overrides_max_records_on_busy_repository() -> None:
    pulls = [_pull(number, NOW - timedelta(days=number)) for number in range(1, 26)]
    with _client_with_pulls(pulls) as client:
        numbers = [
            payload["number"]
            for payload in client.iter_pull_requests(
                "gravitee-io/example",
                updated_since=NOW - timedelta(days=90),
                max_records=5,
                min_updated_since=NOW - timedelta(days=31),
            )
        ]
    # Every pull updated inside the guaranteed 31-day window is yielded even
    # though max_records is 5; the cap only stops the scan beyond the window.
    assert numbers == list(range(1, 26))


def test_without_min_window_max_records_caps_immediately() -> None:
    pulls = [_pull(number, NOW - timedelta(days=number)) for number in range(1, 26)]
    with _client_with_pulls(pulls) as client:
        numbers = [
            payload["number"]
            for payload in client.iter_pull_requests(
                "gravitee-io/example",
                updated_since=NOW - timedelta(days=90),
                max_records=5,
            )
        ]
    assert numbers == [1, 2, 3, 4, 5]
