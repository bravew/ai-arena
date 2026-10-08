# Providers and catalog

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Provider configuration | Load provider endpoints, environment key references, subscription declarations, and enabled state; reject literal credential values | [`arena.providers.config`](../../src/arena/providers/config.py) |
| Model catalog | Validate model refs and require explicit token pricing or subscription pricing; subscription price remains unknown; distinguish disabled providers during model resolution | [`arena.catalog.config`](../../src/arena/catalog/config.py) |
| Config validation CLI | Walk supplied files and directories, report all source paths, and exit non-zero for invalid configuration | [`validate`](../../src/arena/cli.py) |

## Runtime path

1. `arena validate` expands directory arguments to YAML files.
2. `resolve_model` parses the `ModelRef`, checks that its provider exists and is enabled, and only then looks up the model in the catalog.
2. `parse_yaml` loads each document using PyYAML's safe loader and rejects empty files.
3. Provider files are validated as `ProviderConfig`; model catalog files are validated as `ModelCatalog`.
4. Validation issues include the filename and model path and are printed together before the command exits non-zero.

## Constraints and failure behavior

- Provider API credentials use an environment variable name or `${ENV}` reference. Secret-named fields such as `Authorization` also accept only those references.
- A provider must define keys, a subscription, or a built-in `mock`/`cassette` kind. Key and subscription authentication cannot be mixed.
- A disabled provider remains in the configuration. `resolve_model` checks its enabled state before catalog membership, so a requested model reports `provider off` before `unknown model`.
- A catalog model declares either `price_per_mtok` or `pricing: subscription`. Subscription pricing has `price_per_mtok = None`, never zero.
- Missing, unreadable, malformed, empty, and invalid files are errors. Validation continues to report errors from other supplied files.

## Verification

```sh
uv run pytest tests/core/test_config.py
uv run arena validate providers.yaml catalog/
```
