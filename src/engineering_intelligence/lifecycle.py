"""Manifest-based installation, upgrade, and safe agent-adapter removal."""

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engineering_intelligence import __version__
from engineering_intelligence.persistence.database import upgrade_database
from engineering_intelligence.runtime import runtime_paths

AgentName = Literal["codex", "claude-code"]
MCP_SERVER_NAME = "engineering-intelligence"
PRIMARY_SKILL_NAME = "team-status-prep"


class AgentInstallation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_link: str
    skill_target: str
    skill_links: dict[str, str] = Field(default_factory=dict)
    skill_targets: dict[str, str] = Field(default_factory=dict)
    mcp_server_name: str | None = None
    mcp_registered: bool = False


class InstallationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "4"
    application_version: str
    installed_commit: str
    repository_root: str
    data_dir: str
    source_config: str = ""
    teams_config: str = ""
    installed_at: datetime
    updated_at: datetime
    agents: dict[str, AgentInstallation]


class LifecycleService:
    def __init__(
        self,
        repository_root: Path,
        config_dir: Path,
        *,
        codex_home: Path | None = None,
        claude_home: Path | None = None,
        manage_mcp: bool = True,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.config_dir = config_dir.expanduser().resolve()
        self.manifest_path = self.config_dir / "installation.json"
        self.agent_homes = {
            "codex": (codex_home or _codex_home()).expanduser().resolve(),
            "claude-code": (claude_home or _claude_home()).expanduser().resolve(),
        }
        self.manage_mcp = manage_mcp

    def install(
        self,
        data_dir: Path,
        agents: list[AgentName],
        *,
        source_config: Path | None = None,
        teams_config: Path | None = None,
        installed_at: datetime | None = None,
    ) -> InstallationManifest:
        installed_at = installed_at or datetime.now(UTC)
        data_root = data_dir.expanduser().resolve()
        source_config = source_config or self.repository_root / "config/sources.example.yaml"
        teams_config = teams_config or self.repository_root / "config/teams.example.yaml"
        upgrade_database(runtime_paths(data_root).database)
        manifest = self.load() or InstallationManifest(
            application_version=__version__,
            installed_commit=_git_commit(self.repository_root),
            repository_root=str(self.repository_root),
            data_dir=str(data_root),
            source_config=str(source_config.expanduser().resolve()),
            teams_config=str(teams_config.expanduser().resolve()),
            installed_at=installed_at,
            updated_at=installed_at,
            agents={},
        )
        sources = self._skill_sources()
        primary_source = sources[PRIMARY_SKILL_NAME]
        for agent in agents:
            previous = manifest.agents.get(agent)
            linked: list[Path] = []
            try:
                skill_links = {}
                skill_targets = {}
                for skill_name, source in sources.items():
                    destination = self.agent_homes[agent] / "skills" / skill_name
                    self._link_skill(source, destination)
                    linked.append(destination)
                    skill_links[skill_name] = str(destination)
                    skill_targets[skill_name] = str(source)
                if previous is not None:
                    self._remove_stale_skill_links(previous, set(sources))
                if self.manage_mcp:
                    self._register_mcp(
                        agent,
                        data_root,
                        source_config=source_config,
                        teams_config=teams_config,
                        owned=bool(previous and previous.mcp_registered),
                    )
                manifest.agents[agent] = AgentInstallation(
                    skill_link=skill_links[PRIMARY_SKILL_NAME],
                    skill_target=str(primary_source),
                    skill_links=skill_links,
                    skill_targets=skill_targets,
                    mcp_server_name=MCP_SERVER_NAME if self.manage_mcp else None,
                    mcp_registered=self.manage_mcp,
                )
            except Exception:
                if previous is None:
                    for destination in linked:
                        if destination.is_symlink():
                            destination.unlink()
                raise
        manifest.schema_version = "4"
        manifest.application_version = __version__
        manifest.installed_commit = _git_commit(self.repository_root)
        manifest.repository_root = str(self.repository_root)
        manifest.data_dir = str(data_root)
        manifest.source_config = str(source_config.expanduser().resolve())
        manifest.teams_config = str(teams_config.expanduser().resolve())
        manifest.updated_at = installed_at
        self._write_manifest(manifest)
        return manifest

    def upgrade(self, *, upgraded_at: datetime | None = None) -> InstallationManifest:
        manifest = self.load()
        if manifest is None:
            raise ValueError("Engineering Intelligence is not installed")
        upgraded_at = upgraded_at or datetime.now(UTC)
        upgrade_database(runtime_paths(Path(manifest.data_dir)).database)
        if self.manage_mcp and (not manifest.source_config or not manifest.teams_config):
            raise ValueError(
                "The older installation manifest has no configuration paths; "
                "run `engintel install` again with --source-config and --teams-config"
            )
        sources = self._skill_sources()
        for agent, installation in manifest.agents.items():
            skill_links = {}
            skill_targets = {}
            for skill_name, source in sources.items():
                destination = self.agent_homes[agent] / "skills" / skill_name
                self._link_skill(source, destination)
                skill_links[skill_name] = str(destination)
                skill_targets[skill_name] = str(source)
            self._remove_stale_skill_links(installation, set(sources))
            installation.skill_link = skill_links[PRIMARY_SKILL_NAME]
            installation.skill_target = skill_targets[PRIMARY_SKILL_NAME]
            installation.skill_links = skill_links
            installation.skill_targets = skill_targets
            if self.manage_mcp:
                self._register_mcp(
                    agent,  # type: ignore[arg-type]
                    Path(manifest.data_dir),
                    source_config=Path(manifest.source_config),
                    teams_config=Path(manifest.teams_config),
                    owned=installation.mcp_registered,
                )
                installation.mcp_server_name = MCP_SERVER_NAME
                installation.mcp_registered = True
        manifest.schema_version = "4"
        manifest.application_version = __version__
        manifest.installed_commit = _git_commit(self.repository_root)
        manifest.updated_at = upgraded_at
        self._write_manifest(manifest)
        return manifest

    def uninstall(self, agents: list[AgentName] | None = None) -> list[str]:
        manifest = self.load()
        if manifest is None:
            return []
        selected = set(agents or manifest.agents)
        removed: list[str] = []
        for agent in selected:
            installation = manifest.agents.get(agent)
            if installation is None:
                continue
            if installation.mcp_registered and installation.mcp_server_name:
                self._remove_mcp(agent, installation.mcp_server_name)
            # Schema-1/2 manifests recorded only the former dashboard skill in
            # the singular compatibility fields.
            skill_links = installation.skill_links or {
                "dashboard-review": installation.skill_link
            }
            skill_targets = installation.skill_targets or {
                "dashboard-review": installation.skill_target
            }
            for skill_name, link_value in skill_links.items():
                link = Path(link_value)
                target_value = skill_targets.get(skill_name)
                if (
                    target_value is not None
                    and link.is_symlink()
                    and link.resolve() == Path(target_value).resolve()
                ):
                    link.unlink()
                    removed.append(str(link))
            manifest.agents.pop(agent, None)
        if manifest.agents:
            manifest.updated_at = datetime.now(UTC)
            self._write_manifest(manifest)
        elif self.manifest_path.exists():
            self.manifest_path.unlink()
        return removed

    def load(self) -> InstallationManifest | None:
        if not self.manifest_path.exists():
            return None
        return InstallationManifest.model_validate_json(self.manifest_path.read_text())

    @staticmethod
    def _link_skill(source: Path, destination: Path) -> None:
        if destination.is_symlink() and destination.resolve() == source.resolve():
            return
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"Refusing to replace existing agent path: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(source, target_is_directory=True)

    @staticmethod
    def _remove_stale_skill_links(
        installation: AgentInstallation,
        active_skills: set[str],
    ) -> None:
        """Remove obsolete links only when the manifest proves ownership."""
        for skill_name, link_value in installation.skill_links.items():
            if skill_name in active_skills:
                continue
            target_value = installation.skill_targets.get(skill_name)
            link = Path(link_value)
            if (
                target_value is not None
                and link.is_symlink()
                and link.resolve() == Path(target_value).resolve()
            ):
                link.unlink()

    def _skill_sources(self) -> dict[str, Path]:
        skills_root = self.repository_root / "skills"
        sources = {
            path.name: path
            for path in sorted(skills_root.iterdir())
            if path.is_dir() and (path / "SKILL.md").is_file()
        }
        if PRIMARY_SKILL_NAME not in sources:
            raise ValueError(
                f"Report skill is missing: {skills_root / PRIMARY_SKILL_NAME}"
            )
        return sources

    def _write_manifest(self, manifest: InstallationManifest) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(manifest.model_dump_json(indent=2) + "\n")
        os.replace(temporary, self.manifest_path)

    def _register_mcp(
        self,
        agent: AgentName,
        data_dir: Path,
        *,
        source_config: Path,
        teams_config: Path,
        owned: bool,
    ) -> None:
        executable = shutil.which("uv")
        if executable is None:
            raise ValueError("uv is required to register the Engineering Intelligence MCP server")
        if owned:
            self._remove_mcp(agent, MCP_SERVER_NAME, check=False)
        elif self._mcp_exists(agent, MCP_SERVER_NAME):
            raise ValueError(
                f"Refusing to replace unowned MCP server registration: {MCP_SERVER_NAME}"
            )
        server_command = [
            executable,
            "--directory",
            str(self.repository_root),
            "run",
            "engintel-mcp",
        ]
        if agent == "codex":
            command = [
                "codex",
                "mcp",
                "add",
                MCP_SERVER_NAME,
                "--env",
                f"ENGINTEL_DATA_DIR={data_dir}",
                "--env",
                f"ENGINTEL_SOURCE_CONFIG={source_config}",
                "--env",
                f"ENGINTEL_TEAMS_CONFIG={teams_config}",
                "--",
                *server_command,
            ]
        else:
            command = [
                "claude",
                "mcp",
                "add",
                "--scope",
                "user",
                MCP_SERVER_NAME,
                "--env",
                f"ENGINTEL_DATA_DIR={data_dir}",
                "--env",
                f"ENGINTEL_SOURCE_CONFIG={source_config}",
                "--env",
                f"ENGINTEL_TEAMS_CONFIG={teams_config}",
                "--",
                *server_command,
            ]
        self._run_agent_command(agent, command, check=True)
        if not self._mcp_exists(agent, MCP_SERVER_NAME):
            raise ValueError(
                f"{agent} MCP registration was not visible after the add command"
            )

    def _mcp_exists(self, agent: AgentName, name: str) -> bool:
        result = self._run_agent_command(
            agent,
            [agent.split("-")[0], "mcp", "get", name],
            check=False,
        )
        return result.returncode == 0

    def _remove_mcp(
        self,
        agent: AgentName,
        name: str,
        *,
        check: bool = True,
    ) -> None:
        command = [agent.split("-")[0], "mcp", "remove"]
        if agent == "claude-code":
            command.extend(["--scope", "user"])
        command.append(name)
        self._run_agent_command(agent, command, check=check)

    def _run_agent_command(
        self,
        agent: AgentName,
        command: list[str],
        *,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        if agent == "codex":
            environment["CODEX_HOME"] = str(self.agent_homes[agent])
        elif self.agent_homes[agent] != _claude_home().expanduser().resolve():
            environment["CLAUDE_CONFIG_DIR"] = str(self.agent_homes[agent])
        try:
            return subprocess.run(
                command,
                check=check,
                capture_output=True,
                text=True,
                env=environment,
                timeout=30,
            )
        except FileNotFoundError as error:
            raise ValueError(f"{command[0]} CLI is required for the {agent} adapter") from error
        except subprocess.CalledProcessError as error:
            detail = error.stderr.strip() or error.stdout.strip() or "unknown error"
            raise ValueError(f"{agent} MCP registration failed: {detail}") from error
        except subprocess.TimeoutExpired as error:
            raise ValueError(f"{agent} MCP registration timed out") from error


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config_dir() -> Path:
    configured = os.environ.get("ENGINTEL_CONFIG_DIR")
    if configured:
        return Path(configured)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "engineering-intelligence"


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))


def _git_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
