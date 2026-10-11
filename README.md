# AI Arena

AI Arena is a local-first workbench that runs the same tasks against different
contestants (models, agent CLIs, orchestration strategies, skill kits) and
compares them on quality, cost and time. It is built for coding and content
tasks. The design is in [docs/DEV_PLAN.md](docs/DEV_PLAN.md).

> **Status.** Every checkpoint (CP1 to CP7) is merged into `main`, and the
> test suite and web e2e suite pass. Two things are not done yet:
>
> - There is no real provider run. The launch model `anthropic/claude-sonnet-4-5`
>   has no verified price in the catalog, so a metered run refuses to start.
>   [#62](https://github.com/bravew/ai-arena/issues/62) tracks it.
> - `rubric-judge` scoring has only been run against a fake judge. It needs a
>   priced judge model behind a gateway, the same blocker as #62.

## Architecture

```text
 suites/ + contestants/ + kits/          providers.yaml + catalog/models.yaml
          |                                        |
          v                                        v
   arena plan / run  ---------------->  Gateway (Starlette, loopback)
   scheduler, resume,                   auth, model resolve, per-key lanes,
   budget estimate                      cache, hooks, redaction, budget,
          |                             Calls ledger, event log
          v                                        ^
   Trial runners                                   | only route out
   - completion (mock)                             |
   - agent CLI in a Docker sandbox ----------------+
     (internal network, trial token only)          |
          |                                        v
          v                                  provider APIs
   SQLite store + content-addressed          (keys stay in the gateway)
   artifacts  --->  scorers  --->  stats  --->  report bundle
                                                     |
                                                     v
                                     arena serve / static export
                                     (React viewer: Compare, Trace, Sessions,
                                      Kit effect, Live, Ops, blind voting)
```

The parts, each with a reference page in
[docs/subsystems](docs/subsystems/README.md):

| Part | What it does | Code |
| --- | --- | --- |
| Core | Data model, SQLite store (migrations), content addressing, bundle contract | `src/arena/core` |
| Gateway | One authenticated entry for every model call: protocol relay, lanes, rests, budget, Calls ledger, events | `src/arena/gateway`, [gateway.md](docs/subsystems/gateway.md) |
| Providers and catalog | Provider config with env-var credential references, subscription accounts, priced model catalog | `src/arena/providers`, `src/arena/catalog`, [providers-catalog.md](docs/subsystems/providers-catalog.md) |
| Runners and sandbox | Scheduler, resume, concurrency caps, Docker sandbox on an internal network, agent trial bridge | `src/arena/runners`, `src/arena/sandbox`, [runners-sandbox.md](docs/subsystems/runners-sandbox.md) |
| Agent adapters | Aider, Claude Code, Codex CLI, OpenCode, Pi; a check that the agent really reached the gateway | `src/arena/agents`, [agent-adapters.md](docs/subsystems/agent-adapters.md) |
| Kits and sessions | Skill/instruction kits installed in the container; per-session timelines | `src/arena/kits`, [sessions-kit-effect.md](docs/subsystems/sessions-kit-effect.md) |
| Scoring | Deterministic scorers (execution, constraint, reference, visual) and LLM judges with calibration | `src/arena/scorers`, [scoring.md](docs/subsystems/scoring.md) |
| Stats | Cluster bootstrap intervals, ratings, Pareto, run diff, kit effect | `src/arena/stats`, [stats.md](docs/subsystems/stats.md) |
| Server and viewer | Local API, artifact origin, static export; React viewer in `web/` | `src/arena/server`, `web`, [server.md](docs/subsystems/server.md) |
| Importers | Inspect, Harbor and promptfoo results into a bundle | `src/arena/importers`, [importers.md](docs/subsystems/importers.md) |

Rules that hold across the system:

- **Provider credentials never leave the gateway.** A trial container gets a
  trial token and the gateway URL, and sits on a Docker `--internal` network
  whose only reachable peer is the gateway.
- **No cross-model fallback.** A trial never switches model partway.
- **Hooks fail closed.** A hook that throws or times out errors the call.
- **Metered spend has a hard cap** of US$20 per full comparison run. The gateway
  refuses a call before dispatch unless it carries an explicit output-token
  limit and the catalog has input and output prices, reserves a priced upper
  bound, and settles against the final catalog-priced usage. Subscription
  quota and compute are reported separately.
- **Agent config is written only inside trial containers**, never on the host.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node.js and [pnpm](https://pnpm.io/) for the viewer (`web/`)
- Docker, only for agent trials and the gateway container
- Go, only to run `arena selfcheck` on the code suites (their scorers compile Go)

## Build

```sh
uv sync                              # Python environment from uv.lock
pnpm -C web install --frozen-lockfile
pnpm -C web build                    # viewer bundle in web/dist (used by serve and static export)
```

Docker images, only when you run agent contestants:

```sh
docker build -f docker/gateway.Dockerfile -t arena-gateway:dev .   # also built on demand by arena run
docker build -f docker/agents/aider.Dockerfile -t arena-aider:<version> .
docker build -f docker/scorer-python.Dockerfile -t arena-scorer-python:1 docker   # only for run --score-execution
# likewise claude_code, codex_cli and pi; the tag each adapter expects is its IMAGE constant in src/arena/agents/
```

## Run

Everything below runs without Docker or provider credentials.

```sh
uv run arena validate suites/                   # validate every provider, catalog, suite, contestant, kit, rubric file
uv run arena plan suites/smoke -c mock-a -c mock-b --repeats 3     # expand the matrix, show trials and estimated cost
uv run arena run suites/smoke -c mock-a -c mock-b --repeats 1 --db .arena/arena.db   # deterministic mock trials
uv run arena ls --db .arena/arena.db
uv run arena run suites/smoke -c mock-a --dry-run                  # plan only, writes nothing
uv run arena selfcheck suites/ --trust-task-code   # oracle must score 1.0, null 0.0 (runs task code on the host; needs Go)
uv run arena serve --home <dir with arena.db>      # viewer and API on 127.0.0.1:7400, artifacts on :7402
uv run arena doctor                                # Docker, gateway, providers, storage
```

`arena run --resume <run id>` retries errored and missing trials.

### Metered agent runs

A contestant with a `scaffold` runs through a gateway container that `arena run`
starts for the run. Prepare these first:

1. Put the provider key in the environment variable named in `providers.yaml`
   (for example `ANTHROPIC_API_KEY`). The key is passed to the gateway
   container by name, never by value.
2. Make sure `catalog/models.yaml` has a verified price for the model. A model
   without input and output prices is refused before any call.
3. Build the gateway and agent images above.
4. Run with a budget of at most US$20:

```sh
uv run arena run suites/skill-ablation -c contestants/sets/ablation.yaml --repeats 3 --budget-usd 20
```

A trial counts as a success only if the agent finished and the ledger holds
successful, priced calls for that run, trial and model, each with a
`call_finished` event. Subscription-backed models are rejected for agent trials
until an account runtime is wired in.

### Other commands

```sh
uv run arena gateway                       # standalone gateway on 127.0.0.1:8765 (providers.yaml, catalog/models.yaml)
uv run arena score <run id> --scorer <id> --home <dir>
uv run arena report <run id> --home <dir>
uv run arena export <run id> --out <new dir> [--format static] --home <dir>
uv run arena judges --help                 # judge calibration reports
uv run arena votes --help                  # blind human voting
```

## Repository layout

| Path | Contents |
| --- | --- |
| `src/arena/` | Python package (`arena` CLI) |
| `web/` | React viewer, Vitest unit tests, Playwright e2e |
| `suites/` | `smoke`, `code`, `agentic`, `content`, `web`, `skill-ablation` |
| `contestants/` | Mock contestants and the `sets/` for the model, product and ablation axes |
| `kits/` | Skill kits, including `team-coding` |
| `catalog/`, `providers.yaml` | Priced model catalog and provider config |
| `rubrics/`, `fixtures/` | Judge rubrics and calibration fixtures |
| `docker/` | Gateway and agent images |
| `docs/` | Plan, code standards, schemas, [subsystem references](docs/subsystems/README.md) |

## Test

```sh
uv run pytest
uv run ruff format --check src tests
uv run ruff check src tests
uv run pyright
uv run python scripts/check_doc_links.py
uv run arena validate suites/ && uv run arena selfcheck suites/ --trust-task-code
pnpm -C web e2e                           # Chromium and WebKit
```

Tests run under a temporary `HOME` (`tests/testenv`); nothing reads or writes
your real agent configuration.

## Known gaps

- **No real launch run.** Needs a verified catalog price for the launch model
  and provider credentials ([#62](https://github.com/bravew/ai-arena/issues/62)).
  [docs/launch-report.md](docs/launch-report.md) is the readiness report; it
  predates the metered agent path and still says `arena run` has no gateway
  dispatcher.
- **`rubric-judge` has not run against a real model.** `arena run
  --judge-model <model> --judge-url <gateway> [--judge-protocol anthropic]`
  grades each answer against the task's rubric; tests use a fake transport
  only. Without the flags `run` prints the scorer as skipped. `constraints`
  scorers always run, and `execution` scorers run with `--score-execution`
  (Docker, no network; the Python image above plus `node:22-slim`,
  `rust:1-slim` and `golang:1.23`, pulled on first use). A leaderboard score is
  the mean over the scorers that ran.
- **A resumed run** appends a second `run_started` and `run_finished` pair to
  the run's events.
- **Kits and native sessions** are not resolved or persisted by `arena run`.
- **Subscription-backed models** are not usable for agent trials.

## Contributing

Agents and people follow [AGENTS.md](AGENTS.md): one sub-issue per worktree and
branch, PRs into the epic branch, and [code standards](docs/code-standards.md).
SQLite migrations and the shared bundle and event schemas change only through
the schema owner. Read [LESSONS.md](LESSONS.md) before changing code.

## License

MIT, see [LICENSE](LICENSE).
