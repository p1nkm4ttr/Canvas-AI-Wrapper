"""Files reachable only through assignment/announcement links get indexed,
carry an origin, and can be fetched through the link when metadata is refused."""

from unittest.mock import AsyncMock, patch

import pytest

from canvas_mcp.core import db
from canvas_mcp.core.files import _sniff_name, get_file_text_cached
from canvas_mcp.core.indexing import index_course


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CANVAS_DB", str(tmp_path / "h.db"))
    db.close_conn()
    yield
    db.close_conn()


LINK = '<a href="https://canvas.school.edu/courses/1/files/77?verifier=v1">Brief</a>'


async def test_index_course_harvests_links_and_attachments():
    async def fake_fetch(endpoint, params=None):
        if endpoint.endswith("/modules"):
            return [{"id": 1, "items": [{"type": "File", "content_id": 10, "title": "slides.pdf"}]}]
        if endpoint.endswith("/files"):
            return [{"id": 10, "display_name": "slides.pdf"}]
        if endpoint.endswith("/assignments"):
            return [{"id": 5, "name": "HW1", "description": f"<p>{LINK}</p>"}]
        if endpoint == "/announcements":
            assert params["end_date"], "announcements must carry an explicit end_date"
            return [{"id": 9, "title": "Welcome", "message": "",
                     "attachments": [{"id": 78, "display_name": "syllabus.pdf",
                                      "url": "https://canvas.school.edu/files/78/download?verifier=v2"}]}]
        if endpoint.endswith("/pages"):
            return []
        raise AssertionError(endpoint)

    calls = []

    async def fake_text(fid, course_id, origin="", fallback_url=None):
        calls.append((fid, origin, fallback_url))
        return {"fileId": fid, "name": f"f{fid}", "status": "ok", "cached": False}

    with patch("canvas_mcp.core.indexing.fetch_all_paginated_results", side_effect=fake_fetch), \
         patch("canvas_mcp.core.indexing.get_file_text_cached", side_effect=fake_text):
        result = await index_course(1)

    assert result["total"] == 3 and result["harvested"] == 2
    by_id = {c[0]: c for c in calls}
    assert by_id[10] == (10, "", None)
    assert by_id[77] == (77, "linked from assignment 'HW1'", "https://canvas.school.edu/courses/1/files/77?verifier=v1")
    assert by_id[78] == (78, "attached to announcement 'Welcome'", "https://canvas.school.edu/files/78/download?verifier=v2")


def test_origin_is_stored_and_shown_in_search():
    db.put_file_text(77, 1, "Brief.pdf", "t", "ok", "the marking rubric says", origin="linked from assignment 'HW1'")
    hits = db.search_file_text("rubric")
    assert hits[0]["fileId"] == 77 and hits[0]["origin"] == "linked from assignment 'HW1'"
    assert db.get_file_text_row(77, "t")["origin"] == "linked from assignment 'HW1'"
    # an origin learned later fills in, but never overwrites one already known
    db.set_file_origin(77, "somewhere else")
    assert db.get_file_text_row(77, "t")["origin"] == "linked from assignment 'HW1'"


def test_sniff_name_from_magic_bytes():
    assert _sniff_name(1, b"%PDF-1.7 ...") == "file-1.pdf"
    assert _sniff_name(2, b"PK\x03\x04" + b"x" * 10 + b"ppt/slides/slide1.xml", "Deck") == "Deck.pptx"
    assert _sniff_name(3, b"PK\x03\x04word/document.xml") == "file-3.docx"
    assert _sniff_name(4, b"plain words here") == "file-4.txt"
    assert _sniff_name(5, b"\x00\x01\x02binary") == "file-5.bin"


async def test_fallback_url_is_used_when_metadata_is_refused():
    refused = {"error": "HTTP error: 403"}
    pdf = b"%PDF-1.4 fake"

    class Extraction:
        status, text, note = "ok", "extracted words", ""

    with patch("canvas_mcp.core.files.make_canvas_request", new=AsyncMock(return_value=refused)), \
         patch("canvas_mcp.core.files._download", new=AsyncMock(return_value=pdf)), \
         patch("canvas_mcp.core.files.extract_text", return_value=Extraction()):
        result = await get_file_text_cached(
            77, 1, origin="linked from assignment 'HW1'",
            fallback_url="https://canvas.school.edu/courses/1/files/77?verifier=v1",
        )
    assert result["status"] == "ok" and result["text"] == "extracted words"
    assert result["name"] == "file-77.pdf"
    assert "through its link" in result["note"]
    assert db.get_file_text_row(77, "link")["origin"] == "linked from assignment 'HW1'"


async def test_no_fallback_means_the_old_error():
    with patch("canvas_mcp.core.files.make_canvas_request", new=AsyncMock(return_value={"error": "HTTP error: 403"})):
        assert "error" in await get_file_text_cached(77, 1)
