from pathlib import Path

from engineering_intelligence.config import SourceConfig, TeamsConfig, load_yaml_model

ROOT = Path(__file__).resolve().parents[1]


def test_source_example_is_valid() -> None:
    config = load_yaml_model(ROOT / "config/sources.example.yaml", SourceConfig)

    assert [board.id for board in config.jira.boards] == [123]
    assert config.jira.boards[0].role == "portfolio"
    assert all(board.url is not None for board in config.jira.boards)
    assert config.jira.queries == []
    assert config.jira.gravitee_customers_field_id is None
    repositories = {
        repository.full_name: set(repository.team_ids)
        for repository in config.github.repositories
    }
    assert repositories == {"CHANGE_ME/CHANGE_ME": {"example-team"}}


def test_team_example_is_valid() -> None:
    config = load_yaml_model(ROOT / "config/teams.example.yaml", TeamsConfig)

    assert {team.name for team in config.teams} == {"CHANGE_ME Team"}
