"""Module trees with their items, complete.

`GET /courses/:id/modules?include[]=items` OMITS the items array for large
modules (documented Canvas behaviour: callers must then fetch
/modules/:id/items). Treating a missing array as "no items" made exactly the
biggest, most important modules read as empty. This helper fills the gap and
is the only way the tools should fetch a module tree.
"""

from typing import Any

from .client import fetch_all_paginated_results


async def fetch_modules_with_items(course_id: int | str) -> list[dict[str, Any]] | dict[str, Any]:
    """Modules for a course, each with an `items` list. Returns the client's
    error dict unchanged if the module listing itself fails. A module whose
    items could not be fetched gets `items: []` plus `itemsError`, so callers
    can report the gap instead of presenting it as an empty module."""
    mods = await fetch_all_paginated_results(
        f"/courses/{course_id}/modules",
        {"include[]": ["items", "content_details"], "per_page": 100},
    )
    if isinstance(mods, dict):
        return mods

    for m in mods:
        if m.get("items") is not None:
            continue
        if not m.get("id"):
            m["items"] = []
            continue
        items = await fetch_all_paginated_results(
            f"/courses/{course_id}/modules/{m['id']}/items",
            {"include[]": "content_details", "per_page": 100},
        )
        if isinstance(items, dict):
            m["items"] = []
            m["itemsError"] = items.get("error") or "items unavailable"
        else:
            m["items"] = items
    return mods


def items_coverage_note(mods: list[dict[str, Any]]) -> str | None:
    """One honest sentence naming modules whose items could not be loaded."""
    missing = [m.get("name") or f"module {m.get('id')}" for m in mods if m.get("itemsError")]
    if not missing:
        return None
    return "Items could not be loaded for: " + "; ".join(str(x) for x in missing)
