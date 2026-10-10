from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from arena.agents.base import GatewayEndpoint
from arena.core.models import Kit
from arena.kits.install import (
    KitInstallError,
    KitPreflightError,
    KitTarget,
    McpEntry,
    install_kit,
    kit_unapplied,
    preflight_kit,
)
from arena.kits.loading import load_kit

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = GatewayEndpoint(url="http://gateway:7400", token="fixture-trial-token-28")
HOME = "/home/agent"


class FakeBox:
    def __init__(self, *, corrupt: str | None = None) -> None:
        self.files: dict[str, str] = {}
        self.executed: list[tuple[str, ...]] = []
        self.calls = 0
        self.corrupt = corrupt

    def write_text(self, path: str, content: str) -> None:
        self.calls += 1
        self.files[path] = content + "!" if path == self.corrupt else content

    def read_text(self, path: str) -> str:
        return self.files[path]

    def execute(self, command: Sequence[str]) -> str:
        self.calls += 1
        self.executed.append(tuple(command))
        return ""


def _mcp_json(entries: Sequence[McpEntry]) -> list[tuple[str, str]]:
    body = ";".join(
        f"{entry.name}={entry.url or entry.command}|{dict(entry.headers or {})}"
        for entry in entries
    )
    return [(f"{HOME}/.mcp", body)]


FULL = KitTarget(
    agent_id="claude-code",
    instructions_path=f"{HOME}/CLAUDE.md",
    skills_dir=f"{HOME}/skills",
    mcp=_mcp_json,
    settings=lambda settings: [(f"{HOME}/settings", repr(sorted(settings.items())))],
)


class RefusingResolver:
    def resolve(self, repository: str, ref: str) -> str:
        return "0" * 40


def _kit(tmp_path: Path, manifest: str, files: Mapping[str, bytes | str]) -> tuple[Kit, Path]:
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
    (tmp_path / "kit.yaml").write_text(manifest, encoding="utf-8")
    return load_kit(tmp_path / "kit.yaml", resolver=RefusingResolver()), tmp_path


def test_fixture_kit_is_copied_and_matches_the_source_bytes() -> None:
    kit = load_kit(ROOT / "kits" / "fixture-basic" / "kit.yaml")
    box = FakeBox()

    result = install_kit(box, kit, ROOT / "kits" / "fixture-basic", FULL, GATEWAY)

    skill = ROOT / "kits/fixture-basic/skills/smoke-greeting/SKILL.md"
    assert box.files[f"{HOME}/skills/smoke-greeting/SKILL.md"] == skill.read_text()
    assert (
        box.files[f"{HOME}/CLAUDE.md"] == (ROOT / "kits/fixture-basic/instructions.md").read_text()
    )
    assert set(result.written) == set(box.files)
    assert result.refused == ()


def test_no_kit_installs_nothing_and_touches_nothing(tmp_path: Path) -> None:
    box = FakeBox()

    assert install_kit(box, None, tmp_path, FULL, GATEWAY).written == ()
    empty, root = _kit(tmp_path, "id: empty\nversion: 1\n", {})
    assert install_kit(box, empty, root, FULL, GATEWAY).written == ()
    assert box.calls == 0 and box.files == {}


def test_installing_never_changes_the_host_agent_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    for name in (".claude/CLAUDE.md", ".claude.json", ".codex/config.toml"):
        (home / name).parent.mkdir(parents=True, exist_ok=True)
        (home / name).write_text(f"user's {name}")
    monkeypatch.setenv("HOME", str(home))

    def checksums() -> dict[str, str]:
        return {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(home.rglob("*"))
            if path.is_file()
        }

    before = checksums()
    kit = load_kit(ROOT / "kits" / "fixture-basic" / "kit.yaml")
    install_kit(FakeBox(), kit, ROOT / "kits" / "fixture-basic", FULL, GATEWAY)

    assert checksums() == before


def test_what_the_agent_cannot_take_is_refused_not_dropped(tmp_path: Path) -> None:
    kit, root = _kit(
        tmp_path,
        "id: k\nversion: 1\ninstructions: i.md\n"
        "skills: [{path: skills/s}, {git: https://x.test/r, ref: main}]\n"
        "mcp: [{name: docs, url: 'https://m.test/x'}, "
        "{name: cmd, command: srv, args: ['--k=${SETTING}']}]\n"
        "settings: {claude-code: {a: 1}}\n",
        {"i.md": "hello", "skills/s/SKILL.md": "skill"},
    )
    aider = KitTarget(agent_id="aider", instructions_path="/conventions.md")
    box = FakeBox()

    result = install_kit(box, kit, root, aider, GATEWAY)

    assert result.written == ("/conventions.md",)
    assert len(result.refused) == 4
    assert any(
        "skill skills/s: aider does not take skills" in refusal for refusal in result.refused
    )
    assert any("mcp docs: aider does not take MCP servers" in refusal for refusal in result.refused)
    assert not kit_unapplied(kit, result)


