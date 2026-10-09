"""Visual scorer: screenshots at three viewports and a console-error check for web artifacts.

The generated page is untrusted. It is served from its own loopback origin (its own port) with a
`sandbox` CSP, and the browser opens it only inside a `<iframe sandbox="allow-scripts">` served
from a second origin, the way the viewer shows it. Screenshots go into the content-addressed
store and are listed in the score's evidence for the visual judge.
"""

from __future__ import annotations

import html
import importlib
import threading
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

from arena.core.models import Artifact, Score
from arena.scorers.base import ScorerContext, ScorerError

SCORER_ID = "visual"
SCORER_VERSION = "1"

# The artifact gets scripts and inline styles but no network and no same-origin access, so a
# score never depends on a CDN being up and the page cannot reach the host.
ARTIFACT_CSP = (
    "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data: blob:; font-src data:; media-src data: blob:; "
    "connect-src 'none'; form-action 'none'"
)

# Per viewport, so a page that errors in a loop can't grow memory without bound.
MAX_CONSOLE_ERRORS = 100
MAX_ERROR_LENGTH = 300


@dataclass(frozen=True)
class Viewport:
    name: str
    width: int
    height: int


VIEWPORTS: tuple[Viewport, ...] = (
    Viewport("mobile", 375, 812),
    Viewport("tablet", 768, 1024),
    Viewport("desktop", 1440, 900),
)


@dataclass(frozen=True)
class PageCapture:
    """What one browser visit produced: PNG bytes and the console errors, in order."""

    png: bytes
    console_errors: Sequence[str] = ()


class CaptureError(ScorerError):
    """The browser could not load or capture the page. The result is unknown, not zero."""


class PageBrowser(Protocol):
    """Opens a URL at a viewport size. Tests inject a fake; `PlaywrightBrowser` is the real one."""

    def capture(self, url: str, viewport: Viewport) -> PageCapture: ...


class PlaywrightBrowser:
    """Chromium through Playwright, imported lazily so the package is optional."""

    def __init__(self, *, timeout_ms: int = 15_000, settle_ms: int = 250) -> None:
        self.timeout_ms = timeout_ms
        self.settle_ms = settle_ms

    def capture(self, url: str, viewport: Viewport) -> PageCapture:
        try:
            # Loaded by name: the package is optional and not a declared dependency.
            sync_api: Any = importlib.import_module("playwright.sync_api")
        except ImportError as exc:
            raise CaptureError(
                "playwright is not installed; install it and run `playwright install chromium`"
            ) from exc

        errors: list[str] = []

        def on_console(message: Any) -> None:
            if message.type == "error" and len(errors) < MAX_CONSOLE_ERRORS:
                errors.append(message.text[:MAX_ERROR_LENGTH])

        def on_page_error(error: Any) -> None:
            if len(errors) < MAX_CONSOLE_ERRORS:
                errors.append(str(error)[:MAX_ERROR_LENGTH])

        try:
            with sync_api.sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    context = browser.new_context(
                        viewport={"width": viewport.width, "height": viewport.height},
                        accept_downloads=False,
                        reduced_motion="reduce",
                        locale="en-US",
                        timezone_id="UTC",
                    )
                    page = context.new_page()
                    page.on("console", on_console)
                    page.on("pageerror", on_page_error)
                    page.goto(url, wait_until="load", timeout=self.timeout_ms)
                    page.wait_for_timeout(self.settle_ms)
                    png = page.screenshot(type="png")
                finally:
                    browser.close()
        except sync_api.Error as exc:
            raise CaptureError(f"browser failed at {viewport.name}: {exc}") from exc
        return PageCapture(png=png, console_errors=tuple(errors))


@dataclass(frozen=True)
class _Origins:
    """Two loopback origins: one for the frame host page, one for the untrusted artifact."""

    host_url: str
    artifact_url: str


@contextmanager
def _serve(artifact_html: bytes) -> Generator[_Origins]:
    """Serve the artifact and its frame host on two ports, and stop both on exit."""
    artifact_server = _start(_artifact_handler(artifact_html))
    artifact_url = f"http://127.0.0.1:{artifact_server.server_port}/artifact.html"
    host_server = _start(_host_handler(artifact_url))
    try:
        yield _Origins(f"http://127.0.0.1:{host_server.server_port}/", artifact_url)
    finally:
        for server in (host_server, artifact_server):
            server.shutdown()
            server.server_close()


