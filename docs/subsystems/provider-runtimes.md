# Provider runtimes: vendor CLI and sign-in contracts

This page records what the installed `claude` and `codex` CLIs do, as observed
on 2026-10-11 with Claude Code 2.1.296 and codex-cli 0.162.1. Redacted outputs
are under `fixtures/cli-contracts/`. It backs the
[CP8 plan](../PROVIDER_ONBOARDING_PLAN.md). Only status, version and model-list
commands were run. No model call was made.

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Claude status fixtures | Output of `claude auth status` signed in and signed out | [`auth-status.signed-in.json`](../../fixtures/cli-contracts/claude/auth-status.signed-in.json), [`auth-status.signed-out.json`](../../fixtures/cli-contracts/claude/auth-status.signed-out.json) |
| Codex status fixtures | Output of `codex login status`, `codex --version`, `codex debug models` | [`login-status.signed-in.txt`](../../fixtures/cli-contracts/codex/login-status.signed-in.txt), [`debug-models.empty-home.json`](../../fixtures/cli-contracts/codex/debug-models.empty-home.json) |
| Sign-in file shape | Field names and value types of `~/.codex/auth.json`, never values | [`auth-json.shape.json`](../../fixtures/cli-contracts/codex/auth-json.shape.json) |
| Claude flag semantics | What `--restricted`, `--safe-mode`, `--strict-mcp-config`, `--tools`, `--allowedTools`, `--disallowedTools` and `--permission-prompts` do, in the CLI's own words | [`help.flags.txt`](../../fixtures/cli-contracts/claude/help.flags.txt) |
| CLI specs | Per CLI: binary name, path override variable, minimum version, status commands and how their output is parsed | [`specs.py`](../../src/arena/providers/runtimes/specs.py) |
| Detection | Find the binary, read the version and sign-in state, build the child environment, check a model against the CLI's list | [`detect.py`](../../src/arena/providers/runtimes/detect.py) |
| Output | `providers detect [--json]`, the `local-cli` branch of `providers test`, the CLI lines in `doctor` | [`cli_providers.py`](../../src/arena/cli_providers.py) |
| Viewer status | Provider runtime, CLI, version and sign-in status; redacted account metadata and env variable names only | [`providers_api.py`](../../src/arena/server/providers_api.py), [`ProvidersView.tsx`](../../web/src/views/providers/ProvidersView.tsx) |
| Viewer test | Explicit one-call connection test; rate-limited per provider and recorded for local CLI | [`providers_api.py`](../../src/arena/server/providers_api.py) |
| Run integration | `arena run` preflight, routing, per-account lane and rest for `local-cli` contestants | [`local_cli_trial.py`](../../src/arena/runners/local_cli_trial.py), [`cli_run.py`](../../src/arena/cli_run.py) |
| Account identity | The frozen per-run sign-in snapshot and the private, full-length account key that lanes and rests are keyed on | [`account_context.py`](../../src/arena/runners/account_context.py) |

## Runtime path

What each CLI does when a runtime asks it for its state.

1. **Version.** `claude --version` prints `2.1.296 (Claude Code)`. `codex --version` prints `codex-cli 0.162.1`.
2. **Claude sign-in state.** `claude auth status` prints JSON with `loggedIn`, `authMethod`, `apiProvider`, `projectsDirectory` and `configDirectory`. Signed in with a subscription it reports `authMethod: "oauth_token"`. Signed out it reports `loggedIn: false` and `authMethod: "none"`. Exit code is 0 in both cases, so a runtime must read the JSON, not the exit code.
3. **Codex sign-in state.** `codex login status` prints `Logged in using ChatGPT` or `Not logged in`. The signed-out case exits non-zero, so a runtime must treat the text as the answer and the exit code as a hint.
4. **Codex sign-in file.** `~/.codex/auth.json` has `auth_mode` (`chatgpt` for a subscription), `OPENAI_API_KEY` (null for a subscription), `tokens` with `id_token`, `access_token`, `refresh_token` and `account_id`, and `last_refresh`. The existing arena parser reads `tokens.access_token`, `tokens.refresh_token` and `tokens.account_id`, and these names match.
5. **Claude sign-in storage.** On macOS the subscription sign-in is a generic-password Keychain item with service `Claude Code-credentials` in the login keychain. There is no `~/.claude/.credentials.json` on this machine. The Linux file layout was not checked, because no Linux machine was available.
6. **Model lists.** `codex debug models` prints JSON `{"models": [...]}`. Each entry has `slug`, `display_name`, `visibility` (`list` or `hide`), `supported_in_api`, `priority` and more. Under an empty `CODEX_HOME` it lists 11 GPT-family slugs. Claude Code has no equivalent command that was checked.

