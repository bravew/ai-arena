"""Constraint scorer: deterministic content and format checks on one text artifact.

A trial passes only if every configured check passes; the normalized score is the share of
checks that passed. An artifact that cannot be read or decoded is an error, never a failed
check, because "unknown" must not be recorded as a real result.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Annotated, Any, Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from referencing import Registry
from referencing.exceptions import Unresolvable

from arena.core.models import Score
from arena.scorers.base import ScorerContext, ScorerError

# Evidence lists are capped so one pathological answer cannot bloat the `scores` row.
MAX_EVIDENCE_ITEMS = 10
_WORD = re.compile(r"\w+(?:['\u2019-]\w+)*")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r" {0,3}(#+)(.*)$")
_CLOSING_HASHES = re.compile(r"(?:^|\s+)#+\s*$")


class _Check(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WordCount(_Check):
    """Word count within inclusive bounds. A word is a run of letters/digits with inner ' or -."""

    kind: Literal["word_count"] = "word_count"
    min: int | None = Field(default=None, ge=0)
    max: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _bounds(self) -> WordCount:
        if self.min is None and self.max is None:
            raise ValueError("word_count needs min, max or both")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("word_count min must not exceed max")
        return self


class RequiredSections(_Check):
    """Markdown ATX headings that must exist, compared ignoring case and spacing."""

    kind: Literal["required_sections"] = "required_sections"
    headings: list[str] = Field(min_length=1)
    level: int | None = Field(default=None, ge=1, le=6)


class RequiredKeywords(_Check):
    """Words or phrases that must appear, matched as whole words."""

    kind: Literal["required_keywords"] = "required_keywords"
    keywords: list[str] = Field(min_length=1)
    case_sensitive: bool = False


class ForbiddenContent(_Check):
    """Whole-word terms and regular expressions that must not appear."""

    kind: Literal["forbidden_content"] = "forbidden_content"
    terms: list[str] = []
    patterns: list[str] = []
    case_sensitive: bool = False

    @model_validator(mode="after")
    def _non_empty(self) -> ForbiddenContent:
        if not self.terms and not self.patterns:
            raise ValueError("forbidden_content needs at least one term or pattern")
        return self


class JsonValid(_Check):
    """The artifact parses as strict JSON (no NaN or Infinity, no trailing text)."""

    kind: Literal["json_valid"] = "json_valid"


class JsonSchemaCheck(_Check):
    """The artifact is JSON that satisfies a JSON Schema (draft 2020-12, no remote refs)."""

    kind: Literal["json_schema"] = "json_schema"
    json_schema: dict[str, Any]


class MarkdownValid(_Check):
    """Structural Markdown checks: non-empty, closed code fences, well-formed headings."""

    kind: Literal["markdown_valid"] = "markdown_valid"


Check = Annotated[
    WordCount
    | RequiredSections
    | RequiredKeywords
    | ForbiddenContent
    | JsonValid
    | JsonSchemaCheck
    | MarkdownValid,
    Field(discriminator="kind"),
]


class ConstraintScorer:
    """Run ``checks`` against the text artifact at ``path``."""

    def __init__(
        self,
        path: str,
        checks: Sequence[Check],
        *,
        scorer_id: str = "constraint",
        version: str = "1",
    ) -> None:
        if not checks:
            raise ScorerError("constraint scorer needs at least one check")
        self.id = scorer_id
        self.version = version
        self.path = path
        self.checks = tuple(checks)
        self._validators = {
            index: _schema_validator(check)
            for index, check in enumerate(self.checks)
            if isinstance(check, JsonSchemaCheck)
        }
        self._patterns = {
            index: _compile_patterns(check)
            for index, check in enumerate(self.checks)
            if isinstance(check, ForbiddenContent)
        }

    def score(self, context: ScorerContext) -> Score:
        text = read_text(context, self.path)
        results = [self._run(index, check, text) for index, check in enumerate(self.checks)]
        passed_count = sum(1 for result in results if result["passed"])
        failed = [str(result["check"]) for result in results if not result["passed"]]
        return Score(
            trial_id=context.trial_id,
            scorer_id=self.id,
            scorer_version=self.version,
            value=passed_count,
            normalized=passed_count / len(results),
            passed=not failed,
            rationale=(
                f"{passed_count} of {len(results)} checks passed"
                + (f"; failed: {', '.join(failed)}" if failed else "")
            ),
            evidence={"path": self.path, "checks": results},
        )

    def _run(self, index: int, check: Check, text: str) -> dict[str, Any]:
        if isinstance(check, WordCount):
            return _word_count(check, text)
        if isinstance(check, RequiredSections):
            return _required_sections(check, text)
        if isinstance(check, RequiredKeywords):
            return _required_keywords(check, text)
        if isinstance(check, ForbiddenContent):
            return _forbidden_content(check, self._patterns[index], text)
        if isinstance(check, JsonValid):
            return _json_valid(text)
        if isinstance(check, JsonSchemaCheck):
            return _json_schema(self._validators[index], text)
        return _markdown_valid(text)


def read_text(context: ScorerContext, path: str) -> str:
    """Read an artifact as UTF-8 text; bytes that are not text are an error, not empty."""
    data = context.read(path)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScorerError(
            f"artifact {path!r} of trial {context.trial_id} is not valid UTF-8"
        ) from exc


def _word_count(check: WordCount, text: str) -> dict[str, Any]:
    words = len(_WORD.findall(text))
    ok = (check.min is None or words >= check.min) and (check.max is None or words <= check.max)
    return {"check": "word_count", "passed": ok, "words": words, "min": check.min, "max": check.max}


def _normalize_heading(text: str) -> str:
    return " ".join(text.split()).casefold()


