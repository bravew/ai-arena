# Subsystem design references

Each page describes one subsystem as it is now: its responsibilities, runtime
path, constraints and verification. Start with the page for the subsystem you
change, then follow its source links to check the code.

## Pages

None of these exist yet. They are planned in
[DEV_PLAN §11](../DEV_PLAN.md#repository-layout) and are added with the code
they describe.

| Page | Subsystem |
| --- | --- |
| `gateway.md` (planned) | The gateway: protocols, plan, lanes, rests, cache, cassettes, hooks, redaction |
| [`providers-catalog.md`](providers-catalog.md) | Provider config, key resolution, and the model catalog |
| `agent-adapters.md` (planned) | One adapter per agent CLI: wiring, kit install, sessions |
| `runners-sandbox.md` (planned) | Runners, the scheduler, and the Docker sandbox |
| [`scoring.md`](scoring.md) | The scorer framework; deterministic scorers and LLM judges plug into it |
| `stats.md` (planned) | Aggregation, bootstrap, ratings, Pareto, run-diff |
| `observability.md` (planned) | Calls ledger, event stream, spans, metrics |
| [`ops-view.md`](ops-view.md) | The Ops view: calls ledger, usage, lanes, meters and hook stats |
| `viewer-live.md` (planned) | The viewer and the Live stage |

## Page template

Every page has these four sections, in this order.

1. **Responsibilities and sources of truth.** A table with one row per part:

   | Part | Responsibility | Source |
   | --- | --- | --- |
   | what it is | what it must do, and what it must not | file and symbol |

2. **Runtime path.** Numbered steps from the input to the result, naming the
   code that runs at each step.
3. **Constraints and failure behavior.** The rules that must hold, and what
   happens when they don't: which error, which state, what the user sees.
4. **Verification.** The exact test commands that exercise this subsystem.

Document current behavior only. A proposal belongs in its issue or PR.

## Keep a page current

A PR that changes a documented responsibility, state transition or failure
behavior updates the page in the same PR. A refactor that keeps these the same
needs no prose change, and its PR says so.

Link to files and named symbols, not line numbers. Line numbers go stale at the
next edit. Keep each fact on one page and link to it from other pages and from
[AGENTS.md](../../AGENTS.md).

## Describe a PR's behavior change

For each affected subsystem, the PR says what changes, what triggers it, and the
behavior before and after. It links the page, the code and the test that shows
it. This is the author's account, and the reviewer checks it against the diff.

## Review a change

1. Find the affected subsystem and the path the code actually takes.
2. Compare the claimed before and after with the base and head code. Follow
   callers and state transitions beyond the changed lines.
3. Check that changed contracts appear in the page and that its source links
   still resolve.
4. Check that the cited tests exercise the claimed behavior. A doc update alone
   doesn't prove the code. Report any check that wasn't run.
