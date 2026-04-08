"""LinkedIn post engagement scraping tools."""

from __future__ import annotations

import logging
from typing import Any, NoReturn

from fastmcp import Context, FastMCP

from linkedin_mcp_server.constants import (
    COMPANY_ENGAGEMENT_TIMEOUT_SECONDS,
    FEED_ENGAGEMENT_TIMEOUT_SECONDS,
    TOOL_TIMEOUT_SECONDS,
)
from linkedin_mcp_server.core.exceptions import AuthenticationError
from linkedin_mcp_server.error_handler import raise_tool_error
from linkedin_mcp_server.scraping.post import (
    scrape_post_comments,
    scrape_post_details,
    scrape_post_reactors,
)
from linkedin_mcp_server.workflows.company_engagement import collect_company_engagement
from linkedin_mcp_server.workflows.feed_engagement import (
    collect_feed_engagement,
    search_feed_posts as search_feed_posts_workflow,
)

logger = logging.getLogger(__name__)


async def _get_ready_extractor(ctx: Context, *, tool_name: str) -> Any:
    from linkedin_mcp_server.dependencies import get_ready_extractor

    return await get_ready_extractor(ctx, tool_name=tool_name)


async def _handle_auth_error(error: AuthenticationError, ctx: Context) -> NoReturn:
    from linkedin_mcp_server.dependencies import handle_auth_error

    await handle_auth_error(error, ctx)


