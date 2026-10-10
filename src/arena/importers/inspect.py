"""Import Inspect AI JSON log dumps (``inspect log dump`` output)."""

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


def parse_inspect(data: Any) -> ImportedBundle:
    """Normalize an Inspect EvalLog JSON mapping into an imported bundle."""
    log = require_mapping(data, "Inspect EvalLog")
    eval_info_value = log.get("eval")
    if not isinstance(eval_info_value, dict):
        raise ImportFormatError("Inspect EvalLog is missing its eval metadata object")
    eval_info = cast(dict[str, Any], eval_info_value)
    if log.get("version") not in {1, 2}:
        raise ImportFormatError("Inspect EvalLog has an unsupported or missing version")
    if log.get("status") not in {"started", "success", "cancelled", "error"}:
        raise ImportFormatError("Inspect EvalLog has an invalid status")
    sample_rows = log.get("samples", [])
    if not isinstance(sample_rows, list):
        raise ImportFormatError("Inspect EvalLog samples must be an array")
    sample_rows = cast(list[Any], sample_rows)

    trials: list[ImportedTrial] = []
    for index, raw_sample in enumerate(sample_rows):
        sample = require_mapping(raw_sample, f"Inspect sample {index}")
        if "id" not in sample or "epoch" not in sample or "input" not in sample:
            raise ImportFormatError(f"Inspect sample {index} is missing required fields")
        scores = sample.get("scores")
        if scores is not None and not isinstance(scores, dict):
            raise ImportFormatError(f"Inspect sample {index} scores must be an object or null")
        scores = cast(dict[str, Any], scores or {})
        score = next(
            (
                result_score(cast(dict[str, Any], value).get("value"))
                for value in scores.values()
                if isinstance(value, dict)
                and result_score(cast(dict[str, Any], value).get("value")) is not None
            ),
            None,
        )
        output: Any = sample.get("output")
        if isinstance(output, dict):
            output_mapping = cast(dict[str, Any], output)
            choices = output_mapping.get("choices", [None])
            typed_choices = cast(list[Any], choices) if isinstance(choices, list) else []
            output = typed_choices[0] if typed_choices else None
            if isinstance(output, dict):
                output_mapping = cast(dict[str, Any], output)
                message = output_mapping.get("message")
                if isinstance(message, dict):
                    message_mapping = cast(dict[str, Any], message)
                    output = message_mapping.get("content")
                else:
                    output = cast(dict[str, Any], output)
        sample_error = sample.get("error")
        trials.append(
            ImportedTrial(
                id=f"{eval_info.get('run_id', 'inspect')}:{sample['id']}:{sample['epoch']}",
                name=f"{sample['id']} (epoch {sample['epoch']})",
                status="error" if sample_error else "completed",
                input=sample["input"],
                output=output,
                target=sample.get("target"),
                score=score,
                error=str(sample_error) if sample_error else None,
                metadata={"epoch": sample["epoch"], "scores": scores},
                source_data=sample,
            )
        )

    eval_results = log.get("results")
    if eval_results is not None and not isinstance(eval_results, dict):
        raise ImportFormatError("Inspect EvalLog results must be an object or null")
    eval_stats = log.get("stats")
    if eval_stats is not None and not isinstance(eval_stats, dict):
        raise ImportFormatError("Inspect EvalLog stats must be an object or null")
    task_name = eval_info.get("task_display_name") or eval_info.get("task") or "Inspect evaluation"
    return ImportedBundle(
        source="inspect",
        name=str(task_name),
        imported=True,
        trials=tuple(trials),
        metadata={
            "status": log["status"],
            "model": eval_info.get("model"),
            "created": eval_info.get("created"),
            "results": eval_results,
            "stats": eval_stats,
            "log_version": log["version"],
            "evaluation_id": eval_info.get("eval_id"),
            "run_id": eval_info.get("run_id"),
        },
    )


def import_inspect(path: str | Path) -> ImportedBundle:
    """Read an Inspect JSON log or JSON dump and normalize it."""
    return parse_inspect(load_json(path))
