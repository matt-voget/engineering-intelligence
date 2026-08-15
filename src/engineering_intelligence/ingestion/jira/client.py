"""Small read-only Jira Cloud REST client."""

import re
import time
from collections.abc import Iterator
from typing import Any, Self

import httpx


class JiraClient:
    """Access the Jira Agile API with bounded pagination and retries."""

    def __init__(
        self,
        base_url: str,
        email: str,
        token: str,
        *,
        timeout_seconds: float = 30,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.max_retries = max_retries
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            auth=httpx.BasicAuth(email, token),
            headers={"Accept": "application/json"},
            timeout=timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_board_configuration(self, board_id: int) -> dict[str, Any]:
        return self._get_json(f"/rest/agile/1.0/board/{board_id}/configuration")

    def iter_board_issues(
        self,
        board_id: int,
        *,
        page_size: int = 50,
        fields: list[str] | None = None,
    ) -> Iterator[dict[str, Any]]:
        start_at = 0
        requested_fields = fields or [
            "summary",
            "issuetype",
            "status",
            "assignee",
            "project",
            "created",
            "updated",
            "resolutiondate",
            "parent",
            "subtasks",
            "issuelinks",
            "labels",
            "components",
            "fixVersions",
        ]
        while True:
            page = self._get_json(
                f"/rest/agile/1.0/board/{board_id}/issue",
                params={
                    "startAt": start_at,
                    "maxResults": page_size,
                    "fields": ",".join(requested_fields),
                },
            )
            issues = page.get("issues", [])
            yield from issues
            start_at += len(issues)
            total = int(page.get("total", start_at))
            if not issues or start_at >= total:
                break

    def get_issue(self, issue_key: str, *, expand_changelog: bool = True) -> dict[str, Any]:
        params = {"expand": "changelog"} if expand_changelog else None
        return self._get_json(f"/rest/api/3/issue/{issue_key}", params=params)

    def iter_child_issues(
        self,
        parent_keys: list[str],
        *,
        fields: list[str],
        page_size: int = 100,
    ) -> Iterator[dict[str, Any]]:
        if not parent_keys:
            return
        invalid = [key for key in parent_keys if not re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", key)]
        if invalid:
            raise ValueError(f"Invalid Jira keys for hierarchy query: {invalid}")
        quoted = ", ".join(f'"{key}"' for key in parent_keys)
        next_page_token: str | None = None
        while True:
            params: dict[str, str | int] = {
                "jql": f"parent in ({quoted}) ORDER BY key ASC",
                "maxResults": page_size,
                "fields": ",".join(fields),
            }
            if next_page_token:
                params["nextPageToken"] = next_page_token
            page = self._get_json("/rest/api/3/search/jql", params=params)
            yield from page.get("issues", [])
            next_page_token = page.get("nextPageToken")
            if page.get("isLast", not next_page_token) or not next_page_token:
                break

    def iter_jql_issues(
        self,
        jql: str,
        *,
        fields: list[str],
        page_size: int = 100,
    ) -> Iterator[dict[str, Any]]:
        """Execute one configured JQL scope through bounded enhanced pagination."""
        if not jql.strip():
            raise ValueError("JQL must not be empty")
        next_page_token: str | None = None
        while True:
            params: dict[str, str | int] = {
                "jql": jql,
                "maxResults": page_size,
                "fields": ",".join(fields),
            }
            if next_page_token:
                params["nextPageToken"] = next_page_token
            page = self._get_json("/rest/api/3/search/jql", params=params)
            yield from page.get("issues", [])
            next_page_token = page.get("nextPageToken")
            if page.get("isLast", not next_page_token) or not next_page_token:
                break

    def iter_issue_changelogs(
        self,
        issue_ids_or_keys: list[str],
        *,
        field_ids: list[str] | None = None,
        issue_batch_size: int = 1000,
        page_size: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        """Bulk-fetch oldest-first changelogs for at most 1000 issues per request."""
        for start in range(0, len(issue_ids_or_keys), issue_batch_size):
            issue_batch = issue_ids_or_keys[start : start + issue_batch_size]
            next_page_token: str | None = None
            while True:
                body: dict[str, Any] = {
                    "issueIdsOrKeys": issue_batch,
                    "fieldIds": field_ids or ["status"],
                    "maxResults": page_size,
                }
                if next_page_token:
                    body["nextPageToken"] = next_page_token
                page = self._post_json("/rest/api/3/changelog/bulkfetch", json=body)
                yield from page.get("issueChangeLogs", [])
                next_page_token = page.get("nextPageToken")
                if not next_page_token:
                    break

    def _get_json(
        self,
        path: str,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, Any]:
        return self._request_json("GET", path, params=params)

    def _post_json(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", path, json=json)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            response = self._client.request(method, path, params=params, json=json)
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
                return response.json()
            attempt += 1
            if attempt > self.max_retries:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            delay = min(float(retry_after) if retry_after else 2 ** (attempt - 1), 30)
            time.sleep(delay)
