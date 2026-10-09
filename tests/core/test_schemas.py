import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError

from arena.core.bundle_contract import validate_bundle, validate_event, validate_events_jsonl

ROOT = Path(__file__).resolve().parents[2]


def load_bundle() -> dict[str, Any]:
    return json.loads((ROOT / "fixtures/bundles/schema-example.json").read_text(encoding="utf-8"))


def test_handwritten_bundle_fixture_validates_and_covers_contract_entities() -> None:
    bundle = load_bundle()
    validate_bundle(bundle)

    assert len(bundle["contestants"]) == 3
    assert bundle["contestants"][0]["kit_hash"] == "none"
    assert bundle["contestants"][1]["kit_hash"] != "none"
    assert bundle["trials"][2]["flags"]["subscription_served"] is True
    assert bundle["sessions"][0]["turns"][0]["skill_events"]
    assert bundle["calls"][0]["cost_usd"] is None
    assert bundle["kit_installs"] and bundle["artifacts"]


def test_bundle_rejects_unknown_version() -> None:
    bundle = load_bundle()
    bundle["bundle_version"] = 3

    with pytest.raises(ValueError, match="Unsupported bundle version"):
        validate_bundle(bundle)


def test_bundle_v1_schema_remains_readable() -> None:
    bundle = load_bundle()
    bundle["bundle_version"] = 1
    bundle.pop("provenance")
    bundle["artifacts"][0].pop("trial_id")

    validate_bundle(bundle)


def test_bundle_v2_requires_trial_link_and_provenance() -> None:
    bundle = load_bundle()
    bundle["artifacts"][0].pop("trial_id")

    with pytest.raises(ValidationError):
        validate_bundle(bundle)

    bundle = load_bundle()
    bundle.pop("provenance")
    with pytest.raises(ValidationError):
        validate_bundle(bundle)


def test_imported_bundle_provenance_is_explicit() -> None:
    bundle = load_bundle()
    bundle["provenance"] = {
        "origin": "imported",
        "verification": "unverified",
        "importer": "inspect",
        "importer_version": "1.0",
        "source_ref": "source.json",
    }

    validate_bundle(bundle)


@pytest.mark.parametrize(
    "origin,verification",
    [("native", "unverified"), ("imported", "verified")],
)
def test_bundle_rejects_contradictory_provenance(origin: str, verification: str) -> None:
    bundle = load_bundle()
    bundle["provenance"].update(origin=origin, verification=verification)

    with pytest.raises(ValidationError):
        validate_bundle(bundle)


def test_bundle_rejects_normalized_score_outside_unit_interval() -> None:
    bundle = load_bundle()
    bundle["scores"][0]["normalized"] = 1.2

    with pytest.raises(ValidationError):
        validate_bundle(bundle)


def test_bundle_requires_flat_fee_subscription_cost_to_be_nullable() -> None:
    bundle = load_bundle()
    bundle["calls"][0]["cost_usd"] = -1

    with pytest.raises(ValidationError):
        validate_bundle(bundle)


def test_events_fixture_validates_all_event_kinds_included() -> None:
    contents = (ROOT / "fixtures/events/events.jsonl").read_text(encoding="utf-8")
    validate_events_jsonl(contents)
    events = [json.loads(line) for line in contents.splitlines()]
    kinds = {event["kind"] for event in events}

    assert {"kit_installed", "session_turn", "skill_event"} <= kinds


@pytest.mark.parametrize("kind", ["kit_installed", "session_turn", "skill_event"])
def test_each_extended_event_payload_validates(kind: str) -> None:
    contents = (ROOT / "fixtures/events/events.jsonl").read_text(encoding="utf-8")
    event = next(
        json.loads(line) for line in contents.splitlines() if json.loads(line)["kind"] == kind
    )
    validate_event(event)


def test_event_rejects_unknown_kind() -> None:
    event = {
        "event_version": 1,
        "seq": 1,
        "ts": "2026-10-08T00:00:00Z",
        "run_id": "r",
        "kind": "unknown",
    }

    with pytest.raises(ValidationError):
        validate_event(event)


def test_events_jsonl_reports_invalid_line_number() -> None:
    contents = (
        '{"event_version": 1, "seq": 1, "ts": "2026-10-08T00:00:00Z", '
        '"run_id": "r", "kind": "run_started"}\nnot-json\n'
    )

    with pytest.raises(ValueError, match="line 2"):
        validate_events_jsonl(contents)
