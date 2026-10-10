"""Install a kit into a trial container through the AgentBox (DEV_PLAN §6.4).

An adapter describes where its agent reads instructions, skills, MCP servers and settings as a
`KitTarget`; this module copies the files, checks their content identity, and records anything the
agent refuses. Nothing is written on the host.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from arena.agents.base import AgentBox, GatewayEndpoint, KitInstall
from arena.core.models import Kit, McpServer
from arena.kits.hashing import hash_kit

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_MCP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class KitPreflightError(ValueError):
    """A kit cannot run: an `${ENV}` reference has no value on the daemon."""


class KitInstallError(RuntimeError):
    """The files in the container are not the kit's files. The trial must not start."""


@dataclass(frozen=True)
class McpEntry:
    """One MCP server as the container will see it. URL servers point at the gateway."""

    name: str
    url: str | None = None
    headers: Mapping[str, str] | None = None
    command: str | None = None
    args: tuple[str, ...] = ()


FileWriter = Callable[[Sequence[McpEntry]], Sequence[tuple[str, str]]]
SettingsWriter = Callable[[Mapping[str, object]], Sequence[tuple[str, str]]]


@dataclass(frozen=True)
class KitTarget:
    """Where one agent reads a kit. `None` means the agent cannot take that kind of item."""

    agent_id: str
    instructions_path: str | None = None
    skills_dir: str | None = None
    mcp: FileWriter | None = None
    accepts_url_mcp: bool = True
    accepts_command_mcp: bool = True
    settings: SettingsWriter | None = None


def env_references(kit: Kit) -> tuple[str, ...]:
    """Names referenced by URL-server headers, resolved on the daemon."""
    names = {
        name
        for server in kit.mcp
        if server.url
        for value in server.headers.values()
        for name in _ENV_REFERENCE.findall(value)
    }
    return tuple(sorted(names))


def preflight_kit(kit: Kit | None, env: Mapping[str, str]) -> None:
    """Fail naming every unset daemon-side variable before any trial starts."""
    if kit is None:
        return
    missing = [name for name in env_references(kit) if not env.get(name)]
    if missing:
        listed = ", ".join(f"${{{name}}}" for name in missing)
        raise KitPreflightError(f"kit {kit.id!r} needs unset environment variables: {listed}")


def install_kit(
    box: AgentBox,
    kit: Kit | None,
    root: Path,
    target: KitTarget,
    gateway: GatewayEndpoint,
) -> KitInstall:
    """Stage files, verify staged kit identity, then copy and verify container bytes."""
    if kit is None or not _has_content(kit):
        return KitInstall(written=())

    files: dict[str, bytes] = {}
    refused: list[str] = []
    _instructions(kit, root, target, files, refused)
    _skills(kit, root, target, files, refused)
    entries = _mcp_entries(kit, target, gateway, refused)
    for path, content in _mcp_files(target, entries):
        files[path] = content.encode()
    _settings(kit, target, files, refused)

    # Check the manifest's complete identity after staging. A concurrent edit between the read
    # and this hash fails before the first container write; compare copied source paths as well.
    if hash_kit(kit, root) != kit.hash:
        raise KitInstallError(f"kit {kit.id!r} changed on disk after it was loaded")
    _verify_staged_source(kit, root, target, files)

    executable: set[str] = set()
    for skill in kit.skills:
        if not skill.path or target.skills_dir is None:
            continue
        found = _skill_files(root, skill.path)
        if isinstance(found, str):
            continue
        base = PurePosixPath(target.skills_dir) / Path(skill.path).name
        for relative in found:
            source = root / skill.path / relative
            if os.access(source, os.X_OK):
                executable.add(str(base / relative))
    for path, data in files.items():
        box.write_text(path, data.decode())
        if path in executable:
            box.execute(("chmod", "+x", path))
    for path, data in files.items():
        if _digest(box.read_text(path).encode()) != _digest(data):
            raise KitInstallError(f"{path} in the container differs from the kit source")
    return KitInstall(written=tuple(files), refused=tuple(refused))


def _verify_staged_source(
    kit: Kit, root: Path, target: KitTarget, files: Mapping[str, bytes]
) -> None:
    expected: dict[str, bytes] = {}
    if kit.instructions and target.instructions_path:
        data = _read_text(root, kit.instructions)
        if data is not None:
            expected[target.instructions_path] = data
    for skill in kit.skills:
        if not skill.path or target.skills_dir is None:
            continue
        found = _skill_files(root, skill.path)
        if not isinstance(found, str):
            base = PurePosixPath(target.skills_dir) / Path(skill.path).name
            expected.update({str(base / relative): data for relative, data in found.items()})
    for path, data in expected.items():
        if files.get(path) != data:
            raise KitInstallError(f"kit source for {path} changed while it was staged")


