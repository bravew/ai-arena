from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from arena.core.cas import ArtifactStore
from arena.core.models import Artifact
from arena.judges.client import JudgeReply, JudgeRequest
from arena.judges.pairwise import MalformedPairwiseVerdictError, PairwiseJudge
from arena.judges.visual import VisualJudge
from arena.scorers.base import ScorerContext


class FakeTransport:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.requests: list[JudgeRequest] = []
        self.protocol = "openai"

    def complete(self, request: JudgeRequest) -> JudgeReply:
        self.requests.append(request)
        return JudgeReply(text=self.replies.pop(0), model="fake-judge", tokens_in=11, tokens_out=7)


def context(
    tmp_path: Path, trial_id: str, files: dict[str, bytes], config: dict[str, Any] | None = None
) -> ScorerContext:
    blobs = ArtifactStore(tmp_path / trial_id)
    artifacts: dict[str, Artifact] = {}
    for path, data in files.items():
        artifacts[path] = Artifact(
            sha256=blobs.put(data),
            path=path,
            mime="image/png" if path.endswith(".png") else "text/plain",
            render_hint="image" if path.endswith(".png") else "markdown",
        )
    return ScorerContext(trial_id=trial_id, artifacts=artifacts, blobs=blobs, config=config or {})


def verdict(winner: str, rationale: str) -> str:
    return json.dumps({"winner": winner, "rationale": rationale})


def test_position_preference_on_both_orders_is_tie_and_records_bias(tmp_path: Path) -> None:
    transport = FakeTransport(
        verdict("a", "First position is clearer."), verdict("a", "First position is clearer.")
    )
    judge = PairwiseJudge(transport, model="judge/model", run_id="run-7")

    result = judge.judge(
        context(tmp_path, "trial-a", {"answer.md": b"A answer"}),
        context(tmp_path, "trial-b", {"answer.md": b"B answer"}),
        task_id="task-1",
    )

    assert result.trial_a_id == "trial-a"
    assert result.trial_b_id == "trial-b"
    assert result.task_id == "task-1"
    assert result.winner == "tie"
    assert result.position_bias is True
    assert result.evidence["first_order"]["winner"] == "a"
    assert result.evidence["swapped_order"]["winner"] == "a"
    assert len(transport.requests) == 2
    assert "A answer" in transport.requests[0].messages[1].content
    assert transport.requests[0].token == "arena-judge-run-7"


def test_consistent_trial_winner_maps_across_swapped_positions(tmp_path: Path) -> None:
    transport = FakeTransport(verdict("a", "A is stronger."), verdict("b", "A remains stronger."))
    result = PairwiseJudge(transport, model="judge/model", run_id="run-8").judge(
        context(tmp_path, "trial-a", {"answer.md": b"A"}),
        context(tmp_path, "trial-b", {"answer.md": b"B"}),
        task_id="task-2",
    )

    assert result.winner == "trial_a"
    assert result.position_bias is False


def test_pairwise_verdict_requires_winner_and_nonempty_rationale(tmp_path: Path) -> None:
    transport = FakeTransport('{"winner":"a","rationale":" "}')
    judge = PairwiseJudge(transport, model="judge/model", run_id="run-9")

    with pytest.raises(MalformedPairwiseVerdictError, match="rationale"):
        judge.judge(
            context(tmp_path, "trial-a", {"answer.md": b"A"}),
            context(tmp_path, "trial-b", {"answer.md": b"B"}),
            task_id="task-3",
        )


def test_visual_judge_sends_anthropic_image_blocks(tmp_path: Path) -> None:
    transport = FakeTransport(verdict("a", "A is clearer."), verdict("b", "A remains clearer."))
    transport.protocol = "anthropic"
    left = context(
        tmp_path,
        "visual-a",
        {"shot.png": b"PNG A"},
        {"screenshot_path": "shot.png"},
    )
    right = context(
        tmp_path,
        "visual-b",
        {"shot.png": b"PNG B"},
        {"screenshot_path": "shot.png"},
    )

    VisualJudge(transport, model="vision/model", run_id="run-11").judge(
        left, right, task_id="web-2"
    )

    content = transport.requests[0].messages[1].content
    assert isinstance(content, list)
    image_parts = [
        part for part in content if isinstance(part, dict) and part.get("type") == "image"
    ]
    assert image_parts
    source = image_parts[0].get("source")
    assert isinstance(source, dict)
    assert source.get("type") == "base64"


def test_visual_judge_sends_screenshots_and_keeps_paths_in_evidence(tmp_path: Path) -> None:
    transport = FakeTransport(
        verdict("a", "A has clearer hierarchy."), verdict("b", "A is still stronger.")
    )
    left = context(
        tmp_path,
        "visual-a",
        {"screenshots/desktop.png": b"PNG A"},
        {"screenshot_path": "screenshots/desktop.png"},
    )
    right = context(
        tmp_path,
        "visual-b",
        {"screenshots/desktop.png": b"PNG B"},
        {"screenshot_path": "screenshots/desktop.png"},
    )
    judge = VisualJudge(transport, model="vision/model", run_id="run-10")

    result = judge.judge(left, right, task_id="web-1")

    assert result.winner == "trial_a"
    assert result.evidence["trial_a_screenshot"] == "screenshots/desktop.png"
    assert result.evidence["trial_b_screenshot"] == "screenshots/desktop.png"
    first_user_content = transport.requests[0].messages[1].content
    assert isinstance(first_user_content, list)
    assert any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in first_user_content
    )
    assert "design rubric" in transport.requests[0].messages[0].content.lower()
