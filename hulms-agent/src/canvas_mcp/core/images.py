"""Figure extraction: pull embedded images out of course documents.

Lecture slides carry their meaning in diagrams as much as text. Extracted
figures are saved under spaces/.figures/ where the coach can view them with
its (vision-capable) Read tool and the UI can serve them inline.
"""

import hashlib
import io
from pathlib import Path
from typing import Any

from .extract import file_extension
from .local_files import spaces_root

# Below this, an image is almost certainly a logo, bullet glyph, or border.
MIN_IMAGE_BYTES = 4096
DEFAULT_MAX_IMAGES = 12

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}


def _dedupe_key(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _pdf_images(data: bytes, cap: int, min_bytes: int) -> list[tuple[str, bytes]]:
    from pypdf import PdfReader

    out: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    reader = PdfReader(io.BytesIO(data))
    for page_num, page in enumerate(reader.pages, 1):
        try:
            images = page.images
        except Exception:
            continue  # one bad page must not kill the rest
        for img in images:
            try:
                blob = img.data
            except Exception:
                continue
            if len(blob) < min_bytes:
                continue
            key = _dedupe_key(blob)
            if key in seen:
                continue
            seen.add(key)
            ext = file_extension(img.name) or ".png"
            out.append((f"page{page_num:03d}-{len(out) + 1}{ext}", blob))
            if len(out) >= cap:
                return out
    return out


def _pptx_images(data: bytes, cap: int, min_bytes: int) -> list[tuple[str, bytes]]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    out: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    prs = Presentation(io.BytesIO(data))
    for slide_num, slide in enumerate(prs.slides, 1):
        for shape in slide.shapes:
            if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
                continue
            try:
                blob = shape.image.blob
                ext = "." + shape.image.ext
            except Exception:
                continue
            if len(blob) < min_bytes:
                continue
            key = _dedupe_key(blob)
            if key in seen:
                continue
            seen.add(key)
            out.append((f"slide{slide_num:03d}-{len(out) + 1}{ext}", blob))
            if len(out) >= cap:
                return out
    return out


def extract_document_images(
    data: bytes,
    filename: str,
    max_images: int = DEFAULT_MAX_IMAGES,
    min_bytes: int = MIN_IMAGE_BYTES,
) -> list[tuple[str, bytes]] | str:
    """Embedded images of a PDF/PPTX as (name, bytes); a str is an error note."""
    ext = file_extension(filename)
    try:
        if ext == ".pdf":
            return _pdf_images(data, max_images, min_bytes)
        if ext == ".pptx":
            return _pptx_images(data, max_images, min_bytes)
        if ext in _IMAGE_EXTS:
            # The document IS an image.
            return [(f"image{ext}", data)] if len(data) >= min_bytes else []
        return f"No image extractor for '{ext or 'file without extension'}' (PDF and PPTX carry figures)."
    except Exception as e:
        return f"Image extraction failed: {type(e).__name__}: {e}"


def figures_dir(source_key: str) -> Path:
    """Where a source's extracted figures live (under the gitignored spaces/)."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in source_key)[:80]
    return spaces_root() / ".figures" / safe


def save_figures(source_key: str, images: list[tuple[str, bytes]]) -> list[dict[str, Any]]:
    """Write figures to disk; returns view/embed handles for each.

    `file` is the absolute path (for the coach's vision Read); `embed` is the
    UI-served URL path for showing the image inline in chat.
    """
    directory = figures_dir(source_key)
    directory.mkdir(parents=True, exist_ok=True)
    root = spaces_root()
    out = []
    for name, blob in images:
        target = directory / name
        target.write_bytes(blob)
        rel = target.relative_to(root).as_posix()
        out.append({
            "name": name,
            "file": str(target),
            "embed": f"/api/spacefile?p={rel}",
            "sizeBytes": len(blob),
        })
    return out
