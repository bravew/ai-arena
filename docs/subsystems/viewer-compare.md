# Viewer comparison views

## Responsibilities and sources of truth

| Part | Responsibility | Source |
| --- | --- | --- |
| Compare | Show up to four selected trials side by side with model, agent, kit, score and usage details; hide all contestant names/identities when requested; isolate HTML preview in an iframe without same-origin or script privileges; present recorded file changes and test tool results | [`CompareView`](../../web/src/views/compare/CompareView.tsx), [`Trial`, `Artifact`, `Session`](../../web/src/lib/schema/types.ts) |
| Trace | Order recorded calls by sequence and show token composition by prompt part and call details | [`TraceView`](../../web/src/views/trace/TraceView.tsx), [`Call`](../../web/src/lib/schema/types.ts) |
| Run diff | Summarize the loaded run's task outcomes; external run selection is not supported by this static fixture view | [`RunDiffView`](../../web/src/views/rundiff/RunDiffView.tsx) |
| Routes | Render these views from the currently validated bundle in `BundleContext`; a missing or invalid bundle stays on the placeholder/error state | [`App`](../../web/src/app/App.tsx), [`useBundleState`](../../web/src/app/state.ts) |

## Runtime path

1. `App` validates fixture or imported JSON and provides a valid bundle through `BundleContext`. The current schema-example fixture is version 2 while the web validator reads version 1, so it currently renders the validation error until a valid v1 bundle is loaded.
2. The `/compare`, `/trace`, and `/run-diff` route elements read only that context and render their view when available; without a valid bundle they render the shared placeholder.
3. Compare filters only trials present in the loaded bundle, limits the display to four columns, and derives scores, calls, sessions, file changes and test tool outcomes from linked trial IDs. Hide names switches the rendered identity and header strings to anonymous labels. HTML preview uses a sandboxed iframe with no external content source.
4. Trace sorts calls by `seq`, calculates the prompt-part proportions, and exposes call metadata in expandable details.
5. Run diff reports task outcomes for the one loaded run. It does not load or select another bundle.

## Constraints and failure behavior

- URL parameters cannot load a bundle or select a run. Compare's optional task/trial parameters only filter records already present in the loaded bundle; unmatched selectors show an empty state.
- An absent bundle renders the shared placeholder; invalid imported JSON is shown through the shared validation error.
- Hide names removes contestant labels and identifying model/agent/kit strings from Compare's DOM. Stable color indicators remain.
- HTML iframe content is sandboxed without `allow-same-origin` or `allow-scripts`. The current bundle contract supplies artifact metadata but not artifact bytes, so the preview is blank; the compare view does not retrieve external content.
- The bundle records file paths and counts but not diff hunks or test output; Compare presents those recorded facts and labels missing detail rather than inventing it.
- Run diff cannot compare two distinct run bundles in the current loaded-bundle-only fixture view.

## Verification

```sh
pnpm -C web test
pnpm -C web e2e -- compare
pnpm -C web lint
pnpm -C web typecheck
```
