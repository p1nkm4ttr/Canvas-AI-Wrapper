"""Bulk course-file indexing: shared by the canvas-extract CLI and the
index_course_files MCP tool.

Sequential on purpose (verified fact: sequential-with-cache beats parallel on
Canvas throttling). Everything lands in the same SQLite cache the study tools
read, so get_study_context and search_course_content become instant after.
"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from .client import fetch_all_paginated_results, make_canvas_request
from .extract import EXTRACTABLE_EXTS, file_extension
from .files import get_file_text_cached
from .links import harvest_file_refs
from .modules import fetch_modules_with_items

MAX_PAGES_SCANNED = 50


async def index_course(
    course_id: str | int,
    include_all_files: bool = True,
    progress: Callable[[int, int, str, str], None] | None = None,
) -> dict[str, Any]:
    """Extract every reachable file of a course into the local text cache.

    Collects file ids from the module walk (always), the flat files listing
    (include_all_files; the listing being instructor-blocked is fine, module
    files still count), and — the source the other two miss — files referenced
    from assignment descriptions, announcement bodies and attachments, and
    course pages. Canvas marks those files hidden, so they appear in no
    listing; measured live, that is where the assignment briefs live.
    Returns {"counts": {status: n}, "total": n, "notes": [...],
    "newFiles": [...], "harvested": n}.
    """
    file_ids: dict[int, str] = {}
    notes: list[str] = []
    origins: dict[int, str] = {}
    fallback: dict[int, str] = {}

    def _add_refs(refs: list[dict[str, Any]], origin: str) -> None:
        for r in refs:
            fid = int(r["fileId"])
            file_ids.setdefault(fid, r.get("title") or "?")
            origins.setdefault(fid, origin)
            if r.get("url"):
                fallback.setdefault(fid, r["url"])

    modules = await fetch_modules_with_items(course_id)
    if isinstance(modules, list):
        for m in modules:
            if m.get("itemsError"):
                notes.append(f"items unavailable for module {m.get('id')}: {m['itemsError']}")
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

    before_harvest = len(file_ids)

    assignments = await fetch_all_paginated_results(
        f"/courses/{course_id}/assignments", {"per_page": 100}
    )
    if isinstance(assignments, list):
        for a in assignments:
            label = a.get("name") or f"#{a.get('id')}"
            _add_refs(harvest_file_refs(a.get("description")), f"linked from assignment '{label}'")
    else:
        notes.append(f"assignments unavailable for link harvesting: {assignments.get('error')}")

    # Announcements need an explicit end_date: Canvas defaults it to start+28d.
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    anns = await fetch_all_paginated_results(
        "/announcements",
        {"context_codes[]": [f"course_{course_id}"], "start_date": "2000-01-01",
         "end_date": tomorrow, "per_page": 100},
    )
    if isinstance(anns, list):
        for ann in anns:
            label = ann.get("title") or f"#{ann.get('id')}"
            _add_refs(harvest_file_refs(ann.get("message")), f"linked from announcement '{label}'")
            for att in ann.get("attachments") or []:
                if att.get("id"):
                    _add_refs(
                        [{"fileId": att["id"], "url": att.get("url") or "",
                          "title": att.get("display_name") or ""}],
                        f"attached to announcement '{label}'",
                    )
    else:
        notes.append(f"announcements unavailable for link harvesting: {anns.get('error')}")

    pages = await fetch_all_paginated_results(f"/courses/{course_id}/pages", {"per_page": 100})
    if isinstance(pages, list):
        for p in pages[:MAX_PAGES_SCANNED]:
            if not p.get("url"):
                continue
            page = await make_canvas_request("get", f"/courses/{course_id}/pages/{p['url']}")
            if isinstance(page, dict) and "error" not in page:
                _add_refs(harvest_file_refs(page.get("body")),
                          f"linked from page '{p.get('title') or p['url']}'")
        if len(pages) > MAX_PAGES_SCANNED:
            notes.append(f"only the first {MAX_PAGES_SCANNED} of {len(pages)} pages were scanned for links")
    harvested = len(file_ids) - before_harvest

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
        result = await get_file_text_cached(
            fid, int(course_id), origin=origins.get(fid, ""), fallback_url=fallback.get(fid)
        )
        status = result.get("status", "error")
        counts[status] = counts.get(status, 0) + 1
        if status == "ok" and not result.get("cached"):
            new_files.append(result.get("name") or name)
        if progress:
            progress(n, total, status, name)

    return {"counts": counts, "total": total, "notes": notes, "newFiles": new_files,
            "harvested": harvested}
