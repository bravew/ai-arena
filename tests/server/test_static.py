import json
from pathlib import Path

import pytest

from arena.server.static import ViewerAssetsUnavailable, export_viewer_assets


def test_static_export_copies_built_viewer_assets(tmp_path: Path) -> None:
    source = tmp_path / "dist"
    source.mkdir()
    (source / "index.html").write_text(
        '<html><head></head><body><script type="module" crossorigin '
        'src="./assets/app.js"></script></body></html>',
        encoding="utf-8",
    )
    assets = source / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('static')", encoding="utf-8")
    destination = tmp_path / "export"
    destination.mkdir()

    bundle = b'{"run":{"id":"static-run"}}'
    events = b'{"seq":1,"kind":"run_started"}\n'
    export_viewer_assets(destination, source, bundle=bundle, events=events)

    index = (destination / "index.html").read_text(encoding="utf-8")
    assert "arena-static-data" in index
    assert "static-run" in index
    assert "<script>console.log('static')</script>" in index
    payload = index.split('id="arena-static-data" type="application/json">', 1)[1]
    payload = payload.split("</script>", 1)[0]
    assert json.loads(payload) == {
        "input": json.loads(bundle),
        "events": [json.loads(events)],
    }
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
