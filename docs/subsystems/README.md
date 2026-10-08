# Subsystem references

This page describes the subsystem references subsystem. Its implementation modules and tests are planned in the sparse CP7 launch tree. Until those sources land, links below point to current plan sections that define its scope and behavior.

## Responsibilities and sources of truth

| Part | Responsibility | Source of truth |
|---|---|---|
| Scope | Defines what belongs to subsystem references. | [Development plan](../DEV_PLAN.md#12-engineering-conventions) |
| Delivery | Records planned implementation and acceptance behavior. | [Checkpoint plan](../DEV_PLAN.md#13-delivery-plan-checkpoints) |
| Engineering rules | Sets documentation and verification conventions. | [Engineering conventions](../DEV_PLAN.md#12-engineering-conventions) |

## Runtime path

1. The planned implementation follows the lifecycle described in the [checkpoint plan](../DEV_PLAN.md#13-delivery-plan-checkpoints).
2. Inputs and state pass through the components identified there; implementation symbols will be linked when those source files exist on this branch.
3. Outputs are verified using the command below and subsystem acceptance checks in the plan.

## Constraints and failure behavior

- This sparse branch does not yet contain the subsystem implementation. Planned source paths are described as future and are not linked as existing files.
- Preserve the constraints and failure behavior in the [checkpoint plan](../DEV_PLAN.md#12-engineering-conventions); failures must remain explicit and must not be silently converted into success or empty results.
- References use stable headings or symbols, never line numbers, as required by the [documentation convention](../DEV_PLAN.md#12-engineering-conventions).

## Verification

Run the planned subsystem check from the repository root:

```sh
uv run python scripts/check_doc_links.py
```

Check all subsystem documentation links with:

```sh
uv run python scripts/check_doc_links.py
```