def kit_unapplied(kit: Kit | None, install: KitInstall) -> bool:
    """True when a non-empty kit contributed no files or MCP config to the agent."""
    return kit is not None and _has_content(kit) and not install.written


def _has_content(kit: Kit) -> bool:
    return bool(kit.instructions or kit.skills or kit.mcp or kit.settings)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_text(root: Path, relative: str) -> bytes | None:
    """File bytes under the kit root; None if it escapes the root, is a link or is not text."""
    path = root / relative
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            return None
        data = path.read_bytes()
        data.decode()
    except (OSError, UnicodeDecodeError):
        return None
    return data


def _instructions(
    kit: Kit, root: Path, target: KitTarget, files: dict[str, bytes], refused: list[str]
) -> None:
    if not kit.instructions:
        return
    if target.instructions_path is None:
        refused.append(f"instructions: {target.agent_id} has no instructions file")
        return
    data = _read_text(root, kit.instructions)
    if data is None:
        refused.append(f"instructions: {kit.instructions} is not a readable text file in the kit")
        return
    files[target.instructions_path] = data


def _skills(
    kit: Kit, root: Path, target: KitTarget, files: dict[str, bytes], refused: list[str]
) -> None:
    for skill in kit.skills:
        label = skill.path or skill.git or "skill"
        if target.skills_dir is None:
            refused.append(f"skill {label}: {target.agent_id} does not take skills")
            continue
        if skill.git or skill.path is None:
            refused.append(f"skill {label}: git skill sources have no checkout step yet")
            continue
        found = _skill_files(root, skill.path)
        if isinstance(found, str):
            refused.append(f"skill {label}: {found}")
            continue
        base = PurePosixPath(target.skills_dir) / Path(skill.path).name
        files.update({str(base / relative): data for relative, data in found.items()})


def _skill_files(root: Path, skill_path: str) -> dict[str, bytes] | str:
    folder = root / skill_path
    if (
        folder.is_symlink()
        or not folder.is_dir()
        or not folder.resolve().is_relative_to(root.resolve())
    ):
        return "not a folder inside the kit"
    if not (folder / "SKILL.md").is_file():
        return "has no SKILL.md"
    found: dict[str, bytes] = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            return f"{path.relative_to(folder)} is a link, which is never copied"
        if not path.is_file():
            continue
        relative = path.relative_to(folder).as_posix()
        data = _read_text(root, path.relative_to(root).as_posix())
        if data is None:
            return f"{relative} is not a text file"
        found[relative] = data
    return found


def _mcp_entries(
    kit: Kit, target: KitTarget, gateway: GatewayEndpoint, refused: list[str]
) -> list[McpEntry]:
    entries: list[McpEntry] = []
    for server in kit.mcp:
        reason = _mcp_refusal(server, target)
        if reason is not None:
            refused.append(f"mcp {server.name}: {reason}")
        elif server.url:
            entries.append(
                McpEntry(
                    name=server.name,
                    url=f"{gateway.url.rstrip('/')}/mcp/{server.name}",
                    headers={"Authorization": f"Bearer {gateway.token}"},
                )
            )
        else:
            entries.append(
                McpEntry(name=server.name, command=server.command, args=tuple(server.args))
            )
    return entries


def _mcp_refusal(server: McpServer, target: KitTarget) -> str | None:
    if target.mcp is None:
        return f"{target.agent_id} does not take MCP servers"
    if not _MCP_NAME.fullmatch(server.name):
        return "name must be letters, digits, '_' or '-'"
    if server.url:
        return None if target.accepts_url_mcp else f"{target.agent_id} takes no URL MCP servers"
    if not server.command:
        return "has neither a url nor a command"
    if (server.command and _ENV_REFERENCE.search(server.command)) or any(
        _ENV_REFERENCE.search(argument) for argument in server.args
    ):
        return "a command server cannot use ${ENV}: the value would enter the container"
    if not target.accepts_command_mcp:
        return f"{target.agent_id} takes no command MCP servers"
    return None


def _mcp_files(target: KitTarget, entries: Sequence[McpEntry]) -> Sequence[tuple[str, str]]:
    if not entries or target.mcp is None:
        return ()
    return target.mcp(entries)


def _settings(kit: Kit, target: KitTarget, files: dict[str, bytes], refused: list[str]) -> None:
    wanted = kit.settings.get(target.agent_id)
    if wanted is None:
        return
    if target.settings is None:
        refused.append(f"settings: {target.agent_id} takes no settings")
        return
    files.update({path: content.encode() for path, content in target.settings(wanted)})
