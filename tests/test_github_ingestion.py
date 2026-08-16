import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from engineering_intelligence.config import SourceConfig, TeamsConfig
from engineering_intelligence.ingestion.archive import RawPayloadArchive
from engineering_intelligence.ingestion.github.service import GitHubIngestionService
from engineering_intelligence.ingestion.jira.service import JiraIngestionService
from engineering_intelligence.organization import OrganizationService
from engineering_intelligence.persistence.database import (
    create_sqlite_engine,
    session_factory,
    upgrade_database,
)
from engineering_intelligence.persistence.models import (
    GitHubCommit,
    GitHubPullRequest,
    GitHubPullRequestVersion,
    GitHubReview,
    IngestionRun,
    JiraGitHubRelationship,
)
from engineering_intelligence.queries.feature import FeatureQuery
from engineering_intelligence.queries.individual import IndividualQuery
from engineering_intelligence.queries.people import PeopleQuery
from engineering_intelligence.renderers.feature_markdown import render_feature_markdown
from engineering_intelligence.renderers.people_markdown import (
    render_individual_markdown,
    render_people_markdown,
)
from engineering_intelligence.snapshots import SnapshotService

IBR_SOURCES = SourceConfig.model_validate({
    "jira": {"base_url": "https://gravitee.atlassian.net", "boards": [
        {"id": 2168, "name": "IBR", "role": "ibr"}
    ]}
})


FIXTURES = Path(__file__).parent / "fixtures/jira"


