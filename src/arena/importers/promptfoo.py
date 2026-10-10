"""Import promptfoo JSON output (``promptfoo eval --output results.json``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from ._common import (
    ImportedBundle,
    ImportedTrial,
    ImportFormatError,
    load_json,
    require_mapping,
    result_score,
)


def _indexed_value(values: Any, index: Any, label: str, row_index: int) -> Any:
    if not isinstance(values, list):
        raise ImportFormatError(f"promptfoo {label} must be an array")
    typed_values = cast(list[Any], values)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(typed_values):
        raise ImportFormatError(f"promptfoo result {row_index} has invalid {label} index {index!r}")
    return typed_values[index]


def parse_promptfoo(data: Any) -> ImportedBundle:
    """Normalize promptfoo's JSON evaluation result into an imported bundle."""
    output = require_mapping(data, "promptfoo output")
    eval_result = output.get("results", output.get("outputs"))
    if isinstance(eval_result, dict):
        eval_metadata = cast(dict[str, Any], eval_result)
        rows = eval_metadata.get("results")
        prompts = eval_metadata.get("prompts", [])
        tests = output.get("tests", [])
        providers = output.get("providers", [])
    else:
        eval_metadata = output
        rows = eval_result
        tests = output.get("tests", [])
        prompts = output.get("prompts", [])
        providers = output.get("providers", [])
    if not isinstance(rows, list):
        raise ImportFormatError("promptfoo output must contain a results or outputs array")
    rows = cast(list[Any], rows)
    for label, value in (("tests", tests), ("prompts", prompts), ("providers", providers)):
        if not isinstance(value, list):
            raise ImportFormatError(f"promptfoo {label} must be an array")
    tests = cast(list[Any], tests)
    prompts = cast(list[Any], prompts)
    providers = cast(list[Any], providers)
    trials: list[ImportedTrial] = []
    for index, raw_row in enumerate(rows):
        row = require_mapping(raw_row, f"promptfoo result {index}")
        test_value = tests[index] if index < len(tests) else row.get("testCase", {})
        test = require_mapping(test_value, f"promptfoo test {index}")
        test_vars = test.get("vars", row.get("vars"))
        grading_value = row.get("gradingResult")
        if grading_value is None:
            grading_value = {}
        grading = require_mapping(grading_value, f"promptfoo result {index} gradingResult")
        response = row.get("response")
        if response is not None and not isinstance(response, dict | str | int | float | bool):
            raise ImportFormatError(f"promptfoo result {index} response has an invalid shape")
        response_mapping = cast(dict[str, Any], response) if isinstance(response, dict) else {}
        response_output: Any = response_mapping.get("output", response)
        if response_output is None:
            response_output = row.get("output")
        error: Any = row.get("error") or response_mapping.get("error")
        passed = row.get("success", grading.get("pass"))
        if passed is not None and not isinstance(passed, bool):
            raise ImportFormatError(f"promptfoo result {index} success/pass must be boolean")
        status = "error" if error else "failed" if passed is False else "completed"
        score = result_score(row.get("score", grading.get("score")))
        prompt_index = row.get("promptIdx", row.get("promptIndex"))
        provider_index = row.get("providerIdx", row.get("providerIndex"))
        prompt = (
            _indexed_value(prompts, prompt_index, "prompts", index)
            if prompt_index is not None
            else row.get("prompt")
        )
        if isinstance(prompt, dict):
            prompt_mapping = cast(dict[str, Any], prompt)
            prompt = prompt_mapping.get("raw", prompt_mapping.get("label", prompt_mapping))
        provider = (
            _indexed_value(providers, provider_index, "providers", index)
            if provider_index is not None
            else row.get("provider")
        )
        trial_id = row.get(
            "id", f"promptfoo:{output.get('evalId', output.get('eval_id', 'eval'))}:{index}"
        )
        trials.append(
            ImportedTrial(
                id=str(trial_id),
                name=str(
                    test.get("description", test_vars if test_vars is not None else f"case-{index}")
                ),
                status=status,
                input=test_vars,
                output=response_output,
                score=score,
                error=(
                    str(error)
                    if error
                    else str(grading["reason"])
                    if grading.get("reason")
                    else None
                ),
                metadata={
                    "prompt": prompt,
                    "provider": provider,
                    "grading_result": grading,
                    "latency_ms": row.get("latencyMs"),
                    "token_usage": row.get("tokenUsage"),
                    "cost": row.get("cost"),
                },
                source_data=row,
            )
        )
    return ImportedBundle(
        source="promptfoo",
        name=str(output.get("evalId", output.get("eval_id", "promptfoo evaluation"))),
        imported=True,
        trials=tuple(trials),
        metadata={
            "timestamp": eval_metadata.get("timestamp"),
            "stats": eval_metadata.get("stats"),
            "version": eval_metadata.get("version"),
        },
    )


def import_promptfoo(path: str | Path) -> ImportedBundle:
    """Read and normalize promptfoo's JSON output file."""
    return parse_promptfoo(load_json(path))
