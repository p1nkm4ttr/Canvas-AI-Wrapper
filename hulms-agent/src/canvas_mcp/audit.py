"""Corpus audit CLI: what does each (completed) course actually contain?

Usage:
    hulms-audit                 # every completed course, summary table
    hulms-audit "Data Struct"   # one course (id, code, or name substring), detailed
    hulms-audit --fresh         # bypass the SQLite cache

This answers the only question that matters before investing more weekends:
is Habib's Canvas data good enough to build on — and which course is the one
to develop against. Everything is cached to SQLite (7 days) so re-runs are
free; concluded-course access may be withdrawn, so fetched data is kept.
"""

import argparse
import asyncio
import os
import sys
from collections import Counter
from typing import Any

from .core.client import cleanup_http_client
from .core.config import get_config, validate_config
from .core.db import cached_fetch_all, cached_request

CACHE_TTL = 7 * 86400
PAGE_BODY_CAP = 50  # max per-page body fetches per course


def _err(x: Any) -> str | None:
    if isinstance(x, dict) and "error" in x:
        e = x["error"]
        if "403" in e:
            return "403"
        if "404" in e:
            return "off"
        return "err"
    return None


async def audit_course(course: dict, ttl: float) -> dict:
    """Collect the audit facts for one course. Degrades field-by-field."""
    cid = course["id"]
    row: dict[str, Any] = {
        "id": cid,
        "term": (course.get("term") or {}).get("name", "?").strip(),
        "name": course.get("name", "?"),
        "code": course.get("course_code", "?"),
        "notes": [],
    }

    mods = await cached_fetch_all(
        f"/courses/{cid}/modules", {"include[]": "items", "per_page": 100}, ttl
    )
    if _err(mods):
        row["modules"], row["items"], row["module_detail"] = 0, 0, []
        row["notes"].append(f"modules:{_err(mods)}")
        file_item_ids: list[int] = []
    else:
        row["modules"] = len(mods)
        row["items"] = sum(len(m.get("items") or []) for m in mods)
        row["module_detail"] = [
            (m.get("name", "?"), len(m.get("items") or [])) for m in mods
        ]
        file_item_ids = [
            i.get("content_id")
            for m in mods
            for i in (m.get("items") or [])
            if i.get("type") == "File" and i.get("content_id")
        ]

    asgn = await cached_fetch_all(
        f"/courses/{cid}/assignments", {"per_page": 100}, ttl
    )
    if _err(asgn):
        row["asgn"] = row["asgn_due"] = row["asgn_pts"] = 0
        row["notes"].append(f"assignments:{_err(asgn)}")
    else:
        row["asgn"] = len(asgn)
        row["asgn_due"] = sum(1 for a in asgn if a.get("due_at"))
        row["asgn_pts"] = sum(1 for a in asgn if (a.get("points_possible") or 0) > 0)

    files = await cached_fetch_all(f"/courses/{cid}/files", {"per_page": 100}, ttl)
    if _err(files):
        # The flat listing can be instructor-restricted while item-level access
        # still works (measured live) — report what the module walk can reach.
        row["files"] = len(file_item_ids)
        row["files_mb"] = None
        row["ext"] = Counter()
        row["notes"].append(f"files-listing:{_err(files)} ({len(file_item_ids)} via modules)")
    else:
        row["files"] = len(files)
        row["files_mb"] = sum(f.get("size") or 0 for f in files) / 1e6
        row["ext"] = Counter(
            os.path.splitext(f.get("display_name") or f.get("filename") or "")[1].lower() or "(none)"
            for f in files
        )

    pages = await cached_fetch_all(f"/courses/{cid}/pages", {"per_page": 100}, ttl)
    if _err(pages):
        row["pages"], row["pages_chars"] = 0, 0
        if _err(pages) != "off":
            row["notes"].append(f"pages:{_err(pages)}")
    else:
        row["pages"] = len(pages)
        total = 0
        for p in pages[:PAGE_BODY_CAP]:
            detail = await cached_request(
                f"/courses/{cid}/pages/{p.get('url')}", None, ttl
            )
            if not _err(detail):
                total += len(detail.get("body") or "")
        row["pages_chars"] = total
        if len(pages) > PAGE_BODY_CAP:
            row["notes"].append(f"pages-text capped at {PAGE_BODY_CAP}")

    detail = await cached_request(
        f"/courses/{cid}", {"include[]": "syllabus_body"}, ttl
    )
    row["syllabus_chars"] = (
        len(detail.get("syllabus_body") or "") if not _err(detail) else 0
    )

    groups = await cached_fetch_all(
        f"/courses/{cid}/assignment_groups", {"per_page": 100}, ttl
    )
    if _err(groups):
        row["weights"] = []
        row["notes"].append(f"groups:{_err(groups)}")
    else:
        row["weights"] = [
            (g.get("name", "?"), g.get("group_weight") or 0)
            for g in groups
            if (g.get("group_weight") or 0) > 0
        ]

    return row


