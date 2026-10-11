from pathlib import Path

import pytest
from typer.testing import CliRunner

from arena.cli import app
from arena.judges.calibration_report import (
    GoldSetError,
    JudgeStatus,
    build_reports,
    disagreements,
    load_gold_set,
)
from arena.stats.judge_calibration import PairJudgment

GOLD = Path("fixtures/gold")


def _judgment(item: str, verdict: str | None, human: str | None, judge: str = "j") -> PairJudgment:
    return PairJudgment(
        item_id=item,
        judge_id=judge,
        verdict=verdict,  # type: ignore[arg-type]
        human_verdict=human,  # type: ignore[arg-type]
    )


def test_json_report_exports_source_metrics_status_and_ranked_disagreements() -> None:
    import json

    result = CliRunner().invoke(app, ["judges", "report", str(GOLD), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["threshold"] == 0.6
    reports = {item["judge_id"]: item for item in payload["judges"]}
    assert reports["judge-good"]["status"] == "calibrated"
    assert reports["judge-biased"]["status"] == "UNCALIBRATED"
    assert reports["judge-good"]["cohens_kappa"] == pytest.approx(0.8666666666666666)
    assert {
        "human_verdict": "a",
        "item_id": "i09",
        "judge_id": "judge-good",
        "judge_verdict": "b",
        "severity": 2,
    } in payload["disagreements"]


def test_report_prints_kappa_position_bias_and_length_correlation() -> None:
    result = CliRunner().invoke(app, ["judges", "report", str(GOLD)])

    assert result.exit_code == 0, result.output
    report_lines = result.stdout.split("Largest judge/human disagreements", 1)[0].splitlines()
    lines = {line.split()[0]: line for line in report_lines if line.strip()}
    for column in ("kappa", "position_bias", "length_corr"):
        assert column in lines["judge"]
    # kappa: observed 11/12, expected 54/144, so (0.9167 - 0.375) / 0.625 = 0.867
    assert "0.867" in lines["judge-good"]
    assert "0.083" in lines["judge-good"]  # position bias: 1 of 12 flips on swap
    assert "0.902" in lines["judge-good"]  # length correlation
    assert "0.172" in lines["judge-biased"]
    assert "0.500" in lines["judge-biased"]


def test_uncalibrated_judges_are_flagged() -> None:
    result = CliRunner().invoke(app, ["judges", "report", str(GOLD)])

    report_lines = result.stdout.split("Largest judge/human disagreements", 1)[0].splitlines()
    lines = {line.split()[0]: line for line in report_lines if line.strip()}
    assert "calibrated" in lines["judge-good"]
    assert "UNCALIBRATED" not in lines["judge-good"]
    assert "UNCALIBRATED" in lines["judge-biased"]


def test_status_requires_kappa_above_target() -> None:
    reports = build_reports(
        [_judgment(f"i{n}", "a", "a" if n < 5 else "b", "ok") for n in range(10)]
        + [_judgment("x", "a", None, "unlabeled")],
        threshold=0.6,
    )
    by_judge = {report.calibration.judge_id: report for report in reports}

    assert by_judge["ok"].status is JudgeStatus.UNCALIBRATED
    assert by_judge["unlabeled"].status is JudgeStatus.UNCALIBRATED
    assert by_judge["unlabeled"].calibration.cohens_kappa is None


def test_disagreements_rank_opposite_verdicts_before_ties() -> None:
    rows = [
        _judgment("agree", "a", "a"),
        _judgment("tie-vs-a", "tie", "a"),
        _judgment("opposite", "b", "a"),
        _judgment("unknown", None, "a"),
    ]

    ranked = disagreements(rows, judge_id="j", limit=10)

    assert [row.item_id for row in ranked] == ["opposite", "tie-vs-a"]


def test_judge_filter_limits_output() -> None:
    result = CliRunner().invoke(app, ["judges", "report", str(GOLD), "--judge", "judge-good"])

    assert result.exit_code == 0, result.output
    assert "judge-good" in result.stdout
    assert "judge-biased" not in result.stdout


def test_disagreement_section_lists_items() -> None:
    result = CliRunner().invoke(app, ["judges", "report", str(GOLD), "--disagreements", "2"])

    assert "Largest judge/human disagreements" in result.stdout
    assert "i09" in result.stdout  # judge-good: human a, judge b


def test_unknown_judge_fails() -> None:
    result = CliRunner().invoke(app, ["judges", "report", str(GOLD), "--judge", "nobody"])

    assert result.exit_code == 1
    assert "nobody" in result.output


def test_missing_gold_set_is_an_error_not_an_empty_report(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["judges", "report", str(tmp_path / "absent")])
    assert result.exit_code == 1

    (tmp_path / "pair_judgments.jsonl").write_text("")
    empty = CliRunner().invoke(app, ["judges", "report", str(tmp_path)])
    assert empty.exit_code == 1
    assert "no judgments" in empty.output


def test_unreadable_row_names_its_line(tmp_path: Path) -> None:
    (tmp_path / "pair_judgments.jsonl").write_text(
        '{"item_id": "i1", "judge_id": "j", "verdict": "a", "human_verdict": "a"}\n'
        '{"item_id": "i2", "judge_id": "j", "verdict": "maybe", "human_verdict": "a"}\n'
    )

    with pytest.raises(GoldSetError, match=r"pair_judgments.jsonl:2"):
        load_gold_set(tmp_path)


@pytest.mark.parametrize(
    "bad_length",
    [True, False, -1, float("inf"), float("-inf"), float("nan"), "12"],
)
@pytest.mark.parametrize("field_name", ["length_a", "length_b"])
def test_invalid_length_is_rejected_with_line_number(
    tmp_path: Path, field_name: str, bad_length: object
) -> None:
    import json

    row = {"item_id": "i1", "judge_id": "j", "verdict": "a", "human_verdict": "a"}
    (tmp_path / "pair_judgments.jsonl").write_text(
        json.dumps({**row, "length_a": 5, "length_b": 4})
        + "\n"
        + json.dumps({**row, field_name: bad_length})
        + "\n"
    )

    with pytest.raises(GoldSetError, match=rf"pair_judgments.jsonl:2: {field_name}"):
        load_gold_set(tmp_path)


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "pair_judgments.jsonl").write_text(
        '{"item_id": "i1", "judge_id": "j", "verdict": "a", "human_verdict": "a", "extra": 1}\n'
    )

    with pytest.raises(GoldSetError, match="extra"):
        load_gold_set(tmp_path)
