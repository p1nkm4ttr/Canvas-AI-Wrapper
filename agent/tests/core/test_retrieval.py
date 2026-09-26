"""Tests for spaced-retrieval storage and Leitner progression."""

from datetime import date, timedelta

import pytest

from canvas_mcp.core import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HULMS_DB", str(tmp_path / "r.db"))
    db.close_conn()
    yield
    db.close_conn()


def _today_plus(n):
    return (date.today() + timedelta(days=n)).isoformat()


def test_new_item_due_tomorrow_box_one():
    db.add_retrieval_item("What is FIFO?", "First in, first out", "OS")
    assert db.due_retrieval_items() == []  # not due today
    items = db.due_retrieval_items(on_or_before=_today_plus(1))
    assert items[0]["box"] == 1 and items[0]["question"] == "What is FIFO?"


def test_correct_climbs_ladder_and_retires():
    iid = db.add_retrieval_item("Q", "A", first_due=date.today().isoformat())
    expected = [(True, 2, 3), (True, 3, 7), (True, 4, 14), (True, 5, 30)]
    for correct, box, days in expected:
        state = db.record_retrieval_result(iid, correct)
        assert state["box"] == box
        assert state["nextDue"] == _today_plus(days)
    # correct at box 5 -> retired for good
    state = db.record_retrieval_result(iid, True)
    assert state["retired"] is True
    assert db.due_retrieval_items(on_or_before=_today_plus(365)) == []
    assert db.record_retrieval_result(iid, True) is None  # retired = inactive


def test_wrong_resets_to_box_one_due_tomorrow():
    iid = db.add_retrieval_item("Q", "A", first_due=date.today().isoformat())
    db.record_retrieval_result(iid, True)   # box 2
    db.record_retrieval_result(iid, True)   # box 3
    state = db.record_retrieval_result(iid, False)
    assert state["box"] == 1
    assert state["nextDue"] == _today_plus(1)


def test_course_filter_and_counts():
    db.add_retrieval_item("q1", "a", "OS", first_due=date.today().isoformat())
    db.add_retrieval_item("q2", "a", "Nature of Computation", first_due=date.today().isoformat())
    assert len(db.due_retrieval_items("OS")) == 1
    counts = db.count_due_retrieval_items()
    assert counts["OS"] == 1 and counts["Nature of Computation"] == 1


def test_briefs_roundtrip():
    db.upsert_brief("2026-08-25", "2 due · 1 announcement", "DUE:\n  x")
    db.upsert_brief("2026-08-25", "updated", "d")  # idempotent per day
    briefs = db.recent_briefs(days=400)
    assert len([b for b in briefs if b["date"] == "2026-08-25"]) == 1
