"""Tests for figure extraction and the image tools."""

import io
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import FastMCP

import canvas_mcp.core.config as config_module
from canvas_mcp.core.images import extract_document_images, save_figures
from canvas_mcp.tools.images import register_image_tools


def _png(size=(64, 64), color=(200, 30, 30)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _noisy_png(size=(128, 128)):
    """Incompressible image that clears the tiny-logo byte filter."""
    import os
    from PIL import Image
    img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _pptx_with_pictures(*pngs):
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    for png in pngs:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        slide.shapes.add_picture(io.BytesIO(png), Inches(1), Inches(1))
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    root = tmp_path / "hulms-agent"
    root.mkdir()
    monkeypatch.setattr(config_module, "REPO_ROOT", root)
    (tmp_path / "spaces" / "c1").mkdir(parents=True)
    yield tmp_path


def test_pptx_pictures_extracted():
    data = _pptx_with_pictures(_png())
    images = extract_document_images(data, "deck.pptx", min_bytes=0)
    assert isinstance(images, list) and len(images) == 1
    name, blob = images[0]
    assert name.startswith("slide001") and blob[:4] == b"\x89PNG"


def test_duplicate_pictures_deduped():
    same = _png()
    data = _pptx_with_pictures(same, same)
    images = extract_document_images(data, "deck.pptx", min_bytes=0)
    assert len(images) == 1


def test_tiny_images_filtered():
    data = _pptx_with_pictures(_png(size=(4, 4)))
    assert extract_document_images(data, "deck.pptx", min_bytes=4096) == []


def test_unsupported_format_returns_note():
    result = extract_document_images(b"x", "notes.docx")
    assert isinstance(result, str) and "No image extractor" in result


def test_standalone_image_passthrough():
    png = _png()
    images = extract_document_images(png, "diagram.png", min_bytes=0)
    assert images == [("image.png", png)]


def test_save_figures_returns_view_and_embed(fake_root):
    saved = save_figures("canvas-42", [("page001-1.png", _png())])
    assert saved[0]["file"].endswith("page001-1.png")
    assert saved[0]["embed"].startswith("/api/spacefile?p=.figures/canvas-42/")
    from pathlib import Path
    assert Path(saved[0]["file"]).exists()


# ------------------------------------------------------------------- tools

def get_tool(name):
    mcp = FastMCP("test")
    captured = {}
    original = mcp.tool

    def capturing(*a, **k):
        d = original(*a, **k)
        def w(fn):
            captured[fn.__name__] = fn
            return d(fn)
        return w
    mcp.tool = capturing
    register_image_tools(mcp)
    return captured[name]


async def test_document_images_requires_exactly_one_source():
    tool = get_tool("get_document_images")
    assert "error" in await tool()
    assert "error" in await tool(file_id=1, local_name="x.pdf")


async def test_document_images_from_local_pptx(fake_root):
    deck = fake_root / "spaces" / "c1" / "deck.pptx"
    deck.write_bytes(_pptx_with_pictures(_noisy_png()))
    result = await get_tool("get_document_images")(local_name="deck.pptx")
    assert result["count"] == 1
    assert result["images"][0]["embed"].startswith("/api/spacefile?p=.figures/")


async def test_document_images_canvas_error_propagates():
    with patch("canvas_mcp.tools.images.fetch_file_bytes",
               new=AsyncMock(return_value={"error": "Could not read file 9"})):
        assert "error" in await get_tool("get_document_images")(file_id=9)


async def test_fetch_web_image_saves_png(fake_root):
    png = _png()

    class FakeResp:
        headers = {"content-type": "image/png"}
        content = png
        def raise_for_status(self): pass

    class FakeClient:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return FakeResp()

    with patch("canvas_mcp.tools.images.httpx.AsyncClient", FakeClient):
        result = await get_tool("fetch_web_image")("https://example.com/fig.png")
    assert result["embed"].startswith("/api/spacefile?p=.figures/web/")
    assert result["sourceUrl"] == "https://example.com/fig.png"


async def test_fetch_web_image_rejects_non_image(fake_root):
    class FakeResp:
        headers = {"content-type": "text/html"}
        content = b"<html>"
        def raise_for_status(self): pass

    class FakeClient:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): return FakeResp()

    with patch("canvas_mcp.tools.images.httpx.AsyncClient", FakeClient):
        assert "error" in await get_tool("fetch_web_image")("https://example.com/page")
