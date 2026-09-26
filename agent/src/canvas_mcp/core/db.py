"""SQLite cache for Canvas API responses.

Single user, local process — stdlib sqlite3, one file, no ORM. The cache
exists so bulk reads (the corpus audit, file-text extraction later) are free
on re-run, and because concluded-course access is institution-dependent and
may be withdrawn: anything fetched from a completed course is worth keeping.

Writes are never cached; error responses are never cached.
"""

import json
import os
import sqlite3
import time
from typing import Any

_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS http_cache (
    key        TEXT PRIMARY KEY,
    endpoint   TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    payload    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS file_text (
    file_id      INTEGER PRIMARY KEY,
    course_id    INTEGER,
    display_name TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL DEFAULT '',
    extracted_at REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS file_search USING fts5(
    text, display_name, file_id UNINDEXED, course_id UNINDEXED
);
CREATE TABLE IF NOT EXISTS plan_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    date       TEXT NOT NULL,            -- YYYY-MM-DD (local)
    start_time TEXT,                     -- HH:MM local, NULL = all-day
    end_time   TEXT,
    course     TEXT,
    details    TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS retrieval_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    course      TEXT,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    box         INTEGER NOT NULL DEFAULT 1,   -- Leitner box 1..5
    due_date    TEXT NOT NULL,                -- YYYY-MM-DD next review
    reviews     INTEGER NOT NULL DEFAULT 0,
    last_result TEXT,
    retired     INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS briefs (
    date       TEXT PRIMARY KEY,              -- YYYY-MM-DD
    summary    TEXT NOT NULL,
    details    TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS local_text (
    path         TEXT PRIMARY KEY,            -- spaceId/filename
    course_id    INTEGER,                     -- NULL for the general space
    display_name TEXT NOT NULL,
    mtime        REAL NOT NULL,               -- cache key
    status       TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    text         TEXT NOT NULL DEFAULT '',
    extracted_at REAL NOT NULL
);
"""

# Leitner intervals: days until the next review for a CORRECT answer in box N.
LEITNER_INTERVALS = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}


def db_path() -> str:
    """Cache file location. HULMS_DB overrides; default hulms.db at the repo
    root (NOT the CWD — MCP clients spawn the server from arbitrary dirs)."""
    from .config import REPO_ROOT
    return os.getenv("HULMS_DB") or str(REPO_ROOT / "hulms.db")


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(db_path())
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn


def close_conn() -> None:
    """Close and forget the connection (tests, or after HULMS_DB changes)."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def make_key(endpoint: str, params: dict[str, Any] | None) -> str:
    """Stable cache key: endpoint plus sorted, JSON-encoded params."""
    return endpoint + "?" + json.dumps(params or {}, sort_keys=True, default=str)


def cache_get(key: str, max_age_seconds: float) -> Any | None:
    """Return the cached payload if present and younger than max_age_seconds."""
    row = get_conn().execute(
        "SELECT fetched_at, payload FROM http_cache WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    fetched_at, payload = row
    if time.time() - fetched_at > max_age_seconds:
        return None
    return json.loads(payload)


def cache_put(key: str, endpoint: str, payload: Any) -> None:
    get_conn().execute(
        "INSERT OR REPLACE INTO http_cache (key, endpoint, fetched_at, payload) "
        "VALUES (?, ?, ?, ?)",
        (key, endpoint, time.time(), json.dumps(payload, default=str)),
    )
    get_conn().commit()


async def cached_fetch_all(
    endpoint: str,
    params: dict[str, Any] | None = None,
    max_age_seconds: float = 7 * 86400,
) -> Any:
    """fetch_all_paginated_results with a SQLite read-through cache.

    Defaults to a 7-day TTL — tuned for concluded-course content, which only
    changes if the institution edits or withdraws it. Callers wanting fresh
    data (the agenda) should use fetch_all_paginated_results directly.

    Error responses pass through uncached, so a transient failure never
    poisons a week of re-runs.
    """
    from .client import fetch_all_paginated_results

    key = make_key(endpoint, params)
    hit = cache_get(key, max_age_seconds)
    if hit is not None:
        return hit

    result = await fetch_all_paginated_results(endpoint, params)
    if isinstance(result, dict) and "error" in result:
        return result
    cache_put(key, endpoint, result)
    return result


def get_file_text_row(file_id: int, updated_at: str) -> dict[str, Any] | None:
    """Cached extraction for a file, valid only if updated_at matches.

    The cache key is the file's Canvas `updated_at`: when the instructor
    replaces the file, the timestamp changes and the stale text is ignored.
    """
    row = get_conn().execute(
        "SELECT course_id, display_name, status, note, text FROM file_text "
        "WHERE file_id = ? AND updated_at = ?",
        (file_id, updated_at),
    ).fetchone()
    if row is None:
        return None
    course_id, display_name, status, note, text = row
    return {
        "courseId": course_id,
        "name": display_name,
        "status": status,
        "note": note,
        "text": text,
    }


def put_file_text(
    file_id: int,
    course_id: int | None,
    display_name: str,
    updated_at: str,
    status: str,
    text: str,
    note: str = "",
) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO file_text "
        "(file_id, course_id, display_name, updated_at, status, note, text, extracted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (file_id, course_id, display_name, updated_at, status, note, text, time.time()),
    )
    conn.execute("DELETE FROM file_search WHERE file_id = ?", (file_id,))
    if status == "ok" and text:
        conn.execute(
            "INSERT INTO file_search (text, display_name, file_id, course_id) "
            "VALUES (?, ?, ?, ?)",
            (text, display_name, file_id, course_id),
        )
    conn.commit()


def search_file_text(
    query: str, course_id: int | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """FTS5 search over extracted text; snippets, best match first.

    Covers Canvas files AND dropped space files — the latter carry a
    'local:<spaceId>/<name>' key in the file_id column (no schema change;
    the column is unindexed and typeless).
    """
    sql = (
        "SELECT file_id, course_id, display_name, "
        "snippet(file_search, 0, '>>', '<<', ' … ', 20) "
        "FROM file_search WHERE file_search MATCH ? "
    )
    params: list[Any] = [query]
    if course_id is not None:
        sql += "AND course_id = ? "
        params.append(course_id)
    sql += "ORDER BY rank LIMIT ?"
    params.append(limit)
    rows = get_conn().execute(sql, params).fetchall()
    results = []
    for r in rows:
        entry: dict[str, Any] = {"courseId": r[1], "name": r[2], "snippet": r[3]}
        if isinstance(r[0], str) and r[0].startswith("local:"):
            entry["localPath"] = r[0][len("local:"):]
        else:
            entry["fileId"] = r[0]
        results.append(entry)
    return results


def get_local_text_row(path: str, mtime: float) -> dict[str, Any] | None:
    """Cached extraction for a dropped file, valid only while mtime matches."""
    row = get_conn().execute(
        "SELECT status, note, text FROM local_text WHERE path = ? AND mtime = ?",
        (path, mtime),
    ).fetchone()
    if row is None:
        return None
    return {"status": row[0], "note": row[1], "text": row[2]}


def put_local_text(
    path: str,
    course_id: int | None,
    display_name: str,
    mtime: float,
    status: str,
    text: str,
    note: str = "",
) -> None:
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO local_text "
        "(path, course_id, display_name, mtime, status, note, text, extracted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (path, course_id, display_name, mtime, status, note, text, time.time()),
    )
    key = f"local:{path}"
    conn.execute("DELETE FROM file_search WHERE file_id = ?", (key,))
    if status == "ok" and text:
        conn.execute(
            "INSERT INTO file_search (text, display_name, file_id, course_id) "
            "VALUES (?, ?, ?, ?)",
            (text, display_name, key, course_id),
        )
    conn.commit()


def extraction_coverage(course_id: int | None = None) -> dict[str, int]:
    """How much of the corpus is extracted — for honest coverage notes."""
    sql = "SELECT status, COUNT(*) FROM file_text "
    params: list[Any] = []
    if course_id is not None:
        sql += "WHERE course_id = ? "
        params.append(course_id)
    sql += "GROUP BY status"
    return dict(get_conn().execute(sql, params).fetchall())


def add_plan_event(
    title: str,
    date: str,
    start_time: str | None = None,
    end_time: str | None = None,
    course: str | None = None,
    details: str = "",
) -> int:
    cur = get_conn().execute(
        "INSERT INTO plan_events (title, date, start_time, end_time, course, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (title, date, start_time, end_time, course, details, time.time()),
    )
    get_conn().commit()
    return cur.lastrowid


def list_plan_events(
    date_from: str | None = None, date_to: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT id, title, date, start_time, end_time, course, details FROM plan_events "
    clauses, params = [], []
    if date_from:
        clauses.append("date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("date <= ?")
        params.append(date_to)
    if clauses:
        sql += "WHERE " + " AND ".join(clauses) + " "
    sql += "ORDER BY date, start_time"
    rows = get_conn().execute(sql, params).fetchall()
    return [
        {"id": r[0], "title": r[1], "date": r[2], "start": r[3], "end": r[4],
         "course": r[5], "details": r[6]}
        for r in rows
    ]


def delete_plan_event(event_id: int) -> bool:
    cur = get_conn().execute("DELETE FROM plan_events WHERE id = ?", (event_id,))
    get_conn().commit()
    return cur.rowcount > 0


def add_retrieval_item(
    question: str,
    answer: str = "",
    course: str | None = None,
    source: str = "",
    first_due: str | None = None,
) -> int:
    """Log a practice item the student should see again. Starts in box 1,
    due tomorrow unless first_due overrides."""
    from datetime import date, timedelta

    due = first_due or (date.today() + timedelta(days=1)).isoformat()
    cur = get_conn().execute(
        "INSERT INTO retrieval_items (course, question, answer, source, due_date, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (course, question, answer, source, due, time.time()),
    )
    get_conn().commit()
    return cur.lastrowid


def due_retrieval_items(
    course: str | None = None, on_or_before: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Items due for review (oldest due first), never retired ones."""
    from datetime import date

    cutoff = on_or_before or date.today().isoformat()
    sql = (
        "SELECT id, course, question, answer, source, box, due_date, reviews "
        "FROM retrieval_items WHERE retired = 0 AND due_date <= ? "
    )
    params: list[Any] = [cutoff]
    if course:
        sql += "AND course LIKE ? "
        params.append(f"%{course}%")
    sql += "ORDER BY due_date, id LIMIT ?"
    params.append(limit)
    rows = get_conn().execute(sql, params).fetchall()
    return [
        {"id": r[0], "course": r[1], "question": r[2], "answer": r[3],
         "source": r[4], "box": r[5], "dueDate": r[6], "reviews": r[7]}
        for r in rows
    ]


def count_due_retrieval_items(on_or_before: str | None = None) -> dict[str, int]:
    """Due-review counts per course (for the daily brief)."""
    from datetime import date

    cutoff = on_or_before or date.today().isoformat()
    rows = get_conn().execute(
        "SELECT COALESCE(course, 'general'), COUNT(*) FROM retrieval_items "
        "WHERE retired = 0 AND due_date <= ? GROUP BY COALESCE(course, 'general')",
        (cutoff,),
    ).fetchall()
    return dict(rows)


def record_retrieval_result(item_id: int, correct: bool) -> dict[str, Any] | None:
    """Leitner progression: correct climbs a box (retire past box 5),
    wrong resets to box 1 due tomorrow. Returns the new state, None if unknown."""
    from datetime import date, timedelta

    row = get_conn().execute(
        "SELECT box, reviews FROM retrieval_items WHERE id = ? AND retired = 0",
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    box, reviews = row
    if correct:
        if box >= 5:
            get_conn().execute(
                "UPDATE retrieval_items SET retired = 1, reviews = ?, last_result = 'correct' WHERE id = ?",
                (reviews + 1, item_id),
            )
            get_conn().commit()
            return {"id": item_id, "retired": True, "box": box}
        new_box = box + 1
    else:
        new_box = 1
    due = (date.today() + timedelta(days=LEITNER_INTERVALS[new_box])).isoformat()
    get_conn().execute(
        "UPDATE retrieval_items SET box = ?, due_date = ?, reviews = ?, last_result = ? "
        "WHERE id = ?",
        (new_box, due, reviews + 1, "correct" if correct else "wrong", item_id),
    )
    get_conn().commit()
    return {"id": item_id, "retired": False, "box": new_box, "nextDue": due}


def upsert_brief(date_str: str, summary: str, details: str) -> None:
    get_conn().execute(
        "INSERT OR REPLACE INTO briefs (date, summary, details, created_at) VALUES (?, ?, ?, ?)",
        (date_str, summary, details, time.time()),
    )
    get_conn().commit()


def recent_briefs(days: int = 2) -> list[dict[str, Any]]:
    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows = get_conn().execute(
        "SELECT date, summary, details FROM briefs WHERE date >= ? ORDER BY date",
        (cutoff,),
    ).fetchall()
    return [{"date": r[0], "summary": r[1], "details": r[2]} for r in rows]


async def cached_request(
    endpoint: str,
    params: dict[str, Any] | None = None,
    max_age_seconds: float = 7 * 86400,
) -> Any:
    """Single-object GET (no pagination) with the same read-through cache."""
    from .client import make_canvas_request

    key = "single:" + make_key(endpoint, params)
    hit = cache_get(key, max_age_seconds)
    if hit is not None:
        return hit

    result = await make_canvas_request("get", endpoint, params=params)
    if isinstance(result, dict) and "error" in result:
        return result
    cache_put(key, endpoint, result)
    return result
