"""Bounded company post engagement workflow.

This module intentionally does not register MCP tools or CLI commands.  It
orchestrates existing extractor capabilities so both surfaces can reuse the
same bounded, logged, partial-result behavior.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from linkedin_mcp_server.scraping.browser_pacing import BrowserPacer
from linkedin_mcp_server.scraping.post import normalize_post_url

logger = logging.getLogger(__name__)
_COMPANY_PACER = BrowserPacer(logger_name=__name__)

LINKEDIN_ORIGIN = "https://www.linkedin.com"
DEFAULT_POST_LIMIT = 3
DEFAULT_COMMENT_LIMIT = 20
DEFAULT_REACTOR_LIMIT = 0
MAX_POST_LIMIT = 10
MAX_COMMENT_LIMIT = 100
MAX_REACTOR_LIMIT = 250
DEFAULT_DELAY_RANGE_SECONDS = (2.0, 5.0)


def _clamp_limit(value: int | None, *, default: int, maximum: int) -> int:
    """Normalize a caller-supplied limit into a safe bounded integer."""
    if value is None:
        value = default
    return max(0, min(int(value), maximum))


def _company_posts_url(company_name: str) -> str:
    """Build the company posts URL from a LinkedIn company slug."""
    company_slug = company_name.strip().strip("/")
    if not company_slug:
        raise ValueError("company_name must not be empty")
    if company_slug.startswith("http://") or company_slug.startswith("https://"):
        parsed = urlparse(company_slug)
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) >= 2 and path_parts[0] == "company":
            company_slug = path_parts[1]
        else:
            raise ValueError("company_name URL must be a LinkedIn /company/{slug}/ URL")
    return f"{LINKEDIN_ORIGIN}/company/{company_slug}/posts/"


def _absolute_linkedin_url(url: str) -> str:
    """Return an absolute LinkedIn URL for a relative LinkedIn path."""
    if url.startswith("https://www.linkedin.com/"):
        return url
    if url.startswith("/"):
        return f"{LINKEDIN_ORIGIN}{url}"
    return url


def _feed_post_urls(references: list[dict[str, Any]], *, limit: int) -> list[str]:
    """Extract deduplicated feed-post URLs from compact link references."""
    seen: set[str] = set()
    post_urls: list[str] = []
    for reference in references:
        if reference.get("kind") != "feed_post":
            continue
        raw_url = reference.get("url")
        if not isinstance(raw_url, str) or not raw_url:
            continue
        try:
            post_url = normalize_post_url(_absolute_linkedin_url(raw_url)).url
        except ValueError:
            logger.debug("Skipping invalid feed_post reference URL: %r", raw_url)
            continue
        if post_url in seen:
            continue
        seen.add(post_url)
        post_urls.append(post_url)
        if len(post_urls) >= limit:
            break
    return post_urls


def _diagnostic(stage: str, error: Exception, *, post_url: str | None = None) -> dict:
    """Build a compact diagnostic payload for partial workflow failures."""
    diagnostic: dict[str, str] = {
        "stage": stage,
        "error_type": type(error).__name__,
        "error_message": str(error),
    }
    if post_url:
        diagnostic["post_url"] = post_url
    return diagnostic


async def _pace(delay_range: tuple[float, float]) -> None:
    """Sleep for a jittered delay between LinkedIn interactions."""
    minimum, maximum = delay_range
    await _COMPANY_PACER.between_navigation(
        minimum,
        maximum,
        reason="company_engagement before next step",
    )


async def collect_company_engagement(
    extractor: Any,
    company_name: str,
    *,
    limit: int | None = DEFAULT_POST_LIMIT,
    include_comments: bool = True,
    include_reactors: bool = False,
    comment_limit: int | None = DEFAULT_COMMENT_LIMIT,
    reactor_limit: int | None = DEFAULT_REACTOR_LIMIT,
    reaction_type: str | None = None,
    delay_range: tuple[float, float] = DEFAULT_DELAY_RANGE_SECONDS,
) -> dict[str, Any]:
    """Collect bounded post engagement for a company's recent posts.

    The extractor is expected to provide:

    - ``extract_page(url, section_name="posts")`` for company post discovery
    - ``get_post_details(post_url)``
    - ``get_post_comments(post_url, limit=...)``
    - ``get_post_reactors(post_url, limit=..., reaction_type=...)``

    Failures on individual posts are captured in diagnostics and do not abort
    the whole workflow.
    """
    post_limit = _clamp_limit(limit, default=DEFAULT_POST_LIMIT, maximum=MAX_POST_LIMIT)
    capped_comment_limit = _clamp_limit(
        comment_limit,
        default=DEFAULT_COMMENT_LIMIT,
        maximum=MAX_COMMENT_LIMIT,
    )
    capped_reactor_limit = _clamp_limit(
        reactor_limit,
        default=DEFAULT_REACTOR_LIMIT,
        maximum=MAX_REACTOR_LIMIT,
    )
    company_posts_url = _company_posts_url(company_name)

    logger.info(
        "company_engagement: starting company=%s posts_limit=%d include_comments=%s "
        "comment_limit=%d include_reactors=%s reactor_limit=%d reaction_type=%s",
        company_name,
        post_limit,
        include_comments,
        capped_comment_limit,
        include_reactors,
        capped_reactor_limit,
        reaction_type,
    )

    result: dict[str, Any] = {
        "company_name": company_name,
        "company_posts_url": company_posts_url,
        "limits": {
            "posts": post_limit,
            "comments_per_post": capped_comment_limit,
            "reactors_per_post": capped_reactor_limit,
        },
        "posts": [],
        "diagnostics": [],
    }

    if post_limit == 0:
        logger.info("company_engagement: post limit is 0, returning empty result")
        return result

    try:
        extracted = await extractor.extract_page(
            company_posts_url, section_name="posts"
        )
    except Exception as error:
        logger.exception(
            "company_engagement: company post discovery failed for %s",
            company_name,
        )
        result["diagnostics"].append(_diagnostic("discover_company_posts", error))
        return result

    references = list(getattr(extracted, "references", []) or [])
    if getattr(extracted, "error", None):
        result["section_errors"] = {"posts": extracted.error}

    post_urls = _feed_post_urls(references, limit=post_limit)
    result["post_urls"] = post_urls
    logger.info(
        "company_engagement: discovered %d feed post URLs for %s",
        len(post_urls),
        company_name,
    )

    if not post_urls:
        result["diagnostics"].append(
            {
                "stage": "discover_company_posts",
                "error_type": "NoFeedPostReferences",
                "error_message": "No feed_post references were found on the company posts page.",
            }
        )
        return result

    for index, post_url in enumerate(post_urls, start=1):
        logger.info(
            "company_engagement: enriching post %d/%d %s",
            index,
            len(post_urls),
            post_url,
        )
        post_result: dict[str, Any] = {"post_url": post_url, "diagnostics": []}

        try:
            post_result["details"] = await extractor.get_post_details(post_url)
        except Exception as error:
            logger.exception("company_engagement: post details failed for %s", post_url)
            diagnostic = _diagnostic("get_post_details", error, post_url=post_url)
            post_result["diagnostics"].append(diagnostic)
            result["diagnostics"].append(diagnostic)

        if include_comments and capped_comment_limit > 0:
            await _pace(delay_range)
            try:
                post_result["comments"] = await extractor.get_post_comments(
                    post_url,
                    limit=capped_comment_limit,
                )
            except Exception as error:
                logger.exception(
                    "company_engagement: post comments failed for %s",
                    post_url,
                )
                diagnostic = _diagnostic("get_post_comments", error, post_url=post_url)
                post_result["diagnostics"].append(diagnostic)
                result["diagnostics"].append(diagnostic)

        if include_reactors and capped_reactor_limit > 0:
            await _pace(delay_range)
            try:
                post_result["reactors"] = await extractor.get_post_reactors(
                    post_url,
                    limit=capped_reactor_limit,
                    reaction_type=reaction_type,
                )
            except Exception as error:
                logger.exception(
                    "company_engagement: post reactors failed for %s",
                    post_url,
                )
                diagnostic = _diagnostic("get_post_reactors", error, post_url=post_url)
                post_result["diagnostics"].append(diagnostic)
                result["diagnostics"].append(diagnostic)
        elif include_reactors:
            logger.info(
                "company_engagement: reactors requested for %s but reactor_limit is 0",
                post_url,
            )

        result["posts"].append(post_result)
        if index < len(post_urls):
            await _pace(delay_range)

    logger.info(
        "company_engagement: completed company=%s enriched_posts=%d diagnostics=%d",
        company_name,
        len(result["posts"]),
        len(result["diagnostics"]),
    )
    return result
