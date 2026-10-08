# Gateway

This reference follows the subsystem design in the development plan. Implementation modules and tests are planned but are not present in the sparse CP7 launch tree; source links point to existing plan headings until implementation lands.

## Responsibilities and sources of truth

| Part | Responsibility | Source of truth |
|---|---|---|
| Gateway request handling | Accept the planned Chat, Responses, Anthropic Messages, and Gemini protocols; attribute calls to trials; relay passthrough-first; enforce model, budget, and failure policies. | [Development plan](../DEV_PLAN.md#5-providers-models-and-the-gateway) |
| Delivery | Defines implementation stages and acceptance behavior. | [Checkpoint plan](../DEV_PLAN.md#13-delivery-plan-checkpoints) |
| Engineering rules | Defines reference-page structure and link conventions. | [Engineering conventions](../DEV_PLAN.md#12-engineering-conventions) |

## Runtime path

1. An agent or client sends a protocol request with its trial token.
2. The gateway resolves a provider key lane, records the request decision, and relays or translates the request.
3. It records the served model, usage, and call result, then returns the upstream response.

## Constraints and failure behavior

A quota error, rate limit, malformed reply, or stream failure has a distinct failure class and rest policy. A stream that fails after its first byte is not retried; a required served-model mismatch fails the call.

- This branch does not yet contain the subsystem implementation or its tests. Future source paths are described in the plan and are not linked as existing files.
- Keep source links on stable headings or symbols; do not use line-number links.

## Verification

Run the planned subsystem check from the repository root:

```sh
uv run pytest tests/gateway
```

Check all subsystem reference links with:

```sh
uv run python scripts/check_doc_links.py
```
