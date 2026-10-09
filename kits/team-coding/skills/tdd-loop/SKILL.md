---
name: tdd-loop
description: Use for code changes with a concrete behavior or regression that can be checked by a test. Skip for prose, exploration, and changes whose expected behavior cannot be tested locally.
---

# TDD Loop

For a focused code change with a runnable test oracle:

1. Inspect the relevant implementation and test conventions.
2. Add or update the smallest test that expresses the expected behavior. Run it and confirm it fails for the intended reason.
3. Make the smallest implementation change that passes the test.
4. Run the focused test again, then any directly affected checks.
5. Review the diff and report the checks and outcomes.

Do not add tests merely to follow this sequence when the task has no meaningful executable oracle. Do not change unrelated behavior to satisfy a test.
