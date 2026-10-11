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
| CLI specs | Per CLI: binary name, path override variable, minimum version, status commands and how their output is parsed | [`specs.py`](../../src/arena/providers/runtimes/specs.py) |
| Detection | Find the binary, read the version and sign-in state, build the child environment, check a model against the CLI's list | [`detect.py`](../../src/arena/providers/runtimes/detect.py) |
| Output | `providers detect [--json]`, the `local-cli` branch of `providers test`, the CLI lines in `doctor` | [`cli_providers.py`](../../src/arena/cli_providers.py) |
| Provider runner (later sub-issues) | Run a trial through the CLI | built on these fixtures |

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
2. Run `--version` and compare the major and minor number with the recorded floor (Claude 2.1, Codex 0.162). An older CLI is `incompatible_version` and the message names the recorded version.
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
4. **Flags.** Claude runs `--print --output-format stream-json --verbose` with `acceptEdits` and the tools `Read Write Edit Glob Grep` only. Codex runs `exec --json --sandbox workspace-write --skip-git-repo-check --ephemeral`. The runner sets these, not the task.
5. **Process control.** The child starts in its own session. On timeout or cancel the runner signals the whole process group, waits a short grace period, then kills it.
6. **Result.** Success needs a zero exit, at least one parsed event, reported usage and a served model that matches the request. A dated alias (`name-20250929`) matches. Any other model is `model_mismatch`, never a fallback. Failures are typed: `auth_required`, `limit_reached`, `model_mismatch`, `timeout`, `exit_code`, `protocol`, `no_usage`, `spawn`. A limit or auth signal in the stream wins over a zero exit code.
7. **Outputs.** Regular files under `workspace/` come back as bytes. Symlinks, the `task-input/` copy of the task files and files over 10 MiB are skipped.

Claude names the served model in its `init` and `result` events. Codex `exec --json` does not name it, so the runner reads `turn_context.payload.model` from the session record under the scratch `CODEX_HOME`. If that record is missing, the trial can still succeed, but `model_served_verified` is false and a later caller must not report it as verified.

The stream fixtures [`stream.synthetic.jsonl`](../../fixtures/cli-contracts/claude/stream.synthetic.jsonl) and [`exec.synthetic.jsonl`](../../fixtures/cli-contracts/codex/exec.synthetic.jsonl) are synthetic. They follow Neumar's handling of the two CLIs and were not captured from a real run. The live check ([#194](https://github.com/bravew/ai-arena/issues/194)) replaces them.

## Constraints and failure behavior

Findings that change the design, each marked as confirmed or still open. These
resolve the unverified assumptions in plan §5.

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
uv run pytest tests/providers/test_runtimes.py tests/runners/test_local_cli.py
uv run arena providers detect --json
uv run python scripts/check_doc_links.py
grep -rE "eyJ|@" fixtures/cli-contracts || echo "no secrets or identities"
```

To refresh a fixture, run the command named in its file name on a machine with
the pinned CLI version, then redact it the same way.
