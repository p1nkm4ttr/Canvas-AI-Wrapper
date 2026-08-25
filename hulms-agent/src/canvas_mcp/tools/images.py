"""Figure tools: see the diagrams in course documents, and fetch web images.

Both tools return file paths plus embed URLs: the coach VIEWS an image by
passing its `file` path to the Read tool (vision), and SHOWS it to the
student by putting the `embed` URL in a markdown image tag.
"""

import hashlib
from pathlib import Path
from typing import Any

import httpx
from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ..core.files import fetch_file_bytes
from ..core.images import (
    DEFAULT_MAX_IMAGES,
    MAX_RENDER_PAGES,
    extract_document_images,
    render_pdf_pages,
    save_figures,
)
from ..core.local_files import spaces_root
from ..core.validation import validate_params


def _parse_pages(pages: str) -> tuple[int, int] | None:
    import re as _re
    m = _re.fullmatch(r"(\d+)(?:-(\d+))?", pages.strip())
    if not m:
        return None
    first = int(m.group(1))
    last = int(m.group(2)) if m.group(2) else first
    return (first, last) if last >= first else None


async def _resolve_document(
    file_id: int | None, local_name: str | None
) -> tuple[str, bytes, str] | dict:
    """(name, data, source_key) for a Canvas file id or dropped local name."""
    if (file_id is None) == (local_name is None):
        return {"error": "Give exactly one of file_id or local_name."}
    if file_id is not None:
        fetched = await fetch_file_bytes(file_id)
        if isinstance(fetched, dict):
            return fetched
        name, data = fetched
        return name, data, f"canvas-{file_id}"
    cleaned = local_name.replace("\\", "/").strip()
    if cleaned.startswith("/") or ".." in cleaned.split("/") or ":" in cleaned:
        return {"error": "Give a bare filename or spaceId/filename."}
    root = spaces_root()
    candidates = (
        [p for p in [root / cleaned] if p.is_file()]
        if "/" in cleaned
        else [p for p in root.glob(f"*/{cleaned}") if p.is_file()]
    )
    if not candidates:
        return {"error": f"No dropped file matches '{local_name}'."}
    target: Path = candidates[0]
    return target.name, target.read_bytes(), f"local-{target.parent.name}-{target.stem}"

MAX_WEB_IMAGE_BYTES = 15 * 1024 * 1024
_CT_EXT = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
    "image/webp": ".webp", "image/bmp": ".bmp",
}

_HOW_TO_USE = (
    "View an image by passing its `file` path to Read; show it to the "
    "student by embedding `![caption](<embed>)` in your reply."
)


