from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

from arena.cli_score import score_run
from arena.core.cas import ArtifactStore
from arena.core.config import validate_document
from arena.core.models import Artifact, Score
from arena.core.store import Store
from arena.judges.client import (
    HttpJudgeTransport,
    JudgeReply,
    JudgeRequest,
    JudgeTransportError,
)
from arena.judges.rubric import (
    MalformedVerdictError,
    Rubric,
    RubricError,
    RubricJudge,
    load_rubric,
)
from arena.scorers.base import ScorerContext, ScorerError, aggregate_scores
from arena.scorers.registry import ScorerRegistry

REPO = Path(__file__).resolve().parents[2]

RUBRIC_V2: dict[str, Any] = {
    "id": "blog-post",
    "version": 2,
    "kind": "content",
    "scale": "1-5",
    "criteria": [
        {"id": "accuracy", "weight": 3, "desc": "No claim contradicts the brief."},
        {"id": "structure", "weight": 1, "desc": "Clear hook, sections and CTA."},
    ],
    "anchors": {
        "accuracy": {1: "Contradicts the brief.", 5: "Every claim matches the brief."},
        "structure": {1: "No structure.", 5: "Hook, sections and CTA."},
    },
}


def verdict(accuracy: Any = 4, structure: Any = 2, **override: Any) -> str:
    body: dict[str, Any] = {
        "criteria": [
            {"id": "accuracy", "rationale": "Matches the brief.", "score": accuracy},
            {"id": "structure", "rationale": "Weak hook.", "score": structure},
        ]
    }
    body.update(override)
    return json.dumps(body)


