"""Study mode: module-scoped context with text already extracted.

The scoping rule (build brief): a quiz lives in a module, and that module
holds what it assesses. No vector database — Canvas structure IS the index.
"""

import re
from datetime import datetime
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..core.cache import resolve_course
from ..core.client import absolute_url, fetch_all_paginated_results, make_canvas_request
from ..core.db import extraction_coverage, search_file_text
from ..core.files import get_file_text_cached
from ..core.text import strip_html_tags
from ..core.untrusted_content import fence_untrusted, fence_untrusted_inline
from ..core.validation import validate_params

DEFAULT_CHARS_PER_FILE = 12_000
MAX_LINKED_RESOURCES = 5


def _is_err(x: Any) -> bool:
    return isinstance(x, dict) and "error" in x


async def _resolve(course: str | int) -> tuple[str, str] | dict:
    resolved = await resolve_course(course)
    if resolved is None:
        return {"error": f"No course matches '{course}'. Try get_courses to see what exists."}
    return resolved


def _clip(text: str, cap: int) -> tuple[str, bool]:
    if len(text) <= cap:
        return text, False
    return text[:cap] + f"\n...[truncated at {cap} characters]", True


def _find_module(
    modules: list[dict],
    module_id: int | None,
    quiz_id: int | None,
    assignment_id: int | None,
    date: str | None,
) -> dict | str:
    """Locate the target module, or return an explanation string."""
    if module_id is not None:
        for m in modules:
            if m.get("id") == module_id:
                return m
        return f"No module with id {module_id} in this course. Use get_course_map to list modules."

    if quiz_id is not None or assignment_id is not None:
        want_id = quiz_id if quiz_id is not None else assignment_id
        want_types = {"Quiz", "Assignment"} if assignment_id is None else {"Assignment"}
        if quiz_id is not None:
            want_types = {"Quiz"}
        for m in modules:
            for item in m.get("items") or []:
                if item.get("content_id") == want_id and item.get("type") in want_types:
                    return m
        kind = "quiz" if quiz_id is not None else "assignment"
        return (
            f"No module contains {kind} {want_id}. It may not be placed in a "
            "module; try get_course_map, or target the module directly."
        )

    if date is not None:
        try:
            target = datetime.fromisoformat(date).date()
        except ValueError:
            return "date must be an ISO date like 2026-03-10"
        best: tuple[int, dict] | None = None
        for m in modules:
            for item in m.get("items") or []:
                due_raw = (item.get("content_details") or {}).get("due_at")
                if not due_raw:
                    continue
                try:
                    due = datetime.fromisoformat(due_raw.replace("Z", "+00:00")).date()
                except ValueError:
                    continue
                gap = abs((due - target).days)
                if best is None or gap < best[0]:
                    best = (gap, m)
        if best is not None and best[0] <= 7:
            return best[1]
        return (
            f"No module has an item due within a week of {date}. "
            "Target a module directly via get_course_map."
        )

    return "Give exactly one target: module_id, quiz_id, assignment_id, or date."


