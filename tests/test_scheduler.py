import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engineering_intelligence.scheduler import SchedulerService


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command, **_kwargs):
        self.commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")


def test_macos_schedule_install_status_and_uninstall_are_owned(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    service = SchedulerService(
        tmp_path / "repo",
        tmp_path / "config",
        system_name="Darwin",
        home=tmp_path / "home",
        runner=runner,
    )
    state = service.install(
        tmp_path / "data",
        hour=8,
        minute=15,
        backup_dir=tmp_path / "backups",
        backup_retention=3,
        keychain_service="engintel-test",
        keychain_account="fixture",
        github_keychain_service="engintel-github-test",
        github_keychain_account="fixture",
        jira_keychain_service="engintel-jira-test",
        jira_keychain_account="fixture",
        installed_at=datetime(2026, 7, 29, tzinfo=UTC),
    )

    wrapper = Path(state.wrapper_path)
    plist = Path(state.definition_paths[0])
    assert wrapper.exists() and wrapper.stat().st_mode & 0o777 == 0o700
    assert plist.exists()
    wrapper_text = wrapper.read_text()
    assert "find-generic-password" in wrapper_text
    assert "export GITHUB_PAT=" in wrapper_text
    assert "engintel-github-test" in wrapper_text
    assert "export ATLASSIAN_API_TOKEN=" in wrapper_text
    assert "engintel-jira-test" in wrapper_text
    assert "engintel refresh run" in wrapper_text
    assert str(tmp_path / "backups") in wrapper_text
    assert service.status()["installed"] is True
    assert service.status()["github_keychain_enabled"] is True
    assert service.status()["jira_keychain_enabled"] is True
    assert service.status()["state"]["jira_keychain_service"] == "engintel-jira-test"
    assert runner.commands[0][0:2] == ["launchctl", "bootstrap"]

    removed = service.uninstall()

    assert str(wrapper) in removed
    assert str(plist) in removed
    assert service.status() == {"installed": False}
    assert runner.commands[-1][0:2] == ["launchctl", "bootout"]


def test_schedule_refuses_unattended_macos_backup_without_keychain(
    tmp_path: Path,
) -> None:
    service = SchedulerService(
        tmp_path / "repo",
        tmp_path / "config",
        system_name="Darwin",
        home=tmp_path / "home",
        runner=RecordingRunner(),
    )

    with pytest.raises(ValueError, match="Keychain"):
        service.install(tmp_path / "data", backup_dir=tmp_path / "backups")


def test_schedule_requires_complete_github_keychain_identity(tmp_path: Path) -> None:
    service = SchedulerService(
        tmp_path / "repo",
        tmp_path / "config",
        system_name="Darwin",
        home=tmp_path / "home",
        runner=RecordingRunner(),
    )

    with pytest.raises(ValueError, match="configured together"):
        service.install(
            tmp_path / "data",
            github_keychain_service="engintel-github-test",
        )


def test_schedule_requires_complete_jira_keychain_identity(tmp_path: Path) -> None:
    service = SchedulerService(
        tmp_path / "repo",
        tmp_path / "config",
        system_name="Darwin",
        home=tmp_path / "home",
        runner=RecordingRunner(),
    )

    with pytest.raises(ValueError, match="configured together"):
        service.install(
            tmp_path / "data",
            jira_keychain_service="engintel-jira-test",
        )
