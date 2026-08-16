import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engineering_intelligence.config import SourceConfig, TeamsConfig
from engineering_intelligence.ingestion.archive import RawPayloadArchive
from engineering_intelligence.ingestion.github.service import GitHubIngestionService
from engineering_intelligence.ingestion.jira.service import JiraIngestionService
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
    upgrade_database,
)
from engineering_intelligence.queries.team_work import TeamWorkQuery
from engineering_intelligence.snapshots import SnapshotService

IBR_SOURCES = SourceConfig.model_validate({
    "jira": {"base_url": "https://gravitee.atlassian.net", "boards": [
        {"id": 2168, "name": "IBR", "role": "ibr"}
    ]}
})


FIXTURES = Path(__file__).parent / "fixtures/jira"


class JiraClient:
    """IDN-1 sits on the IBR board; IDN-2 (its child) and ES-99 carry the team field."""

    def get_board_configuration(self, _board_id: int) -> dict[str, Any]:
        return json.loads((FIXTURES / "board_2168.json").read_text())

    def iter_board_issues(self, _board_id, *, fields=None) -> list[dict[str, Any]]:
        return [json.loads((FIXTURES / "issue_idn_1.json").read_text())]

    def iter_child_issues(self, parent_keys, *, fields) -> list[dict[str, Any]]:
        children = {
            "IDN-1": json.loads((FIXTURES / "issue_idn_2.json").read_text()),
            "IDN-2": json.loads((FIXTURES / "issue_idn_3.json").read_text()),
        }
        return [children[key] for key in parent_keys if key in children]

    def iter_issue_changelogs(self, _issue_ids_or_keys, *, field_ids=None):
        return []

    def iter_jql_issues(self, _jql: str, *, fields=None):
        child = json.loads((FIXTURES / "issue_idn_2.json").read_text())
        bug = json.loads((FIXTURES / "issue_idn_2.json").read_text())
        bug["id"] = "99999"
        bug["key"] = "ES-99"
        bug["self"] = "https://gravitee.atlassian.net/rest/api/3/issue/99999"
        bug["fields"]["summary"] = "Maintain Edge Stack release tooling"
        bug["fields"]["issuetype"] = {"id": "10004", "name": "Bug"}
        bug["fields"]["parent"] = None
        return [child, bug]


