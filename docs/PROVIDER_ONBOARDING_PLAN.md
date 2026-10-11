# Provider onboarding and subscription runtime plan (CP8)

Status: proposal, 2026-10-11. This extends [DEV_PLAN](DEV_PLAN.md) §5 (providers
and gateway), §5.8 (subscription-backed models) and §6.2 (launch adapters). It
adds one checkpoint, CP8, after the CP1 to CP7 work that is already on `main`.

## 1. Goal

Compare the models of any provider in the arena, whether the provider is
billed by API key or by a subscription (Claude Pro/Max, ChatGPT Plus/Pro). A
user adds a provider, sees that it works, picks its models, and puts them in a
contestant set next to every other provider's models. The report labels each
result with how it was served and how it was costed.

Done means one command sequence takes a machine with a signed-in `claude` and
`codex` to a finished mixed run (API and subscription contestants) with
correct labels, and the Ops page shows the real providers, accounts and calls.

Non-goals: a shared pool of subscription accounts, any rotation across
accounts the user does not own, and any silent fallback to a different model.

## 2. What the references do

### 2.1 Neumar (`/Volumes/4TB_WD/dev/bravew/Neumar`, `src-api/src`)

Neumar calls the local Codex and Claude subscriptions by **running the vendor's
own CLI as a subprocess and letting that CLI use its own sign-in**. It never
reads or copies a token.

| Concern | What Neumar does | Source |
| --- | --- | --- |
| Runtime catalog | One definition per CLI: binary name, version flag, install and update options, fallback models, optional model fetch, stream format, optional auth probe | `shared/agent-runtimes/registry.ts` |
| Binary lookup | Searches a login shell, an extended PATH and common install dirs, because a GUI process has a short PATH. `CLAUDE_CODE_PATH` overrides | `extensions/agent/codex/index.ts` (`resolveCodexBinaryPath`), `extensions/agent/claude/index.ts` |
| Subscription vs key | **No API key means subscription.** The CLI reads its own OAuth store (Codex: `~/.codex/auth.json`). A key is passed only when one exists, and an empty `OPENAI_API_KEY` is never set because it blocks the subscription flow | `extensions/agent/codex/auth.ts`, `createCodexClient` |
| API-key mode | Codex: `CODEX_API_KEY`, optional `OPENAI_API_KEY` and `OPENAI_BASE_URL`. Claude: `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_BASE_URL`, with `ANTHROPIC_API_KEY` deleted so the token wins | `extensions/agent/claude/index.ts` |
| Env hygiene | The Codex SDK replaces the whole environment when `env` is set, so PATH, HOME, TMPDIR and proxy variables are forwarded by hand | `createCodexClient` |
| Auth probe | `claude auth status` is parsed into authenticated, unauthenticated or unknown. Models come from the CLI itself (`codex debug models`, a Claude supported-models probe) | `registry.ts` (`probeClaudeAuth`), `claude-model-probe.ts` |
| Connection test | A typed result (`ok`, `not_installed`, `auth_required`, `incompatible_version`, `unsupported_model`, `protocol_failure`, `permission_failure`) with recovery actions such as an install or login command | `shared/agent-runtimes/connection-test.ts` |
| Cost | A subscription or free billing type forces cost to zero | `extensions/agent/claude/index.ts` (`effectiveCost`) |

### 2.2 magpie (`_sample/magpie`)

magpie manages accounts as first-class objects. It adds what Neumar lacks.

- `magpie accounts add|import|switch|forget|alias|refresh`, plus **discovery**
  of sign-ins that Claude Code and Codex already hold (their files and the
  Keychain), which the user can switch off (`accounts discovery off`,
  `MAGPIE_NO_ACCOUNTS=1`). See `accounts_cli.go`, `account_discovery_cli.go`.
- Per-account usage and limit meters, a seat and cap model, and a warm-up of
  each account's window. See `internal/provider/claude_limits.go`,
  `claude_usage.go`, `codex_credits.go`.
- A Claude subscription is answered by the **real local `claude` binary**, and
  magpie downloads Anthropic's build when a server has none. Tools are bridged
  back over MCP. See `claude_code_cli.go`, `internal/claudebridge`.
- Codex and ChatGPT are served with the account token on the ChatGPT backend.

### 2.3 The arena today

| Area | State on `main` |
| --- | --- |
| Providers | `providers.yaml` has `anthropic-max` (`vendor: claude`) and no ChatGPT provider. `kind`, `keys` and `subscription` are validated |
| Import | `arena providers import <vendor> --from <file>` copies a sign-in into `~/.arena/subscriptions/`. It was tested only on synthetic fixtures. No login, discovery, list, switch or forget |
| Gateway | Signs subscription requests for `anthropic` and `responses`. The ChatGPT backend URL is not configured |
| Agent trials | Subscription-backed models are **rejected** (`cli_run.py`, `runners/metered_gateway.py`) |
| Ledger | `cost_usd` is null for subscription calls. `subscription_served` exists on the contestant |
| Ops page | Reads fixtures (`web/src/views/ops/fixtures.ts`). No live adapter |
| Doctor | Checks Docker, gateway, providers and storage. It does not look for the vendor CLIs |

