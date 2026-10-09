from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from arena.core.models import Kit, McpServer, SkillSource
from arena.kits.git import SubprocessGitResolver, pin_skill_source
from arena.kits.hashing import hash_kit
from arena.kits.loading import load_kit


def make_kit(**changes: object) -> Kit:
    values: dict[str, object] = {"id": "team", "version": 1, "hash": "pending"}
    return Kit.model_validate(values | changes)


def test_kit_hash_tracks_files_and_configuration_but_not_env_values(tmp_path: Path) -> None:
    (tmp_path / "skills" / "tdd").mkdir(parents=True)
    skill = tmp_path / "skills" / "tdd" / "SKILL.md"
    skill.write_bytes(b"one byte")
    kit = make_kit(
        mcp=[
            McpServer(
                name="docs",
                url="https://example.test",
                headers={"Authorization": "Bearer ${DOCS_TOKEN}"},
            )
        ]
    )

    original = hash_kit(kit, tmp_path, env={"DOCS_TOKEN": "first"})
    assert hash_kit(kit, tmp_path, env={"DOCS_TOKEN": "second"}) == original
    skill.write_bytes(b"one byte!")
    assert hash_kit(kit, tmp_path) != original
    assert hash_kit(kit, tmp_path, env={"DOCS_TOKEN": "first"}) != original


def test_mcp_header_reference_name_is_part_of_hash(tmp_path: Path) -> None:
    first = make_kit(
        mcp=[
            McpServer(
                name="docs",
                url="https://example.test",
                headers={"Authorization": "Bearer ${DOCS_TOKEN}"},
            )
        ]
    )
    second = make_kit(
        mcp=[
            McpServer(
                name="docs",
                url="https://example.test",
                headers={"Authorization": "Bearer ${OTHER_TOKEN}"},
            )
        ]
    )
    assert hash_kit(first, tmp_path) != hash_kit(second, tmp_path)


def test_load_kit_parses_manifest_and_computes_hash(tmp_path: Path) -> None:
    (tmp_path / "kit.yaml").write_text(
        "id: team\nversion: 1\ninstructions: instructions.md\nskills: []\nmcp: []\nsettings: {}\n",
        encoding="utf-8",
    )
    (tmp_path / "instructions.md").write_text("Be concise.\n", encoding="utf-8")
    kit = load_kit(tmp_path / "kit.yaml")
    assert kit.id == "team"
    assert len(kit.hash) == 64


def test_load_kit_reports_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot read kit manifest"):
        load_kit(tmp_path / "missing.yaml")


def test_git_tag_resolves_to_pinned_commit_in_local_bare_repo(tmp_path: Path) -> None:
    work = tmp_path / "source"
    work.mkdir()
    subprocess.run(["git", "init", str(work)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(work),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "initial",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(work), "tag", "v1"], check=True, capture_output=True)
    bare = tmp_path / "skills.git"
    subprocess.run(
        ["git", "clone", "--bare", str(work), str(bare)], check=True, capture_output=True
    )

    pinned = pin_skill_source(
        SkillSource(git=str(bare), ref="v1", subdir="skills/tdd"),
        SubprocessGitResolver(),
    )
    resolved = subprocess.run(
        ["git", "--git-dir", str(bare), "rev-parse", "v1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert pinned.ref == resolved
    assert len(pinned.ref or "") == 40


def test_git_ref_is_required_for_git_skill() -> None:
    with pytest.raises(ValueError, match="missing a ref"):
        pin_skill_source(
            SkillSource(git="https://example.test/skills.git"), SubprocessGitResolver()
        )
