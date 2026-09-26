"""Tests for grade-standing computation and the get_my_grades tool."""

from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import FastMCP

from canvas_mcp.tools.grades import register_grade_tools, summarize_groups


def _sub(score, state="graded", excused=False):
    return {"score": score, "workflow_state": state, "excused": excused}


def test_weighted_standing_normalizes_over_graded_groups():
    groups = [
        {"name": "Quizzes", "group_weight": 20, "assignments": [
            {"name": "Q1", "points_possible": 10, "submission": _sub(8)},
            {"name": "Q2", "points_possible": 10, "submission": _sub(6)},
        ]},
        {"name": "Midterm", "group_weight": 30, "assignments": [
            {"name": "Mid", "points_possible": 100, "submission": _sub(50)},
        ]},
        {"name": "Final", "group_weight": 50, "assignments": [
            {"name": "Final", "points_possible": 100,
             "submission": {"workflow_state": "unsubmitted"}},
        ]},
    ]
    s = summarize_groups(groups)
    assert s["weighting"] == "groups"
    # quizzes 70% * 20 + midterm 50% * 30, normalized over 50 weight
    assert s["computedCurrentScore"] == round((70 * 20 + 50 * 30) / 50, 2)
    final_group = s["groups"][2]
    assert final_group["groupPercent"] is None
    assert final_group["ungraded"][0]["name"] == "Final"


def test_points_based_when_unweighted():
    groups = [{"name": "All", "group_weight": 0, "assignments": [
        {"name": "HW1", "points_possible": 50, "submission": _sub(40)},
        {"name": "HW2", "points_possible": 50, "submission": _sub(45)},
    ]}]
    s = summarize_groups(groups)
    assert s["weighting"] == "points"
    assert s["computedCurrentScore"] == 85.0


def test_excused_excluded_graded_zero_counts():
    groups = [{"name": "Q", "group_weight": 100, "assignments": [
        {"name": "A", "points_possible": 10, "submission": _sub(0)},
        {"name": "B", "points_possible": 10, "submission": _sub(None, excused=True)},
        {"name": "C", "points_possible": 10, "submission": _sub(10)},
    ]}]
    s = summarize_groups(groups)
    g = s["groups"][0]
    assert g["gradedPossible"] == 20  # excused B out of both sides
    assert g["groupPercent"] == 50.0  # the zero counts


def test_no_grades_yet():
    groups = [{"name": "Q", "group_weight": 100, "assignments": [
        {"name": "A", "points_possible": 10, "submission": {"workflow_state": "unsubmitted"}},
    ]}]
    assert summarize_groups(groups)["computedCurrentScore"] is None


def get_tool(name):
    mcp = FastMCP("test")
    captured = {}
    original = mcp.tool

    def capturing(*a, **k):
        d = original(*a, **k)
        def w(fn):
            captured[fn.__name__] = fn
            return d(fn)
        return w
    mcp.tool = capturing
    register_grade_tools(mcp)
    return captured[name]


@pytest.fixture(autouse=True)
def canvas_url(monkeypatch):
    from canvas_mcp.core.config import reset_config
    monkeypatch.setenv("CANVAS_API_URL", "https://canvas.school.edu/api/v1")
    reset_config()


async def test_tool_reports_discrepancy_and_fences(monkeypatch):
    groups = [{"name": "Quizzes", "group_weight": 100, "assignments": [
        {"name": "Q1", "points_possible": 10, "submission": _sub(8),
         "html_url": "/courses/1/assignments/2"},
    ]}]
    enrollments = [{"grades": {"current_score": 90.0, "current_grade": "A-",
                               "final_score": 40.0}}]

    async def fake_fetch(endpoint, params=None):
        return groups if "assignment_groups" in endpoint else enrollments

    with patch("canvas_mcp.tools.grades.resolve_course",
               new=AsyncMock(return_value=("1", "Discrete Math"))), \
         patch("canvas_mcp.tools.grades.fetch_all_paginated_results",
               side_effect=fake_fetch):
        result = await get_tool("get_my_grades")("discrete")

    assert result["canvas"]["currentScore"] == 90.0
    assert result["computedCurrentScore"] == 80.0
    assert "discrepancy" in result  # 90 vs 80 -> instructor rules invisible
    assert "UNTRUSTED" in result["groups"][0]["graded"][0]["name"]
    assert result["groups"][0]["graded"][0]["url"].startswith("https://canvas.school.edu")
    assert result["url"].endswith("/courses/1/grades")


async def test_tool_unknown_course():
    with patch("canvas_mcp.tools.grades.resolve_course", new=AsyncMock(return_value=None)):
        assert "error" in await get_tool("get_my_grades")("nope")
