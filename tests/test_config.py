from pathlib import Path

from engineering_intelligence.config import (
    JiraBoardConfig,
    SourceConfig,
    TeamsConfig,
    load_yaml_model,
)

ROOT = Path(__file__).resolve().parents[1]


def test_source_example_is_valid() -> None:
    config = load_yaml_model(ROOT / "config/sources.example.yaml", SourceConfig)

    assert [board.id for board in config.jira.boards] == [123]
    assert config.jira.boards[0].role == "ibr"
    assert all(board.url is not None for board in config.jira.boards)
    assert config.jira.queries == []
    assert config.jira.gravitee_customers_field_id is None
    assert [repository.full_name for repository in config.github.repositories] == [
        "CHANGE_ME/CHANGE_ME"
    ]


def test_legacy_repository_team_ids_are_ignored() -> None:
    repository = SourceConfig.model_validate({
        "jira": {
            "base_url": "https://example.atlassian.net",
            "boards": [{"id": 1, "name": "IBR"}],
        },
        "github": {
            "repositories": [
                {"full_name": "example/repo", "team_ids": ["legacy-team"]}
            ]
        },
    }).github.repositories[0]

    assert repository.model_dump() == {"full_name": "example/repo"}


def test_legacy_portfolio_role_normalizes_to_ibr() -> None:
    board = JiraBoardConfig(id=1, name="IBR", role="portfolio")

    assert board.role == "ibr"


def test_team_example_is_valid() -> None:
    config = load_yaml_model(ROOT / "config/teams.example.yaml", TeamsConfig)

    assert {team.name for team in config.teams} == {"CHANGE_ME Team"}