def register_image_tools(mcp: FastMCP) -> None:
    """Register the figure tools."""

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def get_document_images(
        file_id: int | None = None,
        local_name: str | None = None,
        pages: str | None = None,
        max_images: int = DEFAULT_MAX_IMAGES,
    ) -> dict:
        """Extract the figures (diagrams, charts, screenshots) embedded in a
        course document — PDF or PPTX. Use when the material's meaning is in
        its pictures: circuit diagrams, plots, geometry. Then Read the
        extracted files to actually SEE them, and embed them in your reply
        so the student sees them too.

        For LONG documents (textbooks): the scan walks from page 1 and stops
        at max_images, so front-matter art can exhaust the cap before the
        figure you want. Pass `pages` to target the section (e.g. "50-60").
        The result says exactly how far the scan got.

        Args:
            file_id: A Canvas file id (from get_course_map / get_study_context).
            local_name: OR a dropped file's name/spaceId-name (as in
                read_local_document).
            pages: Optional page/slide window, "N" or "N-M" (1-based).
            max_images: Cap per call (default 12; tiny logos are filtered).
        """
        first, last = 1, None
        if pages is not None:
            parsed = _parse_pages(pages)
            if parsed is None:
                return {"error": 'pages must be "N" or "N-M" (1-based).'}
            first, last = parsed

        resolved = await _resolve_document(file_id, local_name)
        if isinstance(resolved, dict):
            return resolved
        name, data, source_key = resolved

        result = extract_document_images(
            data, name, max_images=max_images, first=first, last=last
        )
        if "error" in result:
            return {"file": name, "images": [], "count": 0, "note": result["error"]}

        unit = result["unit"]
        scanned = f"{unit}s {result['scannedFrom']}-{result['scannedTo']} of {result['total']}"
        if not result["images"]:
            return {
                "file": name, "images": [], "count": 0, "scanned": scanned,
                "note": (
                    f"No substantial EMBEDDED images in {scanned}. Textbook "
                    f"figures are usually vector line-art, invisible to this "
                    f"tool — use render_document_pages on the pages that carry "
                    f"the figure instead. ({result['total']} {unit}s total.)"
                ),
            }
        saved = save_figures(source_key, result["images"])
        out = {
            "file": name,
            "images": saved,
            "count": len(saved),
            "scanned": scanned,
            "note": _HOW_TO_USE,
        }
        if result["capped"]:
            out["coverage"] = (
                f"max_images hit at {unit} {result['scannedTo']} of "
                f"{result['total']} — {unit}s beyond that were NOT scanned. "
                f"Call again with pages=\"{result['scannedTo'] + 1}-...\" to continue."
            )
        return out

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def render_document_pages(
        pages: str,
        file_id: int | None = None,
        local_name: str | None = None,
    ) -> dict:
        """Rasterize whole PDF pages to viewable images. THE tool for
        textbook figures: diagrams are usually vector line-art that
        get_document_images cannot see, but a rendered page shows
        everything. Give the page numbers (find them via get_file_text /
        read_local_document first), then Read the results to view them and
        embed them for the student.

        Args:
            pages: Page window, "N" or "N-M" (1-based, max 6 pages per call).
            file_id: A Canvas file id.
            local_name: OR a dropped file's name (as in read_local_document).
        """
        parsed = _parse_pages(pages)
        if parsed is None:
            return {"error": 'pages must be "N" or "N-M" (1-based).'}
        first, last = parsed

        resolved = await _resolve_document(file_id, local_name)
        if isinstance(resolved, dict):
            return resolved
        name, data, source_key = resolved
        if not name.lower().endswith(".pdf"):
            return {"error": "Page rendering works on PDFs only."}

        result = render_pdf_pages(data, first, last)
        if "error" in result:
            return {"file": name, "images": [], "count": 0, "note": result["error"]}

        saved = save_figures(f"{source_key}-pages", result["images"])
        out = {
            "file": name,
            "images": saved,
            "count": len(saved),
            "scanned": f"pages {result['scannedFrom']}-{result['scannedTo']} of {result['total']}",
            "note": _HOW_TO_USE,
        }
        if result["capped"]:
            out["coverage"] = (
                f"Rendering caps at {MAX_RENDER_PAGES} pages per call; stopped at "
                f"page {result['scannedTo']}. Call again for the rest."
            )
        return out

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    @validate_params
    async def fetch_web_image(url: str) -> dict:
        """Download one image from the web so you can view it (Read) and show
        it to the student (embed URL). Use for figures found via WebSearch/
        WebFetch — an algorithm visualization, a formula sheet, a diagram.

        Args:
            url: Direct http(s) URL of the image itself.
        """
        if not url.startswith(("http://", "https://")):
            return {"error": "url must be http(s)."}
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as e:
            return {"error": f"Fetch failed: {type(e).__name__}: {e}"}

        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        ext = _CT_EXT.get(content_type)
        if ext is None:
            return {"error": f"Not an image (content-type: {content_type or 'unknown'})."}
        if len(resp.content) > MAX_WEB_IMAGE_BYTES:
            return {"error": f"Image too large ({len(resp.content) / 1e6:.0f} MB)."}

        digest = hashlib.md5(url.encode()).hexdigest()[:16]
        saved = save_figures("web", [(f"{digest}{ext}", resp.content)])
        result: dict[str, Any] = {**saved[0], "sourceUrl": url, "note": _HOW_TO_USE}
        return result
