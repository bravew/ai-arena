"""Validation helpers for versioned report bundles and run event records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "docs"


def _load_validator(schema_name: str) -> Draft202012Validator:
    """Load and check a repository JSON Schema."""
    schema_path = _SCHEMA_DIR / schema_name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to load JSON Schema {schema_path}: {exc}") from exc
    typed_schema = cast(dict[str, Any], schema)
    Draft202012Validator.check_schema(typed_schema)
    return Draft202012Validator(typed_schema)


def validate_bundle(bundle: dict[str, Any]) -> None:
    """Validate a v1 or v2 report bundle using its versioned schema."""
    version = bundle.get("bundle_version")
    if type(version) is int and version == 1:
        schema_name = "bundle-schema-v1.json"
    elif version == 2:
        schema_name = "bundle-schema.json"
    else:
        raise ValueError(f"Unsupported bundle version: {version!r}")
    validator = cast(Any, _load_validator(schema_name))
    validator.validate(bundle)


def validate_event(event: dict[str, Any]) -> None:
    """Raise ``ValidationError`` unless *event* conforms to event-schema.json."""
    validator = cast(Any, _load_validator("event-schema.json"))
    validator.validate(event)


def validate_events_jsonl(contents: str) -> None:
    """Validate every non-empty JSONL line as a versioned RunEvent."""
    for line_number, line in enumerate(contents.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on events.jsonl line {line_number}: {exc}") from exc
        if not isinstance(event, dict):
            raise ValueError(f"events.jsonl line {line_number} must contain a JSON object")
        typed_event = cast(dict[str, Any], event)
        try:
            validate_event(typed_event)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid event on events.jsonl line {line_number}: {exc.message}"
            ) from exc
