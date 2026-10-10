# Importers

## Responsibilities and sources of truth

| Part | Responsibility | Source |
|---|---|---|
| Inspect parser | Normalize an Inspect EvalLog record while retaining its source sample and unverified provenance. | [`parse_inspect`](../../src/arena/importers/inspect.py) |
| Harbor parser | Normalize Harbor JobResult trial records while retaining each source record. | [`parse_harbor`](../../src/arena/importers/harbor.py) |
| promptfoo parser | Normalize promptfoo result rows while retaining each source row. | [`parse_promptfoo`](../../src/arena/importers/promptfoo.py) |
| Canonical export | Create an imported report bundle and validate it against the current bundle contract before returning or writing JSON. | [`ImportedBundle.to_bundle_dict`](../../src/arena/importers/_common.py), [`validate_bundle`](../../src/arena/core/bundle_contract.py) |

## Runtime path

1. `import_inspect`, `import_harbor`, or `import_promptfoo` reads only the caller-selected file or Harbor result directory through `load_json`.
2. The parser validates the required external record shape and maps each result to an `ImportedTrial`. Raw source records and source metadata remain available for provenance.
3. `ImportedBundle.to_bundle_dict` emits the repository bundle shape. Imported provenance and retained source records are stored in the contestant's `params`, since the current bundle schema does not define top-level importer metadata.
4. Canonical output declares no fabricated model calls or sessions. Calls and sessions remain empty, trial flags say unmetered, and a score is emitted only when a finite imported score fits the schema's normalized [0, 1] range.
5. The bundle is checked by `validate_bundle`. `export_json` writes that validated JSON to the caller-selected destination.

## Constraints and failure behavior

- All imports are labeled `imported: true` and `verification: unverified`. The current canonical schema has no provenance field, so provenance is retained in the extensible contestant `params` object.
- Missing source timestamps prevent canonical export: the importer raises `ImportFormatError` instead of inventing a run timestamp.
- Input shape, invalid indexes, malformed JSON, and invalid schema output raise explicit errors. Failed reads are not treated as empty inputs.
- Missing metering and session information is represented honestly as empty `calls` and `sessions` arrays, with trials marked `unmetered`; it is not inferred from token totals or transcripts.
- The checked-in Inspect and promptfoo samples are local, unverified samples. The Harbor test row is synthetic. They exercise parser and schema mapping only; interoperability with those upstream tools has not been verified from authentic upstream bytes.
- This CP7 tree contains no viewer implementation or bundle-open entrypoint. The canonical JSON follows the repository bundle schema and is the best available viewer-compatible output, but opening it in the viewer cannot be verified here.

## Verification

Run the importer tests under the repository's temporary test HOME:

```sh
uv run pytest tests/importers
```

Check importer types and style with:

```sh
uv run pyright src/arena/importers
uv run ruff check src/arena/importers tests/importers
```
