"""Bulk course-file indexing: shared by the hulms-extract CLI and the
index_course_files MCP tool.

Sequential on purpose (verified fact: sequential-with-cache beats parallel on
Canvas throttling). Everything lands in the same SQLite cache the study tools
read, so get_study_context and search_course_content become instant after.
"""

from collections.abc import Callable
from typing import Any

from .client import fetch_all_paginated_results
from .extract import EXTRACTABLE_EXTS, file_extension
from .files import get_file_text_cached


async def index_course(
    course_id: str | int,
    include_all_files: bool = True,
    progress: Callable[[int, int, str, str], None] | None = None,
) -> dict[str, Any]:
    """Extract every reachable file of a course into the local text cache.

    Collects file ids from the module walk (always) and the flat files
    listing (include_all_files; catches files not placed in any module —
    the listing being instructor-blocked is fine, module files still count).
    Returns {"counts": {status: n}, "total": n, "notes": [...]}.
    """
    file_ids: dict[int, str] = {}
    notes: list[str] = []

    modules = await fetch_all_paginated_results(
        f"/courses/{course_id}/modules", {"include[]": "items", "per_page": 100}
    )
    if isinstance(modules, list):
        for m in modules:
            for item in m.get("items") or []:
                if item.get("type") == "File" and item.get("content_id"):
                    file_ids[item["content_id"]] = item.get("title") or "?"
    else:
        notes.append(f"modules unavailable: {modules.get('error')}")

    if include_all_files:
        files = await fetch_all_paginated_results(
            f"/courses/{course_id}/files", {"per_page": 100}
        )
        if isinstance(files, list):
            for f in files:
                if f.get("id"):
                    file_ids.setdefault(f["id"], f.get("display_name") or "?")
        else:
            notes.append(
                "files listing blocked (module files still indexed): "
                f"{files.get('error')}"
            )

    total = len(file_ids)
    counts: dict[str, int] = {}
    new_files: list[str] = []
    for n, (fid, name) in enumerate(file_ids.items(), 1):
        # Skip only names with a KNOWN-bad extension; an extensionless title
        # may still be a PDF (routing falls back to file metadata/MIME).
        ext = file_extension(name)
        if ext and ext not in EXTRACTABLE_EXTS:
            counts["skipped-format"] = counts.get("skipped-format", 0) + 1
            if progress:
                progress(n, total, "skipped-format", name)
            continue
        result = await get_file_text_cached(fid, int(course_id))
        status = result.get("status", "error")
        counts[status] = counts.get(status, 0) + 1
        if status == "ok" and not result.get("cached"):
            new_files.append(result.get("name") or name)
        if progress:
            progress(n, total, status, name)

    return {"counts": counts, "total": total, "notes": notes, "newFiles": new_files}