class GitHubClient:
    def get_repository(self, _full_name: str) -> dict[str, Any]:
        return {
            "id": 42,
            "full_name": "gravitee-io/example",
            "html_url": "https://github.com/gravitee-io/example",
            "default_branch": "main",
            "private": True,
            "archived": False,
        }

    def iter_pull_requests(
        self,
        _full_name: str,
        *,
        updated_since: datetime,
        max_records: int,
        min_updated_since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        def pull(number: int, pull_id: int, title: str) -> dict[str, Any]:
            return {
                "id": pull_id,
                "number": number,
                "html_url": f"https://github.com/gravitee-io/example/pull/{number}",
                "title": title,
                "body": "",
                "state": "closed",
                "draft": False,
                "user": {"login": "alex"},
                "head": {"ref": f"feature/pr-{number}"},
                "base": {"ref": "main"},
                "created_at": "2026-07-20T10:00:00Z",
                "updated_at": "2026-07-28T15:00:00Z",
                "closed_at": "2026-07-28T15:00:00Z",
                "merged_at": "2026-07-28T15:00:00Z",
                "merge_commit_sha": None,
            }

        return [
            pull(17, 9001, "IDN-2 Ship the team health projection"),
            pull(18, 9002, "Refactor build scripts"),
            pull(19, 9003, "ES-99 Fix release tooling"),
        ]

    def iter_pull_request_commits(self, _full_name: str, number: int):
        if number != 17:
            return []
        return [
            {
                "sha": "abc123",
                "html_url": "https://github.com/gravitee-io/example/commit/abc123",
                "author": {"login": "alex"},
                "commit": {
                    "message": "IDN-1 add deterministic output",
                    "author": {"name": "Alex Kim", "date": "2026-07-27T12:00:00Z"},
                    "committer": {"date": "2026-07-27T12:05:00Z"},
                },
            },
            {
                "sha": "ghi789",
                "html_url": "https://github.com/gravitee-io/example/commit/ghi789",
                "author": {"login": "alex"},
                "commit": {
                    "message": "Polish the dashboard query",
                    "author": {"name": "Alex Kim", "date": "2026-07-28T11:00:00Z"},
                    "committer": {"date": "2026-07-28T11:05:00Z"},
                },
            },
        ]

    def iter_pull_request_reviews(self, _full_name: str, number: int):
        if number != 17:
            return []
        return [
            {
                "id": 7001,
                "html_url": "https://github.com/gravitee-io/example/pull/17#review-7001",
                "state": "APPROVED",
                "user": {"login": "jordan"},
                "submitted_at": "2026-07-28T14:00:00Z",
            }
        ]


def test_team_work_classifies_jira_and_github_records(tmp_path: Path) -> None:
    database = tmp_path / "engineering-intelligence.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    observed_at = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    jira = JiraIngestionService(
        sessions,
        RawPayloadArchive(tmp_path / "raw"),
        JiraClient(),  # type: ignore[arg-type]
        base_url="https://gravitee.atlassian.net",
        team_field_id="customfield_12345",
        target_date_field_id="customfield_54321",
    )
    jira.ingest_board(2168, observed_at=observed_at)
    jira.ingest_query(
        "team-field-a2a",
        '"Team[Team]" = fixture',
        observed_at=observed_at,
    )
    GitHubIngestionService(
        sessions,
        RawPayloadArchive(tmp_path / "raw"),
        GitHubClient(),  # type: ignore[arg-type]
    ).ingest_repository("gravitee-io/example", observed_at=observed_at)
    SnapshotService(sessions).create(
        [2168],
        jira_queries=["team-field-a2a"],
        github_repositories=["gravitee-io/example"],
        name="work-fixture",
        created_at=observed_at,
        source_config=IBR_SOURCES,
    )
    teams = TeamsConfig.model_validate(
        {
            "teams": [
                {
                    "id": "a2a",
                    "name": "A2A",
                    "members": [
                        {
                            "id": "alex",
                            "name": "Alex Kim",
                            "jira_account_id": "person-2",
                            "github_login": "alex",
                            "starts_on": "2026-01-01",
                        }
                    ],
                }
            ]
        }
    )

    work = TeamWorkQuery(sessions).get("work-fixture", "a2a", teams)

    assert work.scope == "query:team-field-a2a"
    assert work.jira_available is True
    issues = {item.jira_key: item for item in work.jira_issues}
    assert set(issues) == {"IDN-2", "ES-99"}
    assert issues["IDN-2"].classification == "ibr_linked"
    assert issues["IDN-2"].link_basis == "descendant_of_ibr_item"
    assert issues["IDN-2"].ibr_parent_key == "IDN-1"
    assert issues["IDN-2"].assignee_display_name == "Alex Kim"
    # The pull request keyed to IDN-2 surfaces on the issue row.
    assert [pull.record_id for pull in issues["IDN-2"].linked_pull_requests] == [
        "gravitee-io/example#17"
    ]
    assert issues["IDN-2"].linked_pull_requests[0].url == (
        "https://github.com/gravitee-io/example/pull/17"
    )
    assert issues["ES-99"].classification == "non_ibr"
    assert issues["ES-99"].issue_type == "Bug"
    assert issues["ES-99"].linked_pull_requests[0].record_id == (
        "gravitee-io/example#19"
    )
    assert work.jira_split.ibr_linked == 1
    assert work.jira_split.non_ibr == 1

    records = {
        (record.record_type, record.record_id): record
        for record in work.github_records
    }
    linked_pull = records[("pull_request", "gravitee-io/example#17")]
    assert linked_pull.classification == "ibr_linked"
    assert linked_pull.link_basis == "explicit_jira_key"
    assert linked_pull.jira_keys == ["IDN-2"]
    # A commit with its own key keeps the explicit basis.
    assert records[("commit", "abc123")].link_basis == "explicit_jira_key"
    assert records[("commit", "abc123")].classification == "ibr_linked"
    # A commit without a key inherits the pull request's keys.
    inherited = records[("commit", "ghi789")]
    assert inherited.classification == "ibr_linked"
    assert inherited.link_basis == "via_pull_request"
    assert inherited.jira_keys == ["IDN-2"]
    # A review inherits the pull request's keys even for an outside reviewer,
    # because the linked issue carries this team's Team field.
    review = records[("review", "7001")]
    assert review.classification == "ibr_linked"
    assert review.link_basis == "via_pull_request"
    # A keyless pull request by a configured member stays visible as unlinked.
    unlinked = records[("pull_request", "gravitee-io/example#18")]
    assert unlinked.classification == "unlinked"
    assert unlinked.link_basis == "author_identity"
    # A pull request keyed only to non-IBR team work is non-IBR.
    non_ibr = records[("pull_request", "gravitee-io/example#19")]
    assert non_ibr.classification == "non_ibr"
    assert non_ibr.jira_keys == ["ES-99"]
    assert work.github_split.ibr_linked == 4
    assert work.github_split.non_ibr == 1
    assert work.github_split.unlinked == 1


def test_team_work_reports_missing_scope_as_unavailable(tmp_path: Path) -> None:
    database = tmp_path / "engineering-intelligence.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    observed_at = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    JiraIngestionService(
        sessions,
        RawPayloadArchive(tmp_path / "raw"),
        JiraClient(),  # type: ignore[arg-type]
        base_url="https://gravitee.atlassian.net",
        team_field_id="customfield_12345",
        target_date_field_id="customfield_54321",
    ).ingest_board(2168, observed_at=observed_at)
    SnapshotService(sessions).create(
        [2168], name="no-query-fixture", created_at=observed_at,
        source_config=IBR_SOURCES,
    )
    teams = TeamsConfig.model_validate(
        {"teams": [{"id": "a2a", "name": "A2A", "members": []}]}
    )

    work = TeamWorkQuery(sessions).get("no-query-fixture", "a2a", teams)

    assert work.jira_available is False
    assert "team-field-a2a" in work.jira_message
    assert work.jira_issues == []
    assert work.github_available is False
