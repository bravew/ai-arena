from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from arena.core.cas import ArtifactStore
from arena.core.models import Artifact
from arena.scorers.base import ScorerContext, ScorerError, validate_score
from arena.scorers.reference import KeyPointCoverage, ReferenceScorer, Similarity


def _context(tmp_path: Path, data: bytes) -> ScorerContext:
    blobs = ArtifactStore(tmp_path / "artifacts")
    artifact = Artifact(
        sha256=blobs.put(data), path="answer.txt", mime="text/plain", render_hint="code"
    )
    return ScorerContext(trial_id="t1", artifacts={"answer.txt": artifact}, blobs=blobs)


def _check(score: Any, name: str) -> dict[str, Any]:
    return next(result for result in score.evidence["checks"] if result["check"] == name)


def test_similarity_is_case_insensitive_token_multiset_dice(tmp_path: Path) -> None:
    scorer = ReferenceScorer(
        "answer.txt", reference="Cache reads speed up repeated queries.", checks=[Similarity()]
    )
    score = scorer.score(_context(tmp_path, b"cache reads speed up repeated queries"))
    assert score.normalized == 1.0
    assert score.passed is True
    assert _check(score, "similarity")["metric"] == "token_dice"

    empty = scorer.score(_context(tmp_path, b""))
    assert empty.normalized == 0.0
    assert _check(empty, "similarity")["reference_tokens"] == 6

    both_empty = ReferenceScorer("answer.txt", reference="", checks=[Similarity()])
    result = both_empty.score(_context(tmp_path, b""))
    assert result.normalized == 1.0


def test_similarity_uses_multiset_overlap_and_threshold(tmp_path: Path) -> None:
    scorer = ReferenceScorer(
        "answer.txt",
        reference="cache cache repeated queries",
        checks=[Similarity(threshold=0.7)],
    )
    score = scorer.score(_context(tmp_path, b"cache repeated unrelated"))
    result = _check(score, "similarity")
    assert result["matched_tokens"] == 2
    assert result["similarity"] == pytest.approx(4 / 7)
    assert score.passed is False


def test_similarity_normalizes_unicode_and_punctuation_deterministically(tmp_path: Path) -> None:
    scorer = ReferenceScorer("answer.txt", reference="Café—naïve co-operate", checks=[Similarity()])
    score = scorer.score(_context(tmp_path, "CAFÉ, naïve co-operate!".encode()))
    assert score.normalized == 1.0


def test_key_point_coverage_lists_missed_and_matched_points(tmp_path: Path) -> None:
    scorer = ReferenceScorer(
        "answer.txt",
        reference="",
        checks=[
            KeyPointCoverage(points=["cache invalidation", "content digest", "atomic transaction"])
        ],
    )
    score = scorer.score(
        _context(tmp_path, b"The content digest is checked. Cache invalidation matters.")
    )
    result = _check(score, "key_point_coverage")
    assert score.normalized == pytest.approx(2 / 3)
    assert score.passed is False
    assert result["covered"] == ["cache invalidation", "content digest"]
    assert result["missed"] == ["atomic transaction"]
    assert result["evidence"] == {
        "cache invalidation": "cache invalidation",
        "content digest": "content digest",
    }


def test_key_point_coverage_supports_per_point_threshold_and_case_mode(tmp_path: Path) -> None:
    scorer = ReferenceScorer(
        "answer.txt",
        reference="",
        checks=[KeyPointCoverage(points=["cache efficiency"], threshold=0.5, case_sensitive=True)],
    )
    score = scorer.score(_context(tmp_path, b"cache efficiencies"))
    result = _check(score, "key_point_coverage")
    assert score.passed is True
    assert result["coverage"]["cache efficiency"] == pytest.approx(0.5)
    assert result["covered"] == ["cache efficiency"]

    strict = ReferenceScorer(
        "answer.txt",
        reference="",
        checks=[KeyPointCoverage(points=["cache efficiency"], threshold=0.6)],
    )
    assert strict.score(_context(tmp_path, b"cache efficiency")).normalized == 1.0
    assert strict.score(_context(tmp_path, b"cache")).normalized == 0.5


def test_reference_rejects_invalid_and_empty_check_configuration() -> None:
    with pytest.raises(ScorerError, match="at least one check"):
        ReferenceScorer("answer.txt", reference="text", checks=[])
    with pytest.raises(ValueError, match="points"):
        KeyPointCoverage(points=[])
    with pytest.raises(ValueError, match="threshold"):
        Similarity(threshold=1.1)
    with pytest.raises(ValueError, match="threshold"):
        KeyPointCoverage(points=["a"], threshold=-0.1)


def test_unreadable_text_raises_and_score_is_framework_compatible(tmp_path: Path) -> None:
    scorer = ReferenceScorer(
        "answer.txt",
        reference="answer",
        checks=[Similarity(), KeyPointCoverage(points=["answer"])],
        scorer_id="reference-v2",
        version="2",
    )
    context = _context(tmp_path, b"answer")
    score = scorer.score(context)
    assert validate_score(scorer, context, score) is score
    assert scorer.score(context) == score
    assert score.scorer_id == "reference-v2"

    invalid = _context(tmp_path, b"\xff")
    with pytest.raises(ScorerError, match="not valid UTF-8"):
        scorer.score(invalid)
