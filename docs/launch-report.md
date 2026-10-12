# CP7 launch readiness report

**Status:** blocked before provider execution. No trial data is reported as a real launch run.
Nothing here is a substitute for the launch run itself; it records what the arena does now and
what the launch still needs.

This revision is a reconciliation against `main` at `e3b9cec` (2026-10-11). The previous
revision was written against a branch head that predated the metered gateway path, and two of
its statements are no longer true; they are corrected below with the commit that changed them.

## Planned comparison

The configured skill ablation expands to 60 trials: 10 tasks × 2 kit arms × 3 repeats, using the
same configured model and Claude Code scaffold for both arms.

```text
$ uv run arena validate suites/
Validated 114 config file(s).

$ uv run arena plan suites/skill-ablation -c contestants/sets/ablation.yaml --repeats 3
Expanded contestants:
  b5a9bd726e82: anthropic/claude-sonnet-4-5
  7a44e3acc4e5: anthropic/claude-sonnet-4-5
Unsupported pairs: none (no adapter declares an unsupported pair)
Trials: 60
Estimated cost: $0.000000

$ uv run arena run suites/skill-ablation -c contestants/sets/ablation.yaml --repeats 3 --budget-usd 20 --dry-run
Run d9f6e7ec1f39: planned=60 created=0 completed=0 cache_hits=0 skipped=0
```

### The $0.000000 estimate is not a forecast

`anthropic/claude-sonnet-4-5` now has a catalog price (`catalog/models.yaml`: in $3.00, out
$15.00, cache-read $0.30, cache-write $3.75 per Mtok, source `https://claude.com/pricing`), so
the estimate is no longer `unknown`. It is `$0.000000` for a different reason: the estimate is
`tokens × price`, and the ablation set declares no token bounds. `_estimate_cost`
([`cli_run.py`](../../src/arena/cli_run.py)) reads `estimated_input_tokens`,
`estimated_output_tokens` or `max_tokens` from the contestant params and treats a missing bound
as `0`.

A zero here means "no token bound was declared", not "this run is free". It must not be read as
a spend forecast, and it is not what stops an over-budget run: a `0.0` estimate never trips the
scheduler's estimate gate ([`scheduler.py`](../../src/arena/runners/scheduler.py)). The real cap
is enforced in the gateway, described next.

## The budget cap

The previous revision said `arena run` "has no gateway dispatcher" and that non-mock runs fail
closed. That was true at `a6bd833` (2026-10-10 23:59). The metered gateway path landed the next
morning in `08becf5 feat(runners): run agent contestants through a metered gateway container`
(2026-10-11 02:47), so the claim is stale.

On `main`, a non-mock run builds and starts a `GatewayContainer`
([`cli_run.py`](../../src/arena/cli_run.py)) and dispatches agent trials through it. The US$20 cap
is enforced before dispatch: the gateway reserves an upper-bound cost for each metered call and
returns HTTP 402 `budget_exceeded` when `spent + reserved + requested` would pass the cap, so the
over-cap call is never sent upstream. Settlement then prices the call from the tokens the
upstream actually returned, and a settlement that overruns the reservation flips the budget
over-cap so every later call is refused.

What is proven by a test, not by prose:

- `tests/gateway/test_server_integration.py::test_metered_call_overrun_is_recorded_and_stops_later_dispatch`
  drives the real ASGI app: the first call is 200, the second is 402, `dispatches == 1`, and
  `metered_spend` settles to the actual priced cost with `remaining_usd == 0`.
- `tests/obs/test_ledger.py` covers the reservation, the actual-charge overrun and the
  "budget is exhausted" refusal at the cap.
- `tests/runners/test_usage_capture.py` and `tests/obs/test_ledger.py` prove a subscription
  call's tokens never touch `metered_spend`, so a `local-cli` contestant cannot consume the
  metered cap.

What is **not** proven: no test drives the real Docker `GatewayContainer` end to end and asserts
a budget refusal through it. `tests/e2e/test_mixed_providers.py` monkeypatches
`GatewayContainer.start/stop` and `execute_agent_trial`, so it covers the CLI wiring and report
export but asserts nothing about the cap. The cap is proven at the gateway application boundary
and in the budget unit, and the container is what hosts that application; the last hop is
unverified.

## Why no real run is recorded

The plan and the dry run above are real and were run at this head. No provider call was made,
because a real run needs credentials and an explicit go-ahead:

- No `ANTHROPIC_API_KEY`, `ARENA_GATEWAY_URL` or `ARENA_RUN_KEY` is present in this environment.
- Spending up to the US$20 metered cap needs the maintainer's approval, and the `local-cli`
  subscription arms need approval to consume plan quota.

The arena is otherwise ready to attempt it. `arena run` refuses a non-mock run before creating
the store when the gateway cannot start, and it refuses a model with no usable catalog price, so
a misconfigured launch fails before spending rather than partway through.

## The comparison still needs a third contestant

The issue asks for **at least 3 contestants × 3 repeats**. The configured ablation set is a
2-contestant, 1-pair kit ablation (same model and scaffold, `kit: none` vs `kits/team-coding@3`),
so it does not satisfy the count on its own.

Two checked-in sets do expand to 3 contestants:

```text
$ uv run arena plan suites/skill-ablation -c contestants/sets/providers-compare.yaml --repeats 3
Expanded contestants:
  b5a9bd726e82: anthropic/claude-sonnet-4-5
  ac6669953326: anthropic-max/claude-sonnet-5-5
  fff84e7f1d76: openai-plus/gpt-6.1-sol
Unsupported pairs: none (no adapter declares an unsupported pair)
Trials: 90
Estimated cost: unknown
```

`providers-compare.yaml` compares an API key model against the two subscription CLIs (90 trials
over the 10 skill-ablation tasks), and `product-axis.yaml` compares three vendor CLIs each with
its own model (also 90). Either reaches the required count, but neither is the kit ablation the
issue's acceptance criteria describe (`kit: none` vs `team-coding@3`, with uptake and cost
delta).

Choosing between "run the kit ablation and add a third arm" and "run the 3-contestant
cross-provider comparison instead" changes what the launch measures, so it is the maintainer's
call and is not made here. No arm was added silently. This is the open decision blocking the
launch.

## Resolved since the last revision

- **Go is available.** The previous revision recorded that the code tasks' execution scorer
  could not run because `go` was unavailable. `go version` now reports `go1.27.2 darwin/arm64`,
  so `arena selfcheck suites/agentic suites/code --trust-task-code` is no longer blocked on a
  missing toolchain.
- **The launch model has a price.** `anthropic/claude-sonnet-4-5` is priced in
  `catalog/models.yaml`, so `arena plan` reports a number instead of `unknown`.

## Available calibration fixture (not launch data)

`fixtures/gold/pair_judgments.jsonl` is a synthetic fixture, not labels from this launch:

| Judge | Cohen's κ | Position bias | Length correlation | Status |
| --- | ---: | ---: | ---: | --- |
| judge-biased | 0.172 | 0.500 | -0.023 | UNCALIBRATED |
| judge-good | 0.867 | 0.083 | 0.902 | calibrated |

The report contains 7 disagreements.

## Verification at this head (`e3b9cec`)

- `uv run arena validate suites/` — validated 114 config file(s).
- `uv run arena plan suites/skill-ablation -c contestants/sets/ablation.yaml --repeats 3` — 60
  trials, estimated cost $0.000000 (see above).
- `uv run arena run suites/skill-ablation -c contestants/sets/ablation.yaml --repeats 3 --budget-usd 20 --dry-run`
  — planned=60, created=0, completed=0.
- `uv run pytest tests/gateway/test_server_integration.py tests/obs/test_ledger.py tests/runners/test_gateway_lifecycle.py tests/runners/test_usage_capture.py -q`
  — 56 passed, 1 warning (the budget-cap proof among them).
- `uv run pytest -q` — 825 passed, 3 skipped, 1 warning. The skips are the opt-in OpenCode
  Docker contract and two Playwright-dependent tests, and `playwright` is not installed here.
- `uv run ruff check src tests` — passed. `uv run ruff format --check src tests` — 221 files
  already formatted.
- `uv run pyright` — 0 errors, 0 warnings, 0 informations.
- `uv run python scripts/check_doc_links.py` — passed, 15 pages.

Not run at this head: `arena selfcheck` (which needs a task-code trust flag and would run the
execution scorers in Docker) and the web suites (`pnpm -C web test`, `e2e`), because this
revision changes only this report.

No provider call was made and no run bundle or ledger was produced, so the acceptance criteria
that need one (no empty views, Live replay matching its ledger, ablation difference and cost
delta) remain unmet. The issue stays open.
