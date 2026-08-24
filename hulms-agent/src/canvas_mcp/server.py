#!/usr/bin/env python3
"""
HULMS Canvas MCP Server

A single-user, local (stdio-only) Model Context Protocol server for Canvas LMS.
Forked from vishalsachdev/canvas-mcp (MIT); stripped of the educator and hosted
surfaces. Credentials come from .env; there is no HTTP transport, no multi-user
auth, and no anonymization layer.
"""

import argparse
import asyncio
import sys

from fastmcp import FastMCP

from .core.config import get_config, validate_config
from .core.logging import log_error, log_info, log_warning
from .core.tool_results import install_tool_result_contract
from .tools import (
    register_grade_tools,
    register_plan_event_tools,
    register_retrieval_tools,
    register_student_write_tools,
    register_study_tools,
    register_surface_tools,
)


def create_server() -> FastMCP:
    """Create and configure the Canvas MCP server."""
    config = get_config()
    return FastMCP(name=config.mcp_server_name)


def register_all_tools(mcp: FastMCP) -> None:
    """Register the eleven-tool HULMS surface (plus env-gated student writes)."""
    log_info("Registering HULMS tools...")
    install_tool_result_contract(mcp)

    register_surface_tools(mcp)
    register_study_tools(mcp)
    register_plan_event_tools(mcp)
    register_retrieval_tools(mcp)
    register_grade_tools(mcp)
    # Extra write tools (submit_assignment etc.) register only when named in
    # STUDENT_WRITE_TOOLS (default: none).
    register_student_write_tools(mcp)

    log_info("All tools registered.")


async def _validate_token() -> tuple[bool, str]:
    """Validate the Canvas API token by calling /users/self."""
    from .core.client import make_canvas_request

    try:
        response = await make_canvas_request("get", "/users/self")
        if isinstance(response, dict) and "error" in response:
            return (False, f"Token validation failed: {response['error']}")
        user_name = response.get("name", "Unknown") if isinstance(response, dict) else "Unknown"
        return (True, f"Authenticated as: {user_name}")
    except Exception as e:
        return (False, f"Token validation error: {type(e).__name__}: {e}")


def test_connection() -> bool:
    """Test the Canvas API connection."""
    log_info("Testing Canvas API connection...")
    try:
        async def test_api() -> bool:
            ok, message = await _validate_token()
            if ok:
                log_info(f"✓ API connection successful! {message}")
                return True
            log_error(message)
            return False

        return asyncio.run(test_api())
    except Exception as e:
        log_error("API test failed with exception", exc=e)
        return False


def main() -> None:
    """Main entry point for the Canvas MCP server."""
    parser = argparse.ArgumentParser(
        description="HULMS Canvas MCP Server (local, single-user, stdio)"
    )
    parser.add_argument("--test", action="store_true",
                        help="Test Canvas API connection and exit")
    parser.add_argument("--config", action="store_true",
                        help="Show current configuration and exit")
    args = parser.parse_args()

    config = get_config()

    if not validate_config():
        log_error("Please check your .env file configuration")
        sys.exit(1)

    if args.config:
        print("HULMS Canvas MCP Server Configuration:", file=sys.stderr)
        print(f"  Server Name: {config.mcp_server_name}", file=sys.stderr)
        print(f"  Canvas API URL: {config.canvas_api_url}", file=sys.stderr)
        print(f"  Timezone: {config.timezone}", file=sys.stderr)
        print(f"  Debug Mode: {config.debug}", file=sys.stderr)
        print(f"  API Timeout: {config.api_timeout}s", file=sys.stderr)
        sys.exit(0)

    if args.test:
        sys.exit(0 if test_connection() else 1)

    log_info(f"Starting Canvas MCP server with API URL: {config.canvas_api_url}")

    # Validate token on startup (non-fatal: network may be down)
    try:
        ok, message = asyncio.run(_validate_token())
        if ok:
            log_info(f"✓ {message}")
        else:
            log_warning(
                f"Token validation failed: {message}. "
                "Check your CANVAS_TOKEN. Server will start anyway."
            )
    except Exception:
        log_warning(
            "Could not validate token on startup (network may be unavailable). "
            "Server will start anyway."
        )
    finally:
        # asyncio.run() creates and closes its own event loop. Any global httpx
        # client or semaphore created during validation is bound to that closed
        # loop; reset so they are recreated inside the loop mcp.run() starts.
        from .core import client as _client_module
        _client_module.http_client = None
        _client_module._http_client_loop_ref = None
        _client_module._request_semaphore = None
        _client_module._semaphore_loop_ref = None

    log_info("Use Ctrl+C to stop the server")

    mcp = create_server()
    register_all_tools(mcp)

    try:
        mcp.run()
    except KeyboardInterrupt:
        log_info("\nShutting down server...")
    except Exception as e:
        log_error("Server error", exc=e)
        sys.exit(1)
    finally:
        from .core.client import cleanup_http_client
        try:
            asyncio.run(cleanup_http_client())
        except RuntimeError:
            pass  # Event loop already closed
        log_info("Server stopped")


if __name__ == "__main__":
    main()