def _scan_markdown(text: str) -> tuple[list[tuple[int, str]], list[str]]:
    """Return the ATX headings outside code fences and the structural problems found."""
    headings: list[tuple[int, str]] = []
    problems: list[str] = []
    fence: tuple[str, int, int] | None = None  # (character, length, opening line number)
    previous_level = 0
    for number, line in enumerate(text.splitlines(), start=1):
        match = _FENCE.match(line)
        if fence is not None:
            closes = match and match[1][0] == fence[0] and len(match[1]) >= fence[1]
            if closes and not line.strip().strip(fence[0]):
                fence = None
            continue
        if match:
            fence = (match[1][0], len(match[1]), number)
            continue
        heading = _HEADING.match(line)
        if heading is None:
            continue
        marks, rest = heading[1], heading[2]
        if len(marks) > 6:
            problems.append(f"line {number}: heading level above 6")
        elif rest and not rest[0].isspace():
            problems.append(f"line {number}: heading needs a space after '{marks}'")
        else:
            title = _CLOSING_HASHES.sub("", rest).strip()
            if not title:
                problems.append(f"line {number}: empty heading")
            else:
                level = len(marks)
                if previous_level and level > previous_level + 1:
                    problems.append(
                        f"line {number}: heading skips from level {previous_level} to {level}"
                    )
                previous_level = level
                headings.append((level, title))
    if fence is not None:
        problems.append(f"line {fence[2]}: unclosed code fence")
    return headings, problems


def _required_sections(check: RequiredSections, text: str) -> dict[str, Any]:
    headings, _ = _scan_markdown(text)
    present = {
        _normalize_heading(title)
        for level, title in headings
        if check.level is None or level == check.level
    }
    missing = [h for h in check.headings if _normalize_heading(h) not in present]
    return {
        "check": "required_sections",
        "passed": not missing,
        "missing": missing,
        "found": [h for h in check.headings if h not in missing],
        "level": check.level,
    }


def _flags(case_sensitive: bool) -> int:
    return 0 if case_sensitive else re.IGNORECASE


def _term_pattern(term: str, case_sensitive: bool) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(part) for part in term.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", _flags(case_sensitive))


def _required_keywords(check: RequiredKeywords, text: str) -> dict[str, Any]:
    found: list[str] = []
    missing: list[str] = []
    for keyword in check.keywords:
        if not keyword.strip():
            raise ScorerError("required keyword must not be blank")
        hit = _term_pattern(keyword, check.case_sensitive).search(text)
        (found if hit else missing).append(keyword)
    return {"check": "required_keywords", "passed": not missing, "found": found, "missing": missing}


def _compile_patterns(check: ForbiddenContent) -> list[tuple[str, re.Pattern[str]]]:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for term in check.terms:
        if not term.strip():
            raise ScorerError("forbidden term must not be blank")
        compiled.append((term, _term_pattern(term, check.case_sensitive)))
    for pattern in check.patterns:
        try:
            compiled.append((pattern, re.compile(pattern, _flags(check.case_sensitive))))
        except re.error as exc:
            raise ScorerError(f"invalid pattern {pattern!r}: {exc}") from exc
    return compiled


def _forbidden_content(
    check: ForbiddenContent, rules: list[tuple[str, re.Pattern[str]]], text: str
) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    for rule, pattern in rules:
        matches = [m.group(0) for m in pattern.finditer(text) if m.group(0)]
        if matches:
            hits.append(
                {"rule": rule, "count": len(matches), "matches": matches[:MAX_EVIDENCE_ITEMS]}
            )
    return {"check": "forbidden_content", "passed": not hits, "hits": hits}


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _parse_json(text: str) -> tuple[Any, str | None]:
    try:
        return json.loads(text, parse_constant=_reject_constant), None
    except ValueError as exc:
        return None, str(exc)


def _json_valid(text: str) -> dict[str, Any]:
    _, error = _parse_json(text)
    return {"check": "json_valid", "passed": error is None, "error": error}


def _schema_validator(check: JsonSchemaCheck) -> Any:
    try:
        Draft202012Validator.check_schema(check.json_schema)
    except SchemaError as exc:
        raise ScorerError(f"invalid JSON Schema: {exc.message}") from exc
    # An empty registry keeps jsonschema from fetching remote $refs over the network.
    return cast(Any, Draft202012Validator)(check.json_schema, registry=cast(Any, Registry)())


def _json_path(parts: Sequence[str | int]) -> str:
    return "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in parts)


def _json_schema(validator: Any, text: str) -> dict[str, Any]:
    document, error = _parse_json(text)
    if error is not None:
        return {
            "check": "json_schema",
            "passed": False,
            "error_count": 1,
            "errors": [{"path": "$", "message": f"not valid JSON: {error}"}],
        }
    try:
        found = sorted(
            (
                {"path": _json_path(list(e.absolute_path)), "message": e.message}
                for e in validator.iter_errors(document)
            ),
            key=lambda e: (e["path"], e["message"]),
        )
    except Unresolvable as exc:
        raise ScorerError(f"cannot resolve a reference in the JSON Schema: {exc}") from exc
    return {
        "check": "json_schema",
        "passed": not found,
        "error_count": len(found),
        "errors": found[:MAX_EVIDENCE_ITEMS],
    }


def _markdown_valid(text: str) -> dict[str, Any]:
    if not text.strip():
        return {"check": "markdown_valid", "passed": False, "problems": ["document is empty"]}
    _, problems = _scan_markdown(text)
    return {
        "check": "markdown_valid",
        "passed": not problems,
        "problems": problems[:MAX_EVIDENCE_ITEMS],
    }
