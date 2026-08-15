from datetime import UTC, datetime
from pathlib import Path

import pytest

from engineering_intelligence.lifecycle import LifecycleService


def repository_fixture(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    skill = repository / "skills/team-status-prep"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: team-status-prep\n---\n")
    git = repository / ".git"
    git.mkdir()
    return repository


def test_install_upgrade_and_uninstall_are_manifest_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = repository_fixture(tmp_path)
    monkeypatch.setattr(
        "engineering_intelligence.lifecycle._git_commit",
        lambda _root: "abc123",
    )
    service = LifecycleService(
        repository,
        tmp_path / "config",
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        manage_mcp=False,
    )
    installed_at = datetime(2026, 7, 28, 19, 0, tzinfo=UTC)
    manifest = service.install(
        tmp_path / "data",
        ["codex", "claude-code"],
        installed_at=installed_at,
    )

    codex_link = tmp_path / "codex/skills/team-status-prep"
    claude_link = tmp_path / "claude/skills/team-status-prep"
    assert codex_link.resolve() == repository / "skills/team-status-prep"
    assert claude_link.resolve() == repository / "skills/team-status-prep"
    assert manifest.installed_commit == "abc123"
    assert service.upgrade(upgraded_at=installed_at).updated_at == installed_at

    assert service.uninstall(["codex"]) == [str(codex_link)]
    assert not codex_link.exists()
    assert claude_link.exists()
    assert (tmp_path / "data/engineering-intelligence.db").exists()
    assert service.uninstall() == [str(claude_link)]
    assert not service.manifest_path.exists()


def test_installer_refuses_to_replace_unowned_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = repository_fixture(tmp_path)
    monkeypatch.setattr(
        "engineering_intelligence.lifecycle._git_commit",
        lambda _root: "abc123",
    )
    codex_skill = tmp_path / "codex/skills/team-status-prep"
    codex_skill.mkdir(parents=True)
    service = LifecycleService(
        repository,
        tmp_path / "config",
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        manage_mcp=False,
    )

    with pytest.raises(ValueError, match="Refusing"):
        service.install(tmp_path / "data", ["codex"])


def test_installer_discovers_and_removes_all_checked_in_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = repository_fixture(tmp_path)
    for name in ("dashboard-review", "individual-context-prep"):
        skill = repository / "skills" / name
        skill.mkdir()
        (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    monkeypatch.setattr(
        "engineering_intelligence.lifecycle._git_commit",
        lambda _root: "abc123",
    )
    service = LifecycleService(
        repository,
        tmp_path / "config",
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        manage_mcp=False,
    )

    manifest = service.install(tmp_path / "data", ["codex"])

    installation = manifest.agents["codex"]
    assert set(installation.skill_links) == {
        "dashboard-review",
        "individual-context-prep",
        "team-status-prep",
    }
    for name, link in installation.skill_links.items():
        assert Path(link).resolve() == repository / "skills" / name

    removed = service.uninstall()
    assert set(removed) == set(installation.skill_links.values())


def test_upgrade_removes_only_manifest_owned_obsolete_skill_links(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = repository_fixture(tmp_path)
    obsolete = repository / "skills/dashboard-review"
    obsolete.mkdir()
    (obsolete / "SKILL.md").write_text("---\nname: dashboard-review\n---\n")
    monkeypatch.setattr(
        "engineering_intelligence.lifecycle._git_commit",
        lambda _root: "abc123",
    )
    service = LifecycleService(
        repository,
        tmp_path / "config",
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
        manage_mcp=False,
    )
    service.install(tmp_path / "data", ["codex"])
    obsolete_link = tmp_path / "codex/skills/dashboard-review"
    assert obsolete_link.is_symlink()

    (obsolete / "SKILL.md").unlink()
    obsolete.rmdir()
    manifest = service.upgrade()

    assert not obsolete_link.exists()
    assert set(manifest.agents["codex"].skill_links) == {"team-status-prep"}


def test_lifecycle_records_and_removes_owned_mcp_registrations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = repository_fixture(tmp_path)
    monkeypatch.setattr(
        "engineering_intelligence.lifecycle._git_commit",
        lambda _root: "abc123",
    )
    commands: list[list[str]] = []

    def run(
        command: list[str],
        **_kwargs: object,
    ) -> object:
        commands.append(command)
        prior_add = any(
            prior[0] == command[0] and "add" in prior
            for prior in commands[:-1]
        )
        return type(
            "Result",
            (),
            {
                "returncode": 0 if "get" not in command or prior_add else 1,
                "stdout": "",
                "stderr": "",
            },
        )()

    monkeypatch.setattr("engineering_intelligence.lifecycle.subprocess.run", run)
    service = LifecycleService(
        repository,
        tmp_path / "config",
        codex_home=tmp_path / "codex",
        claude_home=tmp_path / "claude",
    )

    manifest = service.install(tmp_path / "data", ["codex", "claude-code"])

    assert manifest.agents["codex"].mcp_registered is True
    assert manifest.agents["claude-code"].mcp_server_name == "engineering-intelligence"
    codex_add = next(command for command in commands if command[:3] == ["codex", "mcp", "add"])
    claude_add = next(
        command for command in commands if command[:3] == ["claude", "mcp", "add"]
    )
    assert f"ENGINTEL_DATA_DIR={tmp_path / 'data'}" in codex_add
    assert "--scope" in claude_add

    service.uninstall()

    assert ["codex", "mcp", "remove", "engineering-intelligence"] in commands
    assert [
        "claude",
        "mcp",
        "remove",
        "--scope",
        "user",
        "engineering-intelligence",
    ] in commands
