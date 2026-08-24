"""Fetch → extract → cache pipeline for Canvas files.

The cache key is the file's Canvas `updated_at` (build brief): a re-uploaded
file gets re-extracted, an unchanged one never touches the network again.
Concluded-course access may be withdrawn, so extracted text is kept in
SQLite permanently.
"""

import httpx

from .client import make_canvas_request
from .db import get_file_text_row, put_file_text
from .extract import MAX_DOWNLOAD_BYTES, extract_text, is_extractable
from .logging import log_debug, log_error


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


async def get_file_text_cached(file_id: int | str, course_id: int | None = None) -> dict:
    """Extracted text for one Canvas file, from cache when fresh.

    Returns {fileId, name, status, text, note, url}; status is
    ok | scanned | unsupported | error, and note says what happened for
    anything other than ok. An {"error": ...} dict means the file itself
    could not even be described.
    """
    meta = await make_canvas_request("get", f"/files/{file_id}")
    if isinstance(meta, dict) and "error" in meta and course_id is not None:
        # /files/{id} is occasionally permission-gated where the
        # course-scoped route still works (measured live in step 0).
        meta = await make_canvas_request("get", f"/courses/{course_id}/files/{file_id}")
    if isinstance(meta, dict) and "error" in meta:
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
        return _result(cached["status"], cached["text"], cached["note"], cached=True)

    if not is_extractable(name, real_filename, content_type):
        note = (
            f"'{name}' ({content_type or 'unknown type'}) is not an extractable "
            "format (slides, docs, and text files are)."
        )
        put_file_text(fid, resolved_course, name, updated_at, "unsupported", "", note)
        return _result("unsupported", "", note)

    if size > MAX_DOWNLOAD_BYTES:
        note = f"File is {size / 1e6:.0f} MB — beyond the {MAX_DOWNLOAD_BYTES / 1e6:.0f} MB extraction cap."
        put_file_text(fid, resolved_course, name, updated_at, "unsupported", "", note)
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
        extraction.status, extraction.text, extraction.note,
    )
    return _result(extraction.status, extraction.text, extraction.note)
