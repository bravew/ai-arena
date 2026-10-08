# Providers and model catalog

This reference follows the subsystem design in the development plan. Implementation modules and tests are planned but are not present in the sparse CP7 launch tree; source links point to existing plan headings until implementation lands.

## Responsibilities and sources of truth

| Part | Responsibility | Source of truth |
|---|---|---|
| Provider configuration and model catalog | Load provider endpoints and environment-variable references without storing secrets; resolve provider keys and model metadata, effort levels, and prices. | [Development plan](../DEV_PLAN.md#5-providers-models-and-the-gateway) |
| Delivery | Defines implementation stages and acceptance behavior. | [Checkpoint plan](../DEV_PLAN.md#13-delivery-plan-checkpoints) |
| Engineering rules | Defines reference-page structure and link conventions. | [Engineering conventions](../DEV_PLAN.md#12-engineering-conventions) |

## Runtime path

1. Startup loads provider configuration and model catalog data.
2. Provider selection resolves a configured endpoint and key reference.
3. Calls use the catalog’s model metadata and price version for validation and accounting.

## Constraints and failure behavior

Secrets stay in environment references, never committed configuration. Unknown models, unresolved keys, or invalid configuration are explicit errors. Pricing is versioned so recorded cost remains reproducible.

- This branch does not yet contain the subsystem implementation or its tests. Future source paths are described in the plan and are not linked as existing files.
- Keep source links on stable headings or symbols; do not use line-number links.

## Verification

Run the planned subsystem check from the repository root:

```sh
uv run pytest tests/providers tests/catalog
```

Check all subsystem reference links with:

```sh
uv run python scripts/check_doc_links.py
```
