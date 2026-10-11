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
| Provider runtimes (later sub-issues) | Detect the CLI, probe sign-in, run a trial | built on these fixtures |

## Runtime path

What each CLI does when a runtime asks it for its state.

1. **Version.** `claude --version` prints `2.1.296 (Claude Code)`. `codex --version` prints `codex-cli 0.162.1`.
2. **Claude sign-in state.** `claude auth status` prints JSON with `loggedIn`, `authMethod`, `apiProvider`, `projectsDirectory` and `configDirectory`. Signed in with a subscription it reports `authMethod: "oauth_token"`. Signed out it reports `loggedIn: false` and `authMethod: "none"`. Exit code is 0 in both cases, so a runtime must read the JSON, not the exit code.
3. **Codex sign-in state.** `codex login status` prints `Logged in using ChatGPT` or `Not logged in`. The signed-out case exits non-zero, so a runtime must treat the text as the answer and the exit code as a hint.
4. **Codex sign-in file.** `~/.codex/auth.json` has `auth_mode` (`chatgpt` for a subscription), `OPENAI_API_KEY` (null for a subscription), `tokens` with `id_token`, `access_token`, `refresh_token` and `account_id`, and `last_refresh`. The existing arena parser reads `tokens.access_token`, `tokens.refresh_token` and `tokens.account_id`, and these names match.
5. **Claude sign-in storage.** On macOS the subscription sign-in is a generic-password Keychain item with service `Claude Code-credentials` in the login keychain. There is no `~/.claude/.credentials.json` on this machine. The Linux file layout was not checked, because no Linux machine was available.
6. **Model lists.** `codex debug models` prints JSON `{"models": [...]}`. Each entry has `slug`, `display_name`, `visibility` (`list` or `hide`), `supported_in_api`, `priority` and more. Under an empty `CODEX_HOME` it lists 11 GPT-family slugs. Claude Code has no equivalent command that was checked.

## Constraints and failure behavior

Findings that change the design, each marked as confirmed or still open. These
resolve the unverified assumptions in plan §5.

- **Confirmed: environment overrides the stored sign-in.** With `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` set, `claude auth status` reports `loggedIn: true, authMethod: "oauth_token"` even under an empty HOME or `CLAUDE_CONFIG_DIR`. With `env -i` and an empty HOME it reports `loggedIn: false`. A `local-cli` runner must build the child environment explicitly and must not inherit these variables, or the probe and the trial will not use the subscription.
- **Confirmed: the Claude sign-in follows HOME.** With a scrubbed environment, the real HOME reports `loggedIn: true` and an empty HOME reports `loggedIn: false`. A scratch HOME therefore does not see the Keychain item. A trial in a scratch HOME needs the sign-in supplied to it, which is the credential copy that decision D1 covers. This was not tested, because it needs D1.
- **Confirmed: Codex sign-in follows `CODEX_HOME`.** An empty `CODEX_HOME` reports `Not logged in`. Whether a copied `auth.json` is enough was not tested, for the same reason.
- **Confirmed: a Codex install can route through another gateway.** On the machine used, `~/.codex/config.toml` sets `model = "group/coder"` and defines providers whose `base_url` is a local gateway. `codex debug models` then lists that gateway's catalog (`group/*`, `claude/*`, `openrouter/*`), not ChatGPT's. Model discovery and detection must read the list with an isolated `CODEX_HOME` and say which config they used.
- **Open: stream formats.** The events of `claude -p --output-format stream-json` and `codex exec --json` (model served, token usage, limit and auth failures) need a model call. Both flags exist in the installed help. Capture them in the live check, [#194](https://github.com/bravew/ai-arena/issues/194).
- **Open: ChatGPT backend URL and headers** for the `gateway` runtime. Nothing here confirms them, and nothing was read from the network.
- **Open: whether each vendor allows a non-vendor client to use its subscription token.** This is a terms question that no local command answers. Decision D2 stays with the maintainer, and the safe default is to prefer the vendor's own CLI.
- **Recommendation for D2.** Use the vendor's own CLI for both vendors (`local-cli`), and treat the `gateway` runtime for ChatGPT as unproven until its URL, headers and terms are confirmed.

Fixtures hold no token, email or home path. Paths are replaced with `<HOME>` and
`<SCRATCH_HOME>`, and the sign-in file is recorded as a shape only.

## Verification

```sh
uv run python scripts/check_doc_links.py
grep -rE "eyJ|@" fixtures/cli-contracts || echo "no secrets or identities"
```

To refresh a fixture, run the command named in its file name on a machine with
the pinned CLI version, then redact it the same way.
