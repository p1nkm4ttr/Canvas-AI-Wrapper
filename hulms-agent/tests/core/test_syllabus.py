"""Tests for syllabus resolution and conservative weight parsing."""

from unittest.mock import AsyncMock, patch

import pytest

from canvas_mcp.core.syllabus import parse_weights, resolve_syllabus

# ---------------------------------------------------------------- parsing ---


def test_parses_standard_breakdown():
    text = """Assessment
    Midterm Exam: 25%
    Final Exam: 35%
    Assignments 20%
    Weekly Quizzes (20%)
    """
    weights = parse_weights(text)
    assert [w["percent"] for w in weights] == [25, 35, 20, 20]
    assert weights[0]["name"] == "Midterm Exam"
    assert weights[3]["name"] == "Weekly Quizzes"


def test_grade_scale_rows_are_excluded():
    text = """Grading:
    Homework 40%
    Project 30%
    Final 30%

    Grade scale:
    A 90-100%
    B+: 85% - 89%
    B 80-84%
    """
    weights = parse_weights(text)
    assert len(weights) == 3
    assert all(w["name"] in ("Homework", "Project", "Final") for w in weights)


def test_rejects_totals_far_from_100():
    # Two stray percentages that don't form a breakdown.
    assert parse_weights("Attendance 5%\nParticipation 10%") == []


def test_rejects_single_component():
    assert parse_weights("Final Exam 100%") == []


def test_rejects_component_above_70():
    # "90%" style rows without letter labels are still scale-ish, not weights.
    text = "Coursework 90%\nAttendance 10%"
    assert parse_weights(text) == []


def test_duplicate_labels_counted_once():
    text = "Quizzes 25%\nQuizzes 25%\nMidterm 35%\nHomework 40%"
    weights = parse_weights(text)
    assert sum(w["percent"] for w in weights) == 100
    assert len(weights) == 3


def test_real_lettered_breakdown_with_prose_distractors():
    """Regression fixture shaped like a real Habib syllabus (course 4650):
    lettered components surrounded by prose that re-mentions percentages."""
    text = """(a) Papers: 35%
    papers will be weighted at 15%. A second final comprehensive essay/paper question
    will be worth 20%.
    Paper #1: 15%, 4 - 5 page paper (due end of Week 8/9, two weeks after prompt is
    (b) Midterm & Final Exam: 30%
    held around Week 7/8 and weighted at 15%. The final exam will be cumulative but
    (c) Quizzes & Short Assignments: 25%
    (d) Attendance: 5%
    (e) Participation: 5%
    attendance (min 85% is required to pass the class) and/or have not completed
    """
    weights = parse_weights(text)
    assert [w["percent"] for w in weights] == [35, 30, 25, 5, 5]
    assert weights[0]["name"] == "Papers"
    assert weights[1]["name"] == "Midterm & Final Exam"


def test_decimal_percentages():
    text = "Labs 12.5%\nQuizzes 12.5%\nMidterm 35%\nFinal 40%"
    weights = parse_weights(text)
    assert sum(w["percent"] for w in weights) == 100.0


# ------------------------------------------------------------- resolution ---


@pytest.mark.asyncio
async def test_field_source_wins():
    with patch(
        "canvas_mcp.core.syllabus.make_canvas_request",
        new=AsyncMock(return_value={"syllabus_body": "<h2>Grading</h2><p>Final 100%</p>"}),
    ):
        result = await resolve_syllabus(4361)
    assert result["source"] == "field"
    assert "Grading" in result["text"] and "<h2>" not in result["text"]
    assert "html" in result


@pytest.mark.asyncio
async def test_file_fallback_extracts():
    async def fake_fetch(endpoint, params=None):
        if endpoint.endswith("/files"):
            return [{"id": 777, "display_name": "CS101 Syllabus.pdf"}]
        raise AssertionError(endpoint)

    with patch(
        "canvas_mcp.core.syllabus.make_canvas_request",
        new=AsyncMock(return_value={"syllabus_body": ""}),
    ), patch(
        "canvas_mcp.core.syllabus.fetch_all_paginated_results", side_effect=fake_fetch
    ), patch(
        "canvas_mcp.core.syllabus.get_file_text_cached",
        new=AsyncMock(return_value={"fileId": 777, "name": "CS101 Syllabus.pdf",
                                    "status": "ok", "text": "Midterm 40%...", "note": "",
                                    "url": "/files/777"}),
    ):
        result = await resolve_syllabus(4361)
    assert result["source"] == "file"
    assert result["name"] == "CS101 Syllabus.pdf"
    assert "/files/777" in result["url"]


@pytest.mark.asyncio
async def test_module_walk_when_files_listing_blocked():
    async def fake_fetch(endpoint, params=None):
        if endpoint.endswith("/files"):
            return {"error": "HTTP error: 403"}
        if endpoint.endswith("/modules"):
            return [{"items": [
                {"type": "File", "content_id": 888, "title": "Course Outline Spring 2025"},
            ]}]
        raise AssertionError(endpoint)

    with patch(
        "canvas_mcp.core.syllabus.make_canvas_request",
        new=AsyncMock(return_value={"syllabus_body": ""}),
    ), patch(
        "canvas_mcp.core.syllabus.fetch_all_paginated_results", side_effect=fake_fetch
    ), patch(
        "canvas_mcp.core.syllabus.get_file_text_cached",
        new=AsyncMock(return_value={"fileId": 888, "name": "Course Outline Spring 2025",
                                    "status": "ok", "text": "weights...", "note": "",
                                    "url": "/files/888"}),
    ):
        result = await resolve_syllabus(4361)
    assert result["source"] == "file"


@pytest.mark.asyncio
async def test_scanned_syllabus_file_reported_honestly():
    async def fake_fetch(endpoint, params=None):
        if endpoint.endswith("/files"):
            return [{"id": 999, "display_name": "syllabus.pdf"}]
        raise AssertionError(endpoint)

    with patch(
        "canvas_mcp.core.syllabus.make_canvas_request",
        new=AsyncMock(return_value={"syllabus_body": ""}),
    ), patch(
        "canvas_mcp.core.syllabus.fetch_all_paginated_results", side_effect=fake_fetch
    ), patch(
        "canvas_mcp.core.syllabus.get_file_text_cached",
        new=AsyncMock(return_value={"fileId": 999, "name": "syllabus.pdf",
                                    "status": "scanned", "text": "",
                                    "note": "likely a scan", "url": "/files/999"}),
    ):
        result = await resolve_syllabus(4361)
    assert result["source"] == "none"
    assert "could not extract" in result["note"]
    assert "likely a scan" in result["note"]


@pytest.mark.asyncio
async def test_nothing_found_is_honest():
    async def fake_fetch(endpoint, params=None):
        return []

    with patch(
        "canvas_mcp.core.syllabus.make_canvas_request",
        new=AsyncMock(return_value={"syllabus_body": ""}),
    ), patch(
        "canvas_mcp.core.syllabus.fetch_all_paginated_results", side_effect=fake_fetch
    ):
        result = await resolve_syllabus(4361)
    assert result["source"] == "none"
    assert "No syllabus" in result["note"]
