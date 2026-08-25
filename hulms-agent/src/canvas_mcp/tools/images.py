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
    extract_document_images,
    save_figures,
)
from ..core.local_files import spaces_root
from ..core.validation import validate_params

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
        max_images: int = DEFAULT_MAX_IMAGES,
    ) -> dict:
        """Extract the figures (diagrams, charts, screenshots) embedded in a
        course document — PDF or PPTX. Use when the material's meaning is in
        its pictures: circuit diagrams, plots, geometry. Then Read the
        extracted files to actually SEE them, and embed them in your reply
        so the student sees them too.

        Args:
            file_id: A Canvas file id (from get_course_map / get_study_context).
            local_name: OR a dropped file's name/spaceId-name (as in
                read_local_document).
            max_images: Cap (default 12; tiny logos/glyphs are filtered out).
        """
        if (file_id is None) == (local_name is None):
            return {"error": "Give exactly one of file_id or local_name."}

        if file_id is not None:
            fetched = await fetch_file_bytes(file_id)
            if isinstance(fetched, dict):
                return fetched
            name, data = fetched
            source_key = f"canvas-{file_id}"
        else:
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
            name, data = target.name, target.read_bytes()
            source_key = f"local-{target.parent.name}-{target.stem}"

        images = extract_document_images(data, name, max_images=max_images)
        if isinstance(images, str):
            return {"file": name, "images": [], "count": 0, "note": images}
        if not images:
            return {
                "file": name, "images": [], "count": 0,
                "note": "No substantial images found (tiny logos/glyphs are filtered).",
            }
        saved = save_figures(source_key, images)
        return {
            "file": name,
            "images": saved,
            "count": len(saved),
            "note": _HOW_TO_USE,
        }

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
