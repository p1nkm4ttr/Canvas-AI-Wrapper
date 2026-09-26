"""Dropped-file extraction: files students place in course space folders.

Same pipeline as Canvas files, cached in SQLite keyed on the file's mtime and
fed into the shared FTS index, so search_course_content covers a dropped
Simple Syllabus PDF exactly like Canvas material.
"""

from pathlib import Path
from typing import Any

from . import config
from .db import get_local_text_row, put_local_text
from .extract import extract_text, is_extractable

# Space bookkeeping files — never course material, never indexed.
RESERVED_NAMES = {"memory.md", "plan.md", "system.md"}


def spaces_root() -> Path:
    # Attribute lookup at call time so tests can repoint config.REPO_ROOT.
    return (config.REPO_ROOT.parent / "spaces").resolve()


def course_id_for_space(space_name: str) -> int | None:
    """'c5536' -> 5536; 'general' (or anything else) -> None."""
    if space_name.startswith("c") and space_name[1:].isdigit():
        return int(space_name[1:])
    return None


def extract_local_file(target: Path) -> dict[str, Any]:
    """Extract one dropped file, cached by mtime, indexed for search.

    Returns {path, name, status, text, note, cached}.
    """
    rel = f"{target.parent.name}/{target.name}"
    mtime = target.stat().st_mtime

    cached = get_local_text_row(rel, mtime)
    if cached is not None:
        return {"path": rel, "name": target.name, "cached": True, **cached}

    extraction = extract_text(target.read_bytes(), target.name)
    if target.name not in RESERVED_NAMES:
        put_local_text(
            rel, course_id_for_space(target.parent.name), target.name, mtime,
            extraction.status, extraction.text, extraction.note,
        )
    return {
        "path": rel,
        "name": target.name,
        "status": extraction.status,
        "text": extraction.text,
        "note": extraction.note,
        "cached": False,
    }


def index_local_spaces() -> dict[str, Any]:
    """Sweep every space folder into the search index. Cheap when cached.

    Returns {"indexed": n, "new": [paths], "skipped": n}.
    """
    root = spaces_root()
    if not root.is_dir():
        return {"indexed": 0, "new": [], "skipped": 0}

    indexed, skipped, new = 0, 0, []
    for f in sorted(root.glob("*/*")):
        if not f.is_file() or f.name in RESERVED_NAMES:
            continue
        if not is_extractable(f.name):
            skipped += 1
            continue
        result = extract_local_file(f)
        if result["status"] == "ok":
            indexed += 1
            if not result["cached"]:
                new.append(result["path"])
        else:
            skipped += 1
    return {"indexed": indexed, "new": new, "skipped": skipped}