### Detection

`arena providers detect` with no URL runs `detect_runtime` for `claude` and `codex`. A URL keeps its old meaning: probe an API base URL.

1. Find the binary: the override variable (`CLAUDE_CODE_PATH`, `CODEX_CLI_PATH`), then `PATH`, then common install directories, then a login shell. An override that names a missing file is `not_installed`, not a fall-through.
2. Run `--version` and compare all three numbers with the recorded floor (Claude 2.1.248, Codex 0.162.0). An older CLI is `incompatible_version` and the message names the recorded version. The patch number is part of the floor for Claude, because `--restricted` is documented only from 2.1.248 and a two-part check would pass 2.1.100.
3. Run the sign-in command and parse it as recorded: Claude JSON `loggedIn`, Codex text.
4. Every command runs with an environment built from an allowlist (`PATH`, `HOME`, `TMPDIR`, locale, proxies, certificate paths, `CODEX_HOME`, `CLAUDE_CONFIG_DIR`). `ANTHROPIC_*`, `OPENAI_*` and `CODEX_API_KEY` are never forwarded, and no variable is set to an empty value.
5. `check_model` lists Codex models under an empty `CODEX_HOME` and offers only entries with `visibility: list`. The Claude CLI has no list command that was checked, so it passes.

Each result has one status: `ok`, `not_installed`, `auth_required`, `incompatible_version`, `unsupported_model`, `protocol_failure` (the CLI answered in an unrecorded shape or timed out) or `permission_failure` (the file is not executable). `auth_required` and `not_installed` carry the command to run as text. The sign-in state is `authenticated`, `unauthenticated` or `unknown`, and `unknown` appears only with a failure status.

`detect` always exits 0, and `doctor` prints the CLI lines without failing, because a missing CLI is a choice. `providers test` fails for a `local-cli` provider whose status is not `ok`. It sends no model request. The capped call that proves a model answers needs the local-cli runner and the maintainer's approval to spend quota, so it belongs to the runner and the live check ([#186](https://github.com/bravew/ai-arena/issues/186), [#194](https://github.com/bravew/ai-arena/issues/194)).

### Local CLI runner

`run_local_cli` ([`local_cli.py`](../../src/arena/runners/local_cli.py)) runs one trial through the vendor's own CLI on the host. It is the named exception to "agent config is written only inside trial containers" (decision D1), and it confines the CLI as follows.

