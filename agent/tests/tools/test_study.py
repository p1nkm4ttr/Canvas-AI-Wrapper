"""Tests for the study-mode tools and the file-text cache."""

from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import FastMCP

from canvas_mcp.core import db
from canvas_mcp.tools.study import register_study_tools


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HULMS_DB", str(tmp_path / "study.db"))
    db.close_conn()
    yield
    db.close_conn()


@pytest.fixture(autouse=True)
def canvas_url(monkeypatch):
    from canvas_mcp.core.config import reset_config
    monkeypatch.setenv("CANVAS_API_URL", "https://canvas.school.edu/api/v1")
    reset_config()


def get_tool(name: str):
    mcp = FastMCP("test")
    captured = {}
    original_tool = mcp.tool

    def capturing_tool(*args, **kwargs):
        decorator = original_tool(*args, **kwargs)

        def wrapper(fn):
            captured[fn.__name__] = fn
            return decorator(fn)

        return wrapper

    mcp.tool = capturing_tool
    register_study_tools(mcp)
    return captured[name]


@pytest.fixture
def resolve_ok():
    with patch(
        "canvas_mcp.tools.study.resolve_course",
        new=AsyncMock(return_value=("4361", "Data Structures")),
    ):
        yield


MODULES = [
    {"id": 10, "name": "Week 03", "items": [
        {"type": "SubHeader", "title": "Lecture Slides"},
        {"type": "File", "title": "Queues.pdf", "content_id": 500,
         "html_url": "/courses/4361/modules/items/1"},
        {"type": "Quiz", "title": "Quiz 3", "content_id": 900,
         "content_details": {"due_at": "2026-02-10T18:59:00Z"},
         "html_url": "/courses/4361/modules/items/2"},
        {"type": "Page", "title": "Notes", "page_url": "notes-week-3",
         "html_url": "/courses/4361/modules/items/3"},
    ]},
    {"id": 11, "name": "Week 04", "items": [
        {"type": "Assignment", "title": "HW2", "content_id": 901,
         "content_details": {"due_at": "2026-02-20T18:59:00Z"}},
    ]},
]


# --------------------------------------------------------- get_study_context

async def test_study_context_requires_exactly_one_target(resolve_ok):
    tool = get_tool("get_study_context")
    assert "error" in await tool("ds")
    assert "error" in await tool("ds", module_id=10, quiz_id=900)


async def test_study_context_walks_module_and_extracts(resolve_ok):
    file_result = {"fileId": 500, "name": "Queues.pdf", "status": "ok",
                   "text": "FIFO order", "note": "", "url": "/files/500"}

    async def fake_request(method, endpoint, **kw):
        assert endpoint.endswith("/pages/notes-week-3")
        return {"body": "<p>Amortized analysis</p>"}

    with patch("canvas_mcp.tools.study.fetch_all_paginated_results",
               new=AsyncMock(return_value=MODULES)), \
         patch("canvas_mcp.tools.study.get_file_text_cached",
               new=AsyncMock(return_value=file_result)), \
         patch("canvas_mcp.tools.study.make_canvas_request", side_effect=fake_request):
        result = await get_tool("get_study_context")("ds", module_id=10)

    assert result["module"].endswith("Week 03>>>")
    assert result["itemCount"] == 4
    assert result["extractedCount"] == 2  # the file and the page
    file_entry = result["items"][1]
    assert "FIFO order" in file_entry["text"] and "UNTRUSTED" in file_entry["text"]
    quiz_entry = result["items"][2]
    assert "not readable via the API" in quiz_entry["note"]
    assert quiz_entry["due"]
    page_entry = result["items"][3]
    assert "Amortized analysis" in page_entry["text"]


async def test_study_context_resolves_quiz_to_its_module(resolve_ok):
    with patch("canvas_mcp.tools.study.fetch_all_paginated_results",
               new=AsyncMock(return_value=MODULES)), \
         patch("canvas_mcp.tools.study.get_file_text_cached",
               new=AsyncMock(return_value={"fileId": 500, "name": "Queues.pdf",
                                           "status": "ok", "text": "x", "note": "",
                                           "url": "/files/500"})), \
         patch("canvas_mcp.tools.study.make_canvas_request",
               new=AsyncMock(return_value={"body": ""})):
        result = await get_tool("get_study_context")("ds", quiz_id=900)
    assert "Week 03" in result["module"]


async def test_study_context_resolves_date_to_nearest_module(resolve_ok):
    with patch("canvas_mcp.tools.study.fetch_all_paginated_results",
               new=AsyncMock(return_value=MODULES)), \
         patch("canvas_mcp.tools.study.make_canvas_request",
               new=AsyncMock(return_value={"body": ""})):
        result = await get_tool("get_study_context")("ds", date="2026-02-19")
    assert "Week 04" in result["module"]


