"""Import Harbor job directories containing per-trial ``result.json`` files."""

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


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return require_mapping(load_json(path), "Harbor JSON")


def _reward_score(rewards: Any) -> float | None:
    if not isinstance(rewards, dict):
        return None
    reward_values = cast(dict[str, Any], rewards)
    return next(
        (score for value in reward_values.values() if (score := result_score(value)) is not None),
        None,
    )


def _trial_from_result(result: Any, index: int) -> ImportedTrial:
    trial = require_mapping(result, f"Harbor trial result {index}")
    required = {"trial_name", "task_name", "config", "agent_info"}
    missing = required - trial.keys()
    if missing:
        raise ImportFormatError(
            f"Harbor trial result {index} is missing fields: {', '.join(sorted(missing))}"
        )
    if not isinstance(trial["config"], dict) or not isinstance(trial["agent_info"], dict):
        raise ImportFormatError(
            f"Harbor trial result {index} config and agent_info must be objects"
        )
    exception = trial.get("exception_info")
    if exception is not None and not isinstance(exception, dict):
        raise ImportFormatError(f"Harbor trial result {index} exception_info must be an object")
    verifier_result = trial.get("verifier_result")
    if verifier_result is not None and not isinstance(verifier_result, dict):
        raise ImportFormatError(f"Harbor trial result {index} verifier_result must be an object")
    verifier_mapping = cast(dict[str, Any] | None, verifier_result)
    rewards = verifier_mapping.get("rewards") if verifier_mapping else None
    if rewards is not None and not isinstance(rewards, dict):
        raise ImportFormatError(f"Harbor trial result {index} rewards must be an object")
    exception_info = cast(dict[str, Any] | None, exception)
    error = exception_info.get("exception_message") if exception_info else None
    status = "error" if exception_info else "completed"
    return ImportedTrial(
        id=str(trial.get("id", trial["trial_name"])),
        name=str(trial["task_name"]),
        status=status,
        input=cast(dict[str, Any], trial["config"]).get("extra_instructions"),
        output=trial.get("agent_result"),
        score=_reward_score(rewards),
        error=str(error) if error else None,
        metadata={
            "trial_name": trial["trial_name"],
            "task_id": trial.get("task_id"),
            "agent_info": trial["agent_info"],
            "verifier_result": verifier_result,
            "started_at": trial.get("started_at"),
            "finished_at": trial.get("finished_at"),
        },
        source_data=trial,
    )


def parse_harbor(data: Any, *, name: str = "Harbor job") -> ImportedBundle:
    """Normalize a Harbor ``JobResult`` mapping."""
    job = require_mapping(data, "Harbor job result")
    rows = job.get("trial_results")
    if not isinstance(rows, list):
        raise ImportFormatError("Harbor job result must contain a trial_results array")
    rows = cast(list[Any], rows)
    trials = tuple(_trial_from_result(row, index) for index, row in enumerate(rows))
    return ImportedBundle(
        source="harbor",
        name=str(job.get("job_name", name)),
        imported=True,
        trials=trials,
        metadata={key: value for key, value in job.items() if key != "trial_results"},
    )


def import_harbor(path: str | Path) -> ImportedBundle:
    """Read a Harbor job directory or a serialized Harbor JobResult file."""
    source = Path(path)
    if source.is_file():
        return parse_harbor(load_json(source), name=source.stem)
    if not source.is_dir():
        raise ImportFormatError(f"Harbor input does not exist: {source}")
    result_path = source / "result.json"
    if not result_path.is_file():
        raise ImportFormatError(f"Harbor job directory is missing {result_path}")
    job_result = require_mapping(load_json(result_path), "Harbor job result")
    rows = job_result.get("trial_results")
    if not isinstance(rows, list):
        raise ImportFormatError("Harbor job result must contain a trial_results array")
    rows = cast(list[Any], rows)
    trials = tuple(_trial_from_result(row, index) for index, row in enumerate(rows))
    config = _read_optional_json(source / "config.json")
    return ImportedBundle(
        source="harbor",
        name=str(config.get("job_name", source.name)),
        imported=True,
        trials=trials,
        metadata={"job_result": job_result, "job_config": config},
    )