def test_a_kit_the_agent_refuses_entirely_is_unapplied(tmp_path: Path) -> None:
    kit, root = _kit(
        tmp_path, "id: k\nversion: 1\nskills: [{path: skills/s}]\n", {"skills/s/SKILL.md": "s"}
    )
    result = install_kit(FakeBox(), kit, root, KitTarget(agent_id="aider"), GATEWAY)

    assert result.written == () and result.refused
    assert kit_unapplied(kit, result)


def test_url_servers_point_at_the_gateway_and_keep_upstream_details_on_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCS_TOKEN", "fixture-daemon-header-28")
    kit, root = _kit(
        tmp_path,
        "id: k\nversion: 1\nmcp: [{name: docs, url: 'https://upstream.invalid/mcp', "
        "headers: {Authorization: 'Bearer ${DOCS_TOKEN}'}}, "
        "{name: local, command: srv, args: [--x]}]\n",
        {},
    )
    box = FakeBox()

    install_kit(box, kit, root, FULL, GATEWAY)

    config = box.files[f"{HOME}/.mcp"]
    assert "http://gateway:7400/mcp/docs" in config and "srv" in config
    everything = config + "".join(box.files.values()) + repr(box.executed)
    assert "upstream.invalid" not in everything
    assert "fixture-daemon-header-28" not in everything and "DOCS_TOKEN" not in everything


def test_unset_env_reference_fails_preflight_naming_the_variable(tmp_path: Path) -> None:
    kit, _ = _kit(
        tmp_path,
        "id: k\nversion: 1\nmcp: [{name: docs, url: 'https://m.test', "
        "headers: {Authorization: 'Bearer ${DOCS_TOKEN}', X-Team: '${TEAM_ID}'}}]\n",
        {},
    )

    with pytest.raises(KitPreflightError, match=r"\$\{DOCS_TOKEN\}, \$\{TEAM_ID\}"):
        preflight_kit(kit, {"DOCS_TOKEN": ""})
    preflight_kit(kit, {"DOCS_TOKEN": "a", "TEAM_ID": "b"})
    preflight_kit(None, {})


def test_a_kit_changed_after_loading_is_not_installed(tmp_path: Path) -> None:
    kit, root = _kit(tmp_path, "id: k\nversion: 1\ninstructions: i.md\n", {"i.md": "one"})
    (root / "i.md").write_text("two")
    box = FakeBox()

    with pytest.raises(KitInstallError, match="changed on disk"):
        install_kit(box, kit, root, FULL, GATEWAY)
    assert box.files == {}


def test_a_file_that_differs_in_the_container_stops_the_trial(tmp_path: Path) -> None:
    kit, root = _kit(tmp_path, "id: k\nversion: 1\ninstructions: i.md\n", {"i.md": "one"})

    with pytest.raises(KitInstallError, match="differs from the kit source"):
        install_kit(FakeBox(corrupt=f"{HOME}/CLAUDE.md"), kit, root, FULL, GATEWAY)


def test_skills_are_copied_with_scripts_executable_and_links_refused(tmp_path: Path) -> None:
    kit, root = _kit(
        tmp_path,
        "id: k\nversion: 1\nskills: [{path: skills/a}, {path: skills/b}]\n",
        {
            "skills/a/SKILL.md": "a",
            "skills/a/run.sh": "#!/bin/sh\necho hi\n",
            "skills/b/SKILL.md": "b",
        },
    )
    os.chmod(root / "skills/a/run.sh", 0o755)
    (root / "skills/b/escape").symlink_to("/etc/hosts")
    kit = load_kit(root / "kit.yaml")
    box = FakeBox()

    result = install_kit(box, kit, root, FULL, GATEWAY)

    assert f"{HOME}/skills/a/run.sh" in result.written
    assert ("chmod", "+x", f"{HOME}/skills/a/run.sh") in box.executed
    assert not any("/skills/b/" in path for path in box.files)
    assert any("skill skills/b" in refusal and "link" in refusal for refusal in result.refused)


def test_skill_paths_cannot_leave_the_kit(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside / "s").mkdir(parents=True)
    (outside / "s" / "SKILL.md").write_text("fixture")
    kit_dir = tmp_path / "kit"
    kit, root = _kit(kit_dir, "id: k\nversion: 1\nskills: [{path: ../outside/s}]\n", {"x": "y"})

    result = install_kit(FakeBox(), kit, root, FULL, GATEWAY)

    assert result.written == () and "not a folder inside the kit" in result.refused[0]
