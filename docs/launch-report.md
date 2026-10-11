# CP7 launch readiness report

**Status:** blocked before provider execution. No trial data is reported as a real launch run.

## Planned comparison

The configured skill ablation expands to 60 trials: 10 tasks × 2 kit arms × 3 repeats, using the same configured model and Claude Code scaffold for both arms.

```text
$ uv run arena validate suites/
Validated 114 config file(s).

$ uv run arena plan suites/skill-ablation -c contestants/sets/ablation.yaml --repeats 3
Expanded contestants:
  b5a9bd726e82: anthropic/claude-sonnet-4-5
  7a44e3acc4e5: anthropic/claude-sonnet-4-5
Unsupported pairs: none (no adapter declares an unsupported pair)
Trials: 60
Estimated cost: unknown

$ uv run arena run suites/skill-ablation -c contestants/sets/ablation.yaml --repeats 3 --budget-usd 20 --dry-run
Run abc024c1a7a7: planned=60 created=0 completed=0 cache_hits=0 skipped=0
```

The only estimate available is unknown; `catalog/models.yaml` does not contain a price entry for the configured model.

## Why no real run is recorded

At this branch head, `arena run` in `src/arena/cli_run.py` calls `mock_completion` directly. It does not start or connect to the gateway dispatcher, so the command cannot run the requested provider-backed experiment. The current environment also has no `ANTHROPIC_API_KEY`, `ARENA_GATEWAY_URL` or `ARENA_RUN_KEY`. A dry run verified the 60-job plan only; no calls were made and no run results exist.

Before real launch evidence can be collected, the runner needs an approved gateway execution path with per-call cost accounting and the $20 hard cap applied to actual costs. The planned model also needs a catalog price entry for cost estimates. A real run must then save the run bundle and ledger for verification of non-empty views and Live replay.

The configured code tasks also need a Go executable for their execution scorer; authorized selfcheck was not completed because `go` is unavailable in this environment.

## Available calibration fixture (not launch data)

`fixtures/gold/pair_judgments.jsonl` is a synthetic fixture, not labels from this launch:

| Judge | Cohen's κ | Position bias | Length correlation | Status |
| --- | ---: | ---: | ---: | --- |
| judge-biased | 0.172 | 0.500 | -0.023 | UNCALIBRATED |
| judge-good | 0.867 | 0.083 | 0.902 | calibrated |

The report contains 7 disagreements.

## Verification at this branch head

- `uv run ruff format --check src tests` — passed.
- `uv run ruff check src tests` — passed.
- `uv run pyright` — 0 errors, 0 warnings, 0 informations.
- `uv run pytest -q` — 667 passed, 3 skipped (opt-in OpenCode Docker contract; two Playwright-dependent Python tests).
- `uv run python scripts/check_doc_links.py` — passed, 14 pages.
- Web TypeScript check, ESLint and Vitest — passed; 39 tests.
- `pnpm -C web e2e` — 68 passed across Chromium and WebKit. Live vote server test is a separate command.
- `pnpm -C web e2e:live` — 1 Chromium test passed, using a temporary seeded home and local `arena serve`; casts 20 votes, checks blindness before voting, post-vote reveal, leaderboard updates and 20 persisted JSONL rows.
- `uv run arena judges report fixtures/gold --json` — passed on the fixture, as shown above.
- `uv run arena selfcheck suites/agentic suites/code --trust-task-code` — stopped with `scorer executable is unavailable: go`; no provider run was attempted.
