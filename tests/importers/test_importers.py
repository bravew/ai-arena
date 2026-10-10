"""Importer tests use local samples; format interoperability remains unverified."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError

from arena.core.bundle_contract import validate_bundle
from arena.importers import (
    ImportFormatError,
    import_harbor,
    import_inspect,
    import_promptfoo,
    parse_harbor,
    parse_inspect,
    parse_promptfoo,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_inspect_sample_normalizes_preserves_provenance_and_exports_valid_bundle(tmp_path):
    imported = import_inspect(FIXTURES / "inspect-log.json")

    assert imported.imported is True
    assert imported.verification == "unverified"
    assert imported.source == "inspect"
    assert imported.metadata["model"] == "ollama/llama3.1"
    assert imported.trials[0].output == "No"

    bundle = imported.to_bundle_dict()
    validate_bundle(bundle)
    assert bundle["contestants"][0]["params"]["imported"] is True
    assert bundle["contestants"][0]["params"]["verification"] == "unverified"
    assert bundle["contestants"][0]["params"]["source_records"][0]["output"]
    exported = imported.export_json(tmp_path / "bundle.json")
    loaded = json.loads(exported.read_text(encoding="utf-8"))
    validate_bundle(loaded)
    assert loaded == bundle
    assert loaded["trials"][0]["flags"]["unmetered"] is True
    assert loaded["calls"] == []
    assert loaded["sessions"] == []


def test_inspect_rejects_wrong_shape_and_malformed_sample():
    with pytest.raises(ImportFormatError, match="eval metadata"):
        parse_inspect({"samples": []})
    data = {
        "version": 2,
        "status": "success",
        "eval": {"task": "qa"},
        "samples": [{"id": 1, "epoch": 1}],
    }
    with pytest.raises(ImportFormatError, match="missing required fields"):
        parse_inspect(data)


def _harbor_trial() -> dict[str, object]:
    """Synthetic Harbor-like row; upstream interoperability is not verified."""
    return {
        "id": "7e847602-99fb-42ca-a32f-23ea46842352",
        "task_name": "hello-world",
        "trial_name": "hello-world__oracle__1",
        "trial_uri": "file:///jobs/hello-world__oracle__1",
        "task_id": "hello-world/hello-world",
        "task_checksum": "0" * 64,
        "config": {
            "task": {"name": "hello-world/hello-world"},
            "trial_name": "hello-world__oracle__1",
        },
        "agent_info": {"name": "oracle", "version": "1.0"},
        "agent_result": {"metadata": {"answer": "hello"}},
        "verifier_result": {"rewards": {"reward": 1.0}},
        "started_at": "2025-01-01T00:00:00Z",
        "finished_at": "2025-01-01T00:00:01Z",
    }


def test_harbor_job_result_directory_exports_valid_bundle(tmp_path):
    trial = tmp_path / "trial-1"
    trial.mkdir()
    (tmp_path / "config.json").write_text(json.dumps({"job_name": "job-a"}), encoding="utf-8")
    (tmp_path / "result.json").write_text(
        json.dumps({"trial_results": [_harbor_trial()]}), encoding="utf-8"
    )

    imported = import_harbor(tmp_path)

    assert imported.imported is True
    assert imported.verification == "unverified"
    assert imported.name == "job-a"
    assert imported.trials[0].name == "hello-world"
    assert imported.trials[0].score == 1.0
    assert imported.trials[0].status == "completed"
    bundle = imported.to_bundle_dict()
    validate_bundle(bundle)
    assert bundle["scores"][0]["value"] == 1.0
    assert bundle["contestants"][0]["params"]["source_records"] == [_harbor_trial()]


def test_harbor_rejects_malformed_job_and_trial():
    with pytest.raises(ImportFormatError, match="trial_results array"):
        parse_harbor({"name": "batch", "trials": []})
    with pytest.raises(ImportFormatError, match="missing fields"):
        parse_harbor({"trial_results": [{"trial_name": "broken"}]})


def test_promptfoo_local_sample_exports_valid_bundle():
    imported = import_promptfoo(FIXTURES / "promptfoo-export.json")

    assert imported.imported is True
    assert imported.verification == "unverified"
    trial = imported.trials[0]
    assert trial.input["riddle"].startswith("I speak without a mouth")
    assert trial.output is not None
    assert trial.metadata["prompt"]
    assert trial.score == 0.75
    bundle = imported.to_bundle_dict()
    validate_bundle(bundle)
    assert bundle["contestants"][0]["params"]["source_records"]


def test_promptfoo_rejects_bad_shape_and_invalid_prompt_index():
    with pytest.raises(ImportFormatError, match="results or outputs"):
        parse_promptfoo({"timestamp": 1})
    data = {"results": {"results": [{"promptIdx": 2}], "prompts": ["prompt"]}}
    with pytest.raises(ImportFormatError, match="invalid prompts index"):
        parse_promptfoo(data)


def test_canonical_bundle_is_rejected_when_schema_contract_is_violated():
    imported = import_inspect(FIXTURES / "inspect-log.json")
    invalid: dict[str, Any] = imported.to_bundle_dict()
    invalid["bundle_version"] = 2
    with pytest.raises(ValidationError):
        validate_bundle(invalid)
