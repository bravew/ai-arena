"""Safe copying of prebuilt viewer assets into static bundle exports."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path


class ViewerAssetsUnavailable(RuntimeError):
    """The web package has not produced distributable viewer assets."""


def web_dist_path() -> Path:
    """Return the conventional Vite build output directory."""
    return Path(__file__).resolve().parents[3] / "web" / "dist"


def export_viewer_assets(
    destination: Path,
    source: Path | None = None,
    *,
    bundle: bytes | None = None,
    events: bytes | None = None,
) -> None:
    """Copy a prebuilt viewer and add safely embedded run data for file:// loading."""
    assets = source or web_dist_path()
    if not assets.is_dir() or not (assets / "index.html").is_file():
        raise ViewerAssetsUnavailable(
            f"viewer assets are unavailable at {assets}; build the web app with `pnpm -C web build`"
        )
    for item in assets.rglob("*"):
        if item.is_symlink():
            raise ViewerAssetsUnavailable(f"viewer asset contains a symlink: {item}")
    shutil.copytree(assets, destination, dirs_exist_ok=True)
    if bundle is None or events is None:
        return
    script = (assets / "index.html").read_text(encoding="utf-8")
    if "</body>" not in script:
        raise ViewerAssetsUnavailable(
            f"viewer entry has no closing body tag: {assets / 'index.html'}"
        )
    script = _inline_assets(script, assets)
    payload = json.dumps(
        {"input": json.loads(bundle), "events": _event_rows(events)},
        ensure_ascii=True,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    embedded = f'<script id="arena-static-data" type="application/json">{payload}</script>'
    (destination / "index.html").write_text(
        script.replace("</body>", f"{embedded}</body>"), encoding="utf-8"
    )


def _inline_assets(document: str, assets: Path) -> str:
    def inline_script(match: re.Match[str]) -> str:
        path = assets / match.group(1)
        if not path.is_file() or path.is_symlink():
            raise ViewerAssetsUnavailable(f"viewer script is unavailable: {path}")
        content = path.read_text(encoding="utf-8").replace("</script", "<\\/script")
        return f"<script>{content}</script>"

    def inline_style(match: re.Match[str]) -> str:
        path = assets / match.group(1)
        if not path.is_file() or path.is_symlink():
            raise ViewerAssetsUnavailable(f"viewer stylesheet is unavailable: {path}")
        return f"<style>{path.read_text(encoding='utf-8')}</style>"

    document = re.sub(
        r'<script type="module" crossorigin src="\./([^"]+)"></script>',
        inline_script,
        document,
    )
    document = re.sub(
        r'<link rel="stylesheet" crossorigin href="\./([^"]+)">', inline_style, document
    )
    return document


def _event_rows(events: bytes) -> list[object]:
    text = events.decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]
