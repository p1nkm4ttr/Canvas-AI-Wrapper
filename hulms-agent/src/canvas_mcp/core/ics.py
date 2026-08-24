"""iCalendar feed generation: Canvas deadlines + coach plan events.

Pure text generation — RFC 5545 basics: CRLF line endings, escaped text,
stable UIDs so subscribed calendars update instead of duplicating.
"""

from datetime import datetime, timedelta, timezone
from hashlib import md5
from typing import Any


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _agenda_vevent(item: dict[str, Any]) -> list[str] | None:
    """A Canvas deadline as a 30-minute event ending at the due time."""
    # Announcements carry a posted date, not a deadline — calendar noise.
    if (item.get("type") or "") == "announcement":
        return None
    due_raw = item.get("due")
    if not due_raw:
        return None
    try:
        due = datetime.fromisoformat(due_raw)
    except ValueError:
        return None
    uid_src = f"{item.get('url', '')}|{due_raw}|{item.get('title', '')}"
    uid = "hulms-canvas-" + md5(uid_src.encode()).hexdigest()
    title = f"Due: {item.get('title', 'Untitled')}"
    course = item.get("course") or ""
    prefix = f"[{course.split('-')[0]}] " if course and course != "-" else ""
    desc = f"{item.get('type', '')} — {item.get('status', '')}\n{item.get('url', '')}"
    return [
        "BEGIN:VEVENT",
        f"UID:{uid}@hulms",
        f"DTSTAMP:{_utc(due)}",
        f"DTSTART:{_utc(due - timedelta(minutes=30))}",
        f"DTEND:{_utc(due)}",
        f"SUMMARY:{_esc(prefix + title)}",
        f"DESCRIPTION:{_esc(desc)}",
        "END:VEVENT",
    ]


def _plan_vevent(ev: dict[str, Any], tz) -> list[str] | None:
    """A coach/student plan event; timed when start is set, else all-day."""
    uid = f"hulms-plan-{ev['id']}@hulms"
    title = ev.get("title") or "Study session"
    course = ev.get("course") or ""
    summary = (f"[{course}] " if course else "") + title
    lines = ["BEGIN:VEVENT", f"UID:{uid}",
             f"DTSTAMP:{_utc(datetime.now(timezone.utc))}"]
    try:
        day = datetime.strptime(ev["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        return None

    if ev.get("start"):
        try:
            sh, sm = map(int, ev["start"].split(":"))
        except ValueError:
            return None
        start = datetime(day.year, day.month, day.day, sh, sm, tzinfo=tz)
        if ev.get("end"):
            try:
                eh, em = map(int, ev["end"].split(":"))
                end = datetime(day.year, day.month, day.day, eh, em, tzinfo=tz)
            except ValueError:
                end = start + timedelta(hours=1)
        else:
            end = start + timedelta(hours=1)
        if end <= start:
            end = start + timedelta(hours=1)
        lines += [f"DTSTART:{_utc(start)}", f"DTEND:{_utc(end)}"]
    else:
        next_day = day + timedelta(days=1)
        lines += [
            f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{next_day.strftime('%Y%m%d')}",
        ]

    lines += [f"SUMMARY:{_esc(summary)}"]
    if ev.get("details"):
        lines += [f"DESCRIPTION:{_esc(ev['details'])}"]
    lines += ["END:VEVENT"]
    return lines


def _brief_vevent(brief: dict[str, Any]) -> list[str] | None:
    """A daily brief as an all-day event: '☀ HULMS: 2 due · 3 announcements'."""
    try:
        day = datetime.strptime(brief["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        return None
    next_day = day + timedelta(days=1)
    lines = [
        "BEGIN:VEVENT",
        f"UID:hulms-brief-{brief['date']}@hulms",
        f"DTSTAMP:{_utc(datetime.now(timezone.utc))}",
        f"DTSTART;VALUE=DATE:{day.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{next_day.strftime('%Y%m%d')}",
        f"SUMMARY:{_esc('☀ HULMS: ' + (brief.get('summary') or 'brief'))}",
    ]
    if brief.get("details"):
        lines.append(f"DESCRIPTION:{_esc(brief['details'])}")
    lines.append("END:VEVENT")
    return lines


def build_ics(
    agenda_items: list[dict],
    plan_events: list[dict],
    tz,
    briefs: list[dict] | None = None,
) -> str:
    """The merged HULMS calendar as an iCalendar document."""
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//HULMS Assistant//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:HULMS",
        "X-WR-CALDESC:Canvas deadlines and study plan",
        "X-PUBLISHED-TTL:PT1H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
    ]
    for item in agenda_items:
        ev = _agenda_vevent(item)
        if ev:
            lines += ev
    for pe in plan_events:
        ev = _plan_vevent(pe, tz)
        if ev:
            lines += ev
    for brief in briefs or []:
        ev = _brief_vevent(brief)
        if ev:
            lines += ev
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
