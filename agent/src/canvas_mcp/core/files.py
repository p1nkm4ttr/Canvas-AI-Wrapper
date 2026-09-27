"""Fetch → extract → cache pipeline for Canvas files.

The cache key is the file's Canvas `updated_at` (build brief): a re-uploaded
file gets re-extracted, an unchanged one never touches the network again.
Concluded-course access may be withdrawn, so extracted text is kept in
SQLite permanently.
"""

import httpx

from .client import make_canvas_request
from .db import get_file_text_row, put_file_text, set_file_origin
from .extract import MAX_DOWNLOAD_BYTES, extract_text, is_extractable
from .logging import log_debug, log_error
from .untrusted_content import fence_untrusted_inline


async def _download(url: str, timeout: float = 120.0) -> bytes | None:
    """Download a pre-signed Canvas file URL.

    A bare client: the URL carries its own verifier token, and forwarding the
    Bearer header across the redirect to storage can break the storage
    signature.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception as e:
        log_error("File download failed", error_type=type(e).__name__)
        return None


async def fetch_file_bytes(
    file_id: int | str, course_id: int | None = None
) -> tuple[str, bytes] | dict:
    """Download a Canvas file's raw bytes: (display_name, data) or {"error"}.

    Used by figure extraction, which needs the original document, not the
    cached text.
    """
    meta = await make_canvas_request("get", f"/files/{file_id}")
    if isinstance(meta, dict) and "error" in meta and course_id is not None:
        meta = await make_canvas_request("get", f"/courses/{course_id}/files/{file_id}")
    if isinstance(meta, dict) and "error" in meta:
        return {"error": f"Could not read file {file_id}: {meta['error']}"}
    name = meta.get("display_name") or meta.get("filename") or f"file {file_id}"
    if (meta.get("size") or 0) > MAX_DOWNLOAD_BYTES:
        return {"error": f"File is too large to download ({(meta.get('size') or 0) / 1e6:.0f} MB)."}
    if not meta.get("url"):
        return {"error": f"File {file_id} has no download URL (permissions?)."}
    data = await _download(meta["url"])
    if data is None:
        return {"error": f"Download failed for file {file_id} ('{name}')."}
    return name, data


def _sniff_name(file_id: int, data: bytes, title: str = "") -> str:
    """A usable filename for bytes fetched through a link, where no metadata
    told us the type. Magic bytes decide the extension; the link text (if
    any) is the stem."""
    stem = (title or f"file-{file_id}").strip().replace("/", "-").replace("\\", "-")[:80]
    if data.startswith(b"%PDF"):
        ext = ".pdf"
    elif data.startswith(b"PK"):
        head = data[:200_000]
        ext = ".pptx" if b"ppt/" in head else ".docx" if b"word/" in head else ".zip"
    elif data.lstrip()[:1] in (b"<",):
        ext = ".html"
    else:
        ext = ".txt" if _looks_like_text(data[:4096]) else ".bin"
    return stem if stem.lower().endswith(ext) else stem + ext


def _looks_like_text(sample: bytes) -> bool:
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\x00" not in sample


async def _from_fallback_url(
    file_id: int | str, course_id: int | None, url: str, origin: str
) -> dict:
    """Fetch through the link's own URL when the metadata routes are refused
    (files owned by the instructor's personal area still open via the
    verifier in the link). Cached under the fixed key 'link', since no
    updated_at is available to invalidate on."""
    fid = int(file_id)
    cached = get_file_text_row(fid, "link")
    if cached is not None:
        return {"fileId": fid, "name": cached["name"], "status": cached["status"],
                "text": cached["text"], "note": cached["note"], "cached": True,
                "url": f"/files/{fid}", "origin": cached.get("origin", "")}
    data = await _download(url)
    if data is None:
        return {"error": f"Could not read file {fid}: metadata refused and the link did not download."}
    name = _sniff_name(fid, data)
    note_prefix = "Fetched through its link (metadata not accessible). "
    if not is_extractable(name):
        note = note_prefix + f"{fence_untrusted_inline(name, 'file name')} is not an extractable format."
        put_file_text(fid, course_id, name, "link", "unsupported", "", note, origin=origin)
        return {"fileId": fid, "name": name, "status": "unsupported", "text": "", "note": note,
                "cached": False, "url": f"/files/{fid}", "origin": origin}
    if len(data) > MAX_DOWNLOAD_BYTES:
        note = note_prefix + f"File is {len(data) / 1e6:.0f} MB — beyond the extraction cap."
        put_file_text(fid, course_id, name, "link", "unsupported", "", note, origin=origin)
        return {"fileId": fid, "name": name, "status": "unsupported", "text": "", "note": note,
                "cached": False, "url": f"/files/{fid}", "origin": origin}
    extraction = extract_text(data, name)
    note = (note_prefix + extraction.note).strip()
    put_file_text(fid, course_id, name, "link", extraction.status, extraction.text, note, origin=origin)
    return {"fileId": fid, "name": name, "status": extraction.status, "text": extraction.text,
            "note": note, "cached": False, "url": f"/files/{fid}", "origin": origin}


async def get_file_text_cached(
    file_id: int | str,
    course_id: int | None = None,
    *,
    origin: str = "",
    fallback_url: str | None = None,
) -> dict:
    """Extracted text for one Canvas file, from cache when fresh.

    Returns {fileId, name, status, text, note, url}; status is
    ok | scanned | unsupported | error, and note says what happened for
    anything other than ok. An {"error": ...} dict means the file itself
    could not even be described.

    `origin` records where the file was found ("linked from assignment X")
    for search results; `fallback_url` is a link with its own verifier, used
    when both metadata routes refuse the id.
    """
    meta = await make_canvas_request("get", f"/files/{file_id}")
    if isinstance(meta, dict) and "error" in meta and course_id is not None:
        # /files/{id} is occasionally permission-gated where the
        # course-scoped route still works (measured live in step 0).
        meta = await make_canvas_request("get", f"/courses/{course_id}/files/{file_id}")
    if isinstance(meta, dict) and "error" in meta:
        if fallback_url:
            return await _from_fallback_url(file_id, course_id, fallback_url, origin)
        return {"error": f"Could not read file {file_id}: {meta['error']}"}

    name = meta.get("display_name") or meta.get("filename") or f"file {file_id}"
    real_filename = meta.get("filename")
    content_type = meta.get("content-type") or meta.get("content_type")
    updated_at = meta.get("updated_at") or ""
    size = meta.get("size") or 0
    file_url = meta.get("url")
    fid = int(meta.get("id") or file_id)
    resolved_course = course_id
    if resolved_course is None:
        # folder_id is useless here; Canvas gives no course on /files/{id}.
        # Callers walking a module pass course_id; standalone calls store NULL.
        resolved_course = None

    def _result(status: str, text: str, note: str, cached: bool = False) -> dict:
        return {
            "fileId": fid,
            "name": name,
            "status": status,
            "text": text,
            "note": note,
            "cached": cached,
            "url": f"/files/{fid}",
        }

    cached = get_file_text_row(fid, updated_at)
    if cached is not None:
        log_debug(f"file text cache hit for {fid}")
        if origin and not cached.get("origin"):
            set_file_origin(fid, origin)
        return _result(cached["status"], cached["text"], cached["note"], cached=True)

    if not is_extractable(name, real_filename, content_type):
        note = (
            f"{fence_untrusted_inline(name, 'file name')} "
            f"({content_type or 'unknown type'}) is not an extractable "
            "format (slides, docs, and text files are)."
        )
        put_file_text(fid, resolved_course, name, updated_at, "unsupported", "", note, origin=origin)
        return _result("unsupported", "", note)

    if size > MAX_DOWNLOAD_BYTES:
        note = f"File is {size / 1e6:.0f} MB — beyond the {MAX_DOWNLOAD_BYTES / 1e6:.0f} MB extraction cap."
        put_file_text(fid, resolved_course, name, updated_at, "unsupported", "", note, origin=origin)
        return _result("unsupported", "", note)

    if not file_url:
        return {"error": f"File {fid} has no download URL (permissions?)."}

    data = await _download(file_url)
    if data is None:
        # NOT cached: a transient network failure must not poison the cache.
        return {"error": f"Download failed for file {fid} ('{name}')."}

    extraction = extract_text(data, name, real_filename, content_type)
    put_file_text(
        fid, resolved_course, name, updated_at,
        extraction.status, extraction.text, extraction.note, origin=origin,
    )
    return _result(extraction.status, extraction.text, extraction.note)
