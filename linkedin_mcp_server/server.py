"""
FastMCP server implementation for LinkedIn integration with tool registration.

Creates and configures the MCP server with comprehensive LinkedIn tool suite including
person profiles, company data, job information, and session management capabilities.
"""

import logging
from typing import Any, AsyncIterator

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan

from linkedin_mcp_server.bootstrap import (
    get_runtime_policy,
    initialize_bootstrap,
    start_background_browser_setup_if_needed,
)
from linkedin_mcp_server.config import get_config
from linkedin_mcp_server.constants import TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.drivers.browser import (
    close_browser,
    get_headless,
    has_active_browser,
    set_headless,
)
from linkedin_mcp_server.error_handler import raise_tool_error
from linkedin_mcp_server.sequential_tool_middleware import (
    SequentialToolExecutionMiddleware,
)
from linkedin_mcp_server.tools.company import register_company_tools
from linkedin_mcp_server.tools.job import register_job_tools
from linkedin_mcp_server.tools.messaging import register_messaging_tools
from linkedin_mcp_server.tools.person import register_person_tools
from linkedin_mcp_server.tools.post import register_post_tools

logger = logging.getLogger(__name__)


@lifespan
async def browser_lifespan(app: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage browser lifecycle — cleanup on shutdown.

    Derived runtime durability must not depend on this hook. Docker runtime
    sessions are checkpoint-committed when they are created.
    """
    del app
    logger.info("LinkedIn MCP Server starting...")
    initialize_bootstrap(get_runtime_policy())
    await start_background_browser_setup_if_needed()
    yield {}
    logger.info("LinkedIn MCP Server shutting down...")
    await close_browser()


def create_mcp_server() -> FastMCP:
    """Create and configure the MCP server with all LinkedIn tools."""
    mcp = FastMCP(
        "linkedin_scraper",
        lifespan=browser_lifespan,
        mask_error_details=True,
    )
    mcp.add_middleware(SequentialToolExecutionMiddleware())

    # Register all tools
    register_person_tools(mcp)
    register_company_tools(mcp)
    register_job_tools(mcp)
    register_messaging_tools(mcp)
    register_post_tools(mcp)

    # Register session management tool
    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Browser Session Mode",
        tags={"session", "browser"},
    )
    async def browser_session_mode(headless: bool | None = None) -> dict[str, Any]:
        """Get or set browser headless mode before opening a LinkedIn browser session.

        Call this before scraping tools when you want to verify whether the next
        browser session will be visible. If a browser is already active, changing
        the mode only affects the next session after close_session.
        """
        try:
            config = get_config()
            previous_headless = get_headless()
            active_browser = has_active_browser()

            if headless is not None:
                config.browser.headless = headless
                set_headless(headless)

            current_headless = get_headless()
            return {
                "status": "success",
                "headless": current_headless,
                "mode": "headless" if current_headless else "no_headless",
                "configured_headless": config.browser.headless,
                "previous_headless": previous_headless,
                "active_browser": active_browser,
                "applied_to_active_browser": not active_browser,
                "requires_close_session": active_browser and headless is not None,
                "message": (
                    "Browser mode updated for the next browser session; call close_session first to relaunch in this mode."
                    if active_browser and headless is not None
                    else "Browser mode is configured for the next browser session."
                ),
            }
        except Exception as e:
            raise_tool_error(e, "browser_session_mode")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Close Session",
        annotations={"destructiveHint": True},
        tags={"session"},
    )
    async def close_session() -> dict[str, Any]:
        """Close the current browser session and clean up resources."""
        try:
            await close_browser()
            return {
                "status": "success",
                "message": "Successfully closed the browser session and cleaned up resources",
            }
        except Exception as e:
            raise_tool_error(e, "close_session")  # NoReturn

    return mcp