async def test_study_context_reports_skipped_files_honestly(resolve_ok):
    scanned = {"fileId": 500, "name": "Queues.pdf", "status": "scanned",
               "text": "", "note": "likely a scan", "url": "/files/500"}
    with patch("canvas_mcp.tools.study.fetch_all_paginated_results",
               new=AsyncMock(return_value=MODULES)), \
         patch("canvas_mcp.tools.study.get_file_text_cached",
               new=AsyncMock(return_value=scanned)), \
         patch("canvas_mcp.tools.study.make_canvas_request",
               new=AsyncMock(return_value={"body": ""})):
        result = await get_tool("get_study_context")("ds", module_id=10)
    assert "coverage" in result
    assert "scanned" in result["coverage"]


async def test_study_context_unknown_module(resolve_ok):
    with patch("canvas_mcp.tools.study.fetch_all_paginated_results",
               new=AsyncMock(return_value=MODULES)):
        result = await get_tool("get_study_context")("ds", module_id=999)
    assert "error" in result


# ------------------------------------------------------------- get_file_text

async def test_file_text_truncation_note(resolve_ok):
    big = {"fileId": 1, "name": "n.pdf", "status": "ok",
           "text": "x" * 100, "note": "", "url": "/files/1"}
    with patch("canvas_mcp.tools.study.get_file_text_cached",
               new=AsyncMock(return_value=big)):
        result = await get_tool("get_file_text")(1, max_chars=10)
    assert "truncated" in result["text"]
    assert "Truncated" in result["note"]


async def test_file_text_propagates_error():
    with patch("canvas_mcp.tools.study.get_file_text_cached",
               new=AsyncMock(return_value={"error": "Could not read file 1: nope"})):
        assert "error" in await get_tool("get_file_text")(1)


# ------------------------------------------------------ search_course_content

async def test_search_finds_seeded_text():
    db.put_file_text(500, 4361, "Queues.pdf", "2026-01-01", "ok",
                     "A queue is FIFO; a deque allows both ends.")
    result = await get_tool("search_course_content")("deque")
    assert result["count"] == 1
    assert result["results"][0]["fileId"] == 500
    assert "deque" in result["results"][0]["snippet"]


async def test_search_empty_index_says_so():
    result = await get_tool("search_course_content")("dijkstra")
    assert result["count"] == 0
    assert "note" in result and "hulms-extract" in result["note"]


async def test_search_scoped_to_course():
    db.put_file_text(1, 100, "a.pdf", "t", "ok", "binary heaps everywhere")
    db.put_file_text(2, 200, "b.pdf", "t", "ok", "binary heaps elsewhere")
    with patch("canvas_mcp.tools.study.resolve_course",
               new=AsyncMock(return_value=("100", "Course A"))):
        result = await get_tool("search_course_content")("heaps", course="Course A")
    assert result["count"] == 1
    assert result["results"][0]["courseId"] if "courseId" in result["results"][0] else True
    assert result["results"][0]["fileId"] == 1


async def test_search_rejects_empty_query():
    assert "error" in await get_tool("search_course_content")("   ")


# ------------------------------------------------- get_announcement_context

async def test_announcement_context_resolves_links(resolve_ok):
    ann = {
        "title": "Quiz material posted",
        "posted_at": "2026-08-20T05:00:00Z",
        "message": '<p>See <a href="https://x/courses/4361/files/500">slides</a> and '
                   '<a href="https://x/courses/4361/assignments/901">HW2</a></p>',
        "html_url": "/courses/4361/discussion_topics/7",
    }
    file_result = {"fileId": 500, "name": "Queues.pdf", "status": "ok",
                   "text": "FIFO", "note": "", "url": "/files/500"}

    async def fake_request(method, endpoint, **kw):
        if "discussion_topics" in endpoint:
            return ann
        if endpoint.endswith("/assignments/901"):
            return {"name": "HW2", "due_at": "2026-02-20T18:59:00Z",
                    "points_possible": 100, "html_url": "/courses/4361/assignments/901"}
        raise AssertionError(endpoint)

    with patch("canvas_mcp.tools.study.make_canvas_request", side_effect=fake_request), \
         patch("canvas_mcp.tools.study.get_file_text_cached",
               new=AsyncMock(return_value=file_result)):
        result = await get_tool("get_announcement_context")(7, "ds")

    assert result["linkedCount"] == 2
    kinds = {entry["kind"] for entry in result["linked"]}
    assert kinds == {"file", "assignment"}
    assert "FIFO" in result["linked"][0]["text"]
    assert "UNTRUSTED" in result["body"]


