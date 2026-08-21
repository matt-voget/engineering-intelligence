"""Install and remove an owned launchd or systemd user schedule."""

import os
import platform
import plistlib
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

LABEL = "com.engineering-intelligence.refresh"


class SchedulerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    platform: str
    installed_at: datetime
    repository_root: str
    data_dir: str
    hour: int
    minute: int
    wrapper_path: str
    definition_paths: list[str]
    backup_dir: str | None = None
    backup_retention: int = 7
    keychain_service: str | None = None
    keychain_account: str | None = None
    github_keychain_service: str | None = None
    github_keychain_account: str | None = None
    jira_keychain_service: str | None = None
    jira_keychain_account: str | None = None
    jira_token_env: str = "ATLASSIAN_API_TOKEN"


Runner = Callable[..., subprocess.CompletedProcess[str]]


class SchedulerService:
    def __init__(
        self,
        repository_root: Path,
        config_dir: Path,
        *,
        system_name: str | None = None,
        home: Path | None = None,
        runner: Runner = subprocess.run,
    ) -> None:
        self.repository_root = repository_root.expanduser().resolve()
        self.config_dir = config_dir.expanduser().resolve()
        self.state_path = self.config_dir / "schedule.json"
        self.system_name = system_name or platform.system()
        self.home = (home or Path.home()).expanduser().resolve()
        self.runner = runner

    def install(
        self,
        data_dir: Path,
        *,
        source_config: Path | None = None,
        teams_config: Path | None = None,
        hour: int = 7,
        minute: int = 0,
        backup_dir: Path | None = None,
        backup_retention: int = 7,
        keychain_service: str | None = None,
        keychain_account: str | None = None,
        github_keychain_service: str | None = None,
        github_keychain_account: str | None = None,
        jira_keychain_service: str | None = None,
        jira_keychain_account: str | None = None,
        jira_token_env: str = "ATLASSIAN_API_TOKEN",
        installed_at: datetime | None = None,
    ) -> SchedulerState:
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("Schedule hour/minute is outside the valid range")
        if backup_retention < 1:
            raise ValueError("Backup retention must be at least 1")
        if self.state_path.exists():
            raise ValueError("A managed Engineering Intelligence schedule already exists")
        if (
            backup_dir is not None
            and self.system_name == "Darwin"
            and (not keychain_service or not keychain_account)
        ):
            raise ValueError(
                "Unattended macOS backup requires Keychain service and account"
            )
        if bool(github_keychain_service) != bool(github_keychain_account):
            raise ValueError(
                "GitHub Keychain service and account must be configured together"
            )
        if github_keychain_service and self.system_name != "Darwin":
            raise ValueError("GitHub Keychain credentials are supported only on macOS")
        if bool(jira_keychain_service) != bool(jira_keychain_account):
            raise ValueError("Jira Keychain service and account must be configured together")
        if jira_keychain_service and self.system_name != "Darwin":
            raise ValueError("Jira Keychain credentials are supported only on macOS")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", jira_token_env):
            raise ValueError("Jira token environment-variable name is invalid")
        uv = shutil.which("uv")
        if uv is None:
            raise ValueError("uv is required to install the refresh schedule")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        wrapper = self.config_dir / "scheduled-refresh.sh"
        definitions = self._definition_paths()
        for path in [wrapper, *definitions]:
            if path.exists() or path.is_symlink():
                raise ValueError(f"Refusing to replace unowned scheduler path: {path}")
        wrapper.write_text(
            self._wrapper(
                Path(uv),
                data_dir.expanduser().resolve(),
                (source_config or self.repository_root / "config/sources.example.yaml")
                .expanduser().resolve(),
                (teams_config or self.repository_root / "config/teams.example.yaml")
                .expanduser().resolve(),
                backup_dir.expanduser().resolve() if backup_dir else None,
                backup_retention,
                keychain_service,
                keychain_account,
                github_keychain_service,
                github_keychain_account,
                jira_keychain_service,
                jira_keychain_account,
                jira_token_env,
            )
        )
        wrapper.chmod(0o700)
        installed_at = installed_at or datetime.now(UTC)
        state = SchedulerState(
            platform=self.system_name,
            installed_at=installed_at,
            repository_root=str(self.repository_root),
            data_dir=str(data_dir.expanduser().resolve()),
            hour=hour,
            minute=minute,
            wrapper_path=str(wrapper),
            definition_paths=[str(path) for path in definitions],
            backup_dir=str(backup_dir.expanduser().resolve()) if backup_dir else None,
            backup_retention=backup_retention,
            keychain_service=keychain_service,
            keychain_account=keychain_account,
            github_keychain_service=github_keychain_service,
            github_keychain_account=github_keychain_account,
            jira_keychain_service=jira_keychain_service,
            jira_keychain_account=jira_keychain_account,
            jira_token_env=jira_token_env,
        )
        try:
            if self.system_name == "Darwin":
                self._install_launchd(wrapper, definitions[0], hour, minute, data_dir)
            elif self.system_name == "Linux":
                self._install_systemd(wrapper, definitions, hour, minute)
            else:
                raise ValueError(
                    f"Unsupported scheduler platform: {self.system_name}"
                )
            self._write_state(state)
        except Exception:
            for path in [wrapper, *definitions]:
                if path.exists():
                    path.unlink()
            raise
        return state

    def status(self) -> dict[str, object]:
        state = self.load()
        if state is None:
            return {"installed": False}
        paths_exist = all(
            Path(path).exists()
            for path in [state.wrapper_path, *state.definition_paths]
        )
        return {
            "installed": True,
            "platform": state.platform,
            "hour": state.hour,
            "minute": state.minute,
            "backup_enabled": state.backup_dir is not None,
            "github_keychain_enabled": state.github_keychain_service is not None,
            "jira_keychain_enabled": state.jira_keychain_service is not None,
            "owned_paths_present": paths_exist,
            "state": state.model_dump(mode="json"),
        }

    def uninstall(self) -> list[str]:
        state = self.load()
        if state is None:
            return []
        if state.platform == "Darwin":
            self.runner(
                [
                    "launchctl",
                    "bootout",
                    f"gui/{os.getuid()}",
                    state.definition_paths[0],
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        elif state.platform == "Linux":
            self.runner(
                ["systemctl", "--user", "disable", "--now", f"{LABEL}.timer"],
                check=False,
                capture_output=True,
                text=True,
            )
        removed = []
        for value in [state.wrapper_path, *state.definition_paths]:
            path = Path(value)
            if path.exists() and path.is_file():
                path.unlink()
                removed.append(str(path))
        if state.platform == "Linux":
            self.runner(
                ["systemctl", "--user", "daemon-reload"],
                check=False,
                capture_output=True,
                text=True,
            )
        if self.state_path.exists():
            self.state_path.unlink()
            removed.append(str(self.state_path))
        return removed

    def load(self) -> SchedulerState | None:
        if not self.state_path.exists():
            return None
        return SchedulerState.model_validate_json(self.state_path.read_text())

    def _definition_paths(self) -> list[Path]:
        if self.system_name == "Darwin":
            return [self.home / "Library" / "LaunchAgents" / f"{LABEL}.plist"]
        if self.system_name == "Linux":
            root = self.home / ".config" / "systemd" / "user"
            return [root / f"{LABEL}.service", root / f"{LABEL}.timer"]
        return []

    def _wrapper(
        self,
        uv: Path,
        data_dir: Path,
        source_config: Path,
        teams_config: Path,
        backup_dir: Path | None,
        backup_retention: int,
        keychain_service: str | None,
        keychain_account: str | None,
        github_keychain_service: str | None,
        github_keychain_account: str | None,
        jira_keychain_service: str | None,
        jira_keychain_account: str | None,
        jira_token_env: str,
    ) -> str:
        arguments = [
            str(uv),
            "--directory",
            str(self.repository_root),
            "run",
            "engintel",
            "refresh",
            "run",
            "--source-config",
            str(source_config),
            "--teams-config",
            str(teams_config),
            "--data-dir",
            str(data_dir),
        ]
        setup = []
        if github_keychain_service is not None:
            github_service = shlex.quote(github_keychain_service)
            github_account = shlex.quote(github_keychain_account or "")
            setup.append(
                "export GITHUB_PAT="
                f'"$(/usr/bin/security find-generic-password -s {github_service} '
                f'-a {github_account} -w)"'
            )
        if jira_keychain_service is not None:
            jira_service = shlex.quote(jira_keychain_service)
            jira_account = shlex.quote(jira_keychain_account or "")
            setup.append(
                f"export {jira_token_env}="
                f'"$(/usr/bin/security find-generic-password -s {jira_service} '
                f'-a {jira_account} -w)"'
            )
        if backup_dir is not None:
            arguments.extend(
                [
                    "--backup-dir",
                    str(backup_dir),
                    "--backup-retention",
                    str(backup_retention),
                ]
            )
            if self.system_name == "Darwin":
                service = shlex.quote(keychain_service or "")
                account = shlex.quote(keychain_account or "")
                setup.append(
                    "export ENGINTEL_BACKUP_PASSPHRASE="
                    f'"$(/usr/bin/security find-generic-password -s {service} '
                    f'-a {account} -w)"'
                )
        command = " ".join(shlex.quote(argument) for argument in arguments)
        shell = "/bin/zsh" if self.system_name == "Darwin" else "/bin/bash"
        profile = self.home / (".zshrc" if self.system_name == "Darwin" else ".profile")
        lines = [
            f"#!{shell}",
            "set -eu",
            f". {shlex.quote(str(profile))} >/dev/null 2>&1 || true",
            *setup,
            f"exec {command}",
            "",
        ]
        return "\n".join(lines)

    def _install_launchd(
        self,
        wrapper: Path,
        plist: Path,
        hour: int,
        minute: int,
        data_dir: Path,
    ) -> None:
        plist.parent.mkdir(parents=True, exist_ok=True)
        logs = data_dir.expanduser().resolve() / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": LABEL,
            "ProgramArguments": [str(wrapper)],
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},
            "StandardOutPath": str(logs / "scheduled-refresh.log"),
            "StandardErrorPath": str(logs / "scheduled-refresh.error.log"),
            "ProcessType": "Background",
        }
        with plist.open("wb") as stream:
            plistlib.dump(payload, stream, sort_keys=True)
        self.runner(
            ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
            check=True,
            capture_output=True,
            text=True,
        )

    def _install_systemd(
        self,
        wrapper: Path,
        definitions: list[Path],
        hour: int,
        minute: int,
    ) -> None:
        service, timer = definitions
        service.parent.mkdir(parents=True, exist_ok=True)
        service.write_text(
            "[Unit]\nDescription=Engineering Intelligence refresh\n\n"
            "[Service]\nType=oneshot\n"
            f"ExecStart={wrapper}\n"
        )
        timer.write_text(
            "[Unit]\nDescription=Daily Engineering Intelligence refresh\n\n"
            "[Timer]\n"
            f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00\n"
            "Persistent=true\n\n"
            "[Install]\nWantedBy=timers.target\n"
        )
        self.runner(
            ["systemctl", "--user", "daemon-reload"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.runner(
            ["systemctl", "--user", "enable", "--now", f"{LABEL}.timer"],
            check=True,
            capture_output=True,
            text=True,
        )

    def _write_state(self, state: SchedulerState) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(state.model_dump_json(indent=2) + "\n")
        os.replace(temporary, self.state_path)
