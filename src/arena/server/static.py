"""Safe copying of prebuilt viewer assets into static bundle exports."""

from __future__ import annotations

import shutil
from pathlib import Path


class ViewerAssetsUnavailable(RuntimeError):
    """The web package has not produced distributable viewer assets."""


def web_dist_path() -> Path:
    """Return the conventional Vite build output directory."""
    return Path(__file__).resolve().parents[3] / "web" / "dist"


def export_viewer_assets(destination: Path, source: Path | None = None) -> None:
    """Copy a prebuilt web/dist tree into an existing bundle directory safely."""
    assets = source or web_dist_path()
    if not assets.is_dir() or not (assets / "index.html").is_file():
        raise ViewerAssetsUnavailable(
            f"viewer assets are unavailable at {assets}; build the web app with `pnpm -C web build`"
        )
    for item in assets.rglob("*"):
        if item.is_symlink():
            raise ViewerAssetsUnavailable(f"viewer asset contains a symlink: {item}")
    shutil.copytree(assets, destination, dirs_exist_ok=True)