## 3. Design

### 3.1 Provider kinds and runtimes

A provider has an auth mode and, for subscriptions, a runtime.

| Auth | Runtime | Who talks to the vendor | How a call is observed | Cost |
| --- | --- | --- | --- | --- |
| API key | gateway (existing) | The gateway, with the key | Gateway ledger row | Priced from the catalog |
| Subscription | `gateway` (existing §5.8) | The gateway, using a daemon-held session | Gateway ledger row | `null`, shown `flat` |
| Subscription | `local-cli` (new, Neumar pattern) | The vendor's own CLI, on its own sign-in | Usage the CLI reports, written as ledger rows with `details.source = "cli_reported"` | `null`, shown `flat` |

The runtime is a field of the provider in `providers.yaml`, so it is explicit and
audited. The spike in 6.1 decides which runtimes are enabled per vendor. The
working default is `local-cli` for Claude (a vendor client is what the
subscription is licensed for) and `gateway` for ChatGPT, with `local-cli`
available for both.

### 3.2 The `local-cli` runtime

- **Detection.** Find the binary the way Neumar does (login shell, PATH, vendor
  env override, common dirs), read its version, run its auth probe. No model call.
- **Environment.** Build the environment explicitly: PATH, HOME, TMPDIR and
  proxy variables forwarded, never an empty key variable. API-key mode sets the
  vendor's documented variables. Subscription mode sets none.
- **Isolation.** Each trial gets its own scratch directory for the workspace and
  for the CLI's home or config dir. The user's real agent configs are never
  written (see decision D1). The vendor's sandbox and permission flags are set
  to the narrowest mode that lets the task run.
- **Capture.** Claude `-p --output-format stream-json` and Codex `exec --json`
  give the transcript, the model served and token usage. These become the
  trial transcript and one `calls` row per model turn.
- **Honesty checks.** A trial counts as a success only if the CLI exited
  cleanly and reported usage for the requested model. A model that differs from
  the contestant's is an error, never a fallback.
- **Limits.** Subscription limit errors map to the existing `subscription_limit`
  failure class and the account rests until its reset. Concurrency per account
  comes from `limits.concurrency`.

### 3.3 Invariants that stay

- No cross-model fallback. Hooks fail closed. The US$20 metered cap applies to
  API-priced calls, and subscription quota is reported separately.
- A secret is referenced by environment variable name in `providers.yaml`, never
  stored there. Credentials never enter a Docker trial container.
- A price is never invented. A catalog model with no verified price is refused
  for metered runs.
- Every result carries its serving label: `api`, `subscription (gateway)`,
  `subscription (local CLI)`. The report never mixes them under one label.

### 3.4 Onboarding flow (what a user does)

```sh
uv run arena providers detect                  # CLIs found, versions, sign-in state, no model call
uv run arena providers add claude              # runs `claude` login, then registers the account
uv run arena providers add codex
uv run arena providers add openai --env OPENAI_API_KEY    # API key by env var name
uv run arena providers models claude           # models the CLI reports, proposed as catalog entries
uv run arena providers test anthropic-max      # typed connection result, opt-in one-token call
uv run arena run suites/smoke -c contestants/sets/providers-compare.yaml --budget-usd 5
```

The Ops page shows the same detect and test results, the accounts and their
meters, and the live calls.

## 4. Decisions needed from the maintainer

| ID | Decision | Default in this plan |
| --- | --- | --- |
| D1 | AGENTS.md says agent config is written only inside trial containers. `local-cli` runs the vendor CLI on the host. Allow it, with a per-trial scratch home and workspace, as the one named exception? | Yes, host-run only for subscription vendors, scratch dirs deleted after the trial |
| D2 | Which runtime serves which vendor? Vendors may refuse subscription tokens used by a non-vendor client | Claude `local-cli`, ChatGPT `gateway`, both overridable. The spike confirms |
| D3 | Sign-in discovery reads existing CLI sign-ins and the macOS Keychain. On or off by default? | Off. `arena providers add` and explicit `import` only |
| D4 | First use of a subscription provider needs a recorded acknowledgement of the vendor terms | Required, stored per provider, recorded in the ledger |

## 5. Risks

- **Terms of service.** Automated evaluation on a consumer plan may breach vendor
  terms and get an account limited. Mitigations are the explicit provider
  declaration, the acknowledgement in D4, and the vendor-key preference in §5.8.
- **CLI drift.** Flags, output events and sign-in file layouts change between
  versions. Mitigations are pinned minimum versions, contract tests on recorded
  real output, and a typed `incompatible_version` result.
