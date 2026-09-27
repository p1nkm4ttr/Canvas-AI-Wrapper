"""CANVAS_DISABLE_TOOLS withholds named tools at registration (used by the
Codex backend to drop fetch_web_image)."""

import asyncio

from fastmcp import FastMCP

from canvas_mcp.server import register_all_tools


def _tool_names(mcp: FastMCP) -> set[str]:
    tools = asyncio.run(mcp.list_tools())
    return {t.name for t in tools}


def test_disabled_tool_is_withheld(monkeypatch):
    monkeypatch.setenv("CANVAS_DISABLE_TOOLS", "fetch_web_image, get_todo")
    mcp = FastMCP("t")
    register_all_tools(mcp)
    names = _tool_names(mcp)
    assert "fetch_web_image" not in names and "get_todo" not in names
    assert "get_agenda" in names


def test_no_env_keeps_everything(monkeypatch):
    monkeypatch.delenv("CANVAS_DISABLE_TOOLS", raising=False)
    mcp = FastMCP("t")
    register_all_tools(mcp)
    assert "fetch_web_image" in _tool_names(mcp)


def test_unknown_name_does_not_break_registration(monkeypatch):
    monkeypatch.setenv("CANVAS_DISABLE_TOOLS", "no_such_tool")
    mcp = FastMCP("t")
    register_all_tools(mcp)
    assert "get_agenda" in _tool_names(mcp)
