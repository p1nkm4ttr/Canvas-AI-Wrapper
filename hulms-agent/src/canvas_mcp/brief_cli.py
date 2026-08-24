"""Daily brief: overnight announcements, the week ahead, new material, reviews.

Runs from Task Scheduler each morning. It re-indexes active courses (cheap:
cached files are skipped, so only genuinely new material downloads), then
writes the digest into the briefs table — which the calendar feed serves as
an all-day event, so it lands on the phone with zero extra infrastructure.
Pure data assembly, no model.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from .agenda import collect_agenda, local_tz
from .core.client import cleanup_http_client, fetch_all_paginated_results
from .core.config import validate_config
from .core.db import cached_fetch_all, count_due_retrieval_items, upsert_brief
from .core.indexing import index_course


def _chunk(seq: list, n: int) -> list[list]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


async def run(force: bool = False) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tz = local_tz()
    today = datetime.now(tz).date()

    if not force:
        from .core.db import recent_briefs
        if any(b["date"] == today.isoformat() for b in recent_briefs(0)):
            # Logon-triggered runs skip when the 07:30 run already fired.
            print(f"Brief for {today} already exists (use --force to regenerate).")
            return 0

    active = await cached_fetch_all(
        "/courses", {"enrollment_state": "active", "per_page": 100}, 6 * 3600
    )
    if isinstance(active, dict):
        print(f"courses unavailable: {active.get('error')}", file=sys.stderr)
        active = []
    names = {c["id"]: (c.get("name") or "?").split("-")[0] for c in active if c.get("id")}

    # 1. Refresh the corpus; collect genuinely new files per course.
    new_material: list[str] = []
    for cid, cname in names.items():
        result = await index_course(cid, include_all_files=True)
        for fname in result["newFiles"]:
            new_material.append(f"{cname}: {fname}")

    # 1b. Sweep dropped space files into the search index too.
    from .core.local_files import index_local_spaces
    local = index_local_spaces()
    for path in local["new"]:
        new_material.append(f"dropped: {path}")

    # 2. Deadlines over the next 7 days.
    start = datetime.now(timezone.utc)
    agenda = await collect_agenda(start, start + timedelta(days=7))
    deadlines = [
        a for a in (agenda if isinstance(agenda, list) else [])
        if a.get("type") != "announcement" and a.get("status") not in ("submitted", "graded")
    ]

    # 3. Announcements posted since yesterday morning.
    since = (today - timedelta(days=1)).isoformat()
    announcements: list[dict] = []
    for chunk in _chunk([f"course_{cid}" for cid in names], 10):
        batch = await fetch_all_paginated_results(
            "/announcements",
            {"context_codes[]": chunk, "start_date": since, "per_page": 100},
        )
        if isinstance(batch, list):
            announcements.extend(batch)

    # 4. Reviews due today.
    review_counts = count_due_retrieval_items()
    reviews_total = sum(review_counts.values())

    # --- assemble ---
    parts = []
    if deadlines:
        parts.append(f"{len(deadlines)} due this week")
    if announcements:
        parts.append(f"{len(announcements)} new announcement{'s' * (len(announcements) != 1)}")
    if new_material:
        parts.append(f"{len(new_material)} new file{'s' * (len(new_material) != 1)}")
    if reviews_total:
        parts.append(f"{reviews_total} review{'s' * (reviews_total != 1)} due")
    summary = " · ".join(parts) if parts else "all quiet"

    lines: list[str] = []
    if deadlines:
        lines.append("DUE THIS WEEK:")
        for a in deadlines[:8]:
            title = a["title"].split("): ", 1)[-1].removesuffix(">>>")
            lines.append(f"  {a['due'][:16]} ({a['daysUntil']}d) [{a['course'].split('-')[0]}] {title}")
    if announcements:
        lines.append("NEW ANNOUNCEMENTS:")
        for ann in announcements[:6]:
            course = names.get(
                int(str(ann.get("context_code", "course_0")).split("_")[-1] or 0), "?"
            )
            lines.append(f"  [{course}] {ann.get('title', 'Untitled')}")
    if new_material:
        lines.append("NEW MATERIAL:")
        lines.extend(f"  {m}" for m in new_material[:8])
    if reviews_total:
        lines.append("REVIEWS DUE: " + ", ".join(f"{c}: {n}" for c, n in review_counts.items()))
    if not lines:
        lines.append("Nothing new overnight and nothing due this week.")

    details = "\n".join(lines)
    upsert_brief(today.isoformat(), summary, details)
    print(f"HULMS brief for {today}: {summary}\n\n{details}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the HULMS daily brief")
    parser.add_argument("--force", action="store_true",
                        help="regenerate even if today's brief already exists")
    args = parser.parse_args()

    if not validate_config():
        sys.exit(1)

    async def _run() -> int:
        try:
            return await run(args.force)
        finally:
            await cleanup_http_client()

    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
