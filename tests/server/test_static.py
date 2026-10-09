from pathlib import Path

import pytest

from arena.server.static import ViewerAssetsUnavailable, export_viewer_assets


def test_static_export_copies_built_viewer_assets(tmp_path: Path) -> None:
    source = tmp_path / "dist"
    source.mkdir()
    (source / "index.html").write_text('<script src="./assets/app.js"></script>', encoding="utf-8")
    assets = source / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('static')", encoding="utf-8")
    destination = tmp_path / "export"
    destination.mkdir()

    export_viewer_assets(destination, source)

    assert (destination / "index.html").is_file()
    assert (destination / "assets" / "app.js").read_text(
        encoding="utf-8"
    ) == "console.log('static')"


def test_static_export_reports_missing_viewer_assets(tmp_path: Path) -> None:
    with pytest.raises(ViewerAssetsUnavailable, match="pnpm -C web build"):
        export_viewer_assets(tmp_path / "export", tmp_path / "missing")


def test_static_export_rejects_symlinked_viewer_assets(tmp_path: Path) -> None:
    source = tmp_path / "dist"
    source.mkdir()
    (source / "index.html").write_text("viewer", encoding="utf-8")
    outside = tmp_path / "outside.js"
    outside.write_text("outside", encoding="utf-8")
    (source / "escape.js").symlink_to(outside)
    destination = tmp_path / "export"
    destination.mkdir()

    with pytest.raises(ViewerAssetsUnavailable, match="symlink"):
        export_viewer_assets(destination, source)
