from typer.testing import CliRunner

from engineering_intelligence.cli import app

runner = CliRunner()


def test_doctor() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Engineering Intelligence is ready." in result.stdout


def test_version_short() -> None:
    result = runner.invoke(app, ["version", "--short"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_database_init(tmp_path) -> None:
    result = runner.invoke(app, ["database", "init", "--data-dir", str(tmp_path)])

    assert result.exit_code == 0
    assert (tmp_path / "engineering-intelligence.db").exists()


def test_setup_creates_private_starter_configuration(tmp_path) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"

    result = runner.invoke(app, [
        "setup", "--config-dir", str(config_dir), "--data-dir", str(data_dir)
    ])

    assert result.exit_code == 0
    assert (config_dir / "sources.yaml").exists()
    assert (config_dir / "teams.yaml").exists()
    assert (data_dir / "engineering-intelligence.db").exists()
    assert "ready_for_configuration" in result.stdout

    repeated = runner.invoke(app, [
        "setup", "--config-dir", str(config_dir), "--data-dir", str(data_dir)
    ])
    assert repeated.exit_code != 0
