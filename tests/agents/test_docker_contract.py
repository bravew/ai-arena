"""Contract check in a real container: a pinned CLI sends the trial token on the right protocol.

Needs a local Docker daemon and the `node:20-bullseye-slim` image; skipped otherwise. The mock
gateway runs in its own container and is reached only through the trial's internal network.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from arena.sandbox import DockerAgentBox, DockerSandbox, SandboxSpec

FIXTURE = Path(__file__).parent / "docker_cli_fixture"
IMAGE = "arena-contract-fixture:test"
TOKEN = "trial-token-27"


def _docker(*args: str) -> str:
    return subprocess.run(
        ("docker", *args), capture_output=True, text=True, check=True, timeout=300
    ).stdout.strip()


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        _docker("image", "inspect", "node:20-bullseye-slim")
    except (subprocess.SubprocessError, OSError):
        return False
    return True


pytestmark = pytest.mark.skipif(not _docker_ready(), reason="Docker or the node image is missing")


@pytest.fixture(scope="module")
def image() -> str:
    _docker("build", "--quiet", "--tag", IMAGE, str(FIXTURE))
    return IMAGE


@pytest.fixture
def gateway(image: str, tmp_path: Path) -> Iterator[tuple[str, Path]]:
    results = tmp_path / "results"
    results.mkdir()
    results.chmod(0o777)
    name = f"arena-contract-gw-{uuid.uuid4().hex[:8]}"
    _docker(
        "run", "--detach", "--name", name, "--mount",
        f"type=bind,src={results},dst=/results", "--entrypoint", "node",
        image, "/opt/arena-fixture/mock-gateway.js",
    )  # fmt: skip
    try:
        yield name, results
    finally:
        subprocess.run(("docker", "rm", "-f", name), capture_output=True, check=False)


def test_pinned_cli_sends_the_trial_token_on_the_expected_protocol(
    image: str, gateway: tuple[str, Path], tmp_path: Path
) -> None:
    gateway_name, results = gateway
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    spec = SandboxSpec(
        trial_id=f"contract-{uuid.uuid4().hex[:8]}",
        image=image,
        command=("node", "/opt/arena-fixture/mock-agent.js", "run"),
        workspace=workspace,
        gateway_url="http://gateway:7400",
        gateway_token=TOKEN,
        gateway_container=gateway_name,
    )

    async def scenario() -> tuple[str, str]:
        async with DockerSandbox().start(spec) as container:
            box = DockerAgentBox(container)
            version = await asyncio.to_thread(box.execute, ("node", spec.command[1], "--version"))
            await asyncio.to_thread(box.write_text, "/home/agent/config", "wired")
            wired = await asyncio.to_thread(box.read_text, "/home/agent/config")
            await asyncio.to_thread(box.execute, spec.command)
        return version.strip(), wired

    version, wired = asyncio.run(scenario())

    assert version == "mock-agent 1.2.3"
    assert wired == "wired"  # /home/agent is writable although the root filesystem is read-only
    first = json.loads((results / "first-call.json").read_text())
    assert first == {"token": TOKEN, "protocol": "anthropic"}
