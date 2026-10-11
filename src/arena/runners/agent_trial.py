"""Execute one configured agent in its isolated trial sandbox."""

from __future__ import annotations

import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any, cast

from arena.agents.aider import AiderAdapter
from arena.agents.base import AgentAdapter, GatewayEndpoint
from arena.agents.claude_code import ClaudeCodeAdapter
from arena.agents.codex_cli import CodexCliAdapter
from arena.agents.opencode import OpenCodeAdapter
from arena.agents.pi import PiAdapter
from arena.core.cas import ArtifactStore
from arena.core.ids import content_id
from arena.core.modelref import ModelRef
from arena.core.models import Contestant
from arena.core.store import Store
from arena.runners.agent_cli import AgentRunner, ReachedState
from arena.runners.completion import CompletionResult
from arena.runners.scheduler import TrialJob
from arena.sandbox.agent_box import DockerAgentBox, DockerExec
from arena.sandbox.docker import DockerSandbox, SandboxLimits, SandboxSpec


class AgentTrialError(RuntimeError):
    """The trial did not complete through its configured metered gateway."""


def adapter_for(contestant: Contestant, kit_root: Path | None = None) -> AgentAdapter:
    scaffold = contestant.scaffold
    if scaffold is None:
        raise ValueError("agent contestant requires a configured scaffold")
    match scaffold.id:
        case "aider":
            return cast(AgentAdapter, AiderAdapter(kit_root=kit_root))
        case "claude-code":
            return cast(AgentAdapter, ClaudeCodeAdapter(kit_root=kit_root))
        case "codex-cli":
            return cast(AgentAdapter, CodexCliAdapter())
        case "opencode":
            return cast(AgentAdapter, OpenCodeAdapter(kit_root=kit_root))
        case "pi":
            return cast(AgentAdapter, PiAdapter(kit_root=kit_root))
        case _:
            raise ValueError(f"unsupported agent scaffold: {scaffold.id}")


async def execute_agent_trial(
    job: TrialJob,
    run_id: str,
    *,
    task_file: Path,
    workspace_root: Path,
    store: Store,
    artifacts: ArtifactStore,
    gateway: Any,
    gateway_url: str,
    gateway_container: str,
    sandbox: DockerSandbox | None = None,
    limits: SandboxLimits | None = None,
    docker_exec: DockerExec | None = None,
    adapter: AgentAdapter | None = None,
    kit: Any = None,
    kit_root: Path | None = None,
) -> CompletionResult:
    """Run, verify gateway attribution, and persist trial output artifacts."""
    trial_id = content_id({"run_id": run_id, "job_id": job.id})
    token = gateway.token(trial_id)
    selected_adapter = adapter or adapter_for(job.contestant, kit_root)
    workspace = workspace_root / trial_id
    workspace.mkdir(parents=True, exist_ok=False)
    prompt_name = Path(job.task.prompt_file).name
    prompt_relative = Path("task-input") / prompt_name
    prompt_target = workspace / prompt_relative
    prompt_target.parent.mkdir(parents=True, exist_ok=True)
    prompt_target.write_bytes(task_file.read_bytes())
    task = job.task.model_copy(update={"prompt_file": f"/workspace/{prompt_relative.as_posix()}"})
    spec = SandboxSpec(
        trial_id=trial_id,
        image=selected_adapter.image,
        command=("sleep", "infinity"),
        workspace=workspace,
        gateway_url=gateway_url,
        gateway_token=token,
        gateway_container=gateway_container,
    )
    sandbox_runner = sandbox or DockerSandbox()
    async with sandbox_runner.start(spec, limits) as container:
        box = (
            DockerAgentBox(container, docker=docker_exec)
            if docker_exec is not None
            else DockerAgentBox(container)
        )
        result = await AgentRunner(first_step_timeout=60).run(
            adapter=selected_adapter,
            task=task,
            settings={
                **dict(job.contestant.params),
                "scaffold_prompt": job.contestant.scaffold_prompt,
            },
            box=box,
            gateway=gateway,
            endpoint=GatewayEndpoint(url=gateway_url, token=token),
            model=ModelRef.parse(str(job.contestant.model)),
            trial_token=token,
            kit=kit,
        )
        if result.state is not ReachedState.REACHED:
            raise AgentTrialError(
                result.detail or f"agent did not complete a metered gateway call ({result.state})"
            )
        calls = await gateway.calls(token)
        if not calls:
            raise AgentTrialError("gateway reported a reached state without persisted calls")
        _persist_workspace(store, artifacts, trial_id, workspace)
        if result.transcript is not None:
            content = getattr(result.transcript, "content", None)
            if isinstance(content, str):
                _persist_artifact(
                    store,
                    artifacts,
                    trial_id,
                    "transcript.txt",
                    content.encode("utf-8"),
                    "text/plain",
                    "markdown",
                )
        total_cost = sum(call.cost_usd or 0.0 for call in calls)
        return CompletionResult(text="", cost_usd=total_cost)


def _persist_workspace(
    store: Store, artifacts: ArtifactStore, trial_id: str, workspace: Path
) -> None:
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(workspace).as_posix()
        if relative.startswith("task-input/"):
            continue
        if not _safe_artifact_path(relative):
            continue
        mime = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        suffix = Path(relative).suffix.lower()
        if suffix in {".html", ".htm"}:
            hint = "html-sandbox"
        elif suffix == ".md":
            hint = "markdown"
        else:
            hint = "code"
        _persist_artifact(store, artifacts, trial_id, relative, path.read_bytes(), mime, hint)


def _persist_artifact(
    store: Store,
    artifacts: ArtifactStore,
    trial_id: str,
    path: str,
    content: bytes,
    mime: str,
    render_hint: str,
) -> None:
    digest = artifacts.put(content)
    store.execute(
        "INSERT OR REPLACE INTO trial_artifacts(trial_id, path, sha256, mime, render_hint) "
        "VALUES (?, ?, ?, ?, ?)",
        (trial_id, path, digest, mime, render_hint),
    )


def _safe_artifact_path(value: str) -> bool:
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)
