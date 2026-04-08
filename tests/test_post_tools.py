from typing import Any, Callable, Coroutine, cast
from unittest.mock import AsyncMock, MagicMock

from fastmcp import FastMCP
from fastmcp.tools import FunctionTool

from linkedin_mcp_server.constants import (
    COMPANY_ENGAGEMENT_TIMEOUT_SECONDS,
    FEED_ENGAGEMENT_TIMEOUT_SECONDS,
)


async def get_tool_fn(
    mcp: FastMCP, name: str
) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    """Extract tool function from FastMCP by name using public API."""
    tool = await mcp.get_tool(name)
    if tool is None:
        raise ValueError(f"Tool '{name}' not found")
    return cast(FunctionTool, tool).fn


async def test_get_post_comments_tool_calls_scraper(monkeypatch, mock_context):
    expected = {"post_url": "/feed/update/urn:li:activity:1/", "comments": []}
    scrape = AsyncMock(return_value=expected)
    monkeypatch.setattr("linkedin_mcp_server.tools.post.scrape_post_comments", scrape)

    from linkedin_mcp_server.tools.post import register_post_tools

    mcp = FastMCP("test")
    register_post_tools(mcp)
    extractor = MagicMock()

    tool_fn = await get_tool_fn(mcp, "get_post_comments")
    result = await tool_fn(
        "urn:li:activity:1", mock_context, limit=5, extractor=extractor
    )

    assert result == expected
    scrape.assert_awaited_once_with(extractor, "urn:li:activity:1", limit=5)


async def test_get_post_reactors_tool_calls_scraper(monkeypatch, mock_context):
    expected = {"post_url": "/feed/update/urn:li:activity:1/", "reactors": []}
    scrape = AsyncMock(return_value=expected)
    monkeypatch.setattr("linkedin_mcp_server.tools.post.scrape_post_reactors", scrape)

    from linkedin_mcp_server.tools.post import register_post_tools

    mcp = FastMCP("test")
    register_post_tools(mcp)
    extractor = MagicMock()

    tool_fn = await get_tool_fn(mcp, "get_post_reactors")
    result = await tool_fn(
        "urn:li:activity:1",
        mock_context,
        limit=7,
        reaction_type="Like",
        extractor=extractor,
    )

    assert result == expected
    scrape.assert_awaited_once_with(
        extractor,
        "urn:li:activity:1",
        limit=7,
        reaction_type="Like",
    )


async def test_company_engagement_tool_uses_dedicated_timeout(
    monkeypatch, mock_context
):
    expected = {"company_name": "testcorp", "posts": [], "diagnostics": []}
    collect = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        "linkedin_mcp_server.tools.post.collect_company_engagement",
        collect,
    )

    from linkedin_mcp_server.tools.post import register_post_tools

    mcp = FastMCP("test")
    register_post_tools(mcp)
    tool = await mcp.get_tool("company_engagement")
    assert tool is not None
    assert tool.timeout == COMPANY_ENGAGEMENT_TIMEOUT_SECONDS

    tool_fn = await get_tool_fn(mcp, "company_engagement")
    extractor = MagicMock()
    result = await tool_fn(
        "testcorp",
        mock_context,
        limit=2,
        include_comments=True,
        include_reactors=True,
        comment_limit=4,
        reactor_limit=6,
        reaction_type="Like",
        extractor=extractor,
    )

    assert result == expected
    collect.assert_awaited_once_with(
        extractor,
        "testcorp",
        limit=2,
        include_comments=True,
        include_reactors=True,
        comment_limit=4,
        reactor_limit=6,
        reaction_type="Like",
    )


async def test_search_feed_posts_tool_calls_workflow(monkeypatch, mock_context):
    expected = {
        "posts": [
            {"post_url": "https://www.linkedin.com/feed/update/urn:li:activity:1/"}
        ]
    }
    search = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        "linkedin_mcp_server.tools.post.search_feed_posts_workflow", search
    )

    from linkedin_mcp_server.tools.post import register_post_tools

    mcp = FastMCP("test")
    register_post_tools(mcp)
    extractor = MagicMock()

    tool_fn = await get_tool_fn(mcp, "search_feed_posts")
    result = await tool_fn(
        mock_context,
        keywords=["legal"],
        max_posts=2,
        scrolls=3,
        min_reactions=4,
        min_comments=5,
        include_promoted=True,
        extractor=extractor,
    )

    assert result == expected
    search.assert_awaited_once_with(
        extractor,
        keywords=["legal"],
        max_posts=2,
        scrolls=3,
        min_reactions=4,
        min_comments=5,
        include_promoted=True,
    )


async def test_feed_engagement_tool_uses_dedicated_timeout(monkeypatch, mock_context):
    expected = {"posts": [], "diagnostics": []}
    collect = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        "linkedin_mcp_server.tools.post.collect_feed_engagement", collect
    )

    from linkedin_mcp_server.tools.post import register_post_tools

    mcp = FastMCP("test")
    register_post_tools(mcp)
    tool = await mcp.get_tool("feed_engagement")
    assert tool is not None
    assert tool.timeout == FEED_ENGAGEMENT_TIMEOUT_SECONDS

    tool_fn = await get_tool_fn(mcp, "feed_engagement")
    extractor = MagicMock()
    result = await tool_fn(
        mock_context,
        keywords=["legal"],
        max_posts=2,
        scrolls=3,
        include_comments=True,
        include_reactors=True,
        comment_limit=4,
        reactor_limit=6,
        reaction_type="Like",
        min_reactions=1,
        min_comments=2,
        include_promoted=True,
        extractor=extractor,
    )

    assert result == expected
    collect.assert_awaited_once_with(
        extractor,
        keywords=["legal"],
        max_posts=2,
        scrolls=3,
        include_comments=True,
        include_reactors=True,
        comment_limit=4,
        reactor_limit=6,
        reaction_type="Like",
        min_reactions=1,
        min_comments=2,
        include_promoted=True,
    )