1. **Scratch directory.** `mkdtemp` makes a 0700 directory holding `home/` and `workspace/`. It is deleted in a `finally`, so success, error, timeout and cancel all remove it. A test compares a hash of a fake real HOME before and after each outcome.
2. **Sign-in.** The caller passes a `SignIn` (relative path to bytes) from [`signin.py`](../../src/arena/agents/local/signin.py). The runner writes each file 0600 under the scratch home and rejects a path that leaves it. `SignIn` and the result never put the bytes in a `repr`. Reading `~/.codex/auth.json` is read-only and requires `auth_mode: chatgpt`. Claude on macOS keeps its sign-in in the Keychain, so the file import fails with a pointer to the explicit Keychain import, which this runner never calls.
3. **Environment.** The child gets the detection allowlist plus `HOME`, and `CLAUDE_CONFIG_DIR` or `CODEX_HOME` set to the scratch copy. `ANTHROPIC_*`, `OPENAI_*` and `CODEX_API_KEY` are never forwarded.
4. **Flags.** Claude runs `--print --output-format stream-json --verbose` with `acceptEdits`, `--restricted`, `--safe-mode`, `--strict-mcp-config`, `--permission-prompts none`, `--tools Read,Write,Edit,Glob,Grep`, `--allowedTools Read Write Edit Glob Grep` and `--disallowedTools mcp__*`. `--tools` decides which built-in tools exist and `--allowedTools` only pre-approves them, so both are set; MCP tools are denied outright and no extra working directory is added. Codex runs `exec --json --sandbox workspace-write --skip-git-repo-check --ephemeral`. The runner sets these, not the task.
5. **Compatibility before the sign-in.** For Claude the runner checks `--version` against the spec floor (2.1.248) and fails with `protocol` before any sign-in file is written and before any process is spawned. A trial whose CLI cannot run in restricted mode never stages a credential.
6. **What the flags do, from the CLI's own help.** The entries are recorded in [`help.flags.txt`](../../fixtures/cli-contracts/claude/help.flags.txt). `--restricted` removes the built-in tools that run commands or code (Bash, PowerShell, REPL) and WebFetch unless `--tools` names them, ignores user, project and local settings files, confines the file tools to the working directories, and refuses `bypassPermissions`. It adds that `--strict-mcp-config` is what skips MCP servers, which the runner sets. `--safe-mode` starts with `CLAUDE.md`, skills, plugins, hooks, MCP servers, custom commands and agents disabled, so a task cannot be answered by the host's customizations, and `--permission-prompts none` denies anything that would otherwise prompt.
7. **What the boundary is not.** These flags bound the CLI's own built-in tools and the customizations it loads. They are not OS isolation: a subprocess the CLI starts, a hook or MCP server that is still configured, and a compromised CLI binary are outside them. A task that needs that boundary needs a container, which is not this runner.
8. **Process control.** The child starts in its own session. On timeout or cancel the runner signals the whole process group, waits a short grace period, then kills it.
9. **Result.** Success needs a zero exit, at least one parsed event, reported usage and a served model that matches the request. A dated alias (`name-20250929`) matches. Any other model is `model_mismatch`, never a fallback. Failures are typed: `auth_required`, `limit_reached`, `model_mismatch`, `timeout`, `exit_code`, `protocol`, `no_usage`, `spawn`. A limit or auth signal in the stream wins over a zero exit code.
10. **Outputs.** Regular files under `workspace/` come back as bytes. Symlinks, the `task-input/` copy of the task files and files over 10 MiB are skipped.

Claude names the served model in its `init` and `result` events. Codex `exec --json` does not name it, so the runner reads `turn_context.payload.model` from the session record under the scratch `CODEX_HOME`. If that record is missing, the trial can still succeed, but `model_served_verified` is false and a later caller must not report it as verified.

The stream fixtures [`stream.synthetic.jsonl`](../../fixtures/cli-contracts/claude/stream.synthetic.jsonl) and [`exec.synthetic.jsonl`](../../fixtures/cli-contracts/codex/exec.synthetic.jsonl) are synthetic. They follow Neumar's handling of the two CLIs and were not captured from a real run. The live check ([#194](https://github.com/bravew/ai-arena/issues/194)) replaces them.

### Accounts

`arena providers add | list | forget` and `import --keychain` ([`cli_accounts.py`](../../src/arena/cli_accounts.py), [`accounts/store.py`](../../src/arena/providers/accounts/store.py)) manage the sign-in a `local-cli` trial copies into its scratch home. They follow decisions D3 and D4.