class JiraFixtureClient:
    def get_board_configuration(self, _board_id: int) -> dict[str, Any]:
        return json.loads((FIXTURES / "board_2168.json").read_text())

    def iter_board_issues(
        self,
        _board_id: int,
        *,
        fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload = json.loads((FIXTURES / "issue_idn_1.json").read_text())
        payload["fields"]["status"] = {
            "id": "3",
            "name": "In Progress",
            "statusCategory": {"key": "indeterminate", "name": "In Progress"},
        }
        return [payload]

    def iter_child_issues(
        self,
        parent_keys: list[str],
        *,
        fields: list[str],
    ) -> list[dict[str, Any]]:
        children = {
            "IDN-1": json.loads((FIXTURES / "issue_idn_2.json").read_text()),
            "IDN-2": json.loads((FIXTURES / "issue_idn_3.json").read_text()),
        }
        return [children[key] for key in parent_keys if key in children]

    def iter_issue_changelogs(self, _issue_ids_or_keys, *, field_ids=None):
        return []

    def iter_jql_issues(self, _jql: str, *, fields=None):
        issue = json.loads((FIXTURES / "issue_idn_2.json").read_text())
        issue["id"] = "99999"
        issue["key"] = "ES-99"
        issue["self"] = "https://gravitee.atlassian.net/rest/api/3/issue/99999"
        issue["fields"]["summary"] = "Maintain Edge Stack release tooling"
        issue["fields"]["parent"] = None
        return [issue]


class GitHubFixtureClient:
    title = "IDN-1 Ship the agent-ready dashboard"

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
        return [
            {
                "id": 9001,
                "number": 17,
                "html_url": "https://github.com/gravitee-io/example/pull/17",
                "title": self.title,
                "body": "Implements the IBR child work.",
                "state": "closed",
                "draft": False,
                "user": {"login": "alex"},
                "head": {"ref": "feature/IDN-1-dashboard"},
                "base": {"ref": "main"},
                "created_at": "2026-07-20T10:00:00Z",
                "updated_at": "2026-07-28T15:00:00Z",
                "closed_at": "2026-07-28T15:00:00Z",
                "merged_at": "2026-07-28T15:00:00Z",
                "merge_commit_sha": "abc123",
            }
        ]

    def iter_pull_request_commits(
        self,
        _full_name: str,
        _number: int,
    ) -> list[dict[str, Any]]:
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
                "sha": "def456",
                "html_url": "https://github.com/gravitee-io/example/commit/def456",
                "author": {"login": "alex"},
                "commit": {
                    "message": "ES-99 Tighten the dashboard query",
                    "author": {"name": "Alex Kim", "date": "2026-07-28T10:00:00Z"},
                    "committer": {"date": "2026-07-28T10:05:00Z"},
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

    def iter_pull_request_reviews(
        self,
        _full_name: str,
        _number: int,
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": 7001,
                "html_url": "https://github.com/gravitee-io/example/pull/17#review-7001",
                "state": "APPROVED",
                "user": {"login": "jordan"},
                "submitted_at": "2026-07-28T14:00:00Z",
            }
        ]


def test_github_ingestion_is_idempotent_and_links_explicit_jira_keys(
    tmp_path: Path,
) -> None:
    database = tmp_path / "engineering-intelligence.db"
    upgrade_database(database)
    sessions = session_factory(create_sqlite_engine(database))
    observed_at = datetime(2026, 7, 28, 16, 0, tzinfo=UTC)
    archive = RawPayloadArchive(tmp_path / "raw")
    jira_service = JiraIngestionService(
        sessions,
        archive,
        JiraFixtureClient(),  # type: ignore[arg-type]
        base_url="https://gravitee.atlassian.net",
        target_date_field_id="customfield_54321",
    )
    jira_service.ingest_board(2168, observed_at=observed_at)
    jira_service.ingest_query(
        "accountable-active-work",
        'assignee in ("person-2") AND statusCategory != Done',
        observed_at=observed_at,
    )
    client = GitHubFixtureClient()
    service = GitHubIngestionService(
        sessions,
        archive,
        client,  # type: ignore[arg-type]
    )

    first_run_id = service.ingest_repository(
        "gravitee-io/example",
        observed_at=observed_at,
    )
    second_run_id = service.ingest_repository(
        "gravitee-io/example",
        observed_at=observed_at,
    )

    with sessions() as session:
        assert session.get(IngestionRun, first_run_id).records_changed == 1
        assert session.get(IngestionRun, second_run_id).records_changed == 0
        assert session.scalar(select(func.count()).select_from(GitHubPullRequest)) == 1
        assert session.scalar(select(func.count()).select_from(GitHubPullRequestVersion)) == 1
        assert session.scalar(select(func.count()).select_from(GitHubCommit)) == 3
        assert session.scalar(select(func.count()).select_from(GitHubReview)) == 1
        links = session.scalars(select(JiraGitHubRelationship)).all()
        assert {(link.github_record_type, link.github_record_id) for link in links} == {
            ("pull_request", "gravitee-io/example#17"),
            ("commit", "abc123"),
            ("commit", "def456"),
        }
        assert all(link.confidence == "confirmed" for link in links)

    client.title = "IDN-1 Ship the improved agent-ready dashboard"
    later = datetime(2026, 7, 29, 16, 0, tzinfo=UTC)
    third_run_id = service.ingest_repository(
        "gravitee-io/example",
        observed_at=later,
    )
    with sessions() as session:
        assert session.get(IngestionRun, third_run_id).records_changed == 1
        assert session.scalar(select(func.count()).select_from(GitHubPullRequestVersion)) == 2

    OrganizationService(sessions).apply(
        TeamsConfig.model_validate(
            {
                "teams": [
                    {
                        "id": "a2a",
                        "name": "A2A",
                        "members": [
                            {
                                "id": "alex",
                                "name": "Alex Kim",
                                "role": "Engineer",
                                "jira_account_id": "person-2",
                                "github_login": "alex",
                                "starts_on": "2026-01-01",
                            },
                            {
                                "id": "jordan",
                                "name": "Jordan Lee",
                                "role": "Engineer",
                                "jira_account_id": "person-1",
                                "github_login": "jordan",
                                "starts_on": "2026-01-01",
                            },
                        ],
                    }
                ]
            }
        )
    )
    SnapshotService(sessions).create(
        [2168],
        jira_queries=["accountable-active-work"],
        github_repositories=["gravitee-io/example"],
        name="github-attribution",
        created_at=later,
        source_config=IBR_SOURCES,
    )
    feature = FeatureQuery(sessions).get("github-attribution", "IDN-1")

    assert feature.github_delivery.available is True
    assert feature.summary.linked_delivery_records == 3
    assert {
        (record.record_type, record.actor_login)
        for record in feature.github_delivery.records
    } == {
        ("pull_request", "alex"),
        ("commit", "alex"),
        ("review", "jordan"),
    }
    assert all(
        record.direct_jira_key == "IDN-1"
        for record in feature.github_delivery.records
    )
    assert {"pull_request_author", "commit_author", "reviewer"} <= {
        person.relationship_type for person in feature.contributors
    }
    assert feature.summary.related_people == 2
    markdown = render_feature_markdown(feature)
    assert "https://github.com/gravitee-io/example/pull/17" in markdown
    assert "confirmed" in markdown

    alex = IndividualQuery(sessions).get("github-attribution", "alex")
    assert alex.identity_mapping_state == "complete"
    assert {
        relationship.relationship_type for relationship in alex.jira_work
    } == {"child_issue_assignee", "active_assignment_outside_ibr"}
    outside_work = next(item for item in alex.jira_work if item.direct_issue_key == "ES-99")
    assert outside_work.feature_key is None
    assert outside_work.active is True
    assert {item.record_type for item in alex.github_contributions} == {
        "pull_request",
        "commit",
    }
    assert any(
        item.record_id == "ghi789"
        and item.relationship_type == "unlinked_activity"
        and item.direct_jira_key is None
        for item in alex.github_contributions
    )
    assert any(
        item.record_id == "def456"
        and item.direct_jira_key == "ES-99"
        and item.rolled_up_to_feature is False
        for item in alex.github_contributions
    )
    assert alex.signals[0].signal_type == "verified_fact"
    assert "child_issue_assignee" in render_individual_markdown(alex)

    jordan = IndividualQuery(sessions).get("github-attribution", "Jordan Lee")
    assert {item.relationship_type for item in jordan.jira_work} == {
        "high_level_assignee"
    }
    assert {item.record_type for item in jordan.github_contributions} == {"review"}
    assert len(jordan.current_work) == 1
    assert jordan.current_work[0].jira_key == "IDN-1"
    assert jordan.current_work[0].target_date_value == "2026-08-15"
    assert [board.board_id for board in jordan.current_work[0].boards] == [2168]
    jordan_markdown = render_individual_markdown(jordan)
    assert "| Issue | Status | Target date | Boards | IBR Feature |" in jordan_markdown
    assert "2026-08-15" in jordan_markdown
    assert "IBR board" in jordan_markdown
    assert "No IBR mapping" not in jordan_markdown
    assert any(signal.title == "Code-review collaboration observed" for signal in jordan.signals)

    people = PeopleQuery(sessions).get("github-attribution")
    assert [person.person_id for person in people.people] == ["alex", "jordan"]
    assert people.people[0].current_features == ["IDN-1"]
    assert "People directory" in render_people_markdown(people)
