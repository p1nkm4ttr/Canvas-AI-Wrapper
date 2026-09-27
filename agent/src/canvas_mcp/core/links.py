"""Harvest file references and outside links from Canvas rich text.

Instructors attach the material that matters to assignments and
announcements, and Canvas marks those files *hidden*: they never appear in
the course files listing and are rarely placed in a module. Measured on a
live term: a course whose listing shows 11 files had 6 more, including the
assignment briefs, reachable only through links in assignment text. The
links themselves carry a verifier, so the file is fetchable even when the
metadata route is refused.

Nothing here fetches anything. It only finds ids, titles and URLs; callers
decide what to do with them and fence the titles before a model sees them.
"""

import re
from html import unescape
from typing import Any

_ANCHOR = re.compile(r'<a\b[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_FILE_REF = re.compile(r"/courses/(\d+)/files/(\d+)|/files/(\d+)")
_TAG = re.compile(r"<[^>]+>")


def _inner_text(html: str) -> str:
    return " ".join(unescape(_TAG.sub("", html)).split())


def _same_origin(href: str, origin: str) -> bool:
    """Relative, or absolute on the configured Canvas host. Anything else is
    not a Canvas file reference no matter what its path looks like — an
    instructor-authored <a href="http://127.0.0.1/files/1"> must never turn
    into a download."""
    h = href.strip().lower()
    if h.startswith(("http://", "https://")):
        return bool(origin) and h.startswith(origin.lower().rstrip("/") + "/")
    return h.startswith("/") and not h.startswith("//")


def _file_id(href: str, origin: str) -> int | None:
    if not _same_origin(href, origin):
        return None
    m = _FILE_REF.search(href)
    if not m:
        return None
    return int(m.group(2) or m.group(3))


_ABS_ATTR = re.compile(r"(?:href|src|data-api-endpoint)=([\"'])((?:https?:)?//[^\"']*)\1", re.IGNORECASE)


def harvest_file_refs(html: str | None, canvas_origin_url: str | None = None) -> list[dict[str, Any]]:
    """Canvas file references in an HTML body, in document order, deduped.

    Each entry is {fileId, url, title}: `url` is the link's own href
    (verifier included) when the reference came from an anchor, else "";
    `title` is the anchor text, else "". Bare references (``data-api-endpoint``
    attributes, inline images) are included after the anchors. Only relative
    references and absolute ones on the configured Canvas origin count.
    """
    html = html or ""
    origin = canvas_origin_url if canvas_origin_url is not None else canvas_origin()
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for href, inner in _ANCHOR.findall(html):
        fid = _file_id(unescape(href), origin)
        if fid is None or fid in seen:
            continue
        seen.add(fid)
        out.append({"fileId": fid, "url": unescape(href), "title": _inner_text(inner)[:120]})
    # Bare references: drop every absolute off-origin attribute value first so
    # an outside URL's path can't masquerade as a Canvas file id.
    def _keep(m: re.Match) -> str:
        return m.group(0) if _same_origin(unescape(m.group(2)), origin) else ""
    scrubbed = _ABS_ATTR.sub(_keep, html)
    for m in _FILE_REF.finditer(scrubbed):
        fid = int(m.group(2) or m.group(3))
        if fid not in seen:
            seen.add(fid)
            out.append({"fileId": fid, "url": "", "title": ""})
    return out


def harvest_external_links(html: str | None, canvas_origin: str) -> list[dict[str, Any]]:
    """Links that leave Canvas (Google Docs, forms, videos...), deduped.

    Returned as {title, url} so a tool can SHOW them; they are never fetched
    by the server — they sit behind logins the server does not have and
    should not have.
    """
    host = (canvas_origin or "").lower().rstrip("/")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for href, inner in _ANCHOR.findall(html or ""):
        url = unescape(href).strip()
        low = url.lower()
        if not low.startswith(("http://", "https://")):
            continue
        if host and low.startswith(host):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append({"title": _inner_text(inner)[:120] or url, "url": url})
    return out


def canvas_origin() -> str:
    """https://host of the configured Canvas, for telling links apart."""
    from .config import get_config
    base = get_config().canvas_api_url or ""
    return base.split("/api/")[0] if "/api/" in base else base.rstrip("/")