def print_table(rows: list[dict]) -> None:
    hdr = (
        f"{'id':>5} {'term':<12} {'course':<34} {'mod':>4} {'items':>5} "
        f"{'asgn':>4} {'due':>4} {'pts':>4} {'files':>5} {'MB':>7} "
        f"{'pages':>5} {'syl':>6} {'wts':>3}  notes"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        mb = f"{r['files_mb']:.0f}" if r["files_mb"] is not None else "?"
        name = r["name"][:33]
        print(
            f"{r['id']:>5} {r['term'][:12]:<12} {name:<34} {r['modules']:>4} {r['items']:>5} "
            f"{r['asgn']:>4} {r['asgn_due']:>4} {r['asgn_pts']:>4} {r['files']:>5} {mb:>7} "
            f"{r['pages']:>5} {r['syllabus_chars']:>6} {len(r['weights']):>3}  {'; '.join(r['notes'])}"
        )


def print_detail(row: dict) -> None:
    print(f"\n{row['name']}  (id {row['id']}, {row['term']})")
    print(f"  code: {row['code']}")
    print(f"\n  modules ({row['modules']}, {row['items']} items):")
    for name, count in row["module_detail"]:
        print(f"    {count:>3}  {name}")
    if row["ext"]:
        print(f"\n  files by extension ({row['files']} files):")
        for ext, count in row["ext"].most_common():
            print(f"    {count:>4}  {ext}")
    print(f"\n  assignments: {row['asgn']} total, {row['asgn_due']} with due_at, {row['asgn_pts']} with points > 0")
    print(f"  pages: {row['pages']} ({row['pages_chars']} chars of body)")
    print(f"  syllabus_body: {row['syllabus_chars']} chars")
    if row["weights"]:
        print("  assignment group weights:")
        for name, w in row["weights"]:
            print(f"    {w:>5.1f}%  {name}")
    else:
        print("  assignment group weights: none (unweighted or empty)")
    if row["notes"]:
        print(f"  notes: {'; '.join(row['notes'])}")


async def run(identifier: str | None, fresh: bool) -> int:
    ttl = 0 if fresh else CACHE_TTL
    courses = await cached_fetch_all(
        "/courses",
        {"enrollment_state": "completed", "include[]": "term", "per_page": 100},
        ttl,
    )
    if isinstance(courses, dict):
        print(f"Error fetching completed courses: {courses.get('error')}", file=sys.stderr)
        return 1

    if identifier:
        ident = identifier.lower()
        targets = [
            c for c in courses
            if str(c.get("id")) == ident
            or ident in (c.get("name") or "").lower()
            or ident in (c.get("course_code") or "").lower()
        ]
        if not targets:
            print(f"No completed course matches '{identifier}'.", file=sys.stderr)
            return 1
    else:
        targets = courses

    rows = []
    for i, course in enumerate(targets, 1):
        print(f"  auditing {i}/{len(targets)}: {course.get('name','?')[:60]}", file=sys.stderr)
        rows.append(await audit_course(course, ttl))

    rows.sort(key=lambda r: (r["term"], r["name"]))
    print()
    print_table(rows)

    if identifier and len(rows) <= 3:
        for r in rows:
            print_detail(r)
    else:
        # The develop-against hint: most extractable material wins.
        best = max(rows, key=lambda r: r["items"] + r["files"] + (50 if r["syllabus_chars"] else 0))
        print(
            f"\nRichest corpus candidate: {best['name']} (id {best['id']}, {best['term']}) -- "
            f"{best['modules']} modules, {best['items']} items, {best['files']} files."
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit completed Canvas courses as a development corpus")
    parser.add_argument("course", nargs="?", help="course id, code, or name substring (default: all completed)")
    parser.add_argument("--fresh", action="store_true", help="bypass the SQLite cache")
    args = parser.parse_args()

    if not validate_config():
        sys.exit(1)
    get_config()

    async def _run() -> int:
        try:
            return await run(args.course, args.fresh)
        finally:
            await cleanup_http_client()

    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