class FakeTransport:
    """In-process stand-in for the gateway: returns canned replies and records requests."""

    def __init__(self, *replies: str | Exception, model: str | None = None) -> None:
        self.replies = list(replies) or [verdict()]
        self.model = model
        self.requests: list[JudgeRequest] = []

    def complete(self, request: JudgeRequest) -> JudgeReply:
        self.requests.append(request)
        reply = self.replies[min(len(self.requests), len(self.replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return JudgeReply(text=reply, model=self.model, tokens_in=120, tokens_out=40)


def write_rubric(tmp_path: Path, doc: dict[str, Any] | None = None, name: str = "r.yaml") -> Path:
    path = tmp_path / "rubrics" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc or RUBRIC_V2), encoding="utf-8")
    return path


def make_judge(
    tmp_path: Path,
    transport: FakeTransport,
    doc: dict[str, Any] | None = None,
    model: str = "mock/judge",
) -> RubricJudge:
    rubric = load_rubric(write_rubric(tmp_path, doc))
    return RubricJudge(rubric, transport, model=model, run_id="run-1")


def make_context(tmp_path: Path, answer: bytes | None = b"Launch post text.") -> ScorerContext:
    blobs = ArtifactStore(tmp_path / "artifacts")
    artifacts = {}
    if answer is not None:
        artifact = Artifact(
            sha256=blobs.put(answer), path="answer.md", mime="text/markdown", render_hint="markdown"
        )
        artifacts[artifact.path] = artifact
    return ScorerContext(
        trial_id="trial-1",
        artifacts=artifacts,
        blobs=blobs,
        config={"prompt": "Write a launch post for the Atlas feature."},
    )


def test_valid_verdict_becomes_a_score_with_rationale_per_criterion(tmp_path: Path) -> None:
    transport = FakeTransport(model="mock/judge-2026-10")
    judge = make_judge(tmp_path, transport)

    score = judge.score(make_context(tmp_path))

    # (4 * 3 + 2 * 1) / 4 = 3.5 on a 1-5 scale -> (3.5 - 1) / 4 = 0.625
    assert (score.scorer_id, score.scorer_version) == ("rubric-judge:blog-post", "2")
    assert score.value == pytest.approx(3.5)
    assert score.normalized == pytest.approx(0.625)
    assert score.passed is None
    assert "accuracy" in score.rationale
    assert "Matches the brief." in score.rationale
    assert "Weak hook." in score.rationale
    criteria = score.evidence["criteria"]
    assert [(c["id"], c["score"], c["weight"]) for c in criteria] == [
        ("accuracy", 4, 3),
        ("structure", 2, 1),
    ]
    assert criteria[1]["rationale"] == "Weak hook."


def test_judge_model_and_rubric_identity_are_reported_in_evidence(tmp_path: Path) -> None:
    judge = make_judge(tmp_path, FakeTransport(model="mock/judge-2026-10"))

    evidence = judge.score(make_context(tmp_path)).evidence

    assert evidence["judge_model"] == "mock/judge"
    assert evidence["judge_reply_model"] == "mock/judge-2026-10"
    assert evidence["rubric_id"] == "blog-post"
    assert evidence["rubric_version"] == "2"
    assert len(evidence["rubric_sha256"]) == 64
    assert evidence["usage"] == {"in": 120, "out": 40}


def test_call_carries_the_judge_token_purpose_and_the_material_to_judge(tmp_path: Path) -> None:
    transport = FakeTransport()
    make_judge(tmp_path, transport).score(make_context(tmp_path, b"My distinctive answer."))

    (request,) = transport.requests
    assert request.token == "arena-judge-run-1"
    assert request.purpose == "judge"
    assert request.model == "mock/judge"
    assert request.temperature == 0
    text = "\n".join(message.content for message in request.messages)
    assert "My distinctive answer." in text
    assert "Write a launch post for the Atlas feature." in text
    assert "No claim contradicts the brief." in text
    assert "Every claim matches the brief." in text


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        ("", "not JSON"),
        ("I think it is good.", "not JSON"),
        ("[1, 2]", "JSON object"),
        ('{"criteria": "great"}', "criteria"),
        ('{"criteria": []}', "missing criteria"),
        (verdict(extra="field"), "unexpected"),
        (
            json.dumps({"criteria": [{"id": "accuracy", "rationale": "ok", "score": 4}]}),
            r"missing criteria \['structure'\]",
        ),
        (
            json.dumps(
                {
                    "criteria": [
                        {"id": "accuracy", "rationale": "ok", "score": 4},
                        {"id": "structure", "rationale": "ok", "score": 4},
                        {"id": "voice", "rationale": "ok", "score": 4},
                    ]
                }
            ),
            r"unknown criteria \['voice'\]",
        ),
        (
            json.dumps(
                {
                    "criteria": [
                        {"id": "accuracy", "rationale": "ok", "score": 4},
                        {"id": "accuracy", "rationale": "again", "score": 5},
                        {"id": "structure", "rationale": "ok", "score": 4},
                    ]
                }
            ),
            "duplicate",
        ),
        (verdict(accuracy=6), "outside the scale 1-5"),
        (verdict(accuracy=0), "outside the scale 1-5"),
        (verdict(accuracy=4.5), "integer"),
        (verdict(accuracy="4"), "integer"),
        (verdict(accuracy=True), "integer"),
        (verdict(accuracy=None), "integer"),
        (
            json.dumps(
                {
                    "criteria": [
                        {"id": "accuracy", "rationale": "  ", "score": 4},
                        {"id": "structure", "rationale": "ok", "score": 4},
                    ]
                }
            ),
            "rationale",
        ),
        (
            json.dumps(
                {
                    "criteria": [
                        {"id": "accuracy", "score": 4},
                        {"id": "structure", "rationale": "ok", "score": 4},
                    ]
                }
            ),
            "rationale",
        ),
    ],
)
def test_malformed_verdict_is_rejected_with_a_clear_error(
    tmp_path: Path, reply: str, message: str
) -> None:
    judge = make_judge(tmp_path, FakeTransport(reply))

    with pytest.raises(MalformedVerdictError, match=message) as error:
        judge.score(make_context(tmp_path))

    assert "trial-1" in str(error.value)
    assert isinstance(error.value, ScorerError)


def test_a_json_code_fence_around_the_verdict_is_accepted(tmp_path: Path) -> None:
    judge = make_judge(tmp_path, FakeTransport(f"```json\n{verdict()}\n```"))

    assert judge.score(make_context(tmp_path)).normalized == pytest.approx(0.625)


def test_prose_around_the_verdict_is_not_guessed_at(tmp_path: Path) -> None:
    judge = make_judge(tmp_path, FakeTransport(f"Here is my verdict: {verdict()}"))

    with pytest.raises(MalformedVerdictError, match="not JSON"):
        judge.score(make_context(tmp_path))


def test_a_malformed_verdict_writes_no_score_and_keeps_existing_ones(tmp_path: Path) -> None:
    store, blobs, index = seed(tmp_path)
    with store:
        judge = make_judge(tmp_path, FakeTransport("not json"))
        registry = ScorerRegistry([judge])
        with pytest.raises(MalformedVerdictError):
            score_run(
                "run-1", store, blobs, index, registry, scorer_refs=["rubric-judge:blog-post"]
            )
        assert store.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 0


def test_transport_failure_is_an_error_not_a_score(tmp_path: Path) -> None:
    transport = FakeTransport(JudgeTransportError("HTTP 429 from the judge endpoint"))
    judge = make_judge(tmp_path, transport)

    with pytest.raises(JudgeTransportError, match="429"):
        judge.score(make_context(tmp_path))

    assert len(transport.requests) == 1  # no retry, no other model