1. **Stored copy.** `~/.arena/accounts/<vendor>/files/...` holds the sign-in bytes and `meta.json` holds a redacted id, the source and a time. Directories are 0700, files 0600. `forget` deletes the stored copy and never touches the CLI's own files.
2. **Discovery (D3).** On by default and read-only. It reads `$CODEX_HOME/auth.json` (only if `auth_mode` is `chatgpt`) and the Claude credentials file, and `list` shows each by redacted id and location. It copies nothing. `ARENA_DISCOVER_SIGNINS=0` turns it off. It never reads the macOS Keychain.
3. **Keychain.** `arena providers import claude --keychain` is the only reader. It runs `security find-generic-password -s "Claude Code-credentials" -w` and stores the result. Whether the CLI accepts that JSON as `.credentials.json` in a scratch home is unverified until the live check ([#194](https://github.com/bravew/ai-arena/issues/194)).
4. **Add.** `add claude|codex` prints a terms and usage warning (D4, nothing is stored or required), runs the vendor's own login (`claude auth login`, `codex login`) with the allowlisted environment, then stores the sign-in it finds. Claude on macOS has no file, so it points at the Keychain import. `--no-login` registers an existing sign-in.
5. **API providers.** `add <provider> --env NAME` checks that the variable is set and prints the `keys:` line for `providers.yaml`. It never reads the value and does not edit `providers.yaml`.
6. **Which sign-in a trial uses.** `resolve_sign_in`: the stored copy first, then the CLI's own file if discovery is on, else an error naming the fix.
7. **Redacted id.** The first 8 hex characters of a SHA-256. For Codex it hashes `tokens.account_id`, so it survives token refresh. For Claude it hashes the sign-in bytes, so it changes when the token does.

### Usage from a local CLI

A `local-cli` trial has no gateway in its path, so the arena records only what the CLI reported. [`UsageCapture`](../../src/arena/runners/usage_capture.py) turns a `LocalCliResult` into records, and [`cli_usage.py`](../../src/arena/obs/cli_usage.py) builds them.

1. **One call per CLI run.** The CLIs report usage for the whole run, not per model turn, so a run is one `calls` row. `cost_usd` is null and the model must be a catalog subscription model, or capture raises.
2. **Labels without a schema change.** `upstream` is `cli_reported`, `protocol_in` is `local-cli`, `protocol_out` is `cli:claude` or `cli:codex`. The `call_finished` event data adds `source: cli_reported` and `runtime`. The bundle and event schemas are unchanged, and a test validates a row and an event against them.
3. **Only `call_finished`.** The CLI's usage is known after it exits, so there is no earlier `call_queued` or `call_started` event. The gateway does not emit `call_queued` either.
4. **Reached.** A trial counts as reached only when the CLI exited cleanly, reported usage, and the served model was verified. A Codex run whose session record was missing is recorded with `model_served` null and is not reached. Every other outcome is recorded as a failed call (429 `subscription_limit`, 401 `auth`, `timeout`, `model_refused` for a model mismatch, `upstream`, `bad_reply`). A spawn failure made no model request and records nothing.
5. **Budget.** The call is reserved at zero and settled as a subscription call, so its tokens add to `subscription_tokens` and never to the metered spend or the US$20 cap.

### Running subscription contestants

`arena run` sends a contestant to the vendor CLI when its model's provider has `subscription.runtime: local-cli`. Mock, API-agent and CLI contestants can share one run. In the checked-in configuration, `anthropic-max/claude-opus-5-5-sub` is the Claude subscription model, while the priced Claude API models use the separate `anthropic` provider ID.

1. **Preflight.** [`preflight_local_cli`](../../src/arena/runners/local_cli_trial.py) runs before any trial, also under `--dry-run`, and makes no model request. Each unmet condition is its own line with a code: `provider_disabled`, `scaffold`, `catalog`, `not_installed`, `auth_required`, `incompatible_version`, `unsupported_model` and `sign_in`. Any issue exits with code 2 and nothing is written.
2. **No scaffold.** The vendor CLI is the agent, so a CLI contestant takes no scaffold, and the model must be a `pricing: subscription` catalog entry.
3. **Terms.** Per D4 no acknowledgement is stored. The run prints one note that subscription trials use the plan's quota under the vendor's terms and that the arena charges no price for them.
4. **Execution.** The trial runs the CLI in a scratch home and workspace, keeps its outputs as the trial's artifacts (`answer.md` falls back to the final message), then records usage as in the section above. A failure raises, the trial is `errored`, and `--resume` runs it again. A trial never switches model or runtime.
5. **Lane and rest.** `provider.limits.concurrency` (default 1) caps the trials in flight on one account, through the scheduler's `lane_for`. The sign-in for each vendor is resolved once, before scheduling, into a frozen snapshot ([`account_context.py`](../../src/arena/runners/account_context.py)), and every trial on that vendor copies the same bytes, so a token refresh mid-run cannot split the run across two accounts. The lane and rest identity is a private, full-length, vendor-scoped SHA-256 of the sign-in (vendor, then each relative path and its bytes, each length-framed), not the 8-character redacted display label and not the provider id; it never leaves the process. Providers that resolve to the same account (aliases) therefore share one lane at the lowest configured `limits.concurrency`, so the effective cap does not depend on which alias the scheduler reaches first. A `subscription_limit` result rests that account in a run-scoped `RestBook` for the rest of the run, and later trials on any alias fail at once instead of spending quota. The gateway keeps its own `RestBook` and its timed default; only the local-cli book is run-scoped. Resuming starts with an empty book.
6. **Events and sequence numbers.** The gateway container owns `events.jsonl` while it runs, so CLI trials write `call_finished` events to a private root and the recorder merges them when the run ends. `calls` is unique on `(run_id, seq)`, so CLI calls are numbered from 1,000,000 up, after the highest existing number on a resume.

## Constraints and failure behavior

The API connection test and model discovery send configured API keys only to an absolute HTTPS endpoint with a valid host and port. URL userinfo is rejected before a key is used, and a loopback host is the one place plain HTTP is allowed, because the gateway is designed to sit on loopback and the Docker bridge. These checks disable redirects so credentials cannot follow a 3xx response to another host. A 2xx response is required for a successful connection test.


- **Confirmed: environment overrides the stored sign-in.** With `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` set, `claude auth status` reports `loggedIn: true, authMethod: "oauth_token"` even under an empty HOME or `CLAUDE_CONFIG_DIR`. With `env -i` and an empty HOME it reports `loggedIn: false`. A `local-cli` runner must build the child environment explicitly and must not inherit these variables, or the probe and the trial will not use the subscription.
- **Confirmed: the Claude sign-in follows HOME.** With a scrubbed environment, the real HOME reports `loggedIn: true` and an empty HOME reports `loggedIn: false`. A scratch HOME therefore does not see the Keychain item. A trial in a scratch HOME needs the sign-in supplied to it, which is the credential copy that decision D1 covers. This was not tested, because it needs D1.
- **Confirmed: Codex sign-in follows `CODEX_HOME`.** An empty `CODEX_HOME` reports `Not logged in`. Whether a copied `auth.json` is enough was not tested, for the same reason.
- **Confirmed: a Codex install can route through another gateway.** On the machine used, `~/.codex/config.toml` sets `model = "group/coder"` and defines providers whose `base_url` is a local gateway. `codex debug models` then lists that gateway's catalog (`group/*`, `claude/*`, `openrouter/*`), not ChatGPT's. Model discovery and detection must read the list with an isolated `CODEX_HOME` and say which config they used.
- **Open: stream formats.** The events of `claude -p --output-format stream-json` and `codex exec --json` (model served, token usage, limit and auth failures) need a model call. The runner parses synthetic fixtures built from Neumar's handling. Capture the real streams in the live check, [#194](https://github.com/bravew/ai-arena/issues/194).
- **Open: ChatGPT backend URL and headers** for the `gateway` runtime. Nothing here confirms them, and nothing was read from the network.
- **Open: whether each vendor allows a non-vendor client to use its subscription token.** This is a terms question that no local command answers. Decision D2 stays with the maintainer, and the safe default is to prefer the vendor's own CLI.
- **Recommendation for D2.** Use the vendor's own CLI for both vendors (`local-cli`), and treat the `gateway` runtime for ChatGPT as unproven until its URL, headers and terms are confirmed.

Fixtures hold no token, email or home path. Paths are replaced with `<HOME>` and
`<SCRATCH_HOME>`, and the sign-in file is recorded as a shape only.

## Verification

```sh
uv run pytest tests/providers/test_runtimes.py tests/runners/test_local_cli.py tests/providers/test_accounts.py tests/runners/test_usage_capture.py tests/runners/test_account_context.py tests/runners/test_subscription_lanes.py
uv run arena providers detect --json
uv run python scripts/check_doc_links.py
grep -rE "eyJ|@" fixtures/cli-contracts || echo "no secrets or identities"
```

To refresh a fixture, run the command named in its file name on a machine with
the pinned CLI version, then redact it the same way.
