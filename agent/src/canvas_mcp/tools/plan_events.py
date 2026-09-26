"""Plan events: the coach's write path to the student's phone calendar.

Events live in the local SQLite database and are served, merged with Canvas
deadlines, as an iCalendar feed the student's iPhone subscribes to. Planning
itself stays model work — these tools only store what was decided.
"""

from datetime import datetime

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..core import db
from ..core.validation import validate_params


def _valid_date(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _valid_time(s: str | None) -> bool:
    if s is None:
        return True
    try:
        datetime.strptime(s, "%H:%M")
        return True
    except ValueError:
        return False


def register_plan_event_tools(mcp: FastMCP) -> None:
    """Register the plan-event tools."""

    @mcp.tool()
    @validate_params
    async def add_plan_event(
        title: str,
        date: str,
        start: str | None = None,
        end: str | None = None,
        course: str | None = None,
        details: str | None = None,
    ) -> dict:
        """Put a study session or milestone on the student's calendar feed
        (their phone subscribes to it). Use when a plan lands on concrete
        dates: revision sessions, paper milestones, exam-prep blocks.
        Times are local (Asia/Karachi). For items that belong in Canvas
        itself, also call create_planner_note.

        Args:
            title: Short event title, e.g. "OS quiz prep: scheduling".
            date: ISO date (YYYY-MM-DD).
            start: Optional start time HH:MM (omit for an all-day event).
            end: Optional end time HH:MM (defaults to start + 1h).
            course: Optional short course label shown in the event title.
            details: Optional description (what to cover, links).
        """
        if not title.strip():
            return {"error": "title must not be empty"}
        if not _valid_date(date):
            return {"error": "date must be YYYY-MM-DD"}
        if not (_valid_time(start) and _valid_time(end)):
            return {"error": "start/end must be HH:MM (24h)"}
        if end and not start:
            return {"error": "end without start makes no sense — give both or neither"}

        event_id = db.add_plan_event(
            title.strip(), date, start, end, (course or "").strip() or None,
            (details or "").strip(),
        )
        return {
            "created": True,
            "id": event_id,
            "title": title.strip(),
            "date": date,
            "start": start,
            "end": end,
            "note": "On the calendar feed; the phone picks it up on its next refresh (~hourly).",
        }

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def list_plan_events(
        date_from: str | None = None, date_to: str | None = None
    ) -> dict:
        """List planned calendar events (study sessions, milestones). Use
        before adding more — check what's already scheduled — or when the
        student asks what their plan looks like.

        Args:
            date_from: Optional ISO date lower bound.
            date_to: Optional ISO date upper bound.
        """
        for bound in (date_from, date_to):
            if bound is not None and not _valid_date(bound):
                return {"error": "date bounds must be YYYY-MM-DD"}
        events = db.list_plan_events(date_from, date_to)
        return {"events": events, "count": len(events)}

    @mcp.tool()
    @validate_params
    async def delete_plan_event(event_id: int) -> dict:
        """Remove a planned event from the calendar feed — when the plan
        changes or a session is done and should disappear.

        Args:
            event_id: The id from add_plan_event / list_plan_events.
        """
        if db.delete_plan_event(event_id):
            return {"deleted": True, "id": event_id}
        return {"error": f"No plan event with id {event_id}."}
