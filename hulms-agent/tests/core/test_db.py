"""Tests for the SQLite response cache."""

from unittest.mock import AsyncMock, patch

import pytest

from canvas_mcp.core import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Each test gets its own database file."""
    monkeypatch.setenv("HULMS_DB", str(tmp_path / "test.db"))
    db.close_conn()
    yield
    db.close_conn()


def test_make_key_is_stable_across_param_order():
    a = db.make_key("/courses", {"b": 2, "a": 1})
    b = db.make_key("/courses", {"a": 1, "b": 2})
    assert a == b


def test_cache_roundtrip():
    key = db.make_key("/courses", {"per_page": 100})
    assert db.cache_get(key, 60) is None
    db.cache_put(key, "/courses", [{"id": 1}])
    assert db.cache_get(key, 60) == [{"id": 1}]


def test_cache_expires():
    key = db.make_key("/x", None)
    db.cache_put(key, "/x", ["v"])
    assert db.cache_get(key, max_age_seconds=0) is None


@pytest.mark.asyncio
async def test_cached_fetch_all_hits_network_once():
    with patch(
        "canvas_mcp.core.client.fetch_all_paginated_results",
        new_callable=AsyncMock,
        return_value=[{"id": 7}],
    ) as mock_fetch:
        first = await db.cached_fetch_all("/courses/1/modules", {"per_page": 100})
        second = await db.cached_fetch_all("/courses/1/modules", {"per_page": 100})

    assert first == second == [{"id": 7}]
    assert mock_fetch.await_count == 1


@pytest.mark.asyncio
async def test_errors_are_not_cached():
    with patch(
        "canvas_mcp.core.client.fetch_all_paginated_results",
        new_callable=AsyncMock,
        return_value={"error": "HTTP error: 500"},
    ) as mock_fetch:
        await db.cached_fetch_all("/broken", None)
        await db.cached_fetch_all("/broken", None)

    assert mock_fetch.await_count == 2, "an error response must not be served from cache"
