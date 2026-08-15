"""Small read-only GitHub REST client with bounded pagination."""

import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any, Self

import httpx


class GitHubClient:
    def __init__(
        self,
        api_url: str,
        token: str,
        *,
        timeout_seconds: float = 30,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=api_url.rstrip("/"),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_repository(self, full_name: str) -> dict[str, Any]:
        return self._get_json(f"/repos/{full_name}")

    def iter_pull_requests(
        self,
        full_name: str,
        *,
        updated_since: datetime,
        max_records: int,
        min_updated_since: datetime | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield pull requests updated since ``updated_since``, newest first.

        ``max_records`` bounds the scan, except that every record updated at or
        after ``min_updated_since`` is always yielded so one bounded run still
        covers the guaranteed re-verification window on busy repositories.
        """
        for index, payload in enumerate(
            self._iter_list(
                f"/repos/{full_name}/pulls",
                params={
                    "state": "all",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": 100,
                },
            )
        ):
            updated_at = datetime.fromisoformat(payload["updated_at"])
            if updated_at < updated_since:
                break
            inside_guaranteed_window = (
                min_updated_since is not None and updated_at >= min_updated_since
            )
            if index >= max_records and not inside_guaranteed_window:
                break
            yield payload

    def iter_pull_request_commits(
        self,
        full_name: str,
        number: int,
    ) -> Iterator[dict[str, Any]]:
        yield from self._iter_list(
            f"/repos/{full_name}/pulls/{number}/commits",
            params={"per_page": 100},
        )

    def iter_pull_request_reviews(
        self,
        full_name: str,
        number: int,
    ) -> Iterator[dict[str, Any]]:
        yield from self._iter_list(
            f"/repos/{full_name}/pulls/{number}/reviews",
            params={"per_page": 100},
        )

    def _iter_list(
        self,
        path: str,
        *,
        params: dict[str, Any],
    ) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            payload = self._get_json(path, params={**params, "page": page})
            if not isinstance(payload, list):
                raise TypeError(f"Expected a GitHub list response for {path}")
            yield from payload
            if len(payload) < int(params.get("per_page", 100)):
                break
            page += 1

    def _get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        attempt = 0
        while True:
            response = self._client.get(path, params=params)
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
                return response.json()
            attempt += 1
            if attempt > self.max_retries:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            delay = min(float(retry_after) if retry_after else 2 ** (attempt - 1), 30)
            time.sleep(delay)
