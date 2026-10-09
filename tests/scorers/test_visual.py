from __future__ import annotations

import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from arena.core.cas import ArtifactStore
from arena.core.models import Artifact
from arena.scorers.base import ScorerContext, ScorerError, validate_score
from arena.scorers.registry import ScorerRegistry
from arena.scorers.visual import (
    VIEWPORTS,
    CaptureError,
    PageCapture,
    Viewport,
    VisualScorer,
)

HTML = b"<!doctype html><title>demo</title><h1>Hello</h1><script>console.error('boom')</script>"


@dataclass
class Visit:
    url: str
    viewport: Viewport
    host_page: str
    frame_url: str
    frame_headers: Mapping[str, str]
    frame_body: bytes
    sibling_status: int


@dataclass
class FakeBrowser:
    """Stands in for Playwright and looks at what the scorer serves while it is 'open'."""

    errors: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    fail_on: str | None = None
    visits: list[Visit] = field(default_factory=list)

    def capture(self, url: str, viewport: Viewport) -> PageCapture:
        if viewport.name == self.fail_on:
            raise CaptureError(f"browser crashed at {viewport.name}")
        with urllib.request.urlopen(url) as response:
            host_page = response.read().decode()
        match = re.search(r'<iframe[^>]*src="([^"]+)"', host_page)
        assert match, host_page
        frame_url = match.group(1)
        with urllib.request.urlopen(frame_url) as response:
            headers = {k.lower(): v for k, v in response.headers.items()}
            body = response.read()
        sibling = urlsplit(frame_url)._replace(path="/other.html").geturl()
        try:
            urllib.request.urlopen(sibling)
            status = 200
        except urllib.error.HTTPError as exc:
            status = exc.code
        self.visits.append(Visit(url, viewport, host_page, frame_url, headers, body, status))
        return PageCapture(
            png=b"\x89PNG-" + viewport.name.encode(),
            console_errors=self.errors.get(viewport.name, ()),
        )


def _context(
    tmp_path: Path,
    html: bytes | None = HTML,
    *,
    trial_id: str = "t1",
    extra: tuple[Artifact, ...] = (),
) -> ScorerContext:
    blobs = ArtifactStore(tmp_path / "artifacts")
    artifacts = {a.path: a for a in extra}
    if html is not None:
        artifacts["index.html"] = Artifact(
            sha256=blobs.put(html), path="index.html", mime="text/html", render_hint="html-sandbox"
        )
    return ScorerContext(trial_id=trial_id, artifacts=artifacts, blobs=blobs)


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return parts.scheme, parts.hostname or "", parts.port


def test_captures_three_viewports_and_stores_screenshots_as_artifacts(tmp_path: Path) -> None:
    browser = FakeBrowser()
    context = _context(tmp_path)

    score = VisualScorer(browser).score(context)

    assert [v.viewport.name for v in browser.visits] == ["mobile", "tablet", "desktop"]
    assert [(v.viewport.width, v.viewport.height) for v in browser.visits] == [
        (375, 812),
        (768, 1024),
        (1440, 900),
    ]
    shots = score.evidence["screenshots"]
    assert [s["viewport"] for s in shots] == ["mobile", "tablet", "desktop"]
    for shot, viewport in zip(shots, VIEWPORTS, strict=True):
        artifact = Artifact.model_validate(shot["artifact"])
        assert artifact.render_hint == "image"
        assert artifact.mime == "image/png"
        assert artifact.path == f"screenshots/visual/{viewport.name}.png"
        assert context.blobs.get(artifact.sha256) == b"\x89PNG-" + viewport.name.encode()
        assert (shot["width"], shot["height"]) == (viewport.width, viewport.height)


def test_console_error_count_is_a_deterministic_check(tmp_path: Path) -> None:
    browser = FakeBrowser(
        errors={
            "mobile": ("Uncaught ReferenceError: x", "Failed to load resource"),
            "tablet": ("Uncaught ReferenceError: x",),
            "desktop": (),
        }
    )
    context = _context(tmp_path)

    first = VisualScorer(browser).score(context)
    second = VisualScorer(FakeBrowser(errors=browser.errors)).score(_context(tmp_path))

    assert first.value == 3  # occurrences across all viewports
    assert first.passed is False
    assert first.normalized == pytest.approx(0.25)
    assert first.evidence["console_errors"] == {
        "mobile": ["Uncaught ReferenceError: x", "Failed to load resource"],
        "tablet": ["Uncaught ReferenceError: x"],
        "desktop": [],
    }
    assert first == second


def test_clean_page_passes_with_full_score(tmp_path: Path) -> None:
    score = VisualScorer(FakeBrowser()).score(_context(tmp_path))
    assert (score.value, score.normalized, score.passed) == (0, 1.0, True)
    assert score.scorer_id == "visual"
    assert score.trial_id == "t1"


