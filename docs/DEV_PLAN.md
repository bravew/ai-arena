# AI Arena — Development Plan

Status: draft rev 3 for review · Date: 2026-10-08 · Owner: bravew

### What changed in rev 3

- **Kits.** A contestant is now *model × agent × kit*. A kit is a versioned bundle of the custom skills, instructions, MCP servers, and agent settings we give an agent (§4, §6.4). Contestant matrices expand models × agents × kits in one file. Skill ablations (same model and agent, with and without the kit) get their own report panel, which also shows whether each skill was actually loaded and invoked.
- **Sessions.** Each trial records its agent sessions (main session and subagents) as turn-by-turn timelines that merge gateway calls, the agent's native transcript, tool/MCP/skill events, and file changes. A new Sessions view lists, filters, and compares them per model, agent, or kit (§4, §9.5, §10).
- **Deployment.** The arena is a **web app served by one daemon (`arena serve`) that runs locally or on a remote box**, not a desktop app. An optional thin desktop window can come later (§3.1, with the research behind the call).
- **Subscriptions are first-class providers.** Several frontier models are sold only through a subscription tied to the vendor's own client (Claude Code, Codex, GitHub Copilot). The arena imports your own existing sign-in on the daemon, never in a container, and serves it on the same protocols as API keys (§5.8).
- **Two harness axes.** The **model axis** runs every model in one neutral harness with the same kit: OpenCode by default, and Pi as the minimal-scaffold baseline. The **product axis** runs each vendor's own CLI with its own model. A contestant can pin the harness's system prompt (`scaffold_prompt: pinned`) so a model-axis run holds the prompt fixed too (§6.2).
- **Delivery workflow.** Each checkpoint is a GitHub epic with its own `epic/*` branch; sub-issues are built in worktrees and merged into the epic branch, and the epic branch merges into `main` once every sub-issue is closed (§13.1).

### What changed in rev 2

