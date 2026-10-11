# Providers and catalog

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Provider configuration | Load provider endpoints, environment key references, subscription declarations, and enabled state; reject literal credential values | [`arena.providers.config`](../../src/arena/providers/config.py) |
| Model catalog | Validate model refs and require explicit token pricing or subscription pricing; subscription price remains unknown; distinguish disabled providers during model resolution | [`arena.catalog.config`](../../src/arena/catalog/config.py) |
| Model discovery | List a provider's models (the Codex CLI list for a `local-cli` subscription, the provider's model-list endpoint for an API provider) and propose catalog entries | [`arena.providers.models_probe`](../../src/arena/providers/models_probe.py) |
| Config validation CLI | Walk supplied files and directories, report all source paths, and exit non-zero for invalid configuration | [`validate`](../../src/arena/cli.py) |

## Runtime path

1. `arena validate` expands directory arguments to YAML files.
2. `resolve_model` parses the `ModelRef`, checks that its provider exists and is enabled, and only then looks up the model in the catalog.
2. `parse_yaml` loads each document using PyYAML's safe loader and rejects empty files.
3. Provider files are validated as `ProviderConfig`; model catalog files are validated as `ModelCatalog`.
4. Validation issues include the filename and model path and are printed together before the command exits non-zero.

### Model discovery

`arena providers models <provider> [--catalog FILE] [--write]` lists the provider's models, marks each `new` or `in catalog`, and prints proposed entries. It sends no model request.

- A `local-cli` ChatGPT subscription asks `codex debug models` under an empty `CODEX_HOME` and keeps only `visibility: list` entries. The Claude CLI has no list command that was checked, and a `gateway` subscription has none, so both report that models must be named in the catalog by hand.
- An API provider calls its model-list route with the provider's first key from the environment: `{base}/models` with a bearer token for `responses` and `chat`, `{base}/v1/models` with `x-api-key` for `anthropic`. An unset variable, an HTTP error or an unexpected body is an error that never includes the key.
- A proposal never has a price. The catalog rejects a model with neither `price_per_mtok` nor `pricing: subscription`, so `--write` appends only subscription entries (and restores the file if it no longer validates). A paid model is printed with its price and source as comments to fill in from the vendor's price page.

## Constraints and failure behavior

- Provider API credentials use an environment variable name or `${ENV}` reference. Secret-named fields such as `Authorization` also accept only those references.
- A provider must define keys, a subscription, or a built-in `mock`/`cassette` kind. Key and subscription authentication cannot be mixed.
- A subscription declares `runtime`: `gateway` (default; the daemon holds the imported session) or `local-cli` (the vendor's own CLI runs on its own sign-in). `local-cli` is accepted only for vendors with a recorded CLI contract (`claude`, `chatgpt`; see [provider runtimes](provider-runtimes.md)). `runtime` on a provider outside `subscription`, or any other value, is an error.
- `anthropic-max` is the Claude subscription provider and `openai-plus` is the ChatGPT subscription provider; both use `local-cli`. The catalog uses `anthropic-max/claude-opus-5-5-sub` for Claude subscription access and `anthropic/claude-opus-5-5` or `anthropic/claude-sonnet-4-5` for priced API access. ChatGPT catalog entries are limited to slugs the Codex CLI lists (`fixtures/cli-contracts/codex/debug-models.empty-home.json`) and have `pricing: subscription` with no price. A `local-cli` provider's models are served by its vendor CLI rather than the gateway.
- A disabled provider remains in the configuration. `resolve_model` checks its enabled state before catalog membership, so a requested model reports `provider off` before `unknown model`.
- A catalog model declares either `price_per_mtok` or `pricing: subscription`. Subscription pricing has `price_per_mtok = None`, never zero.
- Missing, unreadable, malformed, empty, and invalid files are errors. Validation continues to report errors from other supplied files.

## Verification

```sh
uv run pytest tests/core/test_config.py tests/providers/test_models_probe.py
uv run arena validate providers.yaml catalog/
```
