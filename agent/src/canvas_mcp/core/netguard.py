"""The one outbound downloader, and the rules every download obeys.

Anything that fetches a URL on the model's or an instructor's say-so goes
through here: file downloads (including the verifier links harvested from
assignment text) and web images. The rules:

- Only http(s), and only hosts that resolve to public addresses — except the
  configured Canvas host itself, which may legitimately be private (a campus
  install). A public host that redirects to 127.0.0.1 is the classic bypass,
  so redirects are followed by hand and every hop is re-checked.
- Bodies are streamed and abandoned the moment they exceed the caller's cap,
  so a chunked multi-gigabyte response costs bandwidth, not memory.
- No bearer header: Canvas file URLs carry their own verifier, and forwarding
  the token across a redirect to storage both breaks the storage signature
  and leaks the token.
"""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

import httpx


def _size(n: int) -> str:
    return f"{n / (1024 * 1024):.0f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} KB"


def canvas_host() -> str:
    from .config import get_config
    return (urlparse(get_config().canvas_api_url or "").hostname or "").lower()


def public_host_error(host: str, allow_hosts: tuple[str, ...] = ()) -> str | None:
    """None if every address `host` resolves to is public (or the host is
    explicitly allowed); otherwise a one-line reason."""
    if not host:
        return "URL has no host."
    if host.lower() in {h.lower() for h in allow_hosts if h}:
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return f"{host} does not resolve."
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if not ip.is_global:
            return f"{host} resolves to a non-public address; refusing to fetch it."
    return None


def url_error(url: str, allow_hosts: tuple[str, ...] = ()) -> str | None:
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return "url must be http(s)."
    return public_host_error(parsed.hostname or "", allow_hosts)


async def download(
    url: str,
    *,
    max_bytes: int,
    allow_hosts: tuple[str, ...] = (),
    accept_content_type: Callable[[str], bool] | None = None,
    timeout: float = 120.0,
    max_hops: int = 5,
) -> tuple[bytes | None, str, str, str | None]:
    """Fetch `url` under the rules above.

    Returns (data, final_url, content_type, error). Exactly one of data and
    error is set. `accept_content_type`, if given, is consulted before any
    body is read; a False answer is an error, not a download.
    """
    current = url
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        for _ in range(max_hops):
            err = url_error(current, allow_hosts)
            if err:
                return None, current, "", err
            try:
                async with client.stream("GET", current) as resp:
                    if resp.is_redirect:
                        nxt = resp.next_request
                        if nxt is None:
                            return None, current, "", "Redirect without a target."
                        current = str(nxt.url)
                        continue
                    resp.raise_for_status()
                    content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                    if accept_content_type is not None and not accept_content_type(content_type):
                        return None, current, content_type, f"Unexpected content type: {content_type or 'unknown'}."
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_bytes:
                        return None, current, content_type, f"Response is over the {_size(max_bytes)} cap."
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > max_bytes:
                            return None, current, content_type, f"Response exceeded the {_size(max_bytes)} cap."
                    return bytes(buf), current, content_type, None
            except Exception as e:
                return None, current, "", f"Fetch failed: {type(e).__name__}: {e}"
    return None, current, "", "Too many redirects."
