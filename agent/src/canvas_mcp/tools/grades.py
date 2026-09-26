"""Grade standing: every graded item, by assignment group, with the math done.

The current weighted standing is computed HERE (a confidently wrong grade is
worse than none — same rule as date arithmetic). What-if reasoning ("what do
I need on the final for an A") is model work over the exposed structure:
per-group percentages, weights, and what remains ungraded.
"""

from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..core.cache import resolve_course
from ..core.client import absolute_url, fetch_all_paginated_results
from ..core.untrusted_content import fence_untrusted_inline
from ..core.validation import validate_params

MAX_ITEMS_PER_GROUP = 15


def _is_err(x: Any) -> bool:
    return isinstance(x, dict) and "error" in x


def summarize_groups(groups: list[dict]) -> dict[str, Any]:
    """Pure computation: per-group graded/ungraded splits and percentages.

    Canvas semantics: excused submissions are excluded from both earned and
    possible; a graded zero counts. Weighted current standing normalizes over
    the groups that actually have graded work.
    """
    out_groups: list[dict[str, Any]] = []
    weighted = any((g.get("group_weight") or 0) > 0 for g in groups)
    total_earned = total_possible = 0.0
    weight_num = weight_den = 0.0

    for g in groups:
        graded: list[dict[str, Any]] = []
        ungraded: list[dict[str, Any]] = []
        earned = possible = 0.0

        for a in g.get("assignments") or []:
            points = a.get("points_possible")
            sub = a.get("submission") or {}
            if sub.get("excused"):
                continue
            entry_name = a.get("name") or "Unnamed"
            score = sub.get("score")
            if sub.get("workflow_state") == "graded" and score is not None:
                graded.append({
                    "name": entry_name,
                    "score": score,
                    "pointsPossible": points,
                    "url": a.get("html_url"),
                })
                earned += score
                possible += points or 0
            elif (points or 0) > 0:
                ungraded.append({
                    "name": entry_name,
                    "pointsPossible": points,
                    "due": a.get("due_at"),
                })

        pct = round(earned / possible * 100, 2) if possible > 0 else None
        weight = g.get("group_weight") if weighted else None
        if weighted and pct is not None and (g.get("group_weight") or 0) > 0:
            weight_num += g["group_weight"] * pct
            weight_den += g["group_weight"]
        total_earned += earned
        total_possible += possible

        out_groups.append({
            "name": g.get("name") or "Unnamed",
            "weight": weight,
            "gradedCount": len(graded),
            "gradedEarned": earned,
            "gradedPossible": possible,
            "groupPercent": pct,
            "graded": graded,
            "ungraded": ungraded,
        })

    if weighted:
        computed = round(weight_num / weight_den, 2) if weight_den > 0 else None
    else:
        computed = (
            round(total_earned / total_possible * 100, 2) if total_possible > 0 else None
        )
    return {
        "weighting": "groups" if weighted else "points",
        "computedCurrentScore": computed,
        "groups": out_groups,
    }


def register_grade_tools(mcp: FastMCP) -> None:
    """Register the grade-standing tool."""

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_my_grades(course: str | int) -> dict:
        """Get your full grade standing in a course: every graded item by
        assignment group, group percentages, the current weighted score, and
        what remains ungraded. THE tool for "how am I doing" and the data
        for "what do I need on X to get Y" — show that arithmetic step by
        step from these numbers.

        Args:
            course: Course name, code, or id.
        """
        resolved = await resolve_course(course)
        if resolved is None:
            return {"error": f"No course matches '{course}'. Try get_courses."}
        course_id, course_name = resolved

        groups = await fetch_all_paginated_results(
            f"/courses/{course_id}/assignment_groups",
            {"include[]": ["assignments", "submission"], "per_page": 100},
        )
        if _is_err(groups):
            return groups

        summary = summarize_groups(groups)

        # Canvas's own computed score is authoritative; ours is the
        # structural breakdown that makes what-ifs possible.
        canvas_grades: dict[str, Any] = {}
        enrollments = await fetch_all_paginated_results(
            f"/courses/{course_id}/enrollments",
            # Default is active-only, which silently loses grades for
            # completed courses (measured live).
            {"user_id": "self", "state[]": ["active", "completed"], "per_page": 100},
        )
        if isinstance(enrollments, list):
            for e in enrollments:
                g = e.get("grades") or {}
                if g:
                    canvas_grades = {
                        "currentScore": g.get("current_score"),
                        "currentGrade": g.get("current_grade"),
                        "finalScore": g.get("final_score"),
                    }
                    break

        shaped_groups = []
        for g in summary["groups"]:
            graded = g["graded"]
            ungraded = g["ungraded"]
            entry = {
                **g,
                "name": fence_untrusted_inline(g["name"], "group name"),
                "graded": [
                    {**item,
                     "name": fence_untrusted_inline(item["name"], "assignment name"),
                     "url": absolute_url(item["url"])}
                    for item in graded[:MAX_ITEMS_PER_GROUP]
                ],
                "ungraded": [
                    {**item,
                     "name": fence_untrusted_inline(item["name"], "assignment name")}
                    for item in ungraded[:MAX_ITEMS_PER_GROUP]
                ],
            }
            if len(graded) > MAX_ITEMS_PER_GROUP:
                entry["gradedNote"] = f"showing {MAX_ITEMS_PER_GROUP} of {len(graded)} graded items"
            if len(ungraded) > MAX_ITEMS_PER_GROUP:
                entry["ungradedNote"] = f"showing {MAX_ITEMS_PER_GROUP} of {len(ungraded)} ungraded items"
            shaped_groups.append(entry)

        result: dict[str, Any] = {
            "course": course_name,
            "canvas": canvas_grades,
            "weighting": summary["weighting"],
            "computedCurrentScore": summary["computedCurrentScore"],
            "groups": shaped_groups,
            "url": absolute_url(f"/courses/{course_id}/grades"),
        }
        if summary["weighting"] == "points":
            result["note"] = (
                "This course has no weighted assignment groups — the computed "
                "score is points-based and the REAL breakdown may differ; "
                "check get_grade_weights (syllabus recovery) before answering "
                "what-if questions."
            )
        canvas_score = canvas_grades.get("currentScore")
        ours = summary["computedCurrentScore"]
        if (
            canvas_score is not None and ours is not None
            and abs(canvas_score - ours) > 0.5
        ):
            result["discrepancy"] = (
                f"Canvas reports {canvas_score} but the group math gives {ours} "
                "— the instructor may drop lowest scores or use grading rules "
                "this tool cannot see. Trust the Canvas number for standing; "
                "use the breakdown only for structure."
            )
        return result
