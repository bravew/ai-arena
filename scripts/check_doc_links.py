#!/usr/bin/env python3
"""Check local Markdown links and fragments in subsystem references."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "subsystems"
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.M)
SYMBOL = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)\b", re.M)


def slug(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text).lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return re.sub(r"\s+", "-", text.strip())


def anchors(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found = {slug(m.group(1)) for m in HEADING.finditer(text)}
    if path.suffix == ".py":
        found.update(SYMBOL.findall(text))
    return found


def check() -> list[str]:
    failures: list[str] = []
    if not DOCS.is_dir():
        return [f"missing documentation directory: {DOCS.relative_to(ROOT)}"]
    pages = sorted(page for page in DOCS.glob("*.md") if page.name != "README.md")
    if not pages:
        return ["no subsystem Markdown pages found"]
    required = {
        "Responsibilities and sources of truth",
        "Runtime path",
        "Constraints and failure behavior",
        "Verification",
    }
    for page in pages:
        text = page.read_text(encoding="utf-8")
        headings = {m.group(1).strip() for m in HEADING.finditer(text)}
        for heading in sorted(required - headings):
            failures.append(f"{page.relative_to(ROOT)}: missing required section: {heading}")
        for match in LINK.finditer(text):
            target = match.group(1).split()[0].strip("<>\"")
            if not target or target.startswith(("https://", "http://", "mailto:", "#")):
                continue
            path_text, _, fragment = target.partition("#")
            path = (page.parent / path_text).resolve()
            try:
                path.relative_to(ROOT)
            except ValueError:
                failures.append(f"{page.relative_to(ROOT)}: link escapes repository: {target}")
                continue
            if not path.is_file():
                failures.append(f"{page.relative_to(ROOT)}: missing file link: {target}")
                continue
            if fragment and fragment not in anchors(path):
                failures.append(f"{page.relative_to(ROOT)}: missing heading or symbol link: {target}")
    return failures


if __name__ == "__main__":
    errors = check()
    if errors:
        print("Documentation link check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Documentation link check passed ({len([p for p in DOCS.glob('*.md') if p.name != 'README.md'])} pages).")
