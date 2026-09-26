"""Agenda: what's coming up — shared collector plus the CLI.

Uses /api/v1/planner/items — the merged, date-sorted, cross-course feed
(verified fact: never loop over courses fetching assignments). Always fresh,
never cached: deadlines move.
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .core.client import absolute_url, cleanup_http_client, fetch_all_paginated_results
from .core.config import get_config, validate_config


def derive_status(item: dict) -> str:
    """One honest status per item, derived from the submissions block.

    Canvas facts: real state lives in `submissions`; items can be graded but
    NOT submitted; every field is optional.
    """
    sub = item.get("submissions")
    if not isinstance(sub, dict):
        return "-"
    if sub.get("excused"):
        return "excused"
    if sub.get("missing"):
        return "missing"
    if sub.get("graded"):
        return "graded" if sub.get("submitted") else "graded (not submitted)"
    if sub.get("submitted"):
        return "late" if sub.get("late") else "submitted"
    return "todo"


def local_tz() -> ZoneInfo | timezone:
    name = get_config().timezone or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


async def collect_agenda(
    start_utc: datetime, end_utc: datetime
) -> list[dict] | dict:
    """Planner items in [start_utc, end_utc), shaped for humans and models.

    Returns a list of {due, daysUntil, course, type, title, status, url} in
    the configured local timezone, dismissed (marked_complete) items excluded
    — or an {"error": ...} dict when the fetch fails. Date arithmetic happens
    HERE (rule: never let the model do date math).
    """
    items = await fetch_all_paginated_results(
        "/planner/items",
        {
            "start_date": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "per_page": 100,
        },
    )
    if isinstance(items, dict):
        return items

    tz = local_tz()
    today = datetime.now(tz).date()
    agenda: list[dict] = []
    for item in items:
        override = item.get("planner_override")
        if isinstance(override, dict) and override.get("marked_complete"):
            continue  # user dismissed it: not outstanding

        plannable = item.get("plannable") or {}
        due_raw = plannable.get("due_at") or plannable.get("todo_date") or item.get("plannable_date")
        if not due_raw:
            continue
        try:
            due_utc = datetime.fromisoformat(due_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        due_local = due_utc.astimezone(tz)

        agenda.append({
            "due": due_local.isoformat(timespec="minutes"),
            "daysUntil": (due_local.date() - today).days,
            "course": item.get("context_name") or "-",
            "type": (item.get("plannable_type") or "?").replace("_", " "),
            "title": plannable.get("title") or plannable.get("name") or "Untitled",
            "status": derive_status(item),
            "url": absolute_url(item.get("html_url")) or "",
        })
    return agenda


async def run(days: int) -> int:
    tz = local_tz()
    now_local = datetime.now(tz)
    start = now_local.astimezone(timezone.utc)
    agenda = await collect_agenda(start, start + timedelta(days=days))

    if isinstance(agenda, dict):
        print(f"Error fetching planner items: {agenda.get('error')}", file=sys.stderr)
        return 1

    print(f"Agenda -- next {days} days ({now_local:%a %d %b %Y}, {get_config().timezone}):\n")
    for entry in agenda:
        when = "today" if entry["daysUntil"] == 0 else f"in {entry['daysUntil']}d"
        print(f"  {when:>6}  {entry['due']}  [{entry['course']}] {entry['type']}: {entry['title']}")
        print(f"          status: {entry['status']}{'   ' + entry['url'] if entry['url'] else ''}")

    if not agenda:
        print("  Nothing due. (The semester may not have filled up yet.)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the Canvas agenda for the next N days")
    parser.add_argument("days", nargs="?", type=int, default=14)
    args = parser.parse_args()

    if not validate_config():
        sys.exit(1)

    async def _run() -> int:
        try:
            return await run(args.days)
        finally:
            await cleanup_http_client()

    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
