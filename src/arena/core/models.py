"""The entities of DEV_PLAN §4. Everything else in the arena reads or writes these."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from arena.core.frozen import frozen_mapping
from arena.core.ids import content_id
from arena.core.modelref import ModelRef

NO_KIT = "none"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Mutable(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Kit and Contestant ---------------------------------------------------------------------


class SkillSource(Frozen):
    path: str | None = None
    git: str | None = None
    ref: str | None = None
    subdir: str | None = None


class McpServer(Frozen):
    name: str
    url: str | None = None
    command: str | None = None
    args: list[str] = []
    headers: dict[str, str] = {}  # values may hold ${ENV} references


class Kit(Frozen):
    """A versioned bundle. `hash` covers every file's bytes plus the canonical MCP and settings."""

    id: str
    version: int
    hash: str
    instructions: str | None = None
    skills: list[SkillSource] = []
    mcp: list[McpServer] = []
    settings: dict[str, dict[str, Any]] = {}


class Scaffold(Frozen):
    id: str
    version: str
    settings: dict[str, Any] = {}

    @field_validator("settings")
    @classmethod
    def _freeze_settings(cls, value: dict[str, Any]) -> dict[str, Any]:
        return frozen_mapping(value)


ScaffoldPrompt = Literal["native", "pinned"]


class Contestant(Frozen):
    """Identity is the hash of everything that can change behavior. The label is not part of it."""

    label: str | None = None
    model: ModelRef
    params: dict[str, Any] = {}
    scaffold: Scaffold | None = None
    scaffold_prompt: ScaffoldPrompt = "native"
    kit_hash: str = NO_KIT
    orchestration: str = "single"
    prompt_version: str | None = None
    hooks: tuple[str, ...] = ()

    @field_validator("params")
    @classmethod
    def _freeze_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        return frozen_mapping(value)

    def resolved_config(self) -> dict[str, Any]:
        config = self.model_dump(mode="json", exclude={"label", "id"})
        config["model"] = str(self.model)
        return config

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        return content_id(self.resolved_config())


# --- Task, Trial, Session -------------------------------------------------------------------

TaskKind = Literal["codegen", "agentic-code", "content", "web-artifact"]
TrialStatus = Literal["queued", "running", "succeeded", "failed", "errored", "timeout", "skipped"]
ErrorClass = Literal[
    "rate_limit",
    "quota",
    "credit",
    "subscription_limit",
    "auth",
    "auth_refresh",
    "model_refused",
    "bad_reply",
    "upstream",
    "timeout",
    "content_refusal",
]
CallPurpose = Literal["contestant", "judge", "orchestration", "arena"]


class Task(Frozen):
    id: str
    version: int
    kind: TaskKind
    prompt_file: str
    created_at: str | None = None
    scorers: list[dict[str, Any]] = []
    pairwise: bool = False


class TrialFlags(Frozen):
    unmetered: bool = False  # calls bypassed the gateway
    swapped: bool = False  # a reply came from a model other than the one requested
    subscription_served: bool = False  # subscription model, not the vendor's own client
    kit_unapplied: bool = False  # the agent never loaded the kit's skills or instructions


class Trial(Mutable):
    """Immutable once finished: a retry is a new trial with a higher `attempt`."""

    id: str
    run_id: str
    contestant_id: str
    task_id: str
    attempt: int = 1
    status: TrialStatus = "queued"
    error_class: ErrorClass | None = None  # set only when status is "errored"
    flags: TrialFlags = TrialFlags()
    started_at: datetime | None = None
    ended_at: datetime | None = None


class ToolCall(Frozen):
    name: str
    args_digest: str
    result_size: int = 0
    duration_ms: int = 0
    exit_status: int | None = None


class SkillEvent(Frozen):
    kind: Literal["listed", "loaded", "invoked"]
    skill: str
    kit_hash: str


class McpCall(Frozen):
    server: str
    tool: str
    duration_ms: int = 0
    status: str = "ok"


class FileTouched(Frozen):
    path: str
    added: int = 0
    removed: int = 0


class Turn(Frozen):
    call_ids: list[str] = []
    tool_calls: list[ToolCall] = []
    skill_events: list[SkillEvent] = []
    mcp_calls: list[McpCall] = []
    files: list[FileTouched] = []
    unmetered: bool = False  # in the native transcript with no matching gateway call


class Session(Mutable):
    id: str
    trial_id: str
    parent_session_id: str | None = None
    agent: str
    native_session_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: str = "running"
    turns: list[Turn] = []


# --- Call -----------------------------------------------------------------------------------


class Tokens(Frozen):
    in_: int = Field(default=0, alias="in")
    out: int = 0
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0

    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, serialize_by_alias=True
    )


class Try(Frozen):
    account_id: str
    status: int | None = None
    error_class: ErrorClass | None = None
    rest_ms: int = 0
    ms: int = 0


class PromptPart(Frozen):
    """Names and sizes only, never text."""

    kind: Literal["system", "tools", "instructions", "files", "conversation"]
    tokens: int


class Call(Frozen):
    """One upstream model request, written by the gateway as it decides."""

    id: str
    seq: int
    run_id: str
    trial_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    purpose: CallPurpose = "contestant"
    protocol_in: str
    protocol_out: str
    translated: bool = False
    provider: str
    account_id: str  # a hash: an API key id or a subscription session, never the credential
    model_asked: str
    model_served: str | None = None
    swapped: bool = False
    upstream: str | None = None
    effort_asked: str | None = None
    effort_applied: str | None = None
    tries: list[Try] = []
    status: int | None = None
    error_class: ErrorClass | None = None
    tokens: Tokens = Tokens()
    cost_usd: float | None = None  # None for flat-fee subscription calls, never an invented price
    price_version: str | None = None
    queue_ms: int = 0
    ttft_ms: int | None = None
    first_text_ms: int | None = None
    total_ms: int = 0
    cache: Literal["hit", "miss", "off"] = "off"
    prompt_parts: list[PromptPart] = []


# --- Artifact, Score -----------------------------------------------------------------------

RenderHint = Literal["code", "diff", "markdown", "html-sandbox", "svg", "image", "json"]


class Artifact(Frozen):
    sha256: str
    path: str
    mime: str
    render_hint: RenderHint


class Score(Frozen):
    """Scores from different scorer versions are never mixed into one aggregate."""

    trial_id: str
    scorer_id: str
    scorer_version: str
    value: float
    normalized: Annotated[float, Field(ge=0, le=1)]
    passed: bool | None = None
    rationale: str = ""
    evidence: dict[str, Any] = {}


# --- RunEvent ------------------------------------------------------------------------------

RunEventKind = Literal[
    "run_started",
    "trial_queued",
    "trial_started",
    "kit_installed",
    "call_queued",
    "call_try",
    "call_finished",
    "session_turn",
    "skill_event",
    "lane_changed",
    "trial_finished",
    "score_added",
    "budget",
    "run_finished",
]
RUN_EVENT_KINDS: tuple[str, ...] = get_args(RunEventKind)


class RunEvent(Frozen):
    """Append-only; `seq` is monotonic per run."""

    seq: int
    ts: datetime
    run_id: str
    kind: RunEventKind
    ref: str | None = None
    data: dict[str, Any] = {}