def register_study_tools(mcp: FastMCP) -> None:
    """Register the study-mode tools."""

    # ----------------------------------------------------------- study context
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_study_context(
        course: str | int,
        module_id: int | None = None,
        quiz_id: int | None = None,
        assignment_id: int | None = None,
        date: str | None = None,
        max_chars_per_file: int = DEFAULT_CHARS_PER_FILE,
    ) -> dict:
        """Get everything a module teaches, with file text already extracted.
        THE tool for "help me study for X" / "what does week N teach": give it
        a quiz, assignment, module, or date, and it walks to the containing
        module and returns every item's content. First use on a module
        downloads and extracts its files (slow once, cached after).

        Args:
            course: Course name, code, or id.
            module_id: Target module directly (ids from get_course_map).
            quiz_id: A quiz id — resolves to the module that contains it.
            assignment_id: An assignment id — resolves to its module.
            date: ISO date — resolves to the module with work due nearest it.
            max_chars_per_file: Per-file text cap (default 12000).
        """
        targets = [t for t in (module_id, quiz_id, assignment_id, date) if t is not None]
        if len(targets) != 1:
            return {"error": "Give exactly one of module_id, quiz_id, assignment_id, or date."}

        resolved = await _resolve(course)
        if _is_err(resolved):
            return resolved
        course_id, course_name = resolved

        modules = await fetch_all_paginated_results(
            f"/courses/{course_id}/modules",
            {"include[]": ["items", "content_details"], "per_page": 100},
        )
        if _is_err(modules):
            return modules

        module = _find_module(modules, module_id, quiz_id, assignment_id, date)
        if isinstance(module, str):
            return {"error": module}

        items_out: list[dict] = []
        extracted = 0
        skipped: list[str] = []

        for item in module.get("items") or []:
            itype = item.get("type")
            title = item.get("title") or "Untitled"
            entry: dict[str, Any] = {
                "title": fence_untrusted_inline(title, "item title"),
                "type": itype,
                "url": absolute_url(item.get("html_url")),
            }

            if itype == "File" and item.get("content_id"):
                file_result = await get_file_text_cached(item["content_id"], int(course_id))
                if _is_err(file_result):
                    skipped.append(f"{title} ({file_result['error']})")
                    entry["status"] = "error"
                elif file_result["status"] == "ok":
                    text, clipped = _clip(file_result["text"], max_chars_per_file)
                    entry["text"] = fence_untrusted(text, "file content")
                    if clipped:
                        entry["note"] = f"truncated at {max_chars_per_file} chars; get_file_text for all of it"
                    extracted += 1
                else:
                    skipped.append(f"{title} ({file_result['status']}: {file_result['note']})")
                    entry["status"] = file_result["status"]

            elif itype == "Page" and item.get("page_url"):
                page = await make_canvas_request(
                    "get", f"/courses/{course_id}/pages/{item['page_url']}"
                )
                if _is_err(page):
                    skipped.append(f"{title} (page: {page['error']})")
                else:
                    text, clipped = _clip(
                        strip_html_tags(page.get("body") or ""), max_chars_per_file
                    )
                    entry["text"] = fence_untrusted(text, "page content")
                    if clipped:
                        entry["note"] = "truncated"
                    extracted += 1

            elif itype in ("Assignment", "Quiz", "Discussion"):
                due = (item.get("content_details") or {}).get("due_at")
                if due:
                    entry["due"] = due
                if itype == "Quiz":
                    entry["note"] = (
                        "Quiz questions are not readable via the API; study the "
                        "material in this module instead."
                    )

            elif itype in ("ExternalUrl", "ExternalTool"):
                entry["externalUrl"] = item.get("external_url")

            items_out.append(entry)

        result: dict[str, Any] = {
            "course": course_name,
            "module": fence_untrusted_inline(module.get("name") or "Unnamed", "module name"),
            "url": absolute_url(f"/courses/{course_id}/modules"),
            "itemCount": len(items_out),
            "extractedCount": extracted,
            "items": items_out,
        }
        if skipped:
            result["coverage"] = (
                "Not everything could be read — say so if it matters: " + "; ".join(skipped)
            )
        return result

    # -------------------------------------------------------------- file text
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_file_text(
        file_id: int, course: str | int | None = None, max_chars: int | None = None
    ) -> dict:
        """Get the extracted text of one Canvas file (PDF, PPTX, DOCX, or
        text-like). Use for a single document — get_study_context already
        includes file text when reading a whole module. Cached; re-reads
        are instant.

        Args:
            file_id: Canvas file id (get_course_map lists them as fileId).
            course: Optional course name/code/id — helps when the file is
                only readable through its course.
            max_chars: Optional cap on returned text.
        """
        course_id: int | None = None
        if course is not None:
            resolved = await _resolve(course)
            if _is_err(resolved):
                return resolved
            course_id = int(resolved[0])

        result = await get_file_text_cached(file_id, course_id)
        if _is_err(result):
            return result

        text = result["text"]
        note = result["note"]
        if max_chars is not None and len(text) > max_chars:
            text = text[:max_chars] + f"\n...[truncated at {max_chars} characters]"
            note = (note + " " if note else "") + "Truncated; call without max_chars for all of it."
        return {
            "fileId": result["fileId"],
            "name": fence_untrusted_inline(result["name"], "file name"),
            "status": result["status"],
            "text": fence_untrusted(text, "file content") if text else "",
            "note": note,
            "url": absolute_url(result["url"]),
        }

    # ------------------------------------------------------------------ search
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def search_course_content(query: str, course: str | int | None = None) -> dict:
        """Full-text search over extracted course files. ONLY for questions
        that cross modules ("where was Dijkstra covered?") — when the module
        is known, get_study_context is the right tool. Searches what has been
        extracted so far; run `hulms-extract <course>` once to index a whole
        course.

        Args:
            query: Search terms (FTS5 syntax; plain words work).
            course: Optional course name/code/id to scope the search.
        """
        if not query.strip():
            return {"error": "query must not be empty"}

        course_id: int | None = None
        course_name = None
        if course is not None:
            resolved = await _resolve(course)
            if _is_err(resolved):
                return resolved
            course_id, course_name = int(resolved[0]), resolved[1]

        try:
            hits = search_file_text(query, course_id, limit=10)
        except Exception as e:
            return {"error": f"Search failed: {e}"}

        coverage = extraction_coverage(course_id)
        indexed = coverage.get("ok", 0)
        shaped = []
        for h in hits:
            entry = {
                "name": fence_untrusted_inline(h["name"] or "?", "file name"),
                "snippet": fence_untrusted_inline(h["snippet"] or "", "matched text"),
            }
            if "localPath" in h:
                # A dropped space file (e.g. a Simple Syllabus PDF export).
                entry["localPath"] = h["localPath"]
                entry["note"] = f"dropped file — read with read_local_document('{h['localPath']}')"
            else:
                entry["fileId"] = h["fileId"]
                entry["url"] = absolute_url(f"/files/{h['fileId']}")
            shaped.append(entry)
        result: dict[str, Any] = {
            "query": query,
            "course": course_name,
            "results": shaped,
            "count": len(shaped),
            "indexedFiles": indexed,
        }
        if indexed == 0:
            result["note"] = (
                "Nothing is indexed yet"
                + (" for this course" if course_id else "")
                + ". Run `hulms-extract <course>` or read modules via get_study_context first."
            )
        return result

    # ---------------------------------------------------- announcement context
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_announcement_context(
        announcement_id: int, course: str | int
    ) -> dict:
        """Get one announcement in full plus whatever it links to — files
        extracted, pages resolved, assignments summarised. Use when an
        announcement says "see the attached/linked material".

        Args:
            announcement_id: The announcement's id (from get_announcements URLs).
            course: Course name, code, or id.
        """
        resolved = await _resolve(course)
        if _is_err(resolved):
            return resolved
        course_id, course_name = resolved

        ann = await make_canvas_request(
            "get", f"/courses/{course_id}/discussion_topics/{announcement_id}"
        )
        if _is_err(ann):
            return ann

        body_html = ann.get("message") or ""
        linked: list[dict] = []
        seen: set[str] = set()

        # Resolve same-course links out of the body: files, pages, assignments.
        for kind, ref in re.findall(
            r'/courses/\d+/(files|pages|assignments)/([\w\-.%]+)', body_html
        ):
            key = f"{kind}:{ref}"
            if key in seen or len(linked) >= MAX_LINKED_RESOURCES:
                continue
            seen.add(key)
            if kind == "files" and ref.isdigit():
                fr = await get_file_text_cached(int(ref), int(course_id))
                if not _is_err(fr):
                    text, clipped = _clip(fr["text"], DEFAULT_CHARS_PER_FILE)
                    linked.append({
                        "kind": "file",
                        "name": fence_untrusted_inline(fr["name"], "file name"),
                        "status": fr["status"],
                        "text": fence_untrusted(text, "file content") if text else "",
                        "note": fr["note"] + (" (truncated)" if clipped else ""),
                    })
            elif kind == "pages":
                page = await make_canvas_request(
                    "get", f"/courses/{course_id}/pages/{ref}"
                )
                if not _is_err(page):
                    text, _ = _clip(strip_html_tags(page.get("body") or ""), DEFAULT_CHARS_PER_FILE)
                    linked.append({
                        "kind": "page",
                        "name": fence_untrusted_inline(page.get("title") or ref, "page title"),
                        "text": fence_untrusted(text, "page content"),
                    })
            elif kind == "assignments" and ref.isdigit():
                a = await make_canvas_request(
                    "get", f"/courses/{course_id}/assignments/{ref}"
                )
                if not _is_err(a):
                    linked.append({
                        "kind": "assignment",
                        "name": fence_untrusted_inline(a.get("name") or ref, "assignment name"),
                        "due": a.get("due_at"),
                        "points": a.get("points_possible"),
                        "url": absolute_url(a.get("html_url")),
                    })

        return {
            "course": course_name,
            "title": fence_untrusted_inline(ann.get("title") or "Untitled", "announcement title"),
            "postedAt": ann.get("posted_at"),
            "body": fence_untrusted(strip_html_tags(body_html), "announcement body"),
            "url": absolute_url(ann.get("html_url")),
            "linked": linked,
            "linkedCount": len(linked),
        }

    # ------------------------------------------------------------ bulk index
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def index_course_files(course: str | int, include_all_files: bool = True) -> dict:
        """Download and extract EVERY file in a course (modules plus the Files
        tab) into the local text index, so search_course_content and
        get_study_context answer instantly afterwards. Use when the student
        wants all course material available, or before cross-module search.
        Slow on first run for file-heavy courses; re-runs only fetch new or
        changed files. Reads Canvas, writes only the local cache.

        Args:
            course: Course name, code, or id.
            include_all_files: Also index files not placed in any module
                (default true; the Files listing being blocked is tolerated).
        """
        from ..core.db import extraction_coverage
        from ..core.indexing import index_course

        resolved = await _resolve(course)
        if _is_err(resolved):
            return resolved
        course_id, course_name = resolved

        result = await index_course(course_id, include_all_files)
        coverage = extraction_coverage(int(course_id))
        out: dict[str, Any] = {
            "course": course_name,
            "filesConsidered": result["total"],
            "thisRun": result["counts"],
            "indexTotals": coverage,
            "note": (
                "Extracted text is cached and searchable via "
                "search_course_content; get_study_context now answers from "
                "cache for this course."
            ),
        }
        if result["notes"]:
            out["coverage"] = "; ".join(result["notes"])
        skipped = result["counts"].get("scanned", 0) + result["counts"].get("unsupported", 0)
        if skipped:
            out["note"] += f" {skipped} files could not be extracted (scans/videos/unsupported formats) — say so if asked about them."
        return out

    # ---------------------------------------------------- local dropped files
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def read_local_document(name: str, max_chars: int = 40_000) -> dict:
        """Read a file the student dropped into a course space folder —
        Simple Syllabus PDF exports, slides, handouts. Use THIS (not Read)
        for any PDF, PPTX, or DOCX on disk: it runs the same extraction
        pipeline as Canvas files, including scanned-PDF detection.

        Args:
            name: The filename as shown in the space file listing, or
                "<spaceId>/<filename>" to disambiguate across spaces.
            max_chars: Cap on returned text (default 40000).
        """
        from pathlib import Path

        from ..core.config import REPO_ROOT

        spaces_root = (REPO_ROOT.parent / "spaces").resolve()
        if not spaces_root.is_dir():
            return {"error": f"No spaces folder at {spaces_root}."}

        cleaned = name.replace("\\", "/").strip()
        if cleaned.startswith("/") or ".." in cleaned.split("/") or ":" in cleaned:
            return {"error": "Give a bare filename or spaceId/filename, nothing above the spaces folder."}

        candidates: list[Path] = []
        if "/" in cleaned:
            p = (spaces_root / cleaned).resolve()
            if p.is_file() and p.is_relative_to(spaces_root):
                candidates = [p]
        else:
            exact = sorted(spaces_root.glob(f"*/{cleaned}"))
            candidates = [p for p in exact if p.is_file()]
            if not candidates:
                lowered = cleaned.lower()
                candidates = sorted(
                    p for p in spaces_root.glob("*/*")
                    if p.is_file() and lowered in p.name.lower()
                    and p.name not in ("system.md",)
                )

        if not candidates:
            available = sorted(
                f"{p.parent.name}/{p.name}"
                for p in spaces_root.glob("*/*")
                if p.is_file() and p.name not in ("system.md", "memory.md", "plan.md")
            )[:30]
            return {
                "error": f"No dropped file matches '{name}'.",
                "available": available,
            }
        if len(candidates) > 1:
            return {
                "error": f"'{name}' matches several files — pick one by spaceId/filename.",
                "matches": [f"{p.parent.name}/{p.name}" for p in candidates[:10]],
            }

        target = candidates[0]
        from ..core.local_files import extract_local_file

        extraction = extract_local_file(target)
        if extraction["status"] != "ok":
            return {
                "file": target.name,
                "status": extraction["status"],
                "text": "",
                "note": extraction["note"] or "No text could be extracted.",
            }
        text = extraction["text"]
        note = ""
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n...[truncated at {max_chars} characters]"
            note = "Truncated; call again with a larger max_chars for the rest."
        return {
            "file": target.name,
            "space": target.parent.name,
            "status": "ok",
            "text": fence_untrusted(text, "dropped document content"),
            "note": note,
        }