def test_artifact_runs_on_a_separate_origin_inside_a_sandboxed_iframe(tmp_path: Path) -> None:
    browser = FakeBrowser()
    VisualScorer(browser).score(_context(tmp_path))

    visit = browser.visits[0]
    assert _origin(visit.url) != _origin(visit.frame_url)
    assert re.search(r'<iframe[^>]*\bsandbox="allow-scripts"', visit.host_page)
    assert "allow-same-origin" not in visit.host_page
    assert visit.frame_body == HTML
    csp = visit.frame_headers["content-security-policy"]
    assert "sandbox allow-scripts" in csp
    assert "allow-same-origin" not in csp
    assert "connect-src 'none'" in csp
    assert visit.frame_headers["x-content-type-options"] == "nosniff"
    assert visit.frame_headers["cache-control"] == "no-store"
    assert visit.sibling_status == 404  # only the one artifact is served


def test_servers_stop_after_scoring(tmp_path: Path) -> None:
    browser = FakeBrowser()
    VisualScorer(browser).score(_context(tmp_path))
    for url in (browser.visits[0].url, browser.visits[0].frame_url):
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(url, timeout=2)


def test_servers_stop_when_the_browser_fails(tmp_path: Path) -> None:
    browser = FakeBrowser(fail_on="tablet")
    with pytest.raises(ScorerError, match="browser crashed at tablet"):
        VisualScorer(browser).score(_context(tmp_path))
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(browser.visits[0].url, timeout=2)


def test_browser_failure_stores_nothing(tmp_path: Path) -> None:
    context = _context(tmp_path)
    before = sorted(p.name for p in context.blobs.root.iterdir())
    with pytest.raises(ScorerError):
        VisualScorer(FakeBrowser(fail_on="desktop")).score(context)
    assert sorted(p.name for p in context.blobs.root.iterdir()) == before


def test_missing_html_artifact_scores_zero_and_fails(tmp_path: Path) -> None:
    browser = FakeBrowser()
    score = VisualScorer(browser).score(_context(tmp_path, html=None))
    assert (score.normalized, score.passed) == (0.0, False)
    assert "no HTML artifact" in score.rationale
    assert browser.visits == []


def test_several_html_artifacts_need_an_explicit_path(tmp_path: Path) -> None:
    blobs = ArtifactStore(tmp_path / "artifacts")
    other = Artifact(
        sha256=blobs.put(b"<p>two</p>"),
        path="two.html",
        mime="text/html",
        render_hint="html-sandbox",
    )
    context = _context(tmp_path, extra=(other,))
    with pytest.raises(ScorerError, match="more than one HTML artifact"):
        VisualScorer(FakeBrowser()).score(context)

    chosen = ScorerContext(
        trial_id=context.trial_id,
        artifacts=context.artifacts,
        blobs=context.blobs,
        config={"path": "two.html"},
    )
    browser = FakeBrowser()
    VisualScorer(browser).score(chosen)
    assert browser.visits[0].frame_body == b"<p>two</p>"


def test_explicit_path_that_does_not_exist_is_an_error(tmp_path: Path) -> None:
    context = _context(tmp_path)
    missing = ScorerContext(
        trial_id="t1",
        artifacts=context.artifacts,
        blobs=context.blobs,
        config={"path": "nope.html"},
    )
    with pytest.raises(ScorerError, match=r"no artifact 'nope\.html'"):
        VisualScorer(FakeBrowser()).score(missing)


def test_corrupt_blob_is_an_error_not_a_zero(tmp_path: Path) -> None:
    context = _context(tmp_path)
    blob = context.blobs.path_for(context.artifacts["index.html"].sha256)
    blob.write_bytes(b"tampered")
    with pytest.raises(ScorerError, match="cannot read artifact"):
        VisualScorer(FakeBrowser()).score(context)


def test_registers_and_passes_framework_validation(tmp_path: Path) -> None:
    scorer = VisualScorer(FakeBrowser())
    registry = ScorerRegistry([scorer])
    resolved = registry.get("visual@1")
    context = _context(tmp_path)
    validate_score(resolved, context, resolved.score(context))


def test_real_browser_counts_console_errors_and_sandboxes_the_page(tmp_path: Path) -> None:
    sync_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    from arena.scorers.visual import PlaywrightBrowser

    try:
        with sync_api.sync_playwright() as pw:
            pw.chromium.launch().close()
    except sync_api.Error as exc:
        pytest.skip(f"no usable Chromium for Playwright: {str(exc).splitlines()[0]}")

    page = (
        b"<!doctype html><h1 id=x>hi</h1><script>"
        b"console.error('first'); document.title='ran';"
        b"try { top.document.body } catch (e) { console.error('isolated') }"
        b"</script>"
    )
    score = VisualScorer(PlaywrightBrowser()).score(_context(tmp_path, html=page))
    assert score.value == 6
    assert set(score.evidence["console_errors"]["desktop"]) == {"first", "isolated"}
    blobs = _context(tmp_path).blobs
    for shot in score.evidence["screenshots"]:
        data = blobs.get(shot["artifact"]["sha256"])
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
