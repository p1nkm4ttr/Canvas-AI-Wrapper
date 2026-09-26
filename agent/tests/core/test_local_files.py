"""Tests for dropped-file extraction, caching, and search indexing."""

import pytest

import canvas_mcp.core.config as config_module
from canvas_mcp.core import db
from canvas_mcp.core.local_files import extract_local_file, index_local_spaces


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HULMS_DB", str(tmp_path / "l.db"))
    db.close_conn()
    root = tmp_path / "hulms-agent"
    root.mkdir()
    monkeypatch.setattr(config_module, "REPO_ROOT", root)
    spaces = tmp_path / "spaces"
    (spaces / "c5536").mkdir(parents=True)
    (spaces / "general").mkdir()
    yield spaces
    db.close_conn()


def test_extract_caches_by_mtime(env):
    f = env / "c5536" / "syllabus.txt"
    f.write_text("Turing machines decide languages", encoding="utf-8")
    first = extract_local_file(f)
    assert first["status"] == "ok" and first["cached"] is False
    second = extract_local_file(f)
    assert second["cached"] is True
    # touch with new content -> mtime changes -> re-extract
    import os
    import time
    f.write_text("updated content", encoding="utf-8")
    os.utime(f, (time.time() + 5, time.time() + 5))
    third = extract_local_file(f)
    assert third["cached"] is False and "updated" in third["text"]


def test_local_files_searchable_with_course_scope(env):
    f = env / "c5536" / "syllabus.txt"
    f.write_text("Grading: pumping lemma quiz worth 20%", encoding="utf-8")
    extract_local_file(f)
    hits = db.search_file_text("pumping lemma", course_id=5536)
    assert hits and hits[0]["localPath"] == "c5536/syllabus.txt"
    assert "fileId" not in hits[0]
    # scoped away from the course -> no hit
    assert db.search_file_text("pumping lemma", course_id=9999) == []


def test_sweep_skips_reserved_and_counts_new(env):
    (env / "c5536" / "memory.md").write_text("secret notes", encoding="utf-8")
    (env / "c5536" / "handout.txt").write_text("dijkstra shortest path", encoding="utf-8")
    (env / "general" / "video.mov").write_bytes(b"xx")
    result = index_local_spaces()
    assert result["indexed"] == 1
    assert result["new"] == ["c5536/handout.txt"]
    assert db.search_file_text("secret notes") == []
    # second sweep: everything cached, nothing new
    again = index_local_spaces()
    assert again["indexed"] == 1 and again["new"] == []
