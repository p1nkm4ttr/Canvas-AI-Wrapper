"""Tests for plan-event storage and iCalendar generation."""

from zoneinfo import ZoneInfo

import pytest

from canvas_mcp.core import db
from canvas_mcp.core.ics import build_ics

KHI = ZoneInfo("Asia/Karachi")


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HULMS_DB", str(tmp_path / "ics.db"))
    db.close_conn()
    yield
    db.close_conn()


def test_plan_event_roundtrip_and_delete():
    eid = db.add_plan_event("OS quiz prep", "2026-09-07", "19:00", "20:30", "OS", "scheduling")
    events = db.list_plan_events()
    assert events[0]["title"] == "OS quiz prep" and events[0]["start"] == "19:00"
    assert db.delete_plan_event(eid) is True
    assert db.list_plan_events() == []
    assert db.delete_plan_event(999) is False


def test_list_plan_events_date_bounds():
    db.add_plan_event("early", "2026-09-01")
    db.add_plan_event("late", "2026-10-01")
    assert [e["title"] for e in db.list_plan_events(date_from="2026-09-15")] == ["late"]
    assert [e["title"] for e in db.list_plan_events(date_to="2026-09-15")] == ["early"]


def test_ics_timed_event_converts_karachi_to_utc():
    ics = build_ics([], [{"id": 1, "title": "OS prep", "date": "2026-09-07",
                          "start": "19:00", "end": "20:30", "course": "OS", "details": "ch 5"}], KHI)
    assert "BEGIN:VCALENDAR" in ics and ics.endswith("\r\n")
    # 19:00 Karachi == 14:00 UTC
    assert "DTSTART:20260907T140000Z" in ics
    assert "DTEND:20260907T153000Z" in ics
    assert "SUMMARY:[OS] OS prep" in ics
    assert "UID:hulms-plan-1@hulms" in ics


def test_ics_all_day_event():
    ics = build_ics([], [{"id": 2, "title": "Paper due soon", "date": "2026-09-10",
                          "start": None, "end": None, "course": None, "details": ""}], KHI)
    assert "DTSTART;VALUE=DATE:20260910" in ics
    assert "DTEND;VALUE=DATE:20260911" in ics


def test_ics_canvas_deadline_and_escaping():
    agenda = [{"due": "2026-09-08T23:59:00+05:00", "daysUntil": 3, "course": "CS 101-L1",
               "type": "assignment", "title": "HW; with, commas", "status": "todo",
               "url": "https://x/courses/1/assignments/2"}]
    ics = build_ics(agenda, [], KHI)
    assert r"SUMMARY:[CS 101] Due: HW\; with\, commas" in ics
    # 23:59 Karachi == 18:59 UTC; event ends at the due moment
    assert "DTEND:20260908T185900Z" in ics
    assert "hulms-canvas-" in ics


def test_ics_malformed_entries_are_skipped_not_fatal():
    ics = build_ics([{"due": "not-a-date", "title": "x"}],
                    [{"id": 3, "title": "bad", "date": "junk"}], KHI)
    assert "BEGIN:VCALENDAR" in ics
    assert "VEVENT" not in ics


def test_announcements_are_not_calendar_events():
    agenda = [{"due": "2026-09-08T10:00:00+05:00", "type": "announcement",
               "title": "Lab uploaded", "course": "OS", "status": "-", "url": "u"}]
    assert "VEVENT" not in build_ics(agenda, [], KHI)


def test_brief_renders_as_all_day_event():
    details = "DUE THIS WEEK:" + chr(10) + "  HW1"
    ics = build_ics([], [], KHI, briefs=[{"date": "2026-08-25",
                                          "summary": "2 due · 5 reviews",
                                          "details": details}])
    assert "UID:hulms-brief-2026-08-25@hulms" in ics
    assert "DTSTART;VALUE=DATE:20260825" in ics
    assert "SUMMARY:☀ HULMS: 2 due · 5 reviews" in ics
    assert "DESCRIPTION:DUE THIS WEEK:" + chr(92) + "n  HW1" in ics
