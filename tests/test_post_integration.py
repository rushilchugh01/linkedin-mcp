from unittest.mock import AsyncMock, MagicMock

from linkedin_mcp_server.config.schema import AppConfig
from linkedin_mcp_server.constants import (
    COMPANY_ENGAGEMENT_TIMEOUT_SECONDS,
    FEED_ENGAGEMENT_TIMEOUT_SECONDS,
    TOOL_TIMEOUT_SECONDS,
)
from linkedin_mcp_server.server import create_mcp_server


async def test_full_server_registers_post_engagement_tools():
    mcp = create_mcp_server()

    expected_timeouts = {
        "get_post_details": TOOL_TIMEOUT_SECONDS,
        "get_post_comments": TOOL_TIMEOUT_SECONDS,
        "get_post_reactors": TOOL_TIMEOUT_SECONDS,
        "search_feed_posts": TOOL_TIMEOUT_SECONDS,
        "company_engagement": COMPANY_ENGAGEMENT_TIMEOUT_SECONDS,
        "feed_engagement": FEED_ENGAGEMENT_TIMEOUT_SECONDS,
        "browser_session_mode": TOOL_TIMEOUT_SECONDS,
    }

    for tool_name, expected_timeout in expected_timeouts.items():
        tool = await mcp.get_tool(tool_name)
        assert tool is not None
        assert tool.timeout == expected_timeout


async def test_full_server_post_comments_call_uses_ready_extractor(monkeypatch):
    extractor = MagicMock()
    expected = {
        "post_url": "/feed/update/urn:li:activity:1/",
        "comments": [{"comment_text": "Synthetic comment"}],
        "comment_count": 1,
    }
    ready = AsyncMock(return_value=extractor)
    scrape = AsyncMock(return_value=expected)
    monkeypatch.setattr("linkedin_mcp_server.tools.post._get_ready_extractor", ready)
    monkeypatch.setattr("linkedin_mcp_server.tools.post.scrape_post_comments", scrape)

    mcp = create_mcp_server()
    result = await mcp.call_tool(
        "get_post_comments",
        {"post_url": "urn:li:activity:1", "limit": 3},
    )

    assert result.structured_content == expected
    ready.assert_awaited_once()
    assert ready.await_args is not None
    assert ready.await_args.kwargs == {"tool_name": "get_post_comments"}
    scrape.assert_awaited_once_with(extractor, "urn:li:activity:1", limit=3)


async def test_direct_cli_company_engagement_dispatches_workflow(monkeypatch):
    extractor = MagicMock()
    expected = {
        "company_name": "testcorp",
        "posts": [],
        "diagnostics": [],
    }
    ready = AsyncMock(return_value=extractor)
    collect = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        "linkedin_mcp_server.dependencies.get_ready_extractor",
        ready,
    )
    monkeypatch.setattr(
        "linkedin_mcp_server.cli_main.collect_company_engagement",
        collect,
    )

    from linkedin_mcp_server.cli_main import _run_direct_cli_command

    config = AppConfig()
    config.server.cli_command = "company-engagement"
    config.server.cli_args = {
        "company_name": "testcorp",
        "limit": 2,
        "include_comments": True,
        "include_reactors": True,
        "comment_limit": 5,
        "reactor_limit": 7,
        "reaction_type": "Like",
    }

    result = await _run_direct_cli_command(config)

    assert result == expected
    ready.assert_awaited_once_with(None, tool_name="company-engagement")
    collect.assert_awaited_once_with(
        extractor,
        "testcorp",
        limit=2,
        include_comments=True,
        include_reactors=True,
        comment_limit=5,
        reactor_limit=7,
        reaction_type="Like",
    )


async def test_direct_cli_search_feed_posts_dispatches_workflow(monkeypatch):
    extractor = MagicMock()
    expected = {
        "feed_url": "https://www.linkedin.com/feed/",
        "posts": [],
        "diagnostics": [],
    }
    ready = AsyncMock(return_value=extractor)
    search = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        "linkedin_mcp_server.dependencies.get_ready_extractor",
        ready,
    )
    monkeypatch.setattr("linkedin_mcp_server.cli_main.search_feed_posts", search)

    from linkedin_mcp_server.cli_main import _run_direct_cli_command

    config = AppConfig()
    config.server.cli_command = "search-feed-posts"
    config.server.cli_args = {
        "keywords": ["law", "legal"],
        "max_posts": 4,
        "scrolls": 6,
        "min_reactions": 3,
        "min_comments": 2,
        "include_promoted": True,
    }

    result = await _run_direct_cli_command(config)

    assert result == expected
    ready.assert_awaited_once_with(None, tool_name="search-feed-posts")
    search.assert_awaited_once_with(
        extractor,
        keywords=["law", "legal"],
        max_posts=4,
        scrolls=6,
        min_reactions=3,
        min_comments=2,
        include_promoted=True,
    )


async def test_direct_cli_feed_engagement_dispatches_workflow(monkeypatch):
    extractor = MagicMock()
    expected = {
        "feed_url": "https://www.linkedin.com/feed/",
        "posts": [],
        "diagnostics": [],
    }
    ready = AsyncMock(return_value=extractor)
    collect = AsyncMock(return_value=expected)
    monkeypatch.setattr(
        "linkedin_mcp_server.dependencies.get_ready_extractor",
        ready,
    )
    monkeypatch.setattr(
        "linkedin_mcp_server.cli_main.collect_feed_engagement",
        collect,
    )

    from linkedin_mcp_server.cli_main import _run_direct_cli_command

    config = AppConfig()
    config.server.cli_command = "feed-engagement"
    config.server.cli_args = {
        "keywords": ["law", "legal"],
        "max_posts": 3,
        "scrolls": 5,
        "include_comments": True,
        "include_reactors": True,
        "comment_limit": 8,
        "reactor_limit": 9,
        "reaction_type": "Like",
        "min_reactions": 4,
        "min_comments": 2,
        "include_promoted": True,
    }

    result = await _run_direct_cli_command(config)

    assert result == expected
    ready.assert_awaited_once_with(None, tool_name="feed-engagement")
    collect.assert_awaited_once_with(
        extractor,
        keywords=["law", "legal"],
        max_posts=3,
        scrolls=5,
        include_comments=True,
        include_reactors=True,
        comment_limit=8,
        reactor_limit=9,
        reaction_type="Like",
        min_reactions=4,
        min_comments=2,
        include_promoted=True,
    )
