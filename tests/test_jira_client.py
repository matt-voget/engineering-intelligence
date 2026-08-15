import httpx

from engineering_intelligence.ingestion.jira.client import JiraClient


def test_child_search_uses_bounded_enhanced_jql_pagination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "issues": [{"id": "2", "key": "IDN-2", "fields": {}}],
                    "nextPageToken": "next-token",
                    "isLast": False,
                },
            )
        return httpx.Response(
            200,
            json={
                "issues": [{"id": "3", "key": "IDN-3", "fields": {}}],
                "isLast": True,
            },
        )

    with JiraClient(
        "https://example.atlassian.net",
        "owner@example.com",
        "token",
        transport=httpx.MockTransport(handler),
    ) as client:
        issues = list(
            client.iter_child_issues(
                ["IDN-1"],
                fields=["summary", "parent"],
            )
        )

    assert [issue["key"] for issue in issues] == ["IDN-2", "IDN-3"]
    assert requests[0].url.path == "/rest/api/3/search/jql"
    assert 'parent in ("IDN-1")' in requests[0].url.params["jql"]
    assert requests[1].url.params["nextPageToken"] == "next-token"


def test_bulk_changelog_fetch_uses_status_filter_and_pagination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                json={
                    "issueChangeLogs": [{"issueId": "1", "changeHistories": []}],
                    "nextPageToken": "more",
                },
            )
        return httpx.Response(
            200,
            json={"issueChangeLogs": [{"issueId": "2", "changeHistories": []}]},
        )

    with JiraClient(
        "https://example.atlassian.net",
        "owner@example.com",
        "token",
        transport=httpx.MockTransport(handler),
    ) as client:
        changelogs = list(client.iter_issue_changelogs(["1", "2"]))

    assert [item["issueId"] for item in changelogs] == ["1", "2"]
    assert all(request.method == "POST" for request in requests)
    assert requests[0].url.path == "/rest/api/3/changelog/bulkfetch"
    assert requests[0].read().decode().count('"status"') == 1
    assert '"nextPageToken":"more"' in requests[1].read().decode()


def test_named_jql_scope_uses_bounded_pagination() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"issues": [{"id": "1", "key": "A-1"}], "isLast": True},
        )

    with JiraClient(
        "https://example.atlassian.net",
        "owner@example.com",
        "token",
        transport=httpx.MockTransport(handler),
    ) as client:
        issues = list(
            client.iter_jql_issues("project = A", fields=["summary", "status"])
        )

    assert issues[0]["key"] == "A-1"
    assert requests[0].url.params["jql"] == "project = A"
    assert requests[0].url.params["maxResults"] == "100"
