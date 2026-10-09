from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from arena.core.cas import ArtifactStore
from arena.core.models import Artifact
from arena.scorers.base import ScorerContext, ScorerError, validate_score
from arena.scorers.constraint import (
    ConstraintScorer,
    ForbiddenContent,
    JsonSchemaCheck,
    JsonValid,
    MarkdownValid,
    RequiredKeywords,
    RequiredSections,
    WordCount,
)
from arena.scorers.registry import ScorerRegistry


def _context(tmp_path: Path, data: bytes, path: str = "answer.md") -> ScorerContext:
    blobs = ArtifactStore(tmp_path / "artifacts")
    artifact = Artifact(sha256=blobs.put(data), path=path, mime="text/markdown", render_hint="code")
    return ScorerContext(trial_id="t1", artifacts={path: artifact}, blobs=blobs)


def _check_results(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {result["check"]: result for result in evidence["checks"]}


def test_all_checks_pass_scores_one(tmp_path: Path) -> None:
    text = "# Summary\n\nCaching speeds up repeated reads.\n\n## Risks\n\nStale data."
    scorer = ConstraintScorer(
        "answer.md",
        [
            WordCount(min=5, max=50),
            RequiredSections(headings=["Summary", "Risks"]),
            RequiredKeywords(keywords=["caching", "stale data"]),
            ForbiddenContent(terms=["lorem ipsum"]),
            MarkdownValid(),
        ],
    )
    score = scorer.score(_context(tmp_path, text.encode()))
    assert (score.value, score.normalized, score.passed) == (5, 1.0, True)
    assert score.scorer_id == "constraint"
    assert {r["passed"] for r in score.evidence["checks"]} == {True}


def test_partial_failure_reports_fraction_and_names_the_failed_checks(tmp_path: Path) -> None:
    scorer = ConstraintScorer(
        "answer.md",
        [WordCount(max=3), RequiredKeywords(keywords=["cache"]), JsonValid()],
    )
    score = scorer.score(_context(tmp_path, b"one two three four five"))
    assert score.passed is False
    assert score.normalized == pytest.approx(0.0)
    results = _check_results(score.evidence)
    assert results["word_count"]["words"] == 5
    assert results["required_keywords"]["missing"] == ["cache"]
    assert "word_count" in score.rationale and "json_valid" in score.rationale

    one_of_two = ConstraintScorer("answer.md", [WordCount(max=3), WordCount(min=1)])
    assert one_of_two.score(_context(tmp_path, b"a b c d")).normalized == 0.5


def test_word_count_bounds_are_inclusive_and_empty_text_has_zero_words(tmp_path: Path) -> None:
    scorer = ConstraintScorer("answer.md", [WordCount(min=2, max=3)])
    for text, expected in [
        (b"one", False),
        (b"one two", True),
        (b"a b c", True),
        (b"a b c d", False),
    ]:
        assert scorer.score(_context(tmp_path, text)).passed is expected
    empty = ConstraintScorer("answer.md", [WordCount(max=0)]).score(_context(tmp_path, b""))
    assert empty.passed is True
    assert _check_results(empty.evidence)["word_count"]["words"] == 0


def test_word_count_treats_contractions_and_hyphenated_words_as_one_word(tmp_path: Path) -> None:
    score = ConstraintScorer("answer.md", [WordCount(min=5, max=5)]).score(
        _context(tmp_path, b"It doesn't use well-known caches.")
    )
    assert score.passed is True


def test_required_sections_match_headings_not_body_text_or_code_fences(tmp_path: Path) -> None:
    text = "# Overview\n\nRisks are discussed here.\n\n```\n## Risks\n```\n\n### Next steps ###\n"
    scorer = ConstraintScorer(
        "answer.md",
        [RequiredSections(headings=["overview", "Risks", "next   steps"])],
    )
    score = scorer.score(_context(tmp_path, text.encode()))
    result = _check_results(score.evidence)["required_sections"]
    assert result["missing"] == ["Risks"]
    assert result["passed"] is False


def test_required_sections_can_require_a_heading_level(tmp_path: Path) -> None:
    scorer = ConstraintScorer("answer.md", [RequiredSections(headings=["Risks"], level=2)])
    assert scorer.score(_context(tmp_path, b"# Risks\n")).passed is False
    assert scorer.score(_context(tmp_path, b"## Risks\n")).passed is True


def test_keywords_match_whole_words_case_insensitively_by_default(tmp_path: Path) -> None:
    scorer = ConstraintScorer("answer.md", [RequiredKeywords(keywords=["cat", "Time Out"])])
    score = scorer.score(_context(tmp_path, b"Concatenate the TIME OUT value."))
    result = _check_results(score.evidence)["required_keywords"]
    assert result["found"] == ["Time Out"]
    assert result["missing"] == ["cat"]

    exact = ConstraintScorer("answer.md", [RequiredKeywords(keywords=["API"], case_sensitive=True)])
    assert exact.score(_context(tmp_path, b"the api")).passed is False
    assert exact.score(_context(tmp_path, b"the API")).passed is True


def test_forbidden_content_reports_each_hit_and_does_not_match_inside_words(
    tmp_path: Path,
) -> None:
    scorer = ConstraintScorer(
        "answer.md",
        [ForbiddenContent(terms=["ass"], patterns=[r"sk-[A-Za-z0-9]{8,}"])],
    )
    clean = scorer.score(_context(tmp_path, b"A class of problems."))
    assert clean.passed is True

    dirty = scorer.score(_context(tmp_path, b"Use ass here. key=sk-abcdefgh12 and sk-abcdefgh12"))
    result = _check_results(dirty.evidence)["forbidden_content"]
    assert dirty.passed is False
    hits = {hit["rule"]: hit for hit in result["hits"]}
    assert hits["ass"]["count"] == 1
    assert hits[r"sk-[A-Za-z0-9]{8,}"]["count"] == 2
    assert hits[r"sk-[A-Za-z0-9]{8,}"]["matches"] == ["sk-abcdefgh12", "sk-abcdefgh12"]


def test_json_valid_and_markdown_valid_accept_and_reject(tmp_path: Path) -> None:
    json_scorer = ConstraintScorer("answer.md", [JsonValid()])
    assert json_scorer.score(_context(tmp_path, b'{"a": [1, 2]}')).passed is True
    for bad in (b"{'a': 1}", b"", b"NaN", b'{"a": 1} trailing'):
        score = json_scorer.score(_context(tmp_path, bad))
        assert score.passed is False, bad
        assert _check_results(score.evidence)["json_valid"]["error"]

    md_scorer = ConstraintScorer("answer.md", [MarkdownValid()])
    assert md_scorer.score(_context(tmp_path, b"# A\n\n## B\n\n```py\nx\n```\n")).passed is True
    problems = {
        b"# A\n```\nunclosed": "unclosed code fence",
        b"#NoSpace\n": "heading needs a space",
        b"# A\n\n### C\n": "skips from level 1 to 3",
        b"   \n": "empty",
    }
    for text, fragment in problems.items():
        score = md_scorer.score(_context(tmp_path, text))
        assert score.passed is False, text
        found = _check_results(score.evidence)["markdown_valid"]["problems"]
        assert any(fragment in problem for problem in found), (text, found)


def test_json_schema_check_lists_violations_with_paths(tmp_path: Path) -> None:
    schema = {
        "type": "object",
        "required": ["name", "age"],
        "properties": {"name": {"type": "string"}, "age": {"type": "integer", "minimum": 0}},
    }
    scorer = ConstraintScorer("answer.md", [JsonSchemaCheck(json_schema=schema)])
    ok = scorer.score(_context(tmp_path, json.dumps({"name": "Ada", "age": 36}).encode()))
    assert ok.passed is True

    bad = scorer.score(_context(tmp_path, json.dumps({"age": -1}).encode()))
    result = _check_results(bad.evidence)["json_schema"]
    assert bad.passed is False
    assert {error["path"] for error in result["errors"]} == {"$", "$.age"}

    not_json = scorer.score(_context(tmp_path, b"nope"))
    assert not_json.passed is False
    assert (
        "not valid JSON" in _check_results(not_json.evidence)["json_schema"]["errors"][0]["message"]
    )


def test_remote_schema_references_are_an_error_not_a_network_call(tmp_path: Path) -> None:
    scorer = ConstraintScorer(
        "answer.md", [JsonSchemaCheck(json_schema={"$ref": "https://example.invalid/s.json"})]
    )
    with pytest.raises(ScorerError, match="cannot resolve"):
        scorer.score(_context(tmp_path, b"{}"))


def test_invalid_configuration_is_rejected_when_the_scorer_is_built() -> None:
    with pytest.raises(ScorerError, match="at least one check"):
        ConstraintScorer("answer.md", [])
    with pytest.raises(ScorerError, match="invalid JSON Schema"):
        ConstraintScorer("answer.md", [JsonSchemaCheck(json_schema={"type": "nonsense"})])
    with pytest.raises(ScorerError, match="invalid pattern"):
        ConstraintScorer("answer.md", [ForbiddenContent(patterns=["("])])
    with pytest.raises(ValueError, match="min"):
        WordCount()
    with pytest.raises(ValueError, match="min"):
        WordCount(min=5, max=2)
    with pytest.raises(ValueError):
        ForbiddenContent()
    with pytest.raises(ValueError):
        RequiredKeywords(keywords=[])


def test_unreadable_input_is_an_error_not_a_failed_check(tmp_path: Path) -> None:
    scorer = ConstraintScorer("answer.md", [ForbiddenContent(terms=["secret"])])
    with pytest.raises(ScorerError, match="not valid UTF-8"):
        scorer.score(_context(tmp_path, b"\xff\xfe\x00bad"))
    with pytest.raises(ScorerError, match="no artifact"):
        scorer.score(_context(tmp_path, b"fine", path="other.md"))


def test_scoring_is_deterministic_and_fits_the_framework(tmp_path: Path) -> None:
    scorer = ConstraintScorer(
        "answer.md",
        [WordCount(min=1), RequiredKeywords(keywords=["x"])],
        scorer_id="answer-format",
        version="3",
    )
    context = _context(tmp_path, b"x marks the spot")
    first, second = scorer.score(context), scorer.score(context)
    assert first == second
    assert json.dumps(first.evidence, sort_keys=True)  # evidence is plain JSON
    assert validate_score(scorer, context, first) is first
    assert ScorerRegistry([scorer]).get("answer-format@3") is scorer
