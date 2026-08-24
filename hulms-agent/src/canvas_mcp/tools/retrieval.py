"""Spaced retrieval: the missed-question ledger.

The build brief's goal statement: retrieval practice — generate questions,
track what was missed, resurface it later. These tools are the tracking and
resurfacing half; question generation stays model work. Scheduling is a
plain Leitner ladder (1/3/7/14/30 days), computed here — never by the model.
"""

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..core import db
from ..core.validation import validate_params


def register_retrieval_tools(mcp: FastMCP) -> None:
    """Register the spaced-retrieval tools."""

    @mcp.tool()
    @validate_params
    async def log_retrieval_item(
        question: str,
        answer: str,
        course: str | None = None,
        source: str | None = None,
    ) -> dict:
        """Log a practice question worth re-asking later — every question the
        student gets WRONG during a study session, plus any question they
        found hard. It re-surfaces on a spaced schedule (1/3/7/14/30 days).

        Args:
            question: The question, self-contained enough to re-ask cold.
            answer: The correct answer (checked against on review).
            course: Short course label, e.g. "OS" or "Nature of Computation".
            source: Where it came from (file/module), so the student can restudy.
        """
        if not question.strip() or not answer.strip():
            return {"error": "question and answer are both required"}
        item_id = db.add_retrieval_item(
            question.strip(), answer.strip(), (course or "").strip() or None,
            (source or "").strip(),
        )
        return {
            "logged": True,
            "id": item_id,
            "nextReview": "tomorrow",
            "note": "It will appear in get_due_reviews on its due date.",
        }

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_due_reviews(course: str | None = None, limit: int = 10) -> dict:
        """Practice questions now due for review. CALL THIS AT THE START of
        every study session and re-ask them before any new material —
        spaced review of past misses beats new coverage. Also for "what
        should I review today".

        Args:
            course: Optional course label filter (substring match).
            limit: Max items (default 10).
        """
        items = db.due_retrieval_items(course, limit=max(1, min(limit, 50)))
        result = {"due": items, "count": len(items)}
        if not items:
            result["note"] = "Nothing due for review — clear to cover new material."
        else:
            result["note"] = (
                "Ask these cold (do not show the answers first); then call "
                "record_review_result for each with whether the student got it."
            )
        return result

    @mcp.tool()
    @validate_params
    async def record_review_result(item_id: int, correct: bool) -> dict:
        """Record the outcome of one re-asked review question. Correct climbs
        the spacing ladder (1→3→7→14→30 days, then retires); wrong resets
        to tomorrow. Call once per question reviewed.

        Args:
            item_id: The id from get_due_reviews.
            correct: Whether the student answered correctly.
        """
        state = db.record_retrieval_result(item_id, correct)
        if state is None:
            return {"error": f"No active review item with id {item_id}."}
        if state.get("retired"):
            return {
                "recorded": True, "id": item_id, "retired": True,
                "note": "Answered correctly at the top of the ladder — retired for good.",
            }
        return {
            "recorded": True, "id": item_id, "box": state["box"],
            "nextDue": state["nextDue"],
        }
