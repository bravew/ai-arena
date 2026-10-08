# Runners and sandbox

This reference follows the subsystem design in the development plan. Implementation modules and tests are planned but are not present in the sparse CP7 launch tree; source links point to existing plan headings until implementation lands.

## Responsibilities and sources of truth

| Part | Responsibility | Source of truth |
|---|---|---|
| Trial orchestration and sandboxing | Schedule suite × contestant × repeat trials, manage budget and resume behavior, and isolate agent CLIs behind a gateway-only container network. | [Development plan](../DEV_PLAN.md#7-execution-layer) |
| Delivery | Defines implementation stages and acceptance behavior. | [Checkpoint plan](../DEV_PLAN.md#13-delivery-plan-checkpoints) |
| Engineering rules | Defines reference-page structure and link conventions. | [Engineering conventions](../DEV_PLAN.md#12-engineering-conventions) |

## Runtime path

1. The runner expands requested suites and contestants into trials.
2. The scheduler applies concurrency, budget, dry-run, and resume controls.
3. Each trial runs inside the sandbox with only gateway egress.
4. Trial outcomes and child execution spans are recorded.

## Constraints and failure behavior

Internet access is blocked from the sandbox while gateway access remains available. Time limits are enforced. Dry-run writes nothing; resume fills only missing trials; an unreachable provider or harness wiring problem is reported explicitly.

- This branch does not yet contain the subsystem implementation or its tests. Future source paths are described in the plan and are not linked as existing files.
- Keep source links on stable headings or symbols; do not use line-number links.

## Verification

Run the planned subsystem check from the repository root:

```sh
uv run pytest tests/runners tests/sandbox
```

Check all subsystem reference links with:

```sh
uv run python scripts/check_doc_links.py
```
