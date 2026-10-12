"""Preflight and execution of trials served by a vendor CLI on a subscription (`local-cli`).

`arena run` routes a contestant here when its provider's subscription has `runtime: local-cli`.
A trial never switches model or runtime partway: a failure is an errored trial, and `--resume`
runs it again.
"""

from __future__ import annotations

import json
import mimetypes
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from arena.agents.local import SignIn, SignInError
from arena.catalog.config import CatalogModel
from arena.core.cas import ArtifactStore
from arena.core.ids import content_id
from arena.core.models import Contestant, RunEvent
from arena.core.store import Store
from arena.gateway.classify import FailureClass
from arena.gateway.rests import RestBook
from arena.providers.accounts import account_label
from arena.providers.accounts.probe import detect_account
from arena.providers.config import Provider
from arena.providers.runtimes import (
    RuntimeStatus,
    check_model,
    spec_for_vendor,
)
from arena.providers.runtimes.detect import find_binary
from arena.runners.account_context import SignInSnapshot, account_key
from arena.runners.completion import CompletionResult
from arena.runners.local_cli import (
    FailureKind,
    LocalCliRequest,
    adapter_for_runtime,
    run_local_cli,
)
from arena.runners.scheduler import TrialJob
from arena.runners.usage_capture import UsageCapture

DEFAULT_TIMEOUT_SECONDS = 900.0
ANSWER_PATH = "answer.md"
PROMPT_SUFFIX = "\n\nWrite your final answer to a file named answer.md in the current directory."


class LocalCliTrialError(RuntimeError):
    """The trial did not complete through its vendor CLI."""


@dataclass(frozen=True)
class PreflightIssue:
    contestant: str
    code: str
    message: str


def preflight_local_cli(
    contestant: Contestant,
    provider: Provider,
    models: Mapping[str, CatalogModel],
    sign_ins: SignInSnapshot,
    environ: Mapping[str, str],
) -> list[PreflightIssue]:
    """Each missing precondition has its own issue. No model request is made.

    The sign-in was resolved once for the whole run, so preflight reads the snapshot rather than
    resolving again; a vendor with no usable sign-in is already recorded there as `sign_in`.
    """
    name = contestant.label or contestant.id
    ref = str(contestant.model)
    issues: list[PreflightIssue] = []

    def add(code: str, message: str) -> None:
        issues.append(PreflightIssue(name, code, message))

    if not provider.enabled:
        add("provider_disabled", f"provider {provider.id} is disabled in providers.yaml")
    if contestant.scaffold is not None:
        add(
            "scaffold",
            f"{ref} runs in the vendor CLI itself; remove the {contestant.scaffold.id} scaffold",
        )
    model = models.get(ref)
    if model is None or model.pricing != "subscription":
        add("catalog", f"{ref} must be a `pricing: subscription` entry in the model catalog")
    subscription = provider.subscription
    spec = spec_for_vendor(subscription.vendor) if subscription is not None else None
    if spec is None:
        add("runtime", f"no vendor CLI is recorded for provider {provider.id}")
        return issues
    error = sign_ins.error_for(spec.vendor)
    if error is not None:
        add("sign_in", f"{spec.runtime}: {error}")
        return issues
    try:
        selected = sign_ins.for_vendor(spec.vendor)
    except SignInError as error:
        add("sign_in", f"{spec.runtime}: {error}")
        return issues
    report = detect_account(spec, selected, environ)
    if report.status is RuntimeStatus.OK:
        report = check_model(report, contestant.model.model, environ)
    if report.status is not RuntimeStatus.OK:
        hint = f" ({report.hint})" if report.hint else ""
        add(report.status.value, f"{spec.runtime}: {report.detail}{hint}")
    return issues


@dataclass(frozen=True)
class LocalCliRun:
    """What the trials of one run share."""

    store: Store
    artifacts: ArtifactStore
    capture: UsageCapture
    sign_ins: SignInSnapshot
    environ: Mapping[str, str]
    rests: RestBook
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    scratch_base: Path | None = None


def _sign_in(shared: LocalCliRun, vendor: str, runtime: str) -> SignIn:
    try:
        return shared.sign_ins.for_vendor(vendor)
    except SignInError as error:
        raise LocalCliTrialError(f"{runtime}: {error}") from error


