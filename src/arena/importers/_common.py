"""Shared types and validation for external result importers."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from arena.core.bundle_contract import validate_bundle


def _empty_mapping() -> dict[str, Any]:
    return {}


class ImportFormatError(ValueError):
    """Raised when an input does not match a supported external format."""


@dataclass(frozen=True)
class ImportedTrial:
    """One normalized result row, retaining the source payload for traceability."""

    id: str
    name: str
    status: str
    input: Any = None
    output: Any = None
    target: Any = None
    score: float | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=_empty_mapping)
    source_data: dict[str, Any] = field(default_factory=_empty_mapping)


@dataclass(frozen=True)
class ImportedBundle:
    """A portable collection of external results, explicitly unverified/imported."""

    source: str
    name: str
    imported: bool
    trials: tuple[ImportedTrial, ...]
    metadata: dict[str, Any] = field(default_factory=_empty_mapping)
    verification: str = "unverified"

    def to_dict(self) -> dict[str, Any]:
        """Return the source-preserving importer representation."""
        return {
            "source": self.source,
            "name": self.name,
            "imported": self.imported,
            "verification": self.verification,
            "trials": [asdict(trial) for trial in self.trials],
            "metadata": self.metadata,
        }

    def to_bundle_dict(self) -> dict[str, Any]:
        """Return an imported report bundle validated against the repository schema."""
        bundle_id = _safe_id(f"import-{self.source}-{self.name}")
        contestant_id = _safe_id(f"{bundle_id}-contestant")
        trial_ids = [
            _safe_id(f"{bundle_id}-{trial.id}-{index}")
            for index, trial in enumerate(self.trials)
        ]
        timestamp = _source_timestamp(self.metadata, self.trials)
        has_failure = any(trial.status in {"error", "failed"} for trial in self.trials)
        status = "failed" if has_failure else "succeeded"
        model = str(self.metadata.get("model") or "unknown/imported")
        source_records = [trial.source_data for trial in self.trials]
        bundle: dict[str, Any] = {
            "bundle_version": 1,
            "run": {
                "id": bundle_id,
                "suite_id": _safe_id(f"imported-{self.source}"),
                "started_at": timestamp,
                "ended_at": timestamp,
                "status": status,
            },
            "contestants": [
                {
                    "id": contestant_id,
                    "label": f"Imported from {self.source}: {self.name}",
                    "model": model,
                    "params": {
                        "imported": self.imported,
                        "source": self.source,
                        "verification": self.verification,
                        "source_metadata": self.metadata,
                        "source_records": source_records,
                    },
                    "scaffold": None,
                    "scaffold_prompt": "native",
                    "kit_hash": "none",
                    "orchestration": "imported",
                    "prompt_version": None,
                    "hooks": [],
                }
            ],
            "trials": [],
            "calls": [],
            "scores": [],
            "sessions": [],
            "kit_installs": [],
            "artifacts": [],
        }
        for index, (trial, trial_id) in enumerate(zip(self.trials, trial_ids, strict=True)):
            trial_status = _canonical_trial_status(trial.status)
            bundle["trials"].append(
                {
                    "id": trial_id,
                    "run_id": bundle_id,
                    "contestant_id": contestant_id,
                    "task_id": _safe_id(trial.name),
                    "attempt": 1,
                    "status": trial_status,
                    "error_class": trial.error,
                    "flags": {
                        "unmetered": True,
                        "swapped": False,
                        "subscription_served": False,
                        "kit_unapplied": False,
                    },
                    "started_at": None,
                    "ended_at": None,
                }
            )
            score = trial.score
            if score is not None and 0 <= score <= 1:
                bundle["scores"].append(
                    {
                        "trial_id": trial_id,
                        "scorer_id": _safe_id(f"{self.source}-imported-score"),
                        "scorer_version": "unknown",
                        "value": score,
                        "normalized": score,
                        "passed": score >= 1,
                        "rationale": "Imported score; scorer semantics are unverified.",
                        "evidence": {
                            "source": self.source,
                            "source_record_index": index,
                            "source_score": score,
                        },
                    }
                )
        validate_bundle(bundle)
        return bundle

    def export_json(self, path: str | Path) -> Path:
        """Write the validated canonical bundle JSON to a caller-selected path."""
        destination = Path(path)
        bundle = self.to_bundle_dict()
        destination.write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        return destination


def _safe_id(value: str) -> str:
    """Make stable schema-safe identifiers without losing the source in metadata."""
    safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in value)
    safe = safe.strip(".-")
    return safe[:180] or "imported"


def _canonical_trial_status(status: str) -> str:
    if status in {"running", "queued"}:
        return status
    if status in {"error", "errored"}:
        return "errored"
    if status in {"timeout", "timed_out"}:
        return "timeout"
    if status in {"skipped", "cancelled"}:
        return "skipped"
    if status in {"failed", "completed", "succeeded", "success"}:
        return "succeeded" if status in {"completed", "succeeded", "success"} else "failed"
    return "errored"


def _source_timestamp(metadata: dict[str, Any], trials: tuple[ImportedTrial, ...]) -> str:
    candidates: list[Any] = [metadata.get("created"), metadata.get("timestamp")]
    for trial in trials:
        candidates.extend((trial.metadata.get("started_at"), trial.metadata.get("finished_at")))
    for value in candidates:
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
    raise ImportFormatError(
        "Cannot create a schema-valid bundle without a source timestamp; no timestamp was invented"
    )


def load_json(path: str | Path) -> Any:
    """Load JSON from a caller-selected path; never consult user directories."""
    source = Path(path)
    try:
        with source.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportFormatError(f"Cannot read JSON input {source}: {exc}") from exc


def require_mapping(value: Any, format_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ImportFormatError(f"{format_name} input must be a JSON object")
    return cast(dict[str, Any], value)


def result_score(value: Any) -> float | None:
    """Convert common numeric and boolean scores to finite floats."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        score = float(value)
        if math.isfinite(score):
            return score
    return None