def test_artifact_that_is_missing_or_not_text_is_an_error_and_never_judged(
    tmp_path: Path,
) -> None:
    transport = FakeTransport()
    judge = make_judge(tmp_path, transport)

    with pytest.raises(ScorerError, match=r"answer.md"):
        judge.score(make_context(tmp_path, answer=None))
    with pytest.raises(ScorerError, match="UTF-8"):
        judge.score(make_context(tmp_path, answer=b"\xff\xfe\x00bad"))

    assert transport.requests == []


class Index:
    def __init__(self, by_trial: dict[str, Sequence[Artifact]]) -> None:
        self.by_trial = by_trial

    def for_trial(self, trial_id: str) -> Sequence[Artifact]:
        return self.by_trial.get(trial_id, ())


def seed(tmp_path: Path) -> tuple[Store, ArtifactStore, Index]:
    store = Store(tmp_path / "arena.db")
    blobs = ArtifactStore(tmp_path / "artifacts")
    store.execute(
        "INSERT INTO runs (id, config_json, status) VALUES (?, ?, ?)", ("run-1", "{}", "finished")
    )
    store.execute(
        "INSERT INTO trials (id, run_id, contestant_id, task_id, status) VALUES (?, ?, ?, ?, ?)",
        ("trial-1", "run-1", "contestant", "task-1", "succeeded"),
    )
    artifact = Artifact(
        sha256=blobs.put(b"Launch post text."),
        path="answer.md",
        mime="text/markdown",
        render_hint="markdown",
    )
    return store, blobs, Index({"trial-1": [artifact]})


def test_a_new_rubric_version_is_a_separate_score_series(tmp_path: Path) -> None:
    store, blobs, index = seed(tmp_path)
    v3_doc = {**RUBRIC_V2, "version": 3}
    v2 = RubricJudge(
        load_rubric(write_rubric(tmp_path, RUBRIC_V2, "v2.yaml")),
        FakeTransport(verdict(4, 2)),
        model="mock/judge",
        run_id="run-1",
    )
    v3 = RubricJudge(
        load_rubric(write_rubric(tmp_path, v3_doc, "v3.yaml")),
        FakeTransport(verdict(5, 5)),
        model="mock/judge",
        run_id="run-1",
    )
    registry = ScorerRegistry([v2, v3])
    with store:
        for ref in ("rubric-judge:blog-post@2", "rubric-judge:blog-post@3"):
            score_run("run-1", store, blobs, index, registry, scorer_refs=[ref])
        rows = store.execute(
            "SELECT scorer_id, scorer_version, normalized FROM scores ORDER BY scorer_version"
        ).fetchall()
        assert [(r["scorer_id"], r["scorer_version"]) for r in rows] == [
            ("rubric-judge:blog-post", "2"),
            ("rubric-judge:blog-post", "3"),
        ]
        assert [r["normalized"] for r in rows] == [pytest.approx(0.625), pytest.approx(1.0)]
        scores = [
            Score(
                trial_id="trial-1",
                scorer_id=r["scorer_id"],
                scorer_version=r["scorer_version"],
                value=0,
                normalized=r["normalized"],
            )
            for r in rows
        ]
    with pytest.raises(ScorerError, match="mixed scorer versions"):
        aggregate_scores(scores)


def test_rescoring_the_same_rubric_version_replaces_its_row(tmp_path: Path) -> None:
    store, blobs, index = seed(tmp_path)
    judge = make_judge(tmp_path, FakeTransport(verdict(4, 2), verdict(5, 5)))
    registry = ScorerRegistry([judge])
    with store:
        for _ in range(2):
            score_run(
                "run-1", store, blobs, index, registry, scorer_refs=["rubric-judge:blog-post"]
            )
        rows = store.execute("SELECT normalized FROM scores").fetchall()
    assert [r["normalized"] for r in rows] == [pytest.approx(1.0)]


def test_editing_a_rubric_without_a_version_bump_changes_the_evidence_hash(
    tmp_path: Path,
) -> None:
    first = load_rubric(write_rubric(tmp_path, RUBRIC_V2, "a.yaml"))
    edited = {
        **RUBRIC_V2,
        "criteria": [{**RUBRIC_V2["criteria"][0], "weight": 1}, RUBRIC_V2["criteria"][1]],
    }
    second = load_rubric(write_rubric(tmp_path, edited, "b.yaml"))

    assert (first.version, second.version) == ("2", "2")
    assert first.sha256 != second.sha256