async def test_announcement_context_propagates_error(resolve_ok):
    with patch("canvas_mcp.tools.study.make_canvas_request",
               new=AsyncMock(return_value={"error": "HTTP error: 404"})):
        assert "error" in await get_tool("get_announcement_context")(7, "ds")


# ------------------------------------------------------- file cache behavior

async def test_file_cache_keyed_on_updated_at():
    from canvas_mcp.core.files import get_file_text_cached

    meta = {"id": 500, "display_name": "q.pdf", "updated_at": "2026-01-01",
            "size": 10, "url": "https://files/500?verifier=x"}

    with patch("canvas_mcp.core.files.make_canvas_request",
               new=AsyncMock(return_value=meta)), \
         patch("canvas_mcp.core.files._download",
               new=AsyncMock(return_value=b"data")) as dl, \
         patch("canvas_mcp.core.files.extract_text") as ex:
        from canvas_mcp.core.extract import Extraction
        ex.return_value = Extraction("ok", "queue text")

        first = await get_file_text_cached(500)
        second = await get_file_text_cached(500)
        assert first["text"] == second["text"] == "queue text"
        assert dl.await_count == 1, "second read must come from cache"

        # instructor re-uploads: updated_at changes -> re-extract
        meta["updated_at"] = "2026-02-02"
        await get_file_text_cached(500)
        assert dl.await_count == 2


async def test_download_failure_is_not_cached():
    from canvas_mcp.core.files import get_file_text_cached

    meta = {"id": 501, "display_name": "q.pdf", "updated_at": "2026-01-01",
            "size": 10, "url": "https://files/501?verifier=x"}
    with patch("canvas_mcp.core.files.make_canvas_request",
               new=AsyncMock(return_value=meta)), \
         patch("canvas_mcp.core.files._download", new=AsyncMock(return_value=None)):
        result = await get_file_text_cached(501)
    assert "error" in result
    assert db.get_file_text_row(501, "2026-01-01") is None


# ---------------------------------------------------- read_local_document

@pytest.fixture
def fake_spaces(tmp_path, monkeypatch):
    import canvas_mcp.core.config as config_module
    root = tmp_path / "hulms-agent"
    root.mkdir()
    monkeypatch.setattr(config_module, "REPO_ROOT", root)
    spaces = tmp_path / "spaces"
    (spaces / "c1").mkdir(parents=True)
    (spaces / "c2").mkdir()
    (spaces / "c1" / "syllabus.txt").write_text("Grading: Final 40%", encoding="utf-8")
    (spaces / "c2" / "syllabus.txt").write_text("Other course", encoding="utf-8")
    (spaces / "c1" / "notes.txt").write_text("hello notes", encoding="utf-8")
    yield spaces


async def test_read_local_document_by_filename(fake_spaces):
    result = await get_tool("read_local_document")("notes.txt")
    assert result["status"] == "ok"
    assert "hello notes" in result["text"]
    assert "UNTRUSTED" in result["text"]


async def test_read_local_document_ambiguous_asks_to_disambiguate(fake_spaces):
    result = await get_tool("read_local_document")("syllabus.txt")
    assert "matches" in result
    assert "c1/syllabus.txt" in result["matches"]


async def test_read_local_document_space_qualified(fake_spaces):
    result = await get_tool("read_local_document")("c1/syllabus.txt")
    assert "Final 40%" in result["text"]


async def test_read_local_document_rejects_escape(fake_spaces):
    assert "error" in await get_tool("read_local_document")("../secrets.txt")
    assert "error" in await get_tool("read_local_document")("C:/Windows/win.ini")


async def test_read_local_document_not_found_lists_available(fake_spaces):
    result = await get_tool("read_local_document")("missing.pdf")
    assert "error" in result
    assert any("notes.txt" in a for a in result["available"])


# ---------------------------------------------------- index_course_files

async def test_index_course_files_reports_counts(resolve_ok, monkeypatch):
    async def fake_index(course_id, include_all_files=True, progress=None):
        assert include_all_files is True
        return {"counts": {"ok": 12, "scanned": 2, "skipped-format": 3},
                "total": 17, "notes": ["files listing blocked (module files still indexed): HTTP error: 403"]}
    import canvas_mcp.core.indexing as indexing
    monkeypatch.setattr(indexing, "index_course", fake_index)
    result = await get_tool("index_course_files")("ds")
    assert result["filesConsidered"] == 17
    assert result["thisRun"]["ok"] == 12
    assert "coverage" in result and "blocked" in result["coverage"]
    assert "2" in result["note"] or "scans" in result["note"]  # skipped mentioned


async def test_index_course_files_unknown_course(resolve_none):
    assert "error" in await get_tool("index_course_files")("Basket Weaving")


@pytest.fixture
def resolve_none():
    with patch("canvas_mcp.tools.study.resolve_course", new=AsyncMock(return_value=None)):
        yield
