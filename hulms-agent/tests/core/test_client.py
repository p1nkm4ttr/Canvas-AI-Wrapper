"""Unit tests for core HTTP client helpers."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import canvas_mcp.core.client as client_module
from canvas_mcp.core.client import _canvas_auth_headers


class TestCanvasAuthHeaders:
    """All Canvas API requests must carry a User-Agent (Instructure enforces it)."""

    def test_includes_user_agent(self):
        headers = _canvas_auth_headers("some-token")
        assert "User-Agent" in headers
        assert headers["User-Agent"].startswith("canvas-mcp/")

    def test_includes_bearer_authorization(self):
        headers = _canvas_auth_headers("some-token")
        assert headers["Authorization"] == "Bearer some-token"

    def test_user_agent_identifies_project(self):
        """UA should be self-identifying per Instructure's guidance (contact URL)."""
        headers = _canvas_auth_headers("t")
        assert "github.com/vishalsachdev/canvas-mcp" in headers["User-Agent"]


class TestResolveCanvasApiRoot:
    def test_rest_root_is_unchanged(self):
        assert (
            client_module._resolve_canvas_api_root(
                "https://canvas.school.edu/api/v1", "rest"
            )
            == "https://canvas.school.edu/api/v1"
        )

    def test_quiz_root_rewrites_trailing_api_version(self):
        assert (
            client_module._resolve_canvas_api_root(
                "https://canvas.school.edu/lms/api/v2", "quiz"
            )
            == "https://canvas.school.edu/lms/api/quiz/v1"
        )

    def test_quiz_root_rejects_non_api_version_base(self):
        with pytest.raises(ValueError, match="expected trailing /api/v<N>"):
            client_module._resolve_canvas_api_root(
                "https://canvas.school.edu/not-api", "quiz"
            )




class TestLinkHeaderNext:
    """Pagination must follow the Link header rel="next" (verified API fact)."""

    NEXT = "https://canvas.school.edu/api/v1/courses?page=bookmark:WzQyXQ&per_page=100"

    def test_parses_rel_next(self):
        link = f'<{self.NEXT}>; rel="next", <https://x/api/v1/courses?page=1>; rel="first"'
        assert client_module._link_header_next({"Link": link}) == self.NEXT

    def test_header_name_is_case_insensitive(self):
        link = f'<{self.NEXT}>; rel="next"'
        assert client_module._link_header_next({"link": link}) == self.NEXT
        assert client_module._link_header_next({"LINK": link}) == self.NEXT

    def test_no_next_rel_returns_none(self):
        link = '<https://x/api/v1/courses?page=1>; rel="current"'
        assert client_module._link_header_next({"Link": link}) is None

    def test_missing_header_returns_none(self):
        assert client_module._link_header_next({}) is None


class TestIsRateLimited:
    """429 always retries; 403 retries ONLY when it is throttling, never a
    permission denial (an instructor-hidden files tab must not back off)."""

    def _resp(self, status, text="", headers=None):
        return SimpleNamespace(
            status_code=status, text=text, headers=headers or {}
        )

    def test_429_is_rate_limited(self):
        assert client_module._is_rate_limited(self._resp(429)) is True

    def test_403_with_throttle_body_is_rate_limited(self):
        resp = self._resp(403, text="403 Forbidden (Rate Limit Exceeded)")
        assert client_module._is_rate_limited(resp) is True

    def test_403_with_exhausted_quota_header_is_rate_limited(self):
        resp = self._resp(403, headers={"X-Rate-Limit-Remaining": "0.0"})
        assert client_module._is_rate_limited(resp) is True

    def test_permission_403_is_not_rate_limited(self):
        resp = self._resp(
            403,
            text='{"status": "unauthorized"}',
            headers={"X-Rate-Limit-Remaining": "699.0"},
        )
        assert client_module._is_rate_limited(resp) is False

    def test_404_is_not_rate_limited(self):
        assert client_module._is_rate_limited(self._resp(404)) is False


class TestAbsoluteUrl:
    """html_url comes back relative (verified fact); traceable links need the host."""

    def test_relative_url_gets_canvas_origin(self, monkeypatch):
        from canvas_mcp.core.config import reset_config
        monkeypatch.setenv("CANVAS_API_URL", "https://canvas.school.edu/api/v1")
        reset_config()
        assert (
            client_module.absolute_url("/courses/802/assignments/40267")
            == "https://canvas.school.edu/courses/802/assignments/40267"
        )

    def test_absolute_url_is_unchanged(self):
        url = "https://canvas.school.edu/courses/802"
        assert client_module.absolute_url(url) == url

    def test_none_passes_through(self):
        assert client_module.absolute_url(None) is None


class TestPaginatedFetch:
    """The paginated helper follows Link rel="next" and forwards api_root."""

    @pytest.fixture(autouse=True)
    def reset_client_state(self):
        client_module._request_semaphore = None
        client_module._semaphore_loop_ref = None
        yield
        client_module._request_semaphore = None
        client_module._semaphore_loop_ref = None

    NEXT = "https://canvas.school.edu/api/quiz/v1/courses/42/quizzes?page=bookmark:abc&per_page=100"

    @pytest.mark.asyncio
    async def test_quiz_root_is_forwarded_and_link_next_is_followed(self):
        calls = []
        responses = [
            ([{"id": i} for i in range(100)], {"Link": f'<{self.NEXT}>; rel="next"'}),
            ([{"id": 100}], {}),
        ]

        async def fake_request(method, endpoint, **kwargs):
            calls.append((endpoint, kwargs))
            return responses.pop(0)

        with patch.object(
            client_module, "make_canvas_request", side_effect=fake_request
        ) as mock_req:
            result = await client_module.fetch_all_paginated_results(
                "/courses/42/quizzes", api_root="quiz", skip_anonymization=True
            )

        assert len(result) == 101
        assert mock_req.await_count == 2, "expected two page fetches"
        # First call: the relative endpoint with params. Second: the absolute
        # bookmark URL from the Link header, with params suppressed so the
        # cursor query string is not corrupted.
        assert calls[0][0] == "/courses/42/quizzes"
        assert calls[0][1]["params"]["per_page"] == 100
        assert calls[1][0] == self.NEXT
        assert calls[1][1]["params"] is None
        for _, kwargs in calls:
            assert kwargs["api_root"] == "quiz"
            assert kwargs["return_headers"] is True

    @pytest.mark.asyncio
    async def test_stops_after_full_page_without_link_header(self):
        """A full page with no Link header is the last page — no blind page=2."""

        async def fake_request(method, endpoint, **kwargs):
            return [{"id": i} for i in range(100)], {}

        with patch.object(
            client_module, "make_canvas_request", side_effect=fake_request
        ) as mock_req:
            result = await client_module.fetch_all_paginated_results("/x")

        assert len(result) == 100
        assert mock_req.await_count == 1

    @pytest.mark.asyncio
    async def test_rest_root_remains_the_default(self):
        async def fake_request(method, endpoint, **kwargs):
            return [], {}

        with patch.object(
            client_module, "make_canvas_request", side_effect=fake_request
        ) as mock_req:
            await client_module.fetch_all_paginated_results("/courses/42/quizzes")

        assert mock_req.await_args.kwargs["api_root"] == "rest"

    @pytest.mark.asyncio
    async def test_error_page_is_returned_as_is(self):
        async def fake_request(method, endpoint, **kwargs):
            return {"error": "HTTP error: 403"}, {}

        with patch.object(client_module, "make_canvas_request", side_effect=fake_request):
            result = await client_module.fetch_all_paginated_results("/x")

        assert result == {"error": "HTTP error: 403"}