- **Host execution.** A host-run CLI executes model-chosen commands. Mitigations
  are D1's scratch dirs, the vendor sandbox flags, timeouts and cleanup, and a
  test that proves the user's real config dirs are untouched.
- **Weaker observation.** A `local-cli` call is reported by the CLI, not seen by
  the gateway. It is labeled `cli_reported` and a run with no reported usage fails.
- **Unverified assumptions.** The ChatGPT backend URL, the Keychain item names
  and the stream formats are not verified. Sub-issue 1 settles them before any
  code depends on them.

## 6. Delivery plan

One epic, `[Epic CP8] Provider onboarding and subscription runtime`, branch
`epic/cp8-providers`, labels `type:epic` and `cp8`. It follows
[DEV_PLAN §13.1](DEV_PLAN.md#131-issues-branches-and-worktrees): one worktree,
branch and PR per sub-issue, PRs into the epic branch, one epic PR into `main`.
Schema-owner files (SQLite migrations, the bundle and event schemas) are not
edited here. The new fields go in `details_json` and in config models. If a
sub-issue finds it needs a schema change, it asks in its PR.

### 6.1 Sub-issues

| # | Sub-issue | Owns | Blocked by |
| --- | --- | --- | --- |
| 1 | Spike: record the real CLI and sign-in contracts | `fixtures/cli-contracts/**`, `docs/subsystems/provider-runtimes.md` | none |
| 2 | Provider config: auth modes, runtime field, ChatGPT provider | `src/arena/providers/config.py`, `providers.yaml`, `docs/subsystems/providers-catalog.md` | 1 |
| 3 | Runtime detection and connection test (`providers detect`, doctor) | `src/arena/providers/runtimes/**`, `src/arena/cli_providers.py` | 1, 2 |
| 4 | Account onboarding: add, import, list, forget, terms acknowledgement | `src/arena/providers/accounts/**`, `src/arena/cli_accounts.py` | 1, 2 |
| 5 | ChatGPT/Codex subscription adapter in the gateway | `src/arena/gateway/endpoints.py`, `src/arena/providers/subscriptions/chatgpt.py` | 1, 2 |
| 6 | `local-cli` runner for Claude and Codex | `src/arena/runners/local_cli.py`, `src/arena/agents/local/**` | 1, 2, 3 |
| 7 | CLI-reported usage into the Calls ledger and events | `src/arena/obs/cli_usage.py`, `src/arena/runners/usage_capture.py` | 6 |
| 8 | `arena run` and scheduler: allow subscription contestants | `src/arena/cli_run.py`, `src/arena/runners/scheduler.py`, `src/arena/runners/metered_gateway.py` | 4, 5, 6, 7 |
| 9 | Model discovery per provider (`providers models`) | `src/arena/providers/models_probe.py` | 3 |
| 10 | Live Ops data: calls, accounts, meters, provider health | `src/arena/server/ops.py`, `web/src/views/ops/**` | 3, 4, 7 |
| 11 | Provider status and connection test in the viewer | `src/arena/server/providers_api.py`, `web/src/views/providers/**` | 3, 10 |
| 12 | Serving labels in report, Compare and Pareto | `src/arena/stats/**`, `web/src/views/compare/**` | 7, 8 |
| 13 | Mixed-provider contestant set, fake-CLI end-to-end test, docs | `contestants/sets/providers-compare.yaml`, `tests/e2e/**`, `README.md`, subsystem pages | 8, 9, 12 |
| 14 | Live check with real accounts (needs approval) | none (evidence only) | 13 |

### 6.2 Waves

1. Sub-issue 1 alone. Nothing else starts on assumptions it has not verified.
2. Sub-issue 2.
3. Sub-issues 3, 4, 5 in parallel (disjoint files).
4. Sub-issues 6 and 9, then 7.
5. Sub-issues 8, 10, 11, 12.
6. Sub-issue 13, then 14.

### 6.3 Verification

Every sub-issue runs `uv run pytest`, `uv run ruff check`, `uv run pyright`
and `uv run python scripts/check_doc_links.py`. The viewer sub-issues also run
`pnpm -C web test` and the relevant `pnpm -C web e2e` specs. CI uses **fake
`claude` and `codex` executables** that replay the recorded output from
sub-issue 1, so no test needs a sign-in, a network or spend.

Epic checkpoint:

```sh
uv run arena validate providers.yaml catalog/ suites/ contestants/
uv run arena providers detect
uv run pytest tests/e2e -k providers
pnpm -C web e2e
```

### 6.4 Real-use gate

Sub-issue 14 is the only one that calls a real model. It needs explicit
approval from the maintainer for the account, the quota use and the spend, as
#62 did. Until then the epic can finish its code and fake-CLI evidence, and
sub-issue 14 stays open. No real provider call happens before that approval.