def register_post_tools(mcp: FastMCP) -> None:
    """Register post-level tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Post Details",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"post", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_post_details(
        post_url: str,
        ctx: Context,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Get details for one LinkedIn feed post URL."""
        try:
            extractor = extractor or await _get_ready_extractor(
                ctx, tool_name="get_post_details"
            )
            logger.info("Tool get_post_details started: post_url=%s", post_url)
            await ctx.report_progress(
                progress=0,
                total=100,
                message="Starting post details scrape",
            )
            result = await scrape_post_details(extractor, post_url)
            await ctx.report_progress(progress=100, total=100, message="Complete")
            logger.info("Tool get_post_details completed: post_url=%s", post_url)
            return result
        except AuthenticationError as e:
            try:
                await _handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_post_details")
        except Exception as e:
            raise_tool_error(e, "get_post_details")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Post Comments",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"post", "comments", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_post_comments(
        post_url: str,
        ctx: Context,
        limit: int | None = 20,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Get visible comments and commenters for one LinkedIn feed post URL."""
        try:
            extractor = extractor or await _get_ready_extractor(
                ctx, tool_name="get_post_comments"
            )
            logger.info(
                "Tool get_post_comments started: post_url=%s limit=%s",
                post_url,
                limit,
            )
            await ctx.report_progress(
                progress=0,
                total=100,
                message="Starting post comments scrape",
            )
            result = await scrape_post_comments(extractor, post_url, limit=limit)
            await ctx.report_progress(progress=100, total=100, message="Complete")
            logger.info(
                "Tool get_post_comments completed: post_url=%s count=%s",
                post_url,
                result.get("comment_count"),
            )
            return result
        except AuthenticationError as e:
            try:
                await _handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_post_comments")
        except Exception as e:
            raise_tool_error(e, "get_post_comments")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Post Reactors",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"post", "reactions", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_post_reactors(
        post_url: str,
        ctx: Context,
        limit: int | None = 50,
        reaction_type: str | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Get visible reactors/likers for one LinkedIn feed post URL."""
        try:
            extractor = extractor or await _get_ready_extractor(
                ctx, tool_name="get_post_reactors"
            )
            logger.info(
                "Tool get_post_reactors started: post_url=%s limit=%s reaction_type=%s",
                post_url,
                limit,
                reaction_type,
            )
            await ctx.report_progress(
                progress=0,
                total=100,
                message="Starting post reactors scrape",
            )
            result = await scrape_post_reactors(
                extractor,
                post_url,
                limit=limit,
                reaction_type=reaction_type,
            )
            await ctx.report_progress(progress=100, total=100, message="Complete")
            logger.info(
                "Tool get_post_reactors completed: post_url=%s count=%s",
                post_url,
                result.get("reactor_count"),
            )
            return result
        except AuthenticationError as e:
            try:
                await _handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_post_reactors")
        except Exception as e:
            raise_tool_error(e, "get_post_reactors")  # NoReturn

    @mcp.tool(
        timeout=COMPANY_ENGAGEMENT_TIMEOUT_SECONDS,
        title="Company Engagement",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"company", "post", "engagement", "scraping"},
        exclude_args=["extractor"],
    )
    async def company_engagement(
        company_name: str,
        ctx: Context,
        limit: int | None = 3,
        include_comments: bool = True,
        include_reactors: bool = False,
        comment_limit: int | None = 20,
        reactor_limit: int | None = 0,
        reaction_type: str | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Collect bounded recent post engagement for a LinkedIn company."""
        try:
            extractor = extractor or await _get_ready_extractor(
                ctx, tool_name="company_engagement"
            )
            logger.info(
                "Tool company_engagement started: company=%s limit=%s "
                "include_comments=%s include_reactors=%s comment_limit=%s "
                "reactor_limit=%s reaction_type=%s",
                company_name,
                limit,
                include_comments,
                include_reactors,
                comment_limit,
                reactor_limit,
                reaction_type,
            )
            await ctx.report_progress(
                progress=0,
                total=100,
                message="Starting company engagement scrape",
            )
            result = await collect_company_engagement(
                extractor,
                company_name,
                limit=limit,
                include_comments=include_comments,
                include_reactors=include_reactors,
                comment_limit=comment_limit,
                reactor_limit=reactor_limit,
                reaction_type=reaction_type,
            )
            await ctx.report_progress(progress=100, total=100, message="Complete")
            logger.info(
                "Tool company_engagement completed: company=%s posts=%d diagnostics=%d",
                company_name,
                len(result.get("posts", [])),
                len(result.get("diagnostics", [])),
            )
            return result
        except AuthenticationError as e:
            try:
                await _handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "company_engagement")
        except Exception as e:
            raise_tool_error(e, "company_engagement")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Search Feed Posts",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"feed", "post", "scraping"},
        exclude_args=["extractor"],
    )
    async def search_feed_posts(
        ctx: Context,
        keywords: list[str] | None = None,
        max_posts: int | None = 10,
        scrolls: int | None = 10,
        min_reactions: int = 0,
        min_comments: int = 0,
        include_promoted: bool = False,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Search the authenticated LinkedIn home feed for matching posts."""
        try:
            extractor = extractor or await _get_ready_extractor(
                ctx, tool_name="search_feed_posts"
            )
            logger.info(
                "Tool search_feed_posts started: max_posts=%s scrolls=%s "
                "keywords=%s min_reactions=%s min_comments=%s include_promoted=%s",
                max_posts,
                scrolls,
                keywords,
                min_reactions,
                min_comments,
                include_promoted,
            )
            await ctx.report_progress(
                progress=0,
                total=100,
                message="Starting feed post search",
            )
            result = await search_feed_posts_workflow(
                extractor,
                keywords=keywords,
                max_posts=max_posts,
                scrolls=scrolls,
                min_reactions=min_reactions,
                min_comments=min_comments,
                include_promoted=include_promoted,
            )
            await ctx.report_progress(progress=100, total=100, message="Complete")
            logger.info(
                "Tool search_feed_posts completed: posts=%d diagnostics=%d",
                len(result.get("posts", [])),
                len(result.get("diagnostics", [])),
            )
            return result
        except AuthenticationError as e:
            try:
                await _handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "search_feed_posts")
        except Exception as e:
            raise_tool_error(e, "search_feed_posts")  # NoReturn

    @mcp.tool(
        timeout=FEED_ENGAGEMENT_TIMEOUT_SECONDS,
        title="Feed Engagement",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"feed", "post", "engagement", "scraping"},
        exclude_args=["extractor"],
    )
    async def feed_engagement(
        ctx: Context,
        keywords: list[str] | None = None,
        max_posts: int | None = 5,
        scrolls: int | None = 10,
        include_comments: bool = True,
        include_reactors: bool = False,
        comment_limit: int | None = 20,
        reactor_limit: int | None = 0,
        reaction_type: str | None = None,
        min_reactions: int = 0,
        min_comments: int = 0,
        include_promoted: bool = False,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """Discover matching home-feed posts and collect bounded engagement."""
        try:
            extractor = extractor or await _get_ready_extractor(
                ctx, tool_name="feed_engagement"
            )
            logger.info(
                "Tool feed_engagement started: max_posts=%s scrolls=%s "
                "keywords=%s include_comments=%s include_reactors=%s "
                "comment_limit=%s reactor_limit=%s reaction_type=%s "
                "min_reactions=%s min_comments=%s include_promoted=%s",
                max_posts,
                scrolls,
                keywords,
                include_comments,
                include_reactors,
                comment_limit,
                reactor_limit,
                reaction_type,
                min_reactions,
                min_comments,
                include_promoted,
            )
            await ctx.report_progress(
                progress=0,
                total=100,
                message="Starting feed engagement scrape",
            )

            async def report(progress: int, total: int, message: str) -> None:
                await ctx.report_progress(
                    progress=progress,
                    total=total,
                    message=message,
                )

            result = await collect_feed_engagement(
                extractor,
                keywords=keywords,
                max_posts=max_posts,
                scrolls=scrolls,
                include_comments=include_comments,
                include_reactors=include_reactors,
                comment_limit=comment_limit,
                reactor_limit=reactor_limit,
                reaction_type=reaction_type,
                min_reactions=min_reactions,
                min_comments=min_comments,
                include_promoted=include_promoted,
                progress=report,
            )
            logger.info(
                "Tool feed_engagement completed: posts=%d diagnostics=%d",
                len(result.get("posts", [])),
                len(result.get("diagnostics", [])),
            )
            return result
        except AuthenticationError as e:
            try:
                await _handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "feed_engagement")
        except Exception as e:
            raise_tool_error(e, "feed_engagement")  # NoReturn