def test_shipped_rubrics_load_and_pass_arena_validate() -> None:
    paths = sorted((REPO / "rubrics").glob("*.yaml"))
    assert paths, "no rubrics shipped"
    for path in paths:
        rubric = load_rubric(path)
        assert isinstance(rubric, Rubric)
        assert validate_document(path, yaml.safe_load(path.read_text())) == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"criteria": []}, "criteria"),
        ({"criteria": [RUBRIC_V2["criteria"][0], RUBRIC_V2["criteria"][0]]}, "duplicate"),
        ({"criteria": [{"id": "a", "weight": 0, "desc": "x"}]}, "weight"),
        ({"criteria": [{"id": "a", "weight": -1, "desc": "x"}]}, "weight"),
        ({"scale": "five"}, "scale"),
        ({"scale": "5-1"}, "scale"),
        ({"anchors": {"voice": {1: "x"}}}, "unknown criteria"),
        ({"anchors": {"accuracy": {1: "x"}}}, "missing anchors"),
        (
            {
                "anchors": {
                    "accuracy": {9: "x"},
                    "structure": RUBRIC_V2["anchors"]["structure"],
                }
            },
            "outside the scale",
        ),
        ({"version": ""}, "version"),
        ({"surprise": 1}, "surprise"),
    ],
)
def test_invalid_rubric_is_rejected(tmp_path: Path, change: dict[str, Any], message: str) -> None:
    path = write_rubric(tmp_path, {**RUBRIC_V2, **change})

    with pytest.raises(RubricError, match=message):
        load_rubric(path)


def test_unreadable_or_non_mapping_rubric_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(RubricError, match="cannot read"):
        load_rubric(tmp_path / "missing.yaml")
    bad = tmp_path / "list.yaml"
    bad.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(RubricError, match="mapping"):
        load_rubric(bad)
    broken = tmp_path / "broken.yaml"
    broken.write_text("a: [unclosed\n", encoding="utf-8")
    with pytest.raises(RubricError, match="YAML"):
        load_rubric(broken)


@contextmanager
def serve(status: int, body: bytes, content_type: str = "application/json") -> Iterator[Any]:
    seen: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            seen["path"] = self.path
            seen["headers"] = dict(self.headers)
            seen["body"] = json.loads(self.rfile.read(length))
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    seen["url"] = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(**override: Any) -> JudgeRequest:
    from arena.judges.client import judge_request

    return judge_request(
        "run-9",
        "mock/judge",
        system="Be strict.",
        user="Judge this.",
        **override,
    )


def test_http_transport_speaks_openai_chat_with_the_bearer_token() -> None:
    reply = {
        "model": "judge-x",
        "choices": [{"message": {"role": "assistant", "content": "the verdict"}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }
    with serve(200, json.dumps(reply).encode()) as seen:
        result = HttpJudgeTransport(seen["url"], protocol="openai").complete(request())

    assert seen["path"] == "/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer arena-judge-run-9"
    assert seen["body"]["model"] == "mock/judge"
    assert seen["body"]["messages"] == [
        {"role": "system", "content": "Be strict."},
        {"role": "user", "content": "Judge this."},
    ]
    assert (result.text, result.model, result.tokens_in, result.tokens_out) == (
        "the verdict",
        "judge-x",
        11,
        7,
    )


def test_http_transport_speaks_anthropic_messages_with_the_bearer_token() -> None:
    reply = {
        "model": "judge-y",
        "content": [{"type": "text", "text": "the "}, {"type": "text", "text": "verdict"}],
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    with serve(200, json.dumps(reply).encode()) as seen:
        result = HttpJudgeTransport(seen["url"], protocol="anthropic").complete(request())

    assert seen["path"] == "/v1/messages"
    assert seen["headers"]["Authorization"] == "Bearer arena-judge-run-9"
    assert seen["body"]["system"] == "Be strict."
    assert seen["body"]["messages"] == [{"role": "user", "content": "Judge this."}]
    assert (result.text, result.tokens_in, result.tokens_out) == ("the verdict", 5, 3)


@pytest.mark.parametrize(
    ("status", "body", "content_type", "message"),
    [
        (429, b'{"error": "rate limited"}', "application/json", "HTTP 429"),
        (200, b"<html>login</html>", "text/html", "not JSON"),
        (200, b"", "application/json", "not JSON"),
        (200, b'{"choices": []}', "application/json", "no completion"),
        (200, b'{"choices": [{"message": {"content": null}}]}', "application/json", "no text"),
    ],
)
def test_http_transport_classifies_failures(
    status: int, body: bytes, content_type: str, message: str
) -> None:
    with (
        serve(status, body, content_type) as seen,
        pytest.raises(JudgeTransportError, match=message),
    ):
        HttpJudgeTransport(seen["url"], protocol="openai").complete(request())


def test_http_transport_reports_an_unreachable_endpoint() -> None:
    with serve(200, b"{}") as seen:
        url = seen["url"]
    with pytest.raises(JudgeTransportError, match="cannot reach"):
        HttpJudgeTransport(url, protocol="openai", timeout=2).complete(request())
