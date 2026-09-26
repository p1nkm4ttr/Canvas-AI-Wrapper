"""Tests for the backup CLI internals."""

import zipfile

import pytest

import canvas_mcp.core.config as config_module
from canvas_mcp.backup_cli import create_backup, prune_backups
from canvas_mcp.core import db


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CANVAS_DB", str(tmp_path / "canvas.db"))
    db.close_conn()
    root = tmp_path / "agent"
    root.mkdir()
    monkeypatch.setattr(config_module, "REPO_ROOT", root)
    spaces = tmp_path / "spaces"
    (spaces / "c1").mkdir(parents=True)
    (spaces / "c1" / "memory.md").write_text("remember this", encoding="utf-8")
    (spaces / "c1" / "syllabus.pdf").write_bytes(b"%PDF fake")
    yield tmp_path
    db.close_conn()


def test_backup_contains_db_and_spaces_never_env(env):
    db.add_plan_event("session", "2026-09-01")  # ensure the db exists
    (env / "agent" / ".env").write_text("CANVAS_TOKEN=secret", encoding="utf-8")

    target = create_backup(env / "backups")
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    assert "canvas.db" in names
    assert "spaces/c1/memory.md" in names
    assert "spaces/c1/syllabus.pdf" in names
    assert not any(".env" in n for n in names)
    assert not any(n.endswith(".tmp") for n in names)


def test_backup_db_snapshot_is_readable(env):
    db.add_plan_event("session", "2026-09-01")
    target = create_backup(env / "backups")
    import sqlite3
    with zipfile.ZipFile(target) as zf:
        zf.extract("canvas.db", env / "restored")
    conn = sqlite3.connect(env / "restored" / "canvas.db")
    rows = conn.execute("SELECT title FROM plan_events").fetchall()
    conn.close()
    assert rows == [("session",)]


def test_prune_keeps_newest(env):
    dest = env / "backups"
    dest.mkdir()
    for i in range(5):
        (dest / f"canvas-backup-2026082{i}-000000.zip").write_bytes(b"x")
    removed = prune_backups(dest, keep=2)
    assert len(removed) == 3
    survivors = sorted(p.name for p in dest.glob("canvas-backup-*.zip"))
    assert survivors == ["canvas-backup-20260823-000000.zip",
                        "canvas-backup-20260824-000000.zip"]