def _start(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class _QuietHandler(BaseHTTPRequestHandler):
    path_served = "/"
    body = b""
    content_type = "text/html; charset=utf-8"
    policy = ""

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != self.path_served:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.body)))
        self.send_header("Content-Security-Policy", self.policy)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def _artifact_handler(artifact_html: bytes) -> type[BaseHTTPRequestHandler]:
    class ArtifactHandler(_QuietHandler):
        path_served = "/artifact.html"
        body = artifact_html
        policy = ARTIFACT_CSP

    return ArtifactHandler


def _host_handler(artifact_url: str) -> type[BaseHTTPRequestHandler]:
    page = (
        "<!doctype html><meta charset=utf-8><title>frame</title>"
        "<style>html,body{margin:0;height:100%;background:#fff}"
        "iframe{display:block;border:0;width:100%;height:100vh}</style>"
        f'<iframe sandbox="allow-scripts" src="{html.escape(artifact_url)}"></iframe>'
    )
    origin = artifact_url.rsplit("/", 1)[0]

    class HostHandler(_QuietHandler):
        body = page.encode()
        policy = f"default-src 'none'; style-src 'unsafe-inline'; frame-src {origin}"

    return HostHandler


class VisualScorer:
    """Deterministic capture for `web-artifact` tasks.

    The score is the number of console error events across the three viewports. It passes
    with none. Screenshots are stored in the context's blob store and listed in the evidence
    as `Artifact` records for the visual judge. Config `path` names the HTML artifact; without
    it the trial must have exactly one HTML artifact.
    """

    id = SCORER_ID
    version = SCORER_VERSION

    def __init__(self, browser: PageBrowser | None = None) -> None:
        self._browser = browser

    def score(self, context: ScorerContext) -> Score:
        artifact = self._html_artifact(context)
        if artifact is None:
            return Score(
                trial_id=context.trial_id,
                scorer_id=self.id,
                scorer_version=self.version,
                value=0,
                normalized=0.0,
                passed=False,
                rationale="the trial produced no HTML artifact to render",
                evidence={"reason": "no_html_artifact", "screenshots": [], "console_errors": {}},
            )

        browser = self._browser or PlaywrightBrowser()
        captures: list[tuple[Viewport, PageCapture]] = []
        with _serve(context.read(artifact.path)) as origins:
            for viewport in VIEWPORTS:
                captures.append((viewport, browser.capture(origins.host_url, viewport)))

        # Store only after every viewport succeeded, so a failed capture leaves nothing behind.
        screenshots: list[dict[str, Any]] = []
        for viewport, capture in captures:
            shot = Artifact(
                sha256=context.blobs.put(capture.png),
                path=f"screenshots/{self.id}/{viewport.name}.png",
                mime="image/png",
                render_hint="image",
            )
            screenshots.append(
                {
                    "viewport": viewport.name,
                    "width": viewport.width,
                    "height": viewport.height,
                    "artifact": shot.model_dump(),
                }
            )
        errors = {viewport.name: list(capture.console_errors) for viewport, capture in captures}
        count = sum(len(messages) for messages in errors.values())
        return Score(
            trial_id=context.trial_id,
            scorer_id=self.id,
            scorer_version=self.version,
            value=count,
            normalized=1 / (1 + count),
            passed=count == 0,
            rationale=(
                "no console errors at any viewport"
                if count == 0
                else f"{count} distinct console error(s) across mobile, tablet and desktop"
            ),
            evidence={
                "artifact_path": artifact.path,
                "screenshots": screenshots,
                "console_errors": errors,
            },
        )

    @staticmethod
    def _html_artifact(context: ScorerContext) -> Artifact | None:
        configured = context.config.get("path")
        if configured is not None:
            if not isinstance(configured, str):
                raise ScorerError("visual config 'path' must be a string")
            try:
                return context.artifacts[configured]
            except KeyError:
                raise ScorerError(
                    f"trial {context.trial_id} has no artifact {configured!r}"
                ) from None
        candidates = [
            a
            for a in context.artifacts.values()
            if a.render_hint == "html-sandbox" or a.mime.startswith("text/html")
        ]
        if len(candidates) > 1:
            paths = sorted(a.path for a in candidates)
            raise ScorerError(
                f"trial {context.trial_id} has more than one HTML artifact {paths}; "
                "set config 'path'"
            )
        return candidates[0] if candidates else None
