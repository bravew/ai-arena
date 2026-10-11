"""Run-level identity, call polling, and Docker gateway lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import httpx

from arena.agents.base import AgentProtocol
from arena.core.models import Call
from arena.core.store import Store
from arena.gateway.auth import Purpose, parse_token, token_for_trial
from arena.providers.config import ProviderConfig

GATEWAY_IMAGE = "arena-gateway:dev"


class GatewayRuntimeError(RuntimeError):
    """The run-scoped gateway could not be started or reached safely."""


class GatewayContainer:
    """Build, start, health-check, and stop the run's gateway container."""

    def __init__(
        self,
        *,
        project_root: Path,
        run_id: str,
        trial_models: dict[str, str],
        providers_path: Path,
        catalog_path: Path,
        database_path: Path,
        event_root: Path,
        subscriptions_root: Path,
        budget_usd: float,
        providers: ProviderConfig,
        provider_ids: set[str],
        timeout: float = 30.0,
        docker: Any = None,
        http_get: Any = None,
        port_factory: Any = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.run_id = run_id
        self.trial_models = trial_models
        self.providers_path = providers_path.resolve()
        self.catalog_path = catalog_path.resolve()
        self.database_path = database_path.resolve()
        self.event_root = event_root.resolve()
        self.subscriptions_root = subscriptions_root.resolve()
        self.budget_usd = budget_usd
        self.providers = providers
        self.provider_ids = provider_ids
        self.timeout = timeout
        self._docker = docker or _docker
        self._http_get = http_get or httpx.get
        self.name = f"arena-gateway-{uuid.uuid4().hex[:12]}"
        self.port = (port_factory or _free_port)()
        self._started = False
        self._image_ready = False

    @property
    def url(self) -> str:
        return "http://gateway:8765"

    @property
    def running(self) -> bool:
        return self._started

    def preflight(self, images: list[str]) -> None:
        for provider in self.providers.providers:
            if not provider.enabled or provider.id not in self.provider_ids:
                continue
            if provider.subscription is not None:
                raise GatewayRuntimeError(
                    "subscription credential runtime is not configured for agent trials"
                )
            if not provider.keys:
                raise GatewayRuntimeError(f"provider has no configured credential: {provider.id}")
            for key in provider.keys:
                if not os.environ.get(key.env):
                    raise GatewayRuntimeError(f"provider credential is unavailable: {key.env}")
        self.check_images(images)

    def check_images(self, images: list[str]) -> None:
        try:
            self._docker("image", "inspect", GATEWAY_IMAGE, cwd=self.project_root)
            self._image_ready = True
        except GatewayRuntimeError:
            self._image_ready = False
        for image in sorted(set(images)):
            try:
                self._docker("image", "inspect", image, cwd=self.project_root)
            except GatewayRuntimeError as error:
                raise GatewayRuntimeError(
                    f"required agent image is unavailable: {image}; "
                    "build its pinned adapter image first"
                ) from error

    def start(self) -> None:
        self.subscriptions_root.mkdir(parents=True, exist_ok=True)
        self.event_root.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._image_ready:
            self._docker(
                "build",
                "--file",
                "docker/gateway.Dockerfile",
                "--tag",
                GATEWAY_IMAGE,
                ".",
                cwd=self.project_root,
            )
            self._image_ready = True
        env_args: list[str] = []
        for provider in self.providers.providers:
            if not provider.enabled or provider.id not in self.provider_ids:
                continue
            for key in provider.keys:
                env_args.extend(("--env", key.env))
        args = [
            "run",
            "--detach",
            "--name",
            self.name,
            "--publish",
            f"127.0.0.1:{self.port}:8765",
            "--mount",
            f"type=bind,src={self.providers_path},dst=/run/providers.yaml,readonly",
            "--mount",
            f"type=bind,src={self.catalog_path},dst=/run/models.yaml,readonly",
            "--mount",
            f"type=bind,src={self.database_path.parent},dst=/run/data",
            "--mount",
            f"type=bind,src={self.event_root},dst=/run/events",
            "--mount",
            f"type=bind,src={self.subscriptions_root},dst=/run/subscriptions,readonly",
            *env_args,
            GATEWAY_IMAGE,
            "--run-id",
            self.run_id,
            *[
                part
                for trial_id, model_ref in self.trial_models.items()
                for part in ("--trial-model", f"{trial_id}={model_ref}")
            ],
            "--providers",
            "/run/providers.yaml",
            "--catalog",
            "/run/models.yaml",
            "--database",
            f"/run/data/{self.database_path.name}",
            "--events",
            "/run/events",
            "--subscriptions",
            "/run/subscriptions",
            "--budget-usd",
            str(self.budget_usd),
        ]
        self._docker(*args, cwd=self.project_root)
        self._started = True
        self._wait_ready()

    def stop(self) -> None:
        if self._started:
            with suppress(GatewayRuntimeError):
                self._docker("rm", "--force", self.name, cwd=self.project_root)
            self._started = False

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.timeout
        url = f"http://127.0.0.1:{self.port}/arena/health"
        while time.monotonic() < deadline:
            try:
                response = self._http_get(
                    url,
                    headers={"Authorization": "Bearer arena-ops"},
                    timeout=0.5,
                )
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        self.stop()
        raise GatewayRuntimeError("gateway did not become healthy before startup timed out")


def _docker(*args: str, cwd: Path) -> str:
    try:
        result = subprocess.run(
            ("docker", *args),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", "")
        raise GatewayRuntimeError(f"Docker gateway operation failed: {detail or error}") from error
    return result.stdout.strip()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class RunGateway:
    """Allocate call identities and read persisted calls for each trial."""

    def __init__(
        self,
        run_id: str,
        store: Store,
        event_root: Path,
        *,
        account_id: str = "provider",
    ) -> None:
        token_for_trial("trial-validation")
        if not run_id or not account_id:
            raise ValueError("run and account identities must not be empty")
        self.run_id = run_id
        self.store = store
        self.event_root = event_root
        self.account_id = account_id
        self._sequence = 0
        self._lock = threading.Lock()

    def token(self, trial_id: str) -> str:
        return token_for_trial(trial_id)

    def calls_for_trial(self, trial_id: str) -> list[Call]:
        token_for_trial(trial_id)
        rows = self.store.execute(
            "SELECT details_json FROM calls WHERE run_id=? AND trial_id=? ORDER BY seq",
            (self.run_id, trial_id),
        ).fetchall()
        return [Call.model_validate(json.loads(row[0])) for row in rows]

    async def wait_for_call(
        self, trial_token: str, protocol: AgentProtocol, timeout: float
    ) -> bool:
        trial_id = _trial_from_token(trial_token)
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if any(call.protocol_in == protocol for call in self.calls_for_trial(trial_id)):
                return True
            await asyncio.sleep(min(0.05, max(0.0, deadline - asyncio.get_running_loop().time())))
        return any(call.protocol_in == protocol for call in self.calls_for_trial(trial_id))

    async def calls(self, trial_token: str) -> list[Call]:
        return self.calls_for_trial(_trial_from_token(trial_token))

    def call_events_for_trial(self, trial_id: str) -> list[dict[str, Any]]:
        token_for_trial(trial_id)
        path = self.event_root / self.run_id / "events.jsonl"
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if not isinstance(event, dict):
                continue
            event_dict = cast(dict[str, Any], event)
            if event_dict.get("run_id") != self.run_id:
                raise ValueError("gateway event file contains a different run")
            data = event_dict.get("data", {})
            if not isinstance(data, dict):
                continue
            data_dict = cast(dict[str, Any], data)
            if event_dict.get("kind") == "call_finished" and data_dict.get("trial_id") == trial_id:
                events.append(event_dict)
        return events


def _trial_from_token(value: str) -> str:
    caller = parse_token(value)
    if caller is None or caller.purpose is not Purpose.TRIAL or caller.trial_id is None:
        raise ValueError("gateway observer requires a trial token")
    return caller.trial_id


def read_event_file(path: Path, run_id: str) -> list[dict[str, Any]]:
    """Read appended events written by the separate gateway process."""
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("run_id") != run_id:
            raise ValueError("gateway event file contains a different run")
        events.append(row)
    return events
