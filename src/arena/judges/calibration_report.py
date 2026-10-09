"""Read fixture or supplied pairwise judgments for judge calibration views."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from arena.core.models import Call
from arena.stats.judge_calibration import JudgeCalibrationReport, PairJudgment, calibration_report


class GoldSetError(ValueError):
    """A gold set is absent, unreadable, or malformed."""


class JudgeStatus(StrEnum):
    CALIBRATED = "calibrated"
    UNCALIBRATED = "UNCALIBRATED"


@dataclass(frozen=True)
class JudgeReport:
    calibration: JudgeCalibrationReport
    status: JudgeStatus


@dataclass(frozen=True)
class Disagreement:
    item_id: str
    judge_verdict: str
    human_verdict: str
    severity: int


def load_gold_set(path: Path) -> tuple[PairJudgment, ...]:
    """Load JSONL pair judgments from a file or a directory's canonical fixture."""
    source = path / "pair_judgments.jsonl" if path.is_dir() else path
    if not source.is_file():
        raise GoldSetError(f"gold set does not exist: {source}")
    rows: list[PairJudgment] = []
    try:
        with source.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    raw: Any = json.loads(line)
                    if not isinstance(raw, dict):
                        raise ValueError("row must be an object")
                    fields = cast(dict[str, Any], raw)
                    if not all(type(key) is str for key in fields):
                        raise ValueError("row must have string keys")
                    allowed: set[str] = set(PairJudgment.__dataclass_fields__)
                    unknown: set[str] = fields.keys() - allowed
                    if unknown:
                        raise ValueError(f"unknown fields: {', '.join(sorted(unknown))}")
                    row = PairJudgment(**fields)
                    if not row.item_id or not row.judge_id:
                        raise ValueError("item_id and judge_id must be non-empty")
                    if row.verdict not in (None, "a", "b", "tie"):
                        raise ValueError("verdict must be 'a', 'b', 'tie', or null")
                    if row.human_verdict not in (None, "a", "b", "tie"):
                        raise ValueError("human_verdict must be 'a', 'b', 'tie', or null")
                    if row.swapped_verdict not in (None, "a", "b", "tie"):
                        raise ValueError("swapped_verdict must be 'a', 'b', 'tie', or null")
                    rows.append(row)
                except (TypeError, ValueError, json.JSONDecodeError) as error:
                    raise GoldSetError(f"{source}:{line_number}: {error}") from error
    except (OSError, UnicodeError) as error:
        raise GoldSetError(f"cannot read gold set {source}: {error}") from error
    if not rows:
        raise GoldSetError(f"gold set contains no judgments: {source}")
    return tuple(rows)


def build_reports(
    judgments: Iterable[PairJudgment],
    calls: Iterable[Call] = (),
    *,
    threshold: float = 0.6,
    judge_call_ids: dict[str, set[str]] | None = None,
) -> tuple[JudgeReport, ...]:
    """Build calibration reports; unavailable kappa evidence remains uncalibrated."""
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    rows = tuple(judgments)
    judge_ids = sorted({row.judge_id for row in rows})
    if not judge_ids:
        raise ValueError("no judgments")
    call_rows = tuple(calls)
    reports: list[JudgeReport] = []
    for judge_id in judge_ids:
        report = calibration_report(
            judge_id,
            rows,
            call_rows,
            judge_call_ids=(judge_call_ids or {}).get(judge_id),
        )
        status = (
            JudgeStatus.CALIBRATED
            if report.cohens_kappa is not None and report.cohens_kappa >= threshold
            else JudgeStatus.UNCALIBRATED
        )
        reports.append(JudgeReport(report, status))
    return tuple(reports)


def disagreements(
    judgments: Iterable[PairJudgment], *, judge_id: str, limit: int = 20
) -> tuple[Disagreement, ...]:
    """Rank labeled disagreements, with opposite verdicts ahead of judge/human ties."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    ranked = [
        Disagreement(
            row.item_id,
            row.verdict,
            row.human_verdict,
            2 if {row.verdict, row.human_verdict} == {"a", "b"} else 1,
        )
        for row in judgments
        if row.judge_id == judge_id
        and row.verdict is not None
        and row.human_verdict is not None
        and row.verdict != row.human_verdict
    ]
    return tuple(sorted(ranked, key=lambda row: (-row.severity, row.item_id))[:limit])