Rev 2 adopts the application architecture of [magpie](https://github.com/yetone/magpie/blob/main/AGENTS.md) (cloned locally in the gitignored `_sample/magpie`) where it fits an eval workbench, and says where it doesn't:

- A local **arena gateway** now sits between every contestant and every provider, including agent CLIs inside containers. All metering, attribution, budget, caching, and redaction happen there (§5).
- **Provider, model, and agent config** are first-class files with one owner each: `providers.yaml`, `catalog/models.yaml`, and one adapter per agent CLI (§5, §6).
- **Observability**: a per-call ledger, a seq-cursor run event stream recorded at the decision point, and opt-in OTLP export (§9).
- **Live run view**: an animated SVG stage and replay, plus usage charts, all driven by recorded events (§10).
- **Engineering conventions** for humans and coding agents: AGENTS.md, subsystem references, acceptance criteria, and a LESSONS loop (§12).
- The checkpoints were re-cut to fit these changes, still seven (§13).

## 1. Goal

AI Arena is a local-first workbench for running the same tasks against different AI **contestants** and comparing the results. A contestant can be a model, an agent, an orchestration strategy, or an evaluator. The work focuses on **code generation** and **content creation**.

The main deliverable is the **comparison report**. A person should be able to answer these questions in under a minute:

1. Which contestant is best on this suite, and is the lead statistically real or noise?
2. What does each contestant's output for task X look like, side by side?
3. Why did contestant A score higher than B on this task? (Show the evidence: test output, judge rationale, transcript.)
4. What did it cost (in $, tokens, and wall time), and which contestant is the best value?
5. What changed between last week's run and today's?

Two more questions come with agents and custom skills:

6. **Did our custom skill (or instructions, or MCP server) help?** Did the agent actually load and use it, and is the difference real?
7. **What did each session do?** Per model, agent, or session: every turn, tool call, skill use, file change, token, and dollar. Two sessions on the same task can be compared side by side.

A final question applies while a run is in flight: **what is the run doing right now, and is anything stuck, rate-limited, or burning budget?**

### The core workflow

1. Pick a task (coding or content), or a suite of them.
2. Pick contestants as a matrix: providers/models × agent CLIs × kits (`none`, `team-kit@v3`, `team-kit@v4`, …).
3. `arena plan` expands the matrix, drops pairs an adapter can't run, and shows the trial count and estimated cost. `arena run` executes it locally or on a remote arena daemon.
4. Watch it live, then open the report: artifacts side by side, scores with evidence, sessions turn by turn, kit effect, cost.

### Non-goals (v1)

- Hosted multi-tenant SaaS. v1 is single-user and local, with a shareable static export.
- Observability of production traffic (Langfuse and Phoenix already do this). The arena observes **its own runs** and can export them over OTLP to those tools instead of rebuilding them.
- Training or fine-tuning.
- Re-implementing public benchmarks. Import their results or wrap their harnesses instead.

## 2. What we borrow from existing projects

All five reference projects are cloned in `_sample/` so claims can be checked against source.

| Project | What to take | What not to take |
|---|---|---|
| [magpie](https://github.com/yetone/magpie) (`_sample/magpie`) | Local multi-protocol gateway (Chat, Responses, Anthropic Messages, Gemini) with passthrough-first translation; per-caller tokens for attribution (`gateway.TokenFor`); provider config with several keys, per-key concurrency, RPM, and queue lanes (`gateway/concurrency.go`, `rpm.go`); narrow failure classes with different rest times (`gateway/fallback.go`); served model vs. requested model (`Route.Swapped`); a trace recorded where decisions are made and read through a seq cursor (`gateway/trace.go`); the live SVG routing stage with replay (`gui/assets/routing.js`); the per-call ledger and bounded OTLP exporter (`usage/ledger.go`, `usage/otel.go`); **subscription account management: sign-in import, token refresh, and per-account usage meters** (`internal/provider`, `internal/usage`); agent descriptors with field get/set and drift checks (`internal/agent`); subsystem reference docs, code standards, and LESSONS.md (`docs/`); the **Library**: one source of skills, instructions, and MCP servers written into each agent's own paths and formats, with an `Applied` record so only what was written is taken back (`internal/library/targets.go`, `docs/subsystems/library.md`); reading each agent's native session files (`internal/sessions`), and ledger rows sourced from them (`Row.Source = "log"`); one page served to the desktop app, the browser (`magpie web`, with a run key in the sign-in link), or a server in gateway mode (`internal/gui/web.go`, `gatewaymode.go`) | **Cross-model fallback and routing groups.** They change the model mid-trial, which changes the contestant. **Fail-open middleware.** A hook that silently does nothing changes the experiment. **Editing the user's real agent configs on the host.** The arena writes agent config only inside trial containers. **Sharing or pooling subscriptions.** Only the user's own accounts, and usage is kept shaped like the vendor's own client (§5.8) |
| [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) (UK AISI) | Task = dataset + solver + scorer; Docker sandboxing; typed transcript events with `span_begin`/`span_end` and parent ids (`event/_span.py`); its log viewer as a reference for transcript UX | Its single-model-per-eval framing; we need N-way comparison as the main view |
| [Harbor](https://github.com/laude-institute/harbor) / [Terminal-Bench](https://www.tbench.ai/news/announcement-2-0) | Agent task format: instruction + Dockerfile + tests + **oracle solution**; an agent registry with installed-agent recipes (`src/harbor/agents/`); job diff (`job_diff.py`); versioned telemetry event schemas (`telemetry.py`) | Terminal-only scope |
| [promptfoo](https://github.com/promptfoo/promptfoo) | Config-as-code YAML; provider × prompt × test matrix; side-by-side matrix view; **adaptive concurrency** that halves on 429 and recovers +50% after 5 successes (`src/scheduler/adaptiveConcurrency.ts`) | Prompt-centric model; we center on contestants and artifacts |
| [LMArena / WebDev Arena](https://arena.ai/blog/webdev-arena) | Blind pairwise voting; rendering generated web apps in sandboxes; Bradley-Terry ratings with CIs ([arena-rank](https://arena.ai/blog/arena-rank)) | Crowd-scale infra |
| [evalica](https://github.com/dustalov/evalica) (`_sample/evalica`) | Bradley-Terry, Elo, and win-rate computations with bootstrap (Rust core, Python bindings) | — |
| [DeepEval](https://deepeval.com) | Rubric/G-Eval style LLM-judge metrics; pytest-style assertions | Metric sprawl |
| SWE-bench, LiveCodeBench, BigCodeBench | Execution-based scoring (hidden tests decide) | Contaminated public items as our primary signal |
| [Scaffold Effect paper](https://arxiv.org/pdf/2607.22585) | The harness is a hidden variable, so make it a first-class part of the contestant identity | — |

Judge methodology follows current LLM-as-judge findings ([Openlayer guide](https://www.openlayer.com/blog/llm-as-judge-evaluation-guide), [arXiv 2602.02219](https://arxiv.org/html/2602.02219v2), [arXiv 2606.19544](https://arxiv.org/pdf/2606.19544)): versioned rubrics, pairwise judgments in both orders, judges from a different model family than the contestant, calibration against human labels, and length-bias monitoring.

## 3. Architecture

```
                     ┌──────────────── arena serve (one process tree) ────────────────┐
 arena run ──► Orchestrator ──► Scheduler ──► Runner ──► Sandbox (Docker, per trial)  │
   (CLI)        │  run plan      lanes,        completion │  agent CLI ──┐             │
                │  resume        budget        agent-cli  │              │ only egress │
                │                              orchestr.  │              ▼             │
                │                                 └──────────────► Arena Gateway ─────────► providers
                │                                    model calls    127.0.0.1 / bridge     (Anthropic, OpenAI,
                ▼                                                   auth · lanes · cache    Google, OpenRouter,
           Store (SQLite + CAS) ◄──── calls ledger, run events ──── redact · meter · trace   mock / cassette)
                │                                                         │
                ├──► Event log (runs/<id>/events.jsonl, seq cursor) ──────┤──► OTLP exporter (opt-in)
                ▼                                                         │
           API (/api/*) ──► Viewer (React, static-exportable) ◄───────────┘ long-poll /events?after=seq
                                    HTML artifacts on a separate origin
```

### Principles

1. **Every model call goes through the gateway.** Completion runners, judges, orchestration sub-calls, and agent CLIs in containers all call the gateway with a per-trial token. That gives one place for metering, attribution, budget, caching, redaction, and rate limits, whatever the scaffold. An agent CLI that can't be pointed at the gateway still runs, but its trial is marked `unmetered`.
2. **Record at the decision point, never re-derive.** The gateway writes a call's record (who was tried, what each try answered, tokens, timings) from the same values it decided with. The viewer shows recorded fields and never recomputes statistics; `stats/` computes them once into the bundle. (magpie: "Every row, number and sentence comes from what the gateway recorded while deciding.")
3. **Infrastructure failure is not contestant failure.** Classify narrowly: a rate limit, an exhausted quota, an auth failure, a non-API reply, and a model's own refusal are different classes with different handling. They are never merged into one bucket.
4. **Unknown is not empty.** A failed read, parse, or hash of a config, store, or transcript is an error. It never counts as "no providers", "no trials", or "equal".
5. **Nothing touches the host's real agent configs.** Adapters write config only inside trial containers. Tests run under a temporary HOME (§12).
6. **The contestant is pinned.** The gateway may rotate between keys of the same provider and model. It never falls back to another model, and it flags any reply whose served model differs from the requested one.
7. **The daemon owns the work; clients only watch and command it.** Runs live in `arena serve`, not in a browser tab or a terminal. Closing the viewer or the CLI never stops a run.

### 3.1 Deployment: a web app, local or remote (decision)

**Decision: build a web app served by one daemon, `arena serve`, that runs on the laptop by default or on a remote Linux box with Docker. Use the same page for the static export. A desktop window is optional, comes later, and is only a shell around the same page.** This is magpie's "one page, several hosts" pattern (`internal/gui/web.go`). The difference is that the browser, not a desktop window, is the primary host.

**Why not desktop-first, like magpie:**

| Factor | What it means for the arena |
|---|---|
| **Why magpie is a desktop app** | It belongs to one machine: a menu-bar panel, the user's own agents' configs edited in place, and those agents' local traffic routed through it. The arena does none of these. It must *not* touch the host's agent configs (principle 5), and its agents run in containers. |
| **Where the work runs** | Runs are container-heavy and long: dozens of agent containers, hours of wall time, GBs of images. They belong where Docker, cores, and disk are, which is often a remote VM rather than a laptop with its lid closed. A desktop app would need a background daemon anyway, at which point the window is just a browser. |
| **Sharing** | The deliverable is a comparison report that other people read. A URL (a remote daemon or a static export) is shareable; a desktop window isn't. |
| **What comparable tools ship** | Inspect AI (`inspect view`, `inspect view bundle`), promptfoo (`promptfoo view`), and Harbor (`apps/viewer`) all ship a local web viewer plus static publishing. None ships a desktop app. |
| **Packaging cost** | Our core is Python. A desktop build means Tauri v2 running the Python backend as a separate bundled process, packaged with PyInstaller or Nuitka. That brings code signing per OS, auto-update, and process-lifecycle bugs (Tauri may fail to kill a one-file PyInstaller backend it started). That is real work with no benefit to the comparison itself. |
| **What desktop would add** | One-click install, tray notifications, native folder pickers, keychain storage. We cover these with `arena serve --open`, browser notifications when a run ends, typed paths, and the OS keychain via `keyring`. |

**Modes**

| Mode | Command | Who uses it |
|---|---|---|
| Local (default) | `arena serve` binds `127.0.0.1:7400`. The viewer, API, and long-poll stream share that port; the HTML-artifact origin is `:7402`. The gateway listens on the Docker bridge. | One person on a laptop with Docker Desktop |
| Remote | `arena serve --host 0.0.0.0` on a Linux VM. The CLI on a laptop targets it with `ARENA_URL=https://arena.box:7400` and uploads suites, contestants, and kits with the run. | Long or large runs, a shared team box |
| Static | `arena export <run>` writes the bundle and viewer to a folder for `file://`, S3, or GitHub Pages. Live runs as replay. | Reports for people who don't run the arena |
| Desktop shell (later) | `arena app` opens the local daemon's page in a pywebview window (Python-native, no second runtime). Tauri only if signed installers and auto-update become a requirement. | People who want an app icon |

**Remote-mode safety**

- Loopback is the default. Binding any other address **requires auth**. v1 is single-user: a run key in a sign-in link that sets an HttpOnly cookie (magpie's `magpie web` scheme), plus API tokens for the CLI. Multi-user OIDC is v2.
- Every write is checked for CSRF (Origin plus token). The artifact origin serves no cookies.
- Recommended exposure is Tailscale or an SSH tunnel, or a reverse proxy with TLS. The docs show all three and warn against a bare public port.
- The gateway is **never** reachable off-box. It listens only on the Docker bridge and loopback.
- Docker access is root-equivalent. Run the daemon on the VM itself, or in a container with rootless Docker or Sysbox. Never mount `/var/run/docker.sock` into a container that also serves the web UI.
- Provider keys stay in the daemon's environment or OS keychain on the box that runs the gateway. The laptop CLI never sends them.

## 4. Core concepts and data model

Get this shape right first; everything else reads or writes it.

```
Suite ──< Task ──< Scorer refs (+ weights, gates)
Kit         = versioned bundle of skills + instructions + MCP servers + agent settings (content-hashed)
Contestant  = ModelRef + params + scaffold@version + Kit@hash + orchestration + prompt version + gateway hooks
Run         = Suite × [Contestant] × repeats, frozen config snapshot (incl. providers/catalog versions)
Trial       = one Contestant × one Task × one attempt  (the unit of execution)
  ├─ Sessions     (agent conversations: main + subagents; each a list of Turns, see below)
  ├─ Calls        (one per upstream model request, written by the gateway; see below)
  ├─ Transcript   (messages, tool calls, spans: Inspect-style span_begin/span_end with parent ids)
  ├─ Artifacts    (files produced; content-addressed by sha256)
  ├─ Metrics      (rolled up from Calls: tokens, cost $, wall time, queue time, steps, exit status)
  └─ Scores       (one per Scorer: raw value, normalized 0–1, rationale, evidence)
Judgment    = pairwise verdict on (Trial A, Trial B) for one Task by a judge (human | model)
Rating      = derived: Bradley-Terry / win-rate per Suite (+ bootstrap CI), never stored as truth
RunEvent    = append-only, seq-numbered record of what happened in a run (drives Live view and replay)
```

### Entities

- **ModelRef.** `provider/model[:effort]`, e.g. `anthropic/claude-opus-5-5:high` or `openrouter/qwen/qwen3-coder`. The provider is the part before the first `/`. Effort is a normalized level (`off | low | medium | high | max`) that the gateway maps to each provider's parameter. This is the same member syntax magpie uses for routing groups.
- **Subscription.** A provider credential that is a service subscription rather than an API key: `{vendor: claude | chatgpt | copilot, plan, session, refresh}`. Imported from the user's own existing sign-in (the one `claude` or `codex` already uses) and kept on the daemon, where it is refreshed like the vendor's own client refreshes it (§5.8).
- **Kit.** A named, versioned bundle: instructions (one Markdown file, written as `CLAUDE.md`, `AGENTS.md`, or `GEMINI.md` as each agent expects), skills (folders with a `SKILL.md`, from a local path or a git ref pinned to a commit), MCP servers (command or URL, secrets as `${ENV}` references), and per-agent settings. Kit identity = sha256 over every file's bytes plus the canonical MCP and settings config. Env references are hashed by name, never by value. Editing one line of one skill produces a new kit hash. `none` is a kit too, and it is the baseline for ablations.
- **Contestant.** Identity = `sha256(canonical_json(resolved_config))[:12]`. The resolved config includes the ModelRef, params, scaffold id **and pinned version**, `scaffold_prompt` (`native | pinned`), **kit hash**, orchestration, prompt version, and any gateway hooks. Changing any of them produces a new contestant. Display names are labels, not identity.
- **Session.** One agent conversation inside a trial: the main session, a subagent, or an orchestration branch. Fields: `id, trial_id, parent_session_id, agent, native_session_id, started_at, ended_at, status`. A session is a list of **Turns**. Each turn holds its model call(s) (`call_id`s), tool calls (name, args digest, result size, duration, exit status), **skill events** (`listed | loaded | invoked`, skill name, kit hash), **MCP calls** (server, tool, duration, status), and files touched (path, +/− lines). Sessions are assembled from three sources: gateway Calls (the wire), the agent's native transcript (the agent's view), and the container's filesystem diff. Disagreements are flagged; for example, a turn in the native transcript with no matching gateway call marks that turn `unmetered`.
- **Task.** Has a `kind` (`codegen` | `agentic-code` | `content` | `web-artifact`) plus a prompt, fixtures, scorer list, and version. Executable tasks also carry an **oracle solution** and a **null solution** for sanity checks (borrowed from Harbor).
- **Trial.** Immutable once finished. A retry creates a new trial with an `attempt` number. Status is `queued | running | succeeded | failed | errored | timeout | skipped`. `failed` means the contestant produced bad output. `errored` means our infrastructure broke, and it carries an `error_class` (§5.5). Keeping these apart stops infra failures from counting against a model. Flags: `unmetered` (calls bypassed the gateway), `swapped` (a reply came from a model other than the one requested), and `subscription_served` (the model came from a subscription but the requesting scaffold was not that vendor's own client for it).
- **Call.** One upstream model request, written by the gateway as it decides:
  ```
  id, seq, run_id, trial_id, span_id, parent_span_id, purpose (contestant | judge | orchestration | arena)
  protocol_in, protocol_out, translated: bool
  provider, account_id (hash, never the credential — an API key id or a subscription session), model_asked, model_served, swapped, upstream (aggregator's backend)
  effort_asked, effort_applied
  tries: [{account_id, status, error_class, rest_ms, ms}]
  status, error_class
  tokens: {in, out, reasoning, cache_read, cache_write}
  cost_usd (null for flat-fee subscription calls), price_version
  queue_ms, ttft_ms, first_text_ms, total_ms
  cache: hit | miss | off
  prompt_parts: [{kind: system | tools | instructions | files | conversation, tokens}]   # names and sizes only, never text
  ```
- **Artifact.** Stored as `artifacts/<sha256>` with `{path, mime, render_hint}`. Render hints (`code`, `diff`, `markdown`, `html-sandbox`, `svg`, `image`, `json`) tell the viewer how to show it.
- **Score.** Has `scorer_id@version`, `value`, `normalized ∈ [0,1]`, `passed: bool | null`, `rationale`, and `evidence` (log excerpt, failing test names, judge reasoning). A score whose scorer version changed is never mixed into the same aggregate.
- **RunEvent.** `{seq, ts, run_id, kind, ref, data}` where `kind` is one of `run_started | trial_queued | trial_started | kit_installed | call_queued | call_try | call_finished | session_turn | skill_event | lane_changed | trial_finished | score_added | budget | run_finished`. `seq` is monotonic per run. The event schema is versioned (`event_version`) alongside the bundle schema.

### Storage

- `arena.db` (SQLite, WAL mode): metadata, trials, calls, scores, judgments. Plain SQL migrations, numbered.
- `runs/<run_id>/events.jsonl`: the append-only event log. Live view and replay both read it.
- `artifacts/`: content-addressed blobs, deduplicated across runs.
- `cache/`: response cache keyed by `hash(contestant_config, task_version, attempt_seed)` for completion trials, plus gateway **cassettes** (recorded request → response pairs) for replay mode. A rerun of an unchanged completion trial makes no API call, which keeps report iteration cheap.
- DuckDB reads SQLite plus Parquet exports directly for ad-hoc analysis.
- Writes are atomic (write to temp, then rename). A store file that can't be read or parsed is an error, never an empty store.
- `runs/`, `artifacts/`, `cache/`, and `arena.db` are gitignored. Suites, contestants, kits, rubrics, `providers.yaml`, and `catalog/` are committed. Secrets never are.

### Config examples

```yaml
# contestants/claude-opus-direct.yaml
id_label: opus-5.5-direct
model: anthropic/claude-opus-5-5:high     # ModelRef: provider/model[:effort]
params: { temperature: 0.2, max_tokens: 16000 }
scaffold: none                            # none | { id: claude-code, version: 2.4.1 } | custom:<path>
orchestration: single                     # single | best-of-n | planner-executor | debate | custom:<path>
system_prompt: prompts/system/coder-v1.md
```

```yaml
# contestants/codex-on-gpt.yaml
id_label: codex-cli-gpt-x
model: openai/gpt-x:medium
scaffold: { id: codex-cli, version: 0.70.0, settings: { approval: never, sandbox: workspace-write } }
kit: kits/team-coding@3       # or: none
orchestration: single
```

```yaml
# kits/team-coding/kit.yaml — committed; one folder per kit, versioned
id: team-coding
version: 3
instructions: instructions.md            # written as CLAUDE.md / AGENTS.md / GEMINI.md per agent
skills:
  - path: skills/tdd-loop                # local folder with SKILL.md
  - path: skills/house-style
  - git: https://github.com/acme/agent-skills
    ref: 4f2c9e1                         # pinned commit; tags are resolved and pinned at run time
    subdir: skills/code-review
mcp:
  - { name: docs-search, url: https://mcp.acme.dev/docs, headers: { Authorization: "Bearer ${ACME_MCP_TOKEN}" } }
  - { name: repo-tools, command: npx, args: ["-y", "@acme/repo-tools@2.1.0"] }
settings:
  claude-code: { permissions: { allow: ["Bash(npm test:*)", "Bash(pytest:*)"] } }
  codex-cli:   { approval: never }
```

```yaml
# contestants/matrix-team-kit.yaml — expands to one contestant per combination
matrix:
  model:    [anthropic/claude-opus-5-5:high, openai/gpt-x:medium, google/gemini-3-pro:high, openrouter/qwen/qwen3-coder]
  scaffold: [{ id: claude-code, version: 2.4.1 }, { id: codex-cli, version: 0.70.0 }, { id: opencode, version: 1.2.0 }, none]
  kit:      [none, kits/team-coding@3]
exclude:
  - { scaffold: none, kit: kits/team-coding@3 }   # a bare completion has no agent to load skills into
ablation: kit                                      # pair contestants that differ only in `kit` for the Kit effect panel
```

```yaml
# contestants/matrix-model-axis.yaml — which model is best? Hold the harness, its prompt, and the kit fixed.
matrix:
  model:    [anthropic/claude-opus-5-5:high, openai/gpt-x:medium, google/gemini-3-pro:high, openrouter/qwen/qwen3-coder]
  scaffold: [{ id: opencode, version: 1.2.0 }, { id: pi, version: 0.99.2 }]
kit: kits/team-coding@3
scaffold_prompt: pinned          # native (the harness picks its prompt per model family) | pinned (one prompt for every model)
```

`arena plan contestants/matrix-team-kit.yaml suites/code` prints the expanded contestants, the pairs an adapter declares unsupported, the trial count, and the estimated cost. It writes nothing. The gateway translates protocols, so most model × agent pairs are valid; for example, Claude Code can drive an OpenAI model through the gateway's Messages → Responses translation. Such pairs are marked `translated` in the report, because translation is itself a variable.

```yaml
# suites/content/tasks/launch-blog-post/task.yaml
id: content.launch-blog-post
version: 3
kind: content
prompt_file: prompt.md
constraints: { min_words: 600, max_words: 900, must_include: ["pricing", "CTA"] }
scorers:
  - { id: constraints, weight: 0.2, gate: true }        # gate: fail => score capped at 0.3
  - { id: rubric-judge, rubric: rubrics/blog-post-v2.yaml, weight: 0.6 }
  - { id: readability, target_grade: 9, weight: 0.2 }
pairwise: true                # eligible for pairwise judge + blind human voting
```

```yaml
# rubrics/blog-post-v2.yaml
version: 2
scale: 1-5
criteria:
  - { id: accuracy,  weight: 3, desc: "No factual claims contradicted by the brief." }
  - { id: structure, weight: 2, desc: "Clear hook, logical sections, explicit CTA." }
  - { id: voice,     weight: 2, desc: "Matches the brand voice sample." }
  - { id: concision, weight: 1, desc: "Length is not quality; penalize padding." }
anchors: rubrics/anchors/blog-post/   # scored examples at 1, 3, 5 for calibration
```

## 5. Providers, models, and the gateway

### 5.1 Provider config

```yaml
# providers.yaml — committed. Secrets are env references only.
providers:
  - id: anthropic
    apis: { anthropic: https://api.anthropic.com }
    keys:
      - { id: main,   env: ANTHROPIC_API_KEY }
      - { id: backup, env: ANTHROPIC_API_KEY_2 }
    key_routing: order          # order | rotate | least-used — among this provider's keys, same model only
    limits: { concurrency: 8, rpm: 50, queue: 64, queue_wait: 120s }
  - id: openai
    apis: { responses: https://api.openai.com/v1, chat: https://api.openai.com/v1 }
    keys: [ { id: main, env: OPENAI_API_KEY } ]
    limits: { concurrency: 16, adaptive: true }    # promptfoo-style: halve on 429, +50% after 5 successes
  - id: google
    apis: { gemini: https://generativelanguage.googleapis.com/v1beta }
    keys: [ { id: main, env: GEMINI_API_KEY } ]
  - id: openrouter
    apis: { chat: https://openrouter.ai/api/v1 }
    keys: [ { id: main, env: OPENROUTER_API_KEY } ]
    require_served_model: true  # aggregator: a reply from another model fails the call as `swapped`
  - id: mock
    kind: mock                  # deterministic canned outputs; no network
  - id: replay
    kind: cassette              # serves recorded calls from cache/cassettes/; misses are errors
  - id: anthropic-max
    apis: { anthropic: https://api.anthropic.com }   # the subscription's endpoint
    keys: []                                 # a subscription has no API keys
    subscription:
      vendor: claude                         # claude | chatgpt | copilot
      plan: max
      import: own-sign-in                    # read from the user's existing CLI sign-in; refreshed on the daemon
    limits: { concurrency: 2 }               # subscriptions are lane-narrow, and meters differ (§5.8)
```

A provider holds one base URL per protocol it speaks (`chat`, `responses`, `anthropic`, `gemini`), several keys, limits, optional headers, and an optional proxy. **A provider can also carry a subscription instead of keys** (§5.8). `arena providers detect <url>` probes which protocols a base URL speaks, and `arena providers test` sends a one-token request per key or subscription account. A provider that is switched off stays in the file, and a contestant that names it gets "provider off", not "unknown model".

### 5.2 Model catalog

```yaml
# catalog/models.yaml — committed, versioned
price_version: 2026-10-01
models:
  - ref: anthropic/claude-opus-5-5
    protocols: [anthropic]
    context: 1000000
    efforts: { low: {effort: low}, high: {effort: high}, max: {effort: max} }   # normalized → provider param
    price_per_mtok: { in: 15.00, out: 75.00, cache_read: 1.50, cache_write: 18.75 }
    source: https://www.anthropic.com/pricing      # where the number came from, checked on this date
  - ref: anthropic/claude-opus-5-5-sub
    protocols: [anthropic]
    pricing: subscription            # reachable only through a subscription (§5.8); price is unknown, not zero
```

Cost is computed when the call is written, using the catalog's `price_version`, and the report states which version it used. Vendor-specific rules (context caps, cache pricing) are scoped to their vendor, and each rule has a test that another vendor's model is unchanged.

### 5.3 Gateway surface

- `arena gateway` listens on `127.0.0.1` and on the Docker bridge network that trial containers join. It serves `/v1/chat/completions`, `/v1/responses`, `/v1/messages` (+ `count_tokens`), `/v1/models`, Gemini's `/v1beta/models/{model}:streamGenerateContent`, and `/mcp/<name>` for a kit's URL-based MCP servers (§6.5).
- **Auth.** Every request needs a token, loopback included: `arena-<trial_id>` for trials, `arena-judge-<run_id>` for judges, and `arena-ops` for arena's own calls. The token is how a call is attributed to a trial and purpose (magpie's `TokenFor` and `agentOf`).
- **Browser requests are refused.** A request carrying a browser `Origin` gets a 403. Otherwise any web page the user visits could POST `text/plain` to loopback and spend their keys (magpie `cors.go`, `foreignPage`).
- **Status endpoints.** `/arena/health`, `/arena/lanes` (in-flight, queued, and resting per key), and `/arena/stats`.

### 5.4 One request's path

1. Read the body, bounded in size, with gzip/zstd decoded. Resolve the token to a trial and purpose.
2. **Redact** secrets in the request before anything is logged or stored.
3. Resolve the ModelRef. An unknown model, a provider that is off, or a model the provider doesn't serve each get a distinct 404 message.
4. Apply the contestant's effort when the scaffold can't carry one itself, and record `effort_applied` (magpie `AgentEffort`).
5. **Plan candidates**: the provider's keys in `key_routing` order (a subscription provider offers its own account(s) instead), with resting keys last but never dropped. There is never a candidate from another model.
6. **Take a lane slot** (per-key concurrency, then RPM). Wait in the queue up to `queue_wait`, then answer 429 with `Retry-After`. While a streaming request waits, send SSE comments every 15 s so agent CLIs and proxies don't time out (magpie `keepQueued`). Queue time is recorded as `queue_ms` and kept out of latency.
7. **Passthrough first.** If the provider speaks the caller's protocol, relay the bytes as they are, which keeps cache breakpoints and native features intact. Otherwise translate (LiteLLM's translation layer), keep structured-output settings across the translation, and record `translated: true`.
8. **Try.** Hold the reply until it is clearly an API reply. A 2xx that is HTML, empty, or not JSON when declared JSON is a `bad_reply` (magpie `notAnAPIReply`). On a retryable class, rest the key and try the next one. **Once any byte has reached the caller there is no retry**: a caller never gets half a reply from one key and the rest from another.
9. **Record.** Write the Call row and its `call_*` RunEvents from the values used above: tries, tokens (including cache and reasoning), `model_served`, `ttft_ms`, cost. Compare `model_served` to `model_asked`, and set `swapped` when they differ.

### 5.5 Failure classes

| Class | Detected by | Key | Trial |
|---|---|---|---|
| `rate_limit` | 429 with no quota/credit wording; `Retry-After` | rests until retry-after (default 60 s, doubling on repeats) | next key; not counted against the contestant |
| `quota` / `credit` | 429/402/403 naming quota, balance, or credit | rests until the window resets or 30 min | next key; if none are left, `errored` |
| `subscription_limit` | 429 naming usage, weekly, or plan limits | rests the whole **account** until the vendor's window resets (often days; shown as a countdown) | scheduler holds trials that need that model and keeps the rest of the run going; accounts of the same subscription are left to their own meters |
| `auth` | 401, or a 403 naming the key | disabled for the run | `errored` |
| `auth_refresh` | the sign-in or refresh flow fails, or the vendor says the session expired | account disabled for the run, with the account named | `errored`; `arena providers test` names the account to re-sign-in |
| `model_refused` | 400/404/422 saying the model isn't served, with no quota wording | that model rests on that key only | next key; else `errored` |
| `bad_reply` | 2xx that isn't an API reply | short rest | next key |
| `upstream` / `timeout` | 5xx, connect or read timeout | backoff | next key, up to N tries; then `errored` |
| `content_refusal` | vendor safety refusal | no rest | **returned to the contestant as its own answer** and scored normally |

A fix to one class must not widen it to cover another (magpie LESSONS: "Classify narrowly").

### 5.6 Gateway hooks (optional)

Hooks are Python callables that see a request or reply event (`on_request`, `on_event`, `on_response`). They are listed in the contestant config, so they are part of its identity hash. Their limits copy magpie's middleware: 250 ms per request hook and 50 ms per stream event. Unlike magpie, **hooks fail closed**: a hook that throws or times out errors the call (`error_class: hook`), because a silent no-op would change the experiment without anyone seeing. Per-hook calls, µs per call, and failures are shown in the Ops view.

### 5.7 Caching and cassettes

- **Trial cache** (completion runner): as in rev 1, a rerun of an unchanged trial makes 0 calls.
- **Cassettes**: `arena run --record` saves each call's normalized request and response. The `replay` provider serves them back, and a miss is an error, not a live call. CI and viewer development replay real recorded runs with no API keys. This replaces most hand-written mock outputs.

### 5.8 Subscription-backed models

Several frontier models are sold only as a subscription tied to the vendor's own client — Claude Code (a Claude Pro/Max subscription), Codex (ChatGPT), GitHub Copilot. They must still be runnable as contestants, with every call observed and attributed. This is the account model magpie was built around, moved onto the arena's gateway.

**The credential lives on the daemon, never in a container.** `arena providers import claude` reads the user's own existing sign-in — the same one the vendor's CLI is already using — and stores the session on the daemon, refreshing it the way the vendor's own client refreshes it. Containers see only the gateway's address and a per-trial token, exactly as §6.2 wires API-key models.

**The gateway signs requests the way the vendor's client does.** On the caller's protocol, it attaches the session token and the client's own headers and version markers. The per-vendor details matter and are contract-tested against recorded bytes (magpie LESSONS: Copilot answers 403 unless `X-GitHub-Api-Version` sits beside the session token — that one missing header cost four releases; Codex's limit arrives as `rate_limit.allowed === false`, not as an HTTP code).

| Subscription | Served protocol | Models | Preferred contestant |
|---|---|---|---|
| Claude Pro/Max | `anthropic` (Messages) | Claude models | Claude Code adapter — same protocol, so passthrough |
| ChatGPT Plus/Pro | `responses` | GPT-family | Codex CLI adapter — same protocol, so passthrough |
| GitHub Copilot | `chat` (Completions) | Copilot models | the GitHub Models API key where the model is published there; the subscription only for Copilot's own CLI |

**Two ways in, one honest label.** (a) Through the vendor's own agent CLI in a container — the usage shape the subscription is licensed for, and the default. (b) Through any other scaffold — allowed (it is your own subscription) but the trial carries the `subscription_served` flag, so a report never presents subscription traffic as an API-priced result.

**Cost.** A subscription call records its tokens and `cost_usd = null`; the ledger keeps both. Reports show `flat` in cost columns, the Pareto cost axis uses tokens/task so mixed runs still rank, and any owner-entered monthly amortization appears labeled "user-entered".

**Meters are meter-shaped, not key-shaped.** Subscriptions meter with weekly windows and per-model caps that do not map onto RPM lanes. Where the vendor reports its meters, the Ops view shows the account's usage against them; where it does not, the arena counts tokens itself and the window is an owner-set value. Rotation stays inside the user's own accounts — never a shared pool.

**Terms.** A subscription is licensed for interactive use of the vendor's client; automated evaluation may breach those terms, and heavy use can get an account limited. Each subscription is explicit in `providers.yaml`, warned on first use, and audited in the ledger. Where a vendor API key exists for the same model, the provider config is expected to prefer it. Whether to run subscription-backed evaluations is the owner's call; the arena keeps the usage visible and never silently substitutes a different model to dodge a limit.

## 6. Agents and scaffolds

### 6.1 Adapter contract

Each agent CLI has one adapter file in `src/arena/agents/`. It plays the role magpie's `internal/agent` descriptors play, but it targets a container instead of the user's machine.

```python
class AgentAdapter(Protocol):
    id: str                          # "claude-code"
    protocol: Protocol               # the API it speaks: anthropic | responses | chat | gemini
    image: str                       # Dockerfile layer that installs a pinned version
    def version(self, box: Sandbox) -> str: ...                  # read from the installed binary
    def wire(self, box: Sandbox, gw: GatewayEndpoint, model: ModelRef) -> None: ...
        # writes env/config INSIDE the container only: base URL, token arena-<trial_id>, model, effort
    def install_kit(self, box: Sandbox, kit: ResolvedKit) -> KitInstall: ...
        # instructions, skills, MCP servers, settings into this agent's own paths and formats, inside the container;
        # returns what was written and what this agent can't take (e.g. an SSE MCP server), never silently dropped
    def command(self, task: Task, settings: Mapping[str, Any]) -> list[str]: ...
    def collect(self, box: Sandbox) -> NativeTranscript | None: ...  # agent's own session log(s), if any
    def sessions(self, native: NativeTranscript, calls: Sequence[Call]) -> list[Session]: ...
        # assemble turns; emit skill events (listed / loaded / invoked) by this agent's rules
    def check(self, box: Sandbox) -> WiringCheck: ...              # config written as this version reads it
```

### 6.2 Launch adapters

The wiring below is what each CLI is expected to read. Each adapter's contract test confirms it against the pinned version before it ships, because field names change between versions (magpie LESSONS: "Take another app's field types from that app's own data").

| Agent | Protocol | Expected wiring (confirmed by contract test) | Kit targets in the container's `$HOME` | Native transcript |
|---|---|---|---|---|
| Claude Code | anthropic | `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL` env | `~/.claude/CLAUDE.md`, `~/.claude/skills/`, MCP in `~/.claude.json` | `~/.claude/projects/**/*.jsonl` |
| Codex CLI | responses | `~/.codex/config.toml`: `model_provider` entry with `base_url`, `wire_api = "responses"`, `env_key` | `~/.codex/AGENTS.md`, `~/.codex/skills/`, MCP in `config.toml` | `~/.codex/sessions/**` |
| OpenCode (**model-axis default**) | chat / anthropic | provider entry in `opencode.json` with `baseURL` | `AGENTS.md`, `skills/`, MCP in `opencode.json`. It also reads Claude Code's skills folder, so the kit is written to one place only | session store |
| Pi (**minimal baseline**) | chat / anthropic | provider entry in its models config, under `PI_CODING_AGENT_DIR` (else `~/.pi/agent`) | `AGENTS.md`, `skills/`; MCP from its own `mcp.json` from 0.99 (earlier versions read MCP through an extension, so pin ≥ 0.99) | session files |
| Aider | chat | `OPENAI_API_BASE`, `OPENAI_API_KEY`, `--model openai/<name>` | conventions file via `--read`; no skills or MCP (declared unsupported) | `.aider.chat.history.md` |
| Gemini CLI (v1.1) | gemini | base URL env + key, per its pinned docs | `~/.gemini/GEMINI.md`, `~/.gemini/skills/`, MCP in `settings.json` | session log |
| OpenHands (v1.1) | chat | LLM base URL + key in its config | per its pinned docs | event stream |

The kit-target paths come from magpie's Library target table (`internal/library/targets.go`), which tracks where each agent reads instructions, skills, and MCP config. Each adapter's contract test confirms them against the pinned version. When the model comes from a subscription (§5.8), `wire()` writes only the base URL and the gateway token; the subscription session stays on the daemon, and the vendor's own CLI is the preferred scaffold for it.

**Two harness axes.** A neutral harness holds the scaffold constant, which is what a fair model comparison needs. It also understates models that their vendor's CLI is tuned for: "Claude in OpenCode" is not "Claude at its best". So contestant sets come in two kinds:

- **Model axis**: one neutral harness × N models × the same kit. OpenCode is the default (native MCP, plugins, headless runs). Pi is the minimal baseline (one small prompt, a minimal tool set). Some harnesses choose their system prompt by model family. `scaffold_prompt: pinned` makes the adapter send one prompt to every model, and `prompt_parts` records what was actually sent either way. Each adapter's contract test confirms its prompt behavior for the pinned version.
- **Product axis**: each vendor's own CLI with its own model (Claude Code, Codex CLI). This is the "what should we actually use" comparison, and the default home for subscription-only models (§5.8). Running a subscription through a neutral harness is the `subscription_served` case.

### 6.3 Reached check

Writing a config file doesn't prove the agent uses it. After the agent starts, the runner checks that the gateway saw at least one call with this trial's token on the expected protocol, a check modeled on magpie's `Reached`. If none arrives within the first step's timeout:

- if the adapter's `check()` says the config is wrong, the trial is `errored` (`error_class: wiring`);
- if the agent is talking to something else, the trial is flagged `unmetered`, its metrics come from the native transcript only, and the report labels it so.

The scaffold's pinned version is part of the contestant identity, so an agent CLI upgrade shows up as a new contestant in run diff instead of as a silent change.

### 6.4 Kits in the container

- `install_kit` runs after `wire` and before the agent starts. It writes only into the container's `$HOME` and workspace, using the agent's own format, the way magpie's Library does on a real machine. In a container there is nothing of the user's to preserve, so there is no `Applied` record and no backups.
- Skills are **copied**, never linked to host folders, so a trial can't change a kit on the host. The copy's hash is checked against the kit hash before the agent starts.
- What an agent can't take is reported, not dropped. For example, Aider has no skills, and some agents take no SSE MCP server. Each refusal is listed in `KitInstall.refused`, and the trial page shows it. A contestant whose kit was refused entirely is flagged `kit_unapplied` and excluded from the Kit effect panel.
- **Skill telemetry.** A skill that wasn't loaded can't help, and "no difference" often just means "never used". Each adapter emits per-session skill events by its own rules, tested on recorded transcripts:
  - `listed`: the skill's name or description appears in the prompt (from the gateway's `prompt_parts` inspection);
  - `loaded`: the agent read the skill's `SKILL.md` (a file-read tool call on the skills path, or the agent's own skill-load event);
  - `invoked`: the agent used it (e.g., Claude Code's `Skill` tool call, or a tool call into the skill's scripts).
- The same applies to instructions (whether the instructions file was in the prompt) and MCP servers (connected, which tools were listed, which were called).

### 6.5 MCP servers through the gateway

- Command-based MCP servers run inside the container from the pinned package or image layer.
- URL-based MCP servers are reached through the gateway at `/mcp/<name>`, as magpie does. The gateway adds auth headers from the daemon's environment, so MCP secrets never enter the container, and container egress stays gateway-only. Each MCP request becomes a `tool` span with server, tool, duration, and status. `${ENV}` references resolve on the daemon. An unset variable fails the trial at preflight with the variable's name; it never silently connects without auth.

## 7. Execution layer

### Runners

1. **Completion runner** handles single- or multi-turn API calls through the gateway.
2. **Agent-CLI runner** runs an adapter's CLI inside a per-trial Docker container with the task workspace mounted. It captures stdout, the native transcript, the final repo diff, and resource usage. Harbor-format tasks run as-is.
3. **Orchestration runner** composes other runners: `best-of-n` (N samples, then selection by judge or tests), `planner-executor`, `debate/critique`, or a custom Python entrypoint. Each sub-call carries `parent_span_id`, so the Trace view shows where the tokens went.
4. **Mock and cassette providers** give deterministic outputs. CI and UI development use them so they never need API keys.

### Scheduler and run controls

- **Lanes**: per-key concurrency and RPM are enforced in the gateway. The scheduler also caps trials in flight per contestant and overall, so a run can't queue hundreds of agent containers behind one key.
- `--dry-run` estimates calls, tokens, and $ from the catalog before anything executes, and has no side effects. `--budget-usd` is a hard cap checked at the gateway before each call. When the cap is reached, remaining trials become `skipped` and the run ends cleanly.
- Runs are resumable. `arena run --resume <run_id>` executes only trials that are missing or errored. Trial keys are deterministic, so execution is idempotent.
- `--repeats N` (default 3 for any reported comparison) gives variance and enables pass@k and pass^k.

### Safety

- Generated code only ever runs inside containers: CPU/memory/time limits, a read-only base image, and a writable scratch workspace.
- **The gateway is the containers' only egress.** Trial containers join an internal Docker network with no route out, and only the gateway's address is reachable. Agents get their LLM calls and nothing else. Tasks that need package installs get a pinned, pre-built image, not network access.
- API keys come from env or `.env`, live only in the gateway process, and never enter a container. Containers hold only their `arena-<trial_id>` token, which is valid for that trial's lifetime and budget.
- Redaction runs in the gateway on requests, and again on stored transcripts.
- HTML artifacts render in a sandboxed `<iframe sandbox="allow-scripts">` served from a separate origin/port. They never get same-origin access to the viewer.

## 8. Scoring methodology

### Scorer families

| Family | Applies to | Examples |
|---|---|---|
| Execution | codegen, agentic-code | hidden unit tests pass rate, build/compile, type-check, lint warnings, perf benchmark vs. baseline, security scan (semgrep/bandit) |
| Constraint | all | word count, required sections/keywords, format validity (JSON schema, Markdown structure), forbidden content |
| Reference | content (when gold exists) | semantic similarity to a reference, key-point coverage checklist |
| Rubric judge (pointwise) | content, code quality | criteria from a versioned rubric, chain-of-thought rationale, structured JSON verdict |
| Pairwise judge | tasks with `pairwise: true` | A vs B judged in **both orders**; if the verdict flips, it's recorded as a tie |
| Visual judge | web-artifact | Playwright screenshots at 3 viewports, plus a multimodal judge on a design rubric; console-error count as a deterministic check |
| Human | any | blind pairwise votes and pointwise ratings in the viewer |
| Efficiency | all | $ cost (tokens, with `flat` for subscription-backed contestants), tokens, wall time, queue time, agent steps, tool-call count (reported beside quality, not inside it) |

Judges call models through the gateway with `purpose: judge`, so judge spend appears in the ledger separately from contestant spend.

### Aggregation

- **Per-trial score** = weighted mean of normalized scorer outputs. **Gates** cap the score: if the build fails, the trial score is ≤ 0.3, whatever the rubric says.
- **Per-task score** for a contestant = mean over repeats. Also report **pass@k** (any of k succeeds) and **pass^k** (all k succeed). pass^k measures reliability, which matters most for agents.
- **Suite score** = mean of per-task scores, with a **95% CI from a cluster bootstrap** (resample tasks, then repeats within a task).
- **Head-to-head** compares two contestants as paired differences on the same tasks. Report the win/tie/loss counts and a paired significance test. If the CI of the difference crosses 0, the report says so in plain words: "no detectable difference".
- **Ratings**: Bradley-Terry over all pairwise judgments (model judge and human kept as separate leaderboards), with bootstrap CIs, via `arena-rank` or `evalica`.
- **Composite score** uses configurable per-suite weights. The default composite is **quality only**. Cost and latency appear on a Pareto chart so they're never silently traded off inside one number. Mixed runs of API-priced and subscription-backed contestants rank on tokens/task; flat-fee trials show `flat` rather than a dollar figure.
- Trials flagged `swapped` or `unmetered` are excluded from headline numbers by default and counted in a visible footnote.
- **Kit effect** (for matrices with `ablation: kit`). For each model × agent pair, the paired difference between the kit and its baseline (`none`, or the previous kit version) on the same tasks and repeats, with a cluster-bootstrap CI and win/tie/loss. Next to it:
  - **uptake**: the share of sessions where each skill was listed, loaded, and invoked;
  - **score when invoked vs. not invoked**, labeled *observational*: the agent chooses when to invoke a skill, so this is not a causal effect;
  - **cost delta**, because a kit often adds tokens through instructions and skill text in the prompt.

  The panel's headline is the paired difference only.

### Evaluators are contestants too

Judges get evaluated with the same machinery.

- **Gold set**: around 100 human-labeled items per rubric (pointwise) and around 100 human pairwise preferences.
- **Judge report** per judge config: agreement with humans (Cohen's κ; target > 0.6), position bias rate (how often the verdict flips on swap), length–score correlation, self-preference (judging its own model family), test–retest consistency, and cost per judgment.
- A judge config must have a passing judge report before it is allowed in a "headline" leaderboard. Uncalibrated judges still work, but the UI flags their results.

## 9. Observability

The arena watches its own runs at three layers. Each is written once, at the point where the thing happens.

### 9.1 Calls ledger

Every upstream model call is one `calls` row (schema in §4), written by the gateway. The ledger answers "where did the money go" and can be set beside a vendor's bill. It supports filters (run, contestant, purpose, provider, key, model, status, error class, swapped) and exports to CSV or Parquet. Trial metrics are roll-ups of their calls, never separate counters that can drift.

### 9.2 Run event stream

- Every state change is appended to `runs/<id>/events.jsonl` with a monotonic `seq`, and also held in an in-memory ring for live readers.
- Readers use a **seq cursor with long-poll**: `GET /api/runs/{id}/events?after=<seq>&wait=25s` returns every event after `seq`, or waits up to `wait` for one. It survives reconnects, works through proxies, needs no WebSocket state, and is easy to test. It is the same design as magpie's `Server.Trace(ctx, after, wait)`.
- **Replay** reads the same file, so live and replay share one code path in the viewer. A finished run can be replayed at any speed.

### 9.3 Spans and OTLP export

- Transcripts use span events (`span_begin`/`span_end` with `parent_id`, as Inspect does): run → trial → orchestration step → call → tool call.
- **Opt-in OTLP/HTTP export** maps spans to the [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) (`gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, …), plus `arena.*` attributes for run, trial, and contestant. Point it at Phoenix, Langfuse, Jaeger, or any collector.
- The exporter runs only in the gateway process and never blocks a request. Its queue is bounded by count (128) and by bytes. Overflow is dropped and counted (`otel_dropped`). Shutdown drains for at most 3 s. Request and response bodies are **off by default** and redacted when on. (Limits taken from magpie `usage/otel.go`.)

### 9.4 Metrics, logs, health

- **Metrics** (served at `/arena/stats` and as RunEvents): calls in flight and queued per lane; rests by class; RPM used; cache hit rate; budget spent vs. cap; TTFT and total latency p50/p95 per provider/model; gateway overhead per call; scorer and judge durations; hook calls and failures.
- **Logs**: structured JSON (structlog) with `run_id`, `trial_id`, and `call_id` on every line. Repeated errors are rate-limited (at most 60 lines a minute per source), with a count of what was dropped.
- **Health**: `arena doctor` checks Docker, the gateway, each provider key (one-token test), disk space for the CAS, and the pinned versions of agent images.
- **Gateway overhead budget**: added p50 latency < 5 ms over a local fake upstream, measured by `arena gateway bench` in CI. magpie's equivalent for its middleware is `BenchmarkEvent`.

### 9.5 Sessions: observability per model, agent, and session

- Sessions (§4) are the unit a person reads when asking "what did this model do on this task?". They are assembled after each trial by the adapter's `sessions()` from gateway calls, the native transcript, and the filesystem diff. Each turn is linked back to its Call rows, so the dollar and token numbers are the gateway's, not the agent's estimate.
- While a trial runs, a session's turns stream as `session_turn` RunEvents, so the Live view can open a running session and follow it.
- Sessions can be grouped and filtered by model, agent, kit, task, status, and skill events (for example, "sessions where `code-review` was invoked"). Per-group summaries are precomputed into the bundle: turns, tool calls, tool errors, tokens per turn, cost, wall time, and files touched.
- Assembly follows principle 4 (unknown is not empty). A native transcript that can't be parsed leaves the session built from gateway calls only and marked `partial` with the parse error. It never produces an empty session. Parsers are tested on recorded real transcripts, including truncated ones.

## 10. The comparison report and viewer

There is one data contract: a **report bundle** (`manifest.json` + per-run/per-trial JSON + calls summary + `events.jsonl` + artifact blobs). `arena serve` reads it through a small API, and `arena export` writes it as a static site that opens from `file://` or any static host. Both modes share the same viewer code. In a static export, the Live view runs in replay mode only.

### Views

1. **Leaderboard**: contestants ranked by suite score, with CI whiskers, pass^k, $/task, p50 latency, and a "significantly different from #1?" badge. Tabs for overall / per category / per tag. Toggle between pointwise score, BT rating (judge), and BT rating (human).
2. **Pareto chart**: quality vs. cost and quality vs. latency, with the frontier highlighted. Hover shows the contestant config.
3. **Matrix (heatmap)**: tasks × contestants with cells colored by score. Sort by variance to find the most discriminating tasks. Click a cell to open Compare.
4. **Compare (side by side)**: the core screen. One task, 2–4 trials in synchronized columns:
   - Column headers show model, agent, and kit, e.g. `opus-5.5 · claude-code · team-coding@3`.
   - Rendered artifact per column: live sandboxed iframe for HTML, rendered Markdown, syntax-highlighted code, image/SVG. For agentic-code tasks: the repo diff as a file tree with per-file diffs, plus test results.
   - **Diff mode** for any two columns (Monaco diff for code/text; screenshot overlay slider for web artifacts).
   - Score panel: each scorer's value, pass/fail, and expandable evidence (failing tests, judge rationale per criterion).
   - Metrics strip: tokens, $, time, queue time, steps.
   - Repeat switcher: flip through attempts 1..N for each contestant.
   - Identities can be hidden to remove reviewer bias.
5. **Trace**: the agent/orchestration timeline from spans and calls. Each step shows the model call or tool call, its tokens, TTFT, latency, tries, and expandable payloads. A **prompt composition bar** per call shows tokens by part (system, tools, instructions, files read, conversation), so a scaffold that spends half its context on tool definitions is visible at a glance.
6. **Run diff**: run A vs. run B with the same suite. Lists tasks that regressed or improved beyond noise, new failures, cost deltas, and contestants whose scaffold version changed.
7. **Arena (blind voting)**: shows a random task with two anonymized outputs, and the user picks A / B / tie / both bad. Keyboard-driven (`1` `2` `3` `4`). Votes feed the human BT leaderboard and the judge-calibration gold set.
8. **Judges**: the judge reports from §8 plus a disagreement browser that lists items where judge and humans disagree most.
9. **Live**: the run as it happens, or a replay of a finished run. See below.
10. **Ops**: the calls ledger, usage charts, lanes, rests, and hook stats.
11. **Sessions** (new in rev 3): every session in the run, filterable by model, agent, kit, task, status, and skill event, with a summary row per group. Opening one shows its **timeline**: turns down the page, each with its model call (tokens, $, TTFT), tool calls, MCP calls, and skill events highlighted (listed ○, loaded ◐, invoked ●), plus files touched. Subagent sessions are nested under their parent. **Compare sessions** puts two sessions on the same task side by side, aligned by turn or by wall time, to show how two agents or two kits approached the same problem.
12. **Kit effect** (new in rev 3): for an ablation matrix, one row per model × agent with the paired score difference and CI, skill uptake bars, the observational invoked/not-invoked split, and the cost delta. Clicking a row opens Compare for the tasks with the largest differences.

```
┌ Session · agentic.fix-null-deref #1 · opus-5.5 · claude-code 2.4.1 · team-coding@3 ─ $0.38 · 6m12s ┐
│ turn  model call            tools / mcp / skills                                    files      │
│  1    9.8k in · 0.6k out    ○ tdd-loop ○ code-review ○ house-style (listed)                    │
│  2    11.2k · 0.4k · $0.02  Read src/parser.ts · Grep "deref"                                  │
│  3    12.0k · 0.9k · $0.03  ◐ tdd-loop (read SKILL.md) · ● Skill(tdd-loop)                      │
│  4    13.5k · 1.2k · $0.04  Write tests/parser.test.ts · Bash(npm test) ✗ 1 failing     +38    │
│  5    15.1k · 0.7k · $0.03  Edit src/parser.ts · Bash(npm test) ✓                       +6 −2  │
│  6    15.9k · 0.3k · $0.02  mcp docs-search.query ✓ 220ms · ● Skill(code-review)               │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

```
┌ Compare · content.launch-blog-post (v3) ─────────────── [hide names] [diff A↔B] ┐
│ opus-5.5-direct    0.86 ±.04 │ gpt-x-direct     0.79 ±.06 │ sonnet-best-of-4  0.88 │
│ ───────────────────────────── │ ─────────────────────────── │ ────────────────────── │
│ # Introducing Flux Pricing    │ # Flux: Now With Plans      │ # Pay for what you use │
│ (rendered markdown…)          │ (rendered markdown…)        │ (rendered markdown…)   │
│ ───────────────────────────── │ ─────────────────────────── │ ────────────────────── │
│ constraints ✓  rubric 4.3/5   │ constraints ✓  rubric 3.9/5 │ constraints ✓  4.4/5   │
│  ▸ accuracy 5 "all claims…"   │  ▸ accuracy 3 "states 30%…" │  ▸ …                   │
│ $0.041 · 18.2s · 2.1k tok out │ $0.012 · 9.4s · 1.8k tok    │ $0.150 · 41s · 4×      │
│ attempt [1] 2 3               │ attempt [1] 2 3             │ attempt [1] 2 3        │
└───────────────────────────────────────────────────────────────────────────────────┘
```

### Live view: the animated stage

Modeled on magpie's Routing view (`gui/assets/routing.js`).

```
┌ Live · run 2026-10-08-a · 142/216 trials · 1,903 calls · 37 rerouted · 2 errored · $18.40 / $40 ┐
│                                                                                               │
│  opus-5.5-direct  ●──────╮                                ╭──── anthropic/main   ●● 6/8  q2    │
│  gpt-x-direct     ●────╮ │        ┌─────────────┐         ├──── anthropic/backup ●  3/8        │
│  codex-cli-gpt-x  ●──╮ │ ╰──•────►│   gateway   │──•──────┼──── openai/main      ●●● 14/16     │
│  claude-code-opus ●─╮│ ╰───────── │ 1.2 ms ovh  │         ├──── google/main      ○ resting 41s │
│  judge (sonnet)   ◌ ╰┴──────────► └─────────────┘         ╰──── openrouter/main  ●  2/4        │
│                                                                                               │
│  ▸ codex-cli-gpt-x · agentic.fix-null-deref #2 → openai/main · 429, rests 20s → retried ok    │  ← aria-live caption
├───────────────────────────────────────────────────────────────────────────────────────────────┤
│  suite score vs. trials completed (CI bands)        │  trial board: tasks × contestants        │
│  ── opus ── gpt ── codex ── claude-code              │  ■ done ▣ scoring ▢ running · queued     │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Stage**: contestants on the left (each with a stable hue), the gateway hub in the middle, provider keys on the right as lane rows showing in-flight/limit, queue depth, and rest state as a dot. Each call is a dot that travels along a Bézier wire from contestant to hub to key and back. A failed try returns to the hub and leaves again for the next key, so a retry is visible as a retry. Wires are drawn under the dots in a separate SVG layer.
- **Driven only by events.** Every dot, counter, and caption comes from `call_*` and `lane_changed` events. Nothing is simulated or interpolated beyond moving the dot between two recorded states.
- **One animation loop.** A single `requestAnimationFrame` loop moves every flight (magpie keeps a `trips` array rather than one CSS animation per element). It does no work when the tab is hidden or the view is off-screen. When more than ~60 flights are active, calls are aggregated into lane counters with no dots, so frame time stays bounded.
- **Reduced motion.** Under `prefers-reduced-motion`, flights take zero time: the state changes and nothing moves.
- **Reorders** (lane rows, leaderboard rows) use FLIP transitions, so a row slides to its new place instead of jumping.
- **Replay**: any finished run plays again from `events.jsonl` at 1–60×, with a scrubber. Idle gaps are compressed and labeled as compressed. A "replay this trial" link appears on every Compare and Trace page.
- **Accessibility**: the caption line is an `aria-live="polite"` region describing the latest notable event. Every animated fact is also in the Ops tables.
- Below the stage: a **race chart** (suite score with CI band vs. trials completed, per contestant, with a bump-chart toggle for rank) and the **trial board** (the Matrix filling in live).

### Ops view: charts

| Question | Chart |
|---|---|
| Where did the money and tokens go over time? | Stacked columns by minute/hour, split by contestant; the 7 largest are shown and the rest grouped as "Other" (magpie `LED_SHOWN = 7`). The ranking beside it is the legend, and clicking it filters. |
| Which provider is slow? | TTFT and total-latency distributions per provider/model (box or strip plot), with queue time shown separately |
| Are we rate-limited? | Lane timeline: in-flight and queued per key over time, with rest spans by failure class |
| Is caching working? | Cache hit rate per contestant; cache-read tokens as a share of input |
| Is a scaffold wasteful? | Prompt-composition stacked bars per contestant (system / tools / instructions / files / conversation) |
| What exactly happened? | The ledger table: every call, filterable, CSV export |

### Chart and color rules

- One categorical palette assigns each contestant a hue derived from its ID. The hue is the same in every view (stage dots, race chart, Pareto, Ops), and the palette is checked for contrast in both light and dark themes (follow the dataviz guidance before choosing colors).
- State is shown with a dot or swatch plus text, never color alone.
- Charts read precomputed series from the bundle; the viewer doesn't aggregate raw trials.
- No native `<select>`, and a click never scrolls the page. Both rules come from magpie's GUI standards and are tested.

### Outputs beyond the UI

- `arena report <run> --format md` produces a compact Markdown summary (leaderboard table, top regressions, spend by purpose, links into the viewer) for PR comments and chat.
- `arena export <run> --format parquet|csv` produces flat tables (trials, scores, calls) for notebooks.
- A GitHub Action runs a smoke suite on PRs that touch prompts, contestants, or adapters, and posts the Markdown summary.

## 11. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Core, CLI, runners, scorers | Python 3.12, `uv`, Typer, Pydantic v2 | The eval ecosystem (Inspect, arena-rank, evalica, pandas, semgrep) is Python |
| Gateway | Starlette/FastAPI + `httpx` (HTTP/2, streaming), passthrough-first; LiteLLM used only as the protocol-translation library | We need trial attribution, budget, served-model checks, and trace writes at the decision point, which a third-party proxy can't give us without forking it |
| Store | SQLite (WAL) + content-addressed files + `events.jsonl`; DuckDB for analytics | Zero-ops, a single file, easy to ship in a bundle |
| Sandbox | Docker with an internal network (gateway-only egress); pluggable backend for E2B/Modal/Vercel Sandbox later | Same pattern as Harbor and WebDev Arena |
| Server | FastAPI (`arena serve`): viewer assets (content-hashed filenames), `/api/*`, long-poll events, vote endpoints | Thin API over the bundle; same page in serve and static modes |
| Viewer | Vite + React + TypeScript, TanStack Table/Query, Monaco diff | Rich side-by-side UI; static-exportable |
| Charts and stage | Visx (d3-scale/d3-shape as React primitives) for every chart; hand-written SVG + one rAF loop for the Live stage; no animation library | One set of scales and theme tokens across charts and stage; the stage needs frame-level control that declarative chart libraries don't give |
| Observability | OpenTelemetry SDK (GenAI semconv), OTLP/HTTP exporter (opt-in), structlog | Standard spans that Phoenix/Langfuse/Jaeger read as-is |
| Web-artifact rendering | Playwright (screenshots, console errors) | Deterministic visual capture |
| Tests | pytest, pyright; Vitest + Playwright e2e in **Chromium and WebKit** | magpie's rule: UI tests run in both engines |

### Repository layout

```
ai-arena/
├─ AGENTS.md                 # notes for coding agents (CLAUDE.md is `@AGENTS.md @LESSONS.md`)
├─ CLAUDE.md
├─ LESSONS.md                # what merged work got wrong, and the rule that would have caught it
├─ pyproject.toml            # uv workspace
├─ providers.yaml            # provider endpoints, key env refs, limits (no secrets)
├─ catalog/models.yaml       # models, efforts, prices (price_version)
├─ src/arena/
│  ├─ core/        # models, ids, ModelRef, store, migrations, bundle + event contracts
│  ├─ gateway/     # server, protocols, translate, plan, lanes, rests, cache, cassettes, hooks, redact
│  ├─ providers/   # provider config, key resolution, detect/test, mock, cassette
│  ├─ catalog/     # model catalog, efforts, pricing
│  ├─ agents/      # one adapter per agent CLI (wire, install_kit, sessions) + recorded transcripts
│  ├─ kits/        # kit parsing, git-ref pinning, hashing, matrix expansion, ablation pairing
│  ├─ runners/     # completion, agent_cli, orchestration, scheduler
│  ├─ sandbox/     # docker backend + interface, internal network
│  ├─ obs/         # ledger queries, event log + long-poll, OTLP exporter, metrics, logging
│  ├─ scorers/     # execution, constraint, reference, visual
│  ├─ judges/      # rubric, pairwise, calibration
│  ├─ stats/       # aggregation, bootstrap, bradley-terry, pareto, run-diff
│  ├─ importers/   # inspect logs, harbor jobs, promptfoo results
│  ├─ server/      # fastapi app
│  └─ cli.py
├─ web/                      # viewer (pnpm)
│  └─ src/views/{leaderboard,pareto,matrix,compare,trace,rundiff,arena,judges,live,ops}/
├─ suites/{smoke,code,content,agentic,web}/
├─ contestants/  kits/  rubrics/  prompts/
├─ fixtures/{bundles,events,cassettes}/   # recorded from real runs; viewer and CI use these
├─ docs/
│  ├─ DEV_PLAN.md
│  ├─ code-standards.md
│  ├─ task-authoring.md
│  └─ subsystems/{README,gateway,providers-catalog,agent-adapters,runners-sandbox,scoring,stats,observability,viewer-live}.md
└─ tests/  (unit, golden fixtures, synthetic-stats, testenv/)
```

## 12. Engineering conventions

Taken from magpie's AGENTS.md, `docs/code-standards.md`, `docs/subsystems/README.md`, and LESSONS.md. The arena will be built largely by coding agents, so these rules are written down and checked rather than remembered.

- **AGENTS.md** is short and points elsewhere: the subsystem references, the code standards, and LESSONS.md. `CLAUDE.md` contains `@AGENTS.md` and `@LESSONS.md`.
- **Subsystem references** (`docs/subsystems/<name>.md`). Each one has the same four sections: *Responsibilities and sources of truth* (a table of part → responsibility → source file/symbol), *Runtime path* (numbered steps), *Constraints and failure behavior*, and *Verification* (the exact test commands). A PR that changes a documented responsibility, state transition, or failure behavior updates its reference in the same PR. References link to symbols, not line numbers.
- **Acceptance criteria for every change**, stated in the PR with what was run and what it printed:
  1. A test that fails without the change, reported with the failing output.
  2. Tests run under a temporary HOME (`tests/testenv`). No test reads or writes `~/.claude`, `~/.codex`, `~/.config/*`, or a real `arena.db`. `testenv` fails the test if it does.
  3. `uv run pytest`, `uv run pyright`, `pnpm -C web test`, and the e2e suite in Chromium and WebKit for any viewer change.
  4. A check against the real thing where possible (a real provider key, a real agent CLI version), or a plain statement that it wasn't run.
- **Fixtures come from real bytes.** Provider replies, agent transcripts, and error bodies are recorded from real calls (with secrets redacted), including truncated and empty bodies. Invented samples are not enough.
- **A red test on main is a bug now.** "Fails the same on main" is not a baseline.
- **One commit, one goal.** A fix found on the way goes in its own commit.
- **LESSONS.md loop.** After a batch of merged work, record what had to be fixed again as a rule, why it matters, and the evidence. A lesson seen on three separate days moves into `docs/code-standards.md`.

## 13. Delivery plan (checkpoints)

### Throughput check

1. **Blocking first steps.** CP1 has to land first: the data model (including Call, RunEvent, and ModelRef), the store, `providers.yaml`/catalog parsing, and the **bundle and event JSON schemas**. Everything else reads or writes them.
2. **Independent workstreams.** After CP1, four streams can run in parallel:
   - (a) gateway (CP2), then runners and agents (CP3);
   - (b) scorers and judges (CP4), which call the mock provider directly until CP2 lands and the gateway afterwards;
   - (c) stats and bundle (CP5), which need only fixture trials and calls;
   - (d) the viewer (CP6), built against fixture bundles and a fixture `events.jsonl`.
3. **Shared mutable state.** The SQLite schema and migrations, the bundle schema, and the event schema. One owner holds all three, and parallel streams request changes through that owner rather than editing them directly. Both schemas carry a version (`bundle_version`, `event_version`), and the viewer validates against them.
4. **Smallest safe decomposition.** Seven checkpoints. Each verifies with the mock or cassette providers, so none needs API keys or spend; real-provider checks are listed separately as "real use".

### 13.1 Issues, branches, and worktrees

Each checkpoint is one GitHub **epic** issue (label `type:epic`, `cpN`) with its own long-lived branch, `epic/cpN-<slug>`. The work is split into **sub-issues** (GitHub sub-issues of the epic, label `type:task`), each sized for one PR.

- **One sub-issue, one worktree, one branch, one PR.** Branch `cpN/<issue>-<slug>`, cut from `origin/epic/cpN-<slug>`:
  ```sh
  git fetch origin
  git worktree add ../ai-arena-wt/<issue> -b cpN/<issue>-<slug> origin/epic/cpN-<slug>
  ```
  The PR targets the epic branch, never `main`.
- **Sub-issues own disjoint files.** Each lists the files it owns, so parallel worktrees don't collide. A sub-issue that must change a file another one owns says so and is ordered after it. Shared schemas (SQLite migrations, the bundle and event schemas) belong to CP1's schema owner; later work requests a change in its PR rather than editing them in parallel.
- **Order is recorded as GitHub dependencies** ("blocked by"). An unblocked sub-issue can start in its own worktree at any time.
- **Closing.** GitHub closing keywords (`Closes #n`) only fire on merges into the default branch. A sub-issue PR into an epic branch says `Part of #<epic>` and `Closes #<issue>`, and the sub-issue is closed by hand when the PR merges into the epic branch.
- **Epic → main.** When every sub-issue of an epic is closed, open one PR from `epic/cpN-<slug>` into `main`. Its body has the checkpoint's verify commands with their output at the PR head, and `Closes #<epic>`. Merge only that reviewed head (§12).
- **Keeping epic branches current.** Every epic depends on CP1. After CP1's epic PR merges, each open epic branch merges `main` before new sub-issue branches are cut from it, and again before its own PR to `main`.

### CP1: Foundations: data model, store, config, conventions
- **Touches:** `src/arena/core/*`, `providers/` (config only), `catalog/`, `cli.py`, `suites/smoke/`, `contestants/mock-*.yaml`, `kits/` (fixtures), `docs/bundle-schema.json`, `docs/event-schema.json`, `tests/testenv/`, `AGENTS.md`, `CLAUDE.md`, `LESSONS.md`, `docs/code-standards.md`, `docs/subsystems/README.md`.
- **Result:** `arena validate` loads and validates suites, contestants, rubrics, `providers.yaml`, and the catalog. The store creates `arena.db` and the artifact CAS, and an unreadable store is an error, never empty. Contestant IDs are stable hashes. Kits parse and hash (§4), and a matrix file expands to the expected contestants, with `exclude` and `ablation: kit` honored. The bundle and event schemas (including `Session`, `KitInstall`, and the `kit_installed`/`session_turn`/`skill_event` events) are published, with a hand-written fixture bundle and `events.jsonl`. Agent-facing docs exist with the subsystem template.
- **Verify:** `uv run pytest tests/core && uv run pyright && uv run arena validate suites/smoke contestants/ providers.yaml catalog/`.
  - ID stability: the same config gives the same ID; reordered YAML keys give the same ID; a changed temperature or scaffold version gives a new ID.
  - ModelRef round-trips (`openrouter/qwen/qwen3-coder:high` → provider `openrouter`, model `qwen/qwen3-coder`, effort `high`).
  - Kit hash: the same files give the same hash; editing one byte of one skill, or changing an MCP header reference, gives a new hash; an env value changing does not (references are hashed by name). A matrix of 2 models × 2 scaffolds × 2 kits with one `exclude` expands to 7 contestants with distinct IDs.
  - A deliberately corrupted `arena.db` raises instead of returning no runs.
  - A test that writes to the real HOME fails under `testenv`.

### CP2: Gateway, providers, and observability core
- **Touches:** `gateway/`, `providers/`, `catalog/`, `obs/`, `cli.py gateway|providers|doctor`.
- **Result:** `arena gateway` serves the four protocols with passthrough-first relaying and LiteLLM translation. It has per-trial tokens, key lanes (concurrency, RPM, queue, adaptive), the failure classes and rests from §5.5, served-model capture, redaction, the budget cap, Call rows, RunEvents with seq long-poll, an opt-in OTLP exporter, and record/replay cassettes. The mock and one real provider are wired.
- **Verify:** `uv run pytest tests/gateway tests/obs`, using fake upstreams fed recorded real bytes:
  - A 429 with `Retry-After` rests that key and the next key answers. A quota 429 is classed `quota`, not `rate_limit`.
  - A 200 `text/html` is `bad_reply`.
  - A stream that fails after its first byte is not retried.
  - A reply naming another model sets `swapped`, and with `require_served_model` it fails the call.
  - A browser `Origin` gets 403 and a missing token gets 401, both on loopback.
  - A queued streaming request receives SSE keepalive comments.
  - Call cost equals tokens × the catalog fixture price. A flat-fee subscription call records tokens with `cost_usd = null`.
  - A 429 naming weekly usage limits rests the whole account on `subscription_limit` without resting sibling accounts or counting against the contestant. A failed refresh disables the session as `auth_refresh`, naming the account.
  - The long-poll returns events after a given seq and wakes on a new one.
  - Filling the OTLP queue increments `otel_dropped` without slowing requests, and an in-process collector receives `gen_ai.*` spans.
  - `arena gateway bench` shows < 5 ms added p50.
  - **Real use:** one call per configured real provider through `arena providers test`, including one imported subscription account.

### CP3: Runners, sandbox, agent adapters, scheduler
- **Touches:** `runners/`, `sandbox/`, `agents/`, `kits/`, `gateway/` (the `/mcp/<name>` proxy only), `cli.py run|ls|resume`.
- **Result:** `arena run` executes Suite × Contestants × repeats through the gateway, with scheduler caps, dry-run, budget, resume, and the trial cache. The agent-CLI runner runs Claude Code, Codex CLI, OpenCode, Pi, and Aider adapters in containers whose only egress is the gateway, with the reached check. Each adapter's `install_kit` copies the kit into the container's agent paths, hash-checks it, and lists anything it refused; `sessions()` assembles turn timelines from gateway Calls, the native transcript, and the filesystem diff. The orchestration runner records child spans.
- **Verify:**
  - `uv run arena run suites/smoke -c contestants/mock-a.yaml -c contestants/mock-b.yaml --repeats 2` creates exactly `tasks×2×2` trials. Rerunning makes 0 provider calls (cache hit counter). Killing it mid-run and resuming fills only the missing trials. `--dry-run` writes nothing.
  - A container test confirms the internet is unreachable but the gateway answers, and that the time limit is enforced.
  - Each adapter's contract test runs the pinned CLI in a container against the mock provider and asserts that its first request carried `arena-<trial_id>` on the expected protocol and that its native transcript was collected.
  - An adapter with deliberately broken wiring yields `errored`/`wiring`, never `failed`.
  - Kit install, per adapter, in a container: files land in the adapter's documented kit paths and their bytes hash to the kit hash; the host's real agent config directories are untouched (checksum before and after); an item the agent cannot take (a skill for Aider) appears in `KitInstall.refused` and the trial is flagged `kit_unapplied`, not silently dropped; `kit: none` installs nothing and the trial still passes the reached check.
  - Harness specifics: OpenCode's container has the kit's skills in exactly one folder and each skill is listed once; Pi below 0.99 is refused at preflight with the reason; with `scaffold_prompt: pinned`, two different model families in OpenCode receive byte-identical system prompts (checked from `prompt_parts` and the recorded request).
  - Skill telemetry on recorded transcripts: the fixture transcript for each adapter yields the expected `listed`, `loaded`, and `invoked` events for named skills. A truncated or unparseable transcript yields a session marked `partial`, not an empty one.
  - MCP: a URL server is reachable only through `/mcp/<name>` and the upstream sees the daemon-added auth header, which never appears in the container's environment or files; each request becomes a `tool` span; an unset `${ENV}` reference fails preflight naming the variable.
  - Session assembly: for a trial run on the mock provider with a scripted agent, the assembled `Session` has the expected turn count, each turn's calls match the ledger rows by trial token, files touched match the diff, and a trial with an `unmetered` agent shows the native transcript's turns with the `unmetered` flag.
  - **Real use:** one agentic smoke task per adapter against a real provider, under a $1 budget.

### CP4: Scoring: deterministic scorers and LLM judges
- **Touches:** `scorers/`, `judges/`, `rubrics/`, `cli.py score|judge`.
- **Result:** Execution, constraint, and visual scorers work, as do pointwise rubric judging and pairwise judging with order-swap. Gates work. Scores carry evidence, and scoring is re-runnable without re-executing trials. Judge calls are tagged `purpose: judge` in the ledger.
- **Verify:** `uv run pytest tests/scorers tests/judges` with golden fixtures: the oracle solution scores 1.0, the null solution scores 0, and a known-buggy solution fails the expected tests. A mock judge that always prefers position A yields all ties after swap. Changing a rubric version produces a separate score series. Judge spend appears in the ledger under `judge`, separately from contestant spend.

### CP5: Statistics, ratings, and the report bundle
- **Touches:** `stats/`, `core/bundle.py`, `cli.py report|export`.
- **Result:** Aggregation, cluster-bootstrap CIs, pass@k and pass^k, paired head-to-head, Bradley-Terry ratings, Pareto frontier, run diff, the judge calibration report, and precomputed chart series (usage by contestant over time, latency distributions, prompt composition). Also the **Kit effect** computation (paired difference with CI, win/tie/loss, skill uptake, observational invoked/not-invoked split, cost delta), per-session and per-group session summaries, and the Markdown report and bundle export, including `events.jsonl`, the calls summary, and sessions.
- **Verify:** `uv run pytest tests/stats` on synthetic data with planted ground truth. BT recovers the true ordering (Spearman ≥ 0.95 with 2k comparisons). Two identical contestants are reported as "no detectable difference" in ≥ 95% of 200 simulations.
  - Kit effect: a planted +0.15 score lift from the kit is recovered within its CI in ≥ 95% of 200 simulations; a planted zero lift is reported as no detectable difference in ≥ 95%; a kit with 0% skill uptake and a planted zero lift shows uptake 0 and is labeled "kit installed, skills not used". The invoked/not-invoked split carries the "observational" label in the output. `swapped`/`unmetered` trials are excluded from headline numbers and counted in the footnote. The exported bundle validates against both schemas.

### CP6: Viewer: comparison, Sessions, Live, and Ops
- **Touches:** `web/`, `server/` (including `arena serve --host`, run-key sign-in, and the CSRF/Origin checks from §3.1).
- **Result:** Leaderboard, Pareto, Matrix, Compare (with diff, hide-names, repeat switcher, model · agent · kit column headers), Trace (with prompt composition), Run diff, Sessions (list, filter, turn timeline, compare two sessions), Kit effect, Live (stage, race chart, trial board, replay), and Ops (ledger, usage charts, lanes). They work against both `arena serve` (local or remote) and a static export.
- **Verify:** `pnpm -C web test && pnpm -C web e2e` in **Chromium and WebKit**.
  - Comparison flow against the fixture bundle: leaderboard → matrix cell → Compare shows 3 columns with rendered artifacts → toggle diff → toggle hide-names (names gone from the DOM) → open the trace.
  - Live replays the fixture `events.jsonl`, and its final counters equal the ledger totals for the same fixture.
  - Under emulated `prefers-reduced-motion` no flight animates, and the final state is identical.
  - With the tab hidden, no rAF frames run (counted).
  - A fixture of 200 concurrent calls keeps p95 frame time under 16 ms (Playwright performance trace).
  - The static export opened from `file://` passes the smoke e2e, with Live in replay mode.
  - The HTML artifact iframe cannot read `parent.document`.
  - Sessions flow against the fixture bundle: filter by kit `team-coding@3` → only matching sessions listed → open one → timeline shows listed/loaded/invoked markers on the expected turns and the per-turn token and cost sums equal the session total → open Compare sessions with a second session → both timelines align by turn. A `partial` session shows its badge.
  - Kit effect view renders the planted-lift fixture with the expected difference, and its observational split is visibly labeled.
  - Remote mode, tested through the real server (not the handler alone): `arena serve --host 0.0.0.0` refuses a request without the run key (401), accepts the sign-in link, rejects a cross-origin POST (403), and never serves the gateway port off-box.

### CP7: Human evaluation, judge calibration, CI, launch suites, importers
- **Touches:** `web/` (Arena and Judges views), `server/` (vote API), `.github/workflows/arena-smoke.yml`, `suites/*`, `contestants/*`, `importers/`, `docs/task-authoring.md`, `docs/subsystems/*`.
- **Result:**
  - Blind voting with keyboard flow. Votes feed the human BT leaderboard and the gold set. The judge report page and disagreement browser work.
  - The PR workflow replays cassettes and posts a Markdown summary.
  - Starter suites:
    - **code:** around 40 private function/module tasks across Python, TypeScript, Go, and Rust, scored by hidden tests.
    - **agentic:** around 15 repo-level tasks in Harbor format: bug fix, feature add, refactor, migration.
    - **content:** around 30 tasks: blog post, docs page, release notes, marketing email, social thread, summary of a long doc, rewrite to a style guide.
    - **web:** around 15 tasks: landing page, dashboard, interactive widget, SVG illustration.
  - Contestant sets compare (a) models on a fixed scaffold (the model axis: OpenCode, plus Pi as the minimal baseline, with `scaffold_prompt: pinned`), (a′) each vendor's own CLI with its own model (the product axis), (b) scaffolds on a fixed model, (c) orchestration strategies on a fixed model, (d) judge configs, and (e) **kits**: the same model × agent with `kit: none` vs `kits/team-coding@3`.
  - A **skill-ablation starter suite** (about 10 tasks, half coding and half content) written so that one example skill (for instance `tdd-loop`) should matter on some tasks and not on others, which lets the Kit effect panel be checked against known expectations.
  - Importers cover Inspect logs, Harbor job outputs, and promptfoo results.
  - Every subsystem has a reference page.
- **Verify:**
  - The e2e test casts 20 votes and checks that the human leaderboard updates and identities stay hidden until after the vote.
  - `uv run arena judges report` produces κ, position-bias, and length-correlation numbers on a fixture gold set.
  - The workflow runs green on a test PR using cassettes.
  - `uv run arena validate suites/` passes, and `uv run arena selfcheck suites/` shows each executable task's oracle scoring 1.0 and its null solution scoring 0.
  - One real end-to-end run at ≥ 3 contestants × 3 repeats stays under the agreed budget, and its report opens with no empty views. Its Live replay matches its ledger.
  - Each importer round-trips a sample file into a viewable bundle.
  - The ablation suite runs with `kit: none` vs `team-coding@3` on one model × agent at ≥ 3 repeats. The Kit effect panel shows a difference, uptake, and cost delta; the Sessions view shows at least one session per arm with skill events visible (or the "skills not used" label); changing one byte of a skill produces a new kit hash and a separate result series.
  - Every link in `docs/subsystems/*` resolves to an existing file and symbol (doc link check in CI).

## 14. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Benchmark contamination makes public tasks meaningless | Primary suites are private and authored in-repo; a `created_at` per task lets reports filter to post-cutoff tasks; public benchmarks enter only via importers and are labeled as such |
| Judge bias is mistaken for a model difference | Order-swap, cross-family judges, calibration gate, length-correlation alarm, human BT leaderboard shown alongside |
| Noise is reported as a ranking | Repeats ≥ 3, CIs everywhere, "no detectable difference" language, run diff thresholds tied to CI |
| API spend runs away | Dry-run estimate, a hard budget cap enforced at the gateway before each call, response cache, cassettes and mock for all dev/CI |
| Agent CLIs change output formats or config fields | Pinned versions in contestant identity; an adapter contract test per version on recorded transcripts; metrics come from the gateway, not from parsing agent output |
| An agent CLI ignores the gateway (hard-coded endpoint, OAuth-only mode) | Reached check; the trial is flagged `unmetered` and excluded from headline cost numbers; container egress is gateway-only, so the bypass fails rather than silently succeeding |
| The gateway becomes a bottleneck or a single point of failure | Overhead benchmark in CI (< 5 ms p50); lanes isolate a slow provider; gateway crash marks in-flight trials `errored` (infra), and resume reruns only those |
| A subscription's terms are breached by automated evaluation, or an account is limited | Each subscription is explicit, opt-in, and warned on first use; the vendor's own CLI is the preferred client and a vendor API key the preferred credential where one exists; all subscription traffic is visible in the ledger and reported, never labeled as API traffic |
| Subscription quota resets mid-run and stalls trials | `subscription_limit` rests only that account, not the key pool; the scheduler holds trials that need it with a visible reset countdown while other models, accounts, and tasks keep running |
| Subscription sign-in expires mid-run | `auth_refresh` disables the account and names it; `arena providers test` tells the owner which sign-in to renew before the next run |
| Generated code or HTML escapes the sandbox | Containers with gateway-only egress and resource limits; keys never enter containers; HTML iframe on a separate origin with `sandbox`; never execute on the host |
| A web page spends the user's keys through the loopback gateway | Every request needs a token; any browser `Origin` is refused |
| A provider silently changes the model behind an alias or aggregator | `model_served` per call; `swapped` flag; `require_served_model` for aggregators; the report flags runs where it varied |
| Infra failures counted against a contestant | Narrow failure classes (§5.5); `errored` vs `failed`; a test per class that a neighboring class is unchanged |
| The Live view lies or drifts from the data | Driven only by RunEvents; an e2e check that replay counters equal ledger totals |
| Animation hurts performance or accessibility | One rAF loop, paused when hidden; aggregation above ~60 flights; reduced-motion support; aria-live caption; all facts also in tables |
| A kit's skills are installed but never used, so a "kit effect" is really noise | Skill telemetry (`listed`/`loaded`/`invoked`) is recorded per session; the Kit effect panel shows uptake beside the score difference and labels low-uptake kits; the invoked/not-invoked split is marked observational, and only the `kit: none` ablation is called causal |
| A kit changes behavior without the agent accepting it (format drift, refused item) | Hash check after install; `KitInstall.refused` kept; `kit_unapplied` flag excludes the trial from kit comparisons; adapter contract tests on recorded transcripts per pinned agent version |
| A session timeline is wrong because a native transcript format changed | `partial` flag instead of an empty timeline; gateway Calls remain the source of truth for tokens, cost, and latency; contract tests per pinned version |
| The remote web app exposes keys, the Docker socket, or the gateway | Loopback default; run-key sign-in; Origin/CSRF checks; gateway never bound off-box; provider keys stay on the daemon; a documented SSH/Tailscale tunnel recommendation and Docker socket warning (§3.1); remote-mode e2e through the real server |
| Schema churn breaks parallel streams | Single schema owner; versioned bundle and event schemas; viewer validates and shows a clear error |

## 15. Open decisions (defaults in bold)

1. **Stack**: **Python core + React viewer**. Alternative: all-TypeScript, which simplifies the frontend but loses the Python eval ecosystem.
2. **Providers at launch**: **Anthropic, OpenAI, Google, OpenRouter (for open-weight models), mock, cassette**.
3. **Agent CLIs at launch**: **OpenCode (model-axis default), Pi (minimal baseline), Claude Code, Codex CLI (product axis), Aider**; then Gemini CLI and OpenHands.
4. **Hosting and shell** (resolved in §3.1): **web app served by one daemon, local by default and optionally remote** (run-key auth, tunnel recommended), plus static export. A desktop shell is deferred: pywebview first if wanted, Tauri only if signed installers or auto-update become requirements. A multi-user hosted instance with accounts is still v2.
5. **Budget per full comparison run**: needs a number from the owner before CP7.
6. **Use Inspect AI as the execution engine** instead of our own runners? **No for v1.** We need N-way, artifact-first comparison and agent-CLI scaffolds as contestants. We import Inspect logs instead and can revisit later.
7. **Gateway**: **our own thin gateway, with LiteLLM as a translation library**. Alternatives:
   - LiteLLM Proxy: adds Postgres and its own key management, and doesn't write trial-attributed records at the decision point.
   - magpie as the gateway: its routing groups and model fallbacks are built to change the model, which the arena forbids. It can still be registered as an upstream **provider** for subscription accounts, with no fallback models configured.
8. **Live transport**: **seq-cursor long-poll**. Alternatives: SSE with `Last-Event-ID` (similar, slightly lower latency) or WebSocket (more state to manage). The cursor design lets us switch to SSE later without changing the event log.
9. **Chart library**: **Visx**. Alternative: ECharts, which has more built-in charts but its own theming and a canvas renderer that is harder to share with the SVG stage.
10. **Gateway hooks in v1**: **interface only, no shipped hooks**. They become useful for perturbation experiments (system-prompt variants, tool filtering) once the base comparison works.
11. **Kit sources in v1**: **local directories and git refs pinned to a commit**. Alternatives: a registry or plugin marketplace, deferred until kits are shared across teams.
12. **Agents with no skill or MCP support (Aider)**: **declared unsupported for kits** (`refused`, `kit_unapplied`); their kit contribution is instructions only, and only where the agent reads an instructions file.
13. **Remote mode exposure**: **loopback by default; `--host` requires the run key**. Whether v1 ships built-in TLS or relies on a tunnel/reverse proxy: **tunnel/reverse proxy**.
14. **Subscription-backed models**: **imported from the user's own sign-in, opt-in per provider, vendor's own CLI the preferred contestant, vendor API key preferred where one exists**. Alternatives: register magpie as the upstream provider that holds the subscriptions (decision 7) instead of importing sessions directly; or exclude subscription-only models from the arena entirely. Direct import is the default because it keeps attribution inside one process.

## 16. References

- magpie: https://github.com/yetone/magpie (local clone `_sample/magpie`): `AGENTS.md`, `LESSONS.md`, `docs/code-standards.md`, `docs/subsystems/{gateway-routing,providers-accounts,agent-wiring,gateway-middleware,gui-shell}.md`, `internal/gateway/trace.go`, `internal/usage/{ledger,otel}.go`, `internal/gui/assets/routing.js`
- magpie deployment and kits: `internal/gui/{web,gatewaymode}.go` (one page, several hosts), `internal/library/targets.go` (per-agent skill, instruction, and MCP paths), `internal/sessions` (reading native agent logs), `docs/subsystems/{library,gui-shell}.md`
- Desktop shell options (deferred): pywebview https://pywebview.flowrl.com · Tauri sidecars https://tauri.app/develop/sidecar/
- Inspect AI: https://github.com/UKGovernmentBEIS/inspect_ai (`inspect view`, `inspect view bundle` for the local viewer and static publishing)
- Harbor: https://github.com/laude-institute/harbor · Terminal-Bench 2.0 + Harbor: https://www.tbench.ai/news/announcement-2-0 · Running Terminal-Bench: https://harborframework.com/docs/running-tbench
- promptfoo: https://github.com/promptfoo/promptfoo (`src/scheduler/` for adaptive concurrency and rate-limit state)
- WebDev Arena: https://arena.ai/blog/webdev-arena · Arena-Rank: https://arena.ai/blog/arena-rank · WebDev preference data: `lmarena-ai/webdev-arena-preference-10k` (Hugging Face)
- evalica: https://github.com/dustalov/evalica
- DeepEval: https://deepeval.com/blog/top-5-llm-evaluation-frameworks
- OpenTelemetry GenAI semantic conventions: https://opentelemetry.io/docs/specs/semconv/gen-ai/
- The Scaffold Effect in Coding Agents: https://arxiv.org/pdf/2607.22585
- LLM-judge position bias: https://arxiv.org/html/2602.02219v2 · Judge reliability vs. validity: https://arxiv.org/pdf/2606.19544 · Openlayer judge guide: https://www.openlayer.com/blog/llm-as-judge-evaluation-guide