async def execute_local_cli_trial(
    job: TrialJob,
    run_id: str,
    shared: LocalCliRun,
    provider: Provider,
    prompt: str,
    fixtures: Mapping[str, bytes] | None = None,
) -> CompletionResult:
    subscription = provider.subscription
    spec = spec_for_vendor(subscription.vendor) if subscription is not None else None
    if spec is None:
        raise LocalCliTrialError(f"no vendor CLI is recorded for provider {provider.id}")
    sign_in = _sign_in(shared, spec.vendor, spec.runtime)
    # The lane and rest key is the account's private hash, not the redacted display label, so two
    # aliases of one account share both. `lane_for` was built from the same key before scheduling.
    account = account_key(spec.vendor, sign_in)
    if shared.rests.is_resting(account):
        raise LocalCliTrialError(
            f"{provider.id}: subscription limit reached earlier in this run; "
            "resume after your plan resets"
        )
    binary = find_binary(spec, shared.environ)
    if binary is None:
        raise LocalCliTrialError(f"{spec.runtime} is not installed; run `{spec.install_hint}`")
    trial_id = _trial_id(run_id, job)
    result = await run_local_cli(
        LocalCliRequest(
            adapter=adapter_for_runtime(spec.runtime),
            binary=binary,
            model=job.contestant.model.model,
            prompt=prompt + PROMPT_SUFFIX,
            sign_in=sign_in,
            timeout=shared.timeout,
            environ=shared.environ,
            scratch_base=shared.scratch_base,
            fixtures=fixtures or {},
        )
    )
    if result.failure is FailureKind.LIMIT_REACHED:
        shared.rests.rest(account, FailureClass.SUBSCRIPTION_LIMIT)
    outcome = await shared.capture.capture(
        run_id=run_id,
        trial_id=trial_id,
        model_ref=str(job.contestant.model),
        runtime=spec.runtime,
        account_id=f"cli-{account_label(spec.vendor, dict(sign_in.files))}",
        result=result,
    )
    if not outcome.reached:
        raise LocalCliTrialError(f"{spec.runtime}: {outcome.reason}")
    assert result.summary is not None
    outputs = dict(result.outputs)
    outputs.setdefault(ANSWER_PATH, result.summary.final_text.encode("utf-8"))
    _persist(shared.store, shared.artifacts, trial_id, outputs)
    return CompletionResult(
        text=outputs[ANSWER_PATH].decode("utf-8", errors="replace"), cost_usd=None
    )


def _trial_id(run_id: str, job: TrialJob) -> str:
    return content_id({"run_id": run_id, "job_id": job.id})


def _persist(
    store: Store, artifacts: ArtifactStore, trial_id: str, outputs: Mapping[str, bytes]
) -> None:
    for relative, content in sorted(outputs.items()):
        path = PurePosixPath(relative)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            continue
        suffix = path.suffix.lower()
        hint = (
            "html-sandbox"
            if suffix in {".html", ".htm"}
            else "markdown"
            if suffix == ".md"
            else "code"
        )
        mime = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        store.execute(
            "INSERT OR REPLACE INTO trial_artifacts(trial_id, path, sha256, mime, render_hint) "
            "VALUES (?, ?, ?, ?, ?)",
            (trial_id, relative, artifacts.put(content), mime, hint),
        )


# `calls` is unique on (run_id, seq) and the gateway container numbers its own calls from 1, so
# the calls of CLI trials start well above anything it will reach.
CLI_SEQ_BASE = 1_000_000


def next_cli_seq(store: Store, run_id: str) -> int:
    """The first call sequence number a CLI trial may use, also after a resume."""
    row = store.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM calls WHERE run_id=?", (run_id,)
    ).fetchone()
    return max(CLI_SEQ_BASE, int(row[0]) + 1 if row is not None else 0)


def read_staged_events(root: Path, run_id: str) -> list[RunEvent]:
    """The events the CLI trials wrote to a private root, for the run recorder to merge."""
    path = root / run_id / "events.jsonl"
    if not path.exists():
        return []
    events: list[RunEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            payload = json.loads(line)
            payload.pop("event_version", None)
            events.append(RunEvent.model_validate(payload))
    return events
