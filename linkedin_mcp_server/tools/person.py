"""
LinkedIn person profile scraping tools.

Uses innerText extraction for resilient profile data capture
with configurable section selection.
"""

import logging
from typing import Any

from fastmcp import Context, FastMCP

from linkedin_mcp_server.callbacks import MCPContextProgressCallback
from linkedin_mcp_server.constants import TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.core.exceptions import AuthenticationError
from linkedin_mcp_server.dependencies import get_ready_extractor, handle_auth_error
from linkedin_mcp_server.error_handler import raise_tool_error
from linkedin_mcp_server.scraping import parse_person_sections
from linkedin_mcp_server.scraping.connections import scrape_recent_connections

logger = logging.getLogger(__name__)


def register_person_tools(mcp: FastMCP) -> None:
    """Register all person-related tools with the MCP server."""

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Person Profile",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"person", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_person_profile(
        linkedin_username: str,
        ctx: Context,
        sections: str | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Get a specific person's LinkedIn profile.

        Args:
            linkedin_username: LinkedIn username (e.g., "stickerdaniel", "williamhgates")
            ctx: FastMCP context for progress reporting
            sections: Comma-separated list of extra sections to scrape.
                The main profile page and contact-info overlay are always included.
                Available sections: experience, education, interests, honors, languages, certifications, contact_info, posts
                Examples: "experience,education", "contact_info", "certifications", "honors,languages", "posts"
                Default (None) scrapes the main profile page and contact-info overlay.

        Returns:
            Dict with url, sections (name -> raw text), connection metadata, and
            optional references.
            Sections may be absent if extraction yielded no content for that page.
            Includes unknown_sections list when unrecognised names are passed.
            Includes contact_info with structured emails, phones, profile URLs,
            websites, and connected_since when LinkedIn exposes them.
            The connection field includes status, degree, is_connected,
            is_pending, and is_connectable.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_person_profile"
            )
            requested, unknown = parse_person_sections(sections)

            logger.info(
                "Scraping profile: %s (sections=%s)",
                linkedin_username,
                sections,
            )

            cb = MCPContextProgressCallback(ctx)
            result = await extractor.scrape_person(
                linkedin_username, requested, callbacks=cb
            )

            if unknown:
                result["unknown_sections"] = unknown

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_person_profile")
        except Exception as e:
            raise_tool_error(e, "get_person_profile")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Search People",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"person", "search"},
        exclude_args=["extractor"],
    )
    async def search_people(
        keywords: str,
        ctx: Context,
        location: str | None = None,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Search for people on LinkedIn.

        Args:
            keywords: Search keywords (e.g., "software engineer", "recruiter at Google")
            ctx: FastMCP context for progress reporting
            location: Optional location filter (e.g., "New York", "Remote")

        Returns:
            Dict with url, sections (name -> raw text), and optional references.
            The LLM should parse the raw text to extract individual people and their profiles.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="search_people"
            )
            logger.info(
                "Searching people: keywords='%s', location='%s'",
                keywords,
                location,
            )

            await ctx.report_progress(
                progress=0, total=100, message="Starting people search"
            )

            result = await extractor.search_people(keywords, location)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "search_people")
        except Exception as e:
            raise_tool_error(e, "search_people")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Connect With Person",
        annotations={"destructiveHint": True, "openWorldHint": True},
        tags={"person", "actions"},
        exclude_args=["extractor"],
    )
    async def connect_with_person(
        linkedin_username: str,
        ctx: Context,
        note: str | None = None,
        send_without_note: bool = True,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Send a LinkedIn connection request or accept an incoming one.

        Outgoing requests use this fork's Veridis-style profile top-card flow
        first: it scopes buttons to the visible profile header, handles direct
        Connect and More -> Connect, and uses the shadow-DOM "Send without a
        note" confirmation when LinkedIn exposes it.
        By default the tool sends without a note because many non-Premium
        LinkedIn accounts cannot send personalized invites. Set
        send_without_note=false to explicitly try the note flow.

        The tool is annotated with destructiveHint so MCP clients will
        prompt for user confirmation before execution.

        Args:
            linkedin_username: LinkedIn username (e.g., "stickerdaniel", "williamhgates")
            ctx: FastMCP context for progress reporting
            note: Optional note to include with the invitation
            send_without_note: When true, ignore note and send using
                "Send without a note"; defaults to true for non-Premium accounts

        Returns:
            Dict with url, status, message, and note_sent.
            Statuses: pending, already_connected, follow_only,
            connect_unavailable, unavailable, send_failed,
            note_not_supported, connected, or accepted.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="connect_with_person"
            )
            logger.info(
                "Connecting with person: %s (note=%s, send_without_note=%s)",
                linkedin_username,
                note is not None,
                send_without_note,
            )

            await ctx.report_progress(
                progress=0,
                total=100,
                message="Starting LinkedIn connection flow",
            )

            result = await extractor.connect_with_person(
                linkedin_username,
                note=note,
                send_without_note=send_without_note,
            )

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "connect_with_person")
        except Exception as e:
            raise_tool_error(e, "connect_with_person")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Sidebar Profiles",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"person", "scraping"},
        exclude_args=["extractor"],
    )
    async def get_sidebar_profiles(
        linkedin_username: str,
        ctx: Context,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Get profile links from sidebar recommendation sections on a LinkedIn profile page.

        Extracts profiles from "More profiles for you", "Explore premium profiles",
        and "People you may know" sidebar sections. Follows "Show all" links to
        return the full list from each section. Sections that redirect to
        linkedin.com/premium are skipped.

        Args:
            linkedin_username: LinkedIn username of the profile page to scrape
                (e.g., "stickerdaniel", "williamhgates")
            ctx: FastMCP context for progress reporting

        Returns:
            Dict with url and sidebar_profiles mapping section key to a list of
            /in/username/ paths. Only sections present on the page are included.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_sidebar_profiles"
            )
            logger.info("Getting sidebar profiles for: %s", linkedin_username)

            await ctx.report_progress(
                progress=0, total=100, message="Extracting sidebar profiles"
            )

            result = await extractor.get_sidebar_profiles(linkedin_username)

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_sidebar_profiles")
        except Exception as e:
            raise_tool_error(e, "get_sidebar_profiles")  # NoReturn

    @mcp.tool(
        timeout=TOOL_TIMEOUT_SECONDS,
        title="Get Recent Connections",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"person", "scraping", "connections"},
        exclude_args=["extractor"],
    )
    async def get_recent_connections(
        ctx: Context,
        days: int = 10,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Get recent LinkedIn connections from the past N days.

        Navigates to the connections page (sorted by "Recently added"),
        scrolls until connections older than the cutoff date are reached,
        and returns structured profile data for each connection.

        This does NOT consume LinkedIn search limits — it reads your own
        connections page directly.

        Args:
            ctx: FastMCP context for progress reporting
            days: How many days back to look (default: 10, max: 365)

        Returns:
            Dict with url, connections list, total_found,
            total_within_range, cutoff_date, and diagnostics.
            Each connection includes name, username, profile_url,
            headline, connected_date, connected_date_raw, and profile_urn.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="get_recent_connections"
            )
            logger.info("Getting recent connections: days=%d", days)

            await ctx.report_progress(
                progress=0,
                total=100,
                message=f"Fetching connections from the past {days} days",
            )

            result = await scrape_recent_connections(
                extractor, days=days
            )

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "get_recent_connections")
        except Exception as e:
            raise_tool_error(e, "get_recent_connections")  # NoReturn
