"""The shared guarded downloader: host rules, redirect re-validation, streaming cap."""

from types import SimpleNamespace
from unittest.mock import patch

from canvas_mcp.core import netguard


class _Resp:
    def __init__(self, content_type="application/pdf", body=b"", redirect_to=None, headers=None):
        self.headers = {"content-type": content_type, **(headers or {})}
        self._body = body
        self.is_redirect = redirect_to is not None
        self.next_request = SimpleNamespace(url=redirect_to) if redirect_to else None
        self.chunks_served = 0

    def raise_for_status(self):
        pass

    async def aiter_bytes(self):
        for i in range(0, len(self._body), 1024):
            self.chunks_served += 1
            yield self._body[i:i + 1024]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _client_for(hops):
    requested = []

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url):
            requested.append(url)
            return hops.pop(0)

    return FakeClient, requested


async def test_streams_and_aborts_past_the_cap():
    resp = _Resp(body=b"x" * 100_000)
    client, _ = _client_for([resp])
    with patch("canvas_mcp.core.netguard.httpx.AsyncClient", client), \
         patch("canvas_mcp.core.netguard.public_host_error", return_value=None):
        data, _, _, err = await netguard.download("https://files.example/a.pdf", max_bytes=5_000)
    assert data is None and "cap" in err
    assert resp.chunks_served <= 6          # stopped early, not after buffering 100 KB


async def test_declared_length_over_cap_is_refused_before_reading():
    resp = _Resp(body=b"x" * 10, headers={"content-length": "99999999"})
    client, _ = _client_for([resp])
    with patch("canvas_mcp.core.netguard.httpx.AsyncClient", client), \
         patch("canvas_mcp.core.netguard.public_host_error", return_value=None):
        data, _, _, err = await netguard.download("https://files.example/a.pdf", max_bytes=5_000)
    assert data is None and "cap" in err and resp.chunks_served == 0


async def test_redirect_to_private_host_is_refused_before_connecting():
    client, requested = _client_for([_Resp(redirect_to="http://127.0.0.1:8000/admin")])

    def host_check(host, allow_hosts=()):
        return None if host == "public.example" else "non-public"

    with patch("canvas_mcp.core.netguard.httpx.AsyncClient", client), \
         patch("canvas_mcp.core.netguard.public_host_error", side_effect=host_check):
        data, _, _, err = await netguard.download("https://public.example/f", max_bytes=1000)
    assert data is None and "non-public" in err
    assert requested == ["https://public.example/f"]


def test_canvas_host_is_allowed_even_if_private():
    private = [(None, None, None, None, ("10.0.0.5", 0))]
    with patch("canvas_mcp.core.netguard.socket.getaddrinfo", return_value=private):
        assert netguard.public_host_error("canvas.campus.local") is not None
        assert netguard.public_host_error("canvas.campus.local", allow_hosts=("canvas.campus.local",)) is None


def test_scheme_rule():
    assert netguard.url_error("ftp://x/y") == "url must be http(s)."
    assert netguard.url_error("file:///etc/passwd") == "url must be http(s)."


async def test_content_type_gate_runs_before_any_body_is_read():
    resp = _Resp(content_type="text/html", body=b"<html>" * 1000)
    client, _ = _client_for([resp])
    with patch("canvas_mcp.core.netguard.httpx.AsyncClient", client), \
         patch("canvas_mcp.core.netguard.public_host_error", return_value=None):
        data, _, ct, err = await netguard.download(
            "https://x.example/p", max_bytes=10_000, accept_content_type=lambda c: c.startswith("image/")
        )
    assert data is None and ct == "text/html" and resp.chunks_served == 0
