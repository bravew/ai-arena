## Summary

<!-- What changes and why. One goal. -->

Part of #<epic>
Closes #<issue>

<!-- Closes only fires on merges into main. Close the sub-issue by hand when this merges into the epic branch. -->

## Test that fails without this change

<!-- The test, and the output it gives without the change. -->

```
```

## What I ran at this head

<!-- Head sha, the commands, and their output. For example: uv run pytest, uv run pyright, uv run ruff check. -->

```
```

## Real-thing check

<!-- A real provider key or agent CLI version, and what it showed. Or: "not run, because ...". -->

## Checklist

- [ ] Tests run under `tests/testenv` and touch nothing in the real HOME
- [ ] `docs/subsystems/` updated if a documented responsibility, state transition or failure behavior changed
- [ ] One goal per commit
