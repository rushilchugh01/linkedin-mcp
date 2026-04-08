"""Post-level LinkedIn scraping helpers.

This module keeps DOM-dependent post/comment/reaction extraction isolated from
the generic ``LinkedInExtractor``.  Prefer URL navigation and broad structural
selectors over LinkedIn class names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

from linkedin_mcp_server.core.utils import detect_rate_limit, handle_modal_close
from linkedin_mcp_server.scraping.browser_pacing import BrowserPacer

logger = logging.getLogger(__name__)
_POST_PACER = BrowserPacer(logger_name=__name__)

_RATE_LIMITED_MSG = (
    "[Rate limited] LinkedIn blocked this section. "
    "Try again later or request fewer sections."
)
_LINKEDIN_HOSTS = {"linkedin.com", "www.linkedin.com"}
_POST_PATH_RE = re.compile(
    r"^/feed/update/(?P<urn>urn:li:(?:activity|share|ugcPost):(?P<id>\d+))/?$"
)
_POST_URN_RE = re.compile(r"urn:li:(?:activity|share|ugcPost):(?P<id>\d+)")
_COUNT_RE = re.compile(r"(?P<count>\d[\d,]*)")
_RELATIVE_TIME_RE = re.compile(r"(?P<value>\d+)\s*(?P<unit>[hdwm])\b", re.I)


@dataclass(frozen=True)
class PostUrl:
    """Normalized LinkedIn feed post URL metadata."""

    url: str
    path: str
    activity_urn: str
    activity_id: str


def normalize_post_url(value: str) -> PostUrl:
    """Normalize a LinkedIn feed post URL or activity URN.

    Accepts absolute LinkedIn URLs, relative ``/feed/update/...`` paths, and
    bare post URNs such as ``urn:li:activity:...``, ``urn:li:share:...``,
    and ``urn:li:ugcPost:...`` values.
    """
    raw = value.strip()
    if not raw:
        raise ValueError("post_url is required")

    if _POST_URN_RE.fullmatch(raw):
        raw = f"/feed/update/{raw}/"

    if raw.startswith("/"):
        parsed = urlparse(f"https://www.linkedin.com{raw}")
    else:
        parsed = urlparse(raw)

    host = parsed.netloc.lower()
    if host not in _LINKEDIN_HOSTS:
        raise ValueError("post_url must be a LinkedIn URL or feed update path")

    match = _POST_PATH_RE.match(parsed.path)
    if not match:
        raise ValueError(
            "post_url must point to /feed/update/urn:li:<activity|share|ugcPost>:<id>/"
        )

    path = f"/feed/update/{match.group('urn')}/"
    return PostUrl(
        url=urlunparse(("https", "www.linkedin.com", path, "", "", "")),
        path=path,
        activity_urn=match.group("urn"),
        activity_id=match.group("id"),
    )


def extract_activity_urn(value: str) -> str | None:
    """Extract a LinkedIn feed post URN from a string or URL."""
    match = _POST_URN_RE.search(value)
    return match.group(0) if match else None


def parse_count(value: str | None) -> int:
    """Parse a visible LinkedIn count string such as ``1,234 reactions``."""
    if not value:
        return 0
    match = _COUNT_RE.search(value)
    if not match:
        return 0
    return int(match.group("count").replace(",", ""))


def parse_engagement_counts(text: str) -> dict[str, int]:
    """Parse visible reaction/comment/repost counts from LinkedIn text."""
    reaction_match = re.search(r"(\d[\d,]*)\s*reactions?", text, re.I)
    others_match = re.search(r"and\s+(\d[\d,]*)\s+others?", text, re.I)
    comment_match = re.search(r"(\d[\d,]*)\s*comments?", text, re.I)
    repost_match = re.search(r"(\d[\d,]*)\s*reposts?", text, re.I)

    if reaction_match:
        reaction_count = parse_count(reaction_match.group(1))
    elif others_match:
        reaction_count = parse_count(others_match.group(1)) + 1
    else:
        reaction_count = 0

    return {
        "reaction_count": reaction_count,
        "comment_count": parse_count(comment_match.group(1) if comment_match else None),
        "repost_count": parse_count(repost_match.group(1) if repost_match else None),
    }


def _normalize_optional_limit(limit: int | None) -> int | None:
    """Normalize an optional item limit while preserving None as unbounded."""
    if limit is None:
        return None
    return max(0, int(limit))


def approximate_timestamp(relative_timestamp: str, observed_at: datetime) -> str | None:
    """Convert LinkedIn relative timestamps to a best-effort ISO timestamp."""
    match = _RELATIVE_TIME_RE.search(relative_timestamp)
    if not match:
        return None
    value = int(match.group("value"))
    unit = match.group("unit").lower()
    delta = {
        "h": timedelta(hours=value),
        "d": timedelta(days=value),
        "w": timedelta(weeks=value),
        "m": timedelta(days=value * 30),
    }[unit]
    return (observed_at - delta).isoformat().replace("+00:00", "Z")


def _base_result(post: PostUrl) -> dict[str, Any]:
    return {
        "url": post.url,
        "post_url": post.path,
        "activity_urn": post.activity_urn,
        "activity_id": post.activity_id,
    }


async def scrape_post_details(extractor: Any, post_url: str) -> dict[str, Any]:
    """Navigate to a post URL and return raw details plus parsed counts."""
    post = normalize_post_url(post_url)
    logger.info("Scraping post details: %s", post.url)

    extracted = await extractor.extract_page(
        post.url,
        section_name="post",
    )

    sections: dict[str, str] = {}
    references: dict[str, list[dict[str, Any]]] = {}
    section_errors: dict[str, dict[str, Any]] = {}
    engagement = {"reaction_count": 0, "comment_count": 0, "repost_count": 0}

    if extracted.text and extracted.text != _RATE_LIMITED_MSG:
        sections["post"] = extracted.text
        engagement = parse_engagement_counts(extracted.text)
        if extracted.references:
            references["post"] = extracted.references
    elif extracted.error:
        section_errors["post"] = extracted.error

    result = _base_result(post)
    result["sections"] = sections
    result["engagement"] = engagement
    if references:
        result["references"] = references
    if section_errors:
        result["section_errors"] = section_errors

    logger.info(
        "Post details scrape complete: activity_id=%s text=%s references=%d",
        post.activity_id,
        bool(sections),
        len(references.get("post", [])),
    )
    return result


async def _navigate_to_post(extractor: Any, post: PostUrl) -> None:
    logger.debug("Navigating to post URL: %s", post.url)
    await extractor._navigate_to_page(post.url)
    await detect_rate_limit(extractor._page)
    try:
        await extractor._page.wait_for_selector("main", timeout=5000)
    except Exception:
        logger.debug("No <main> element found on post page %s", post.url)
    await handle_modal_close(extractor._page)


async def scrape_post_comments(
    extractor: Any,
    post_url: str,
    *,
    limit: int | None = 20,
) -> dict[str, Any]:
    """Extract visible comments/commenters from one post URL."""
    post = normalize_post_url(post_url)
    limit = _normalize_optional_limit(limit)
    logger.info("Scraping post comments: %s (limit=%s)", post.url, limit)
    if limit == 0:
        logger.info("Post comments scrape skipped because limit is 0: %s", post.url)
        result = _base_result(post)
        result["comments"] = []
        result["comment_count"] = 0
        return result

    await _navigate_to_post(extractor, post)

    await _open_comments_if_needed(extractor._page)
    await _load_more_comments(extractor._page, limit=limit)

    observed_at = datetime.now(timezone.utc)
    comments = await _extract_comments_from_page(extractor._page, limit=limit)
    for comment in comments:
        comment["post_url"] = post.path
        comment["observed_at"] = observed_at.isoformat().replace("+00:00", "Z")
        relative = comment.get("relative_timestamp", "")
        comment["approx_timestamp"] = approximate_timestamp(relative, observed_at)

    result = _base_result(post)
    result["comments"] = comments
    result["comment_count"] = len(comments)
    logger.info(
        "Post comments scrape complete: activity_id=%s comments=%d",
        post.activity_id,
        len(comments),
    )
    return result


async def _open_comments_if_needed(page: Any) -> None:
    clicked = await page.evaluate(
        """() => {
            const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
            const candidates = Array.from(document.querySelectorAll('main button, main [role="button"]'));
            const target = candidates.find(el => /^Comment$/.test(normalize(el.innerText || el.textContent)));
            if (!target) return false;
            target.click();
            return true;
        }"""
    )
    logger.debug("Comment button click attempted: %s", clicked)
    if clicked:
        await _POST_PACER.after_click(reason="post comments open click")


async def _load_more_comments(page: Any, *, limit: int | None) -> None:
    max_clicks = 3 if limit is None else max(1, min(5, (limit // 10) + 1))
    for click_index in range(max_clicks):
        clicked = await page.evaluate(
            """() => {
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                const candidates = Array.from(document.querySelectorAll('main button, main [role="button"]'));
                const target = candidates.find(el => normalize(el.innerText || el.textContent) === 'Load more comments');
                if (!target) return false;
                target.click();
                return true;
            }"""
        )
        logger.debug(
            "Load more comments click %d/%d: %s",
            click_index + 1,
            max_clicks,
            clicked,
        )
        if not clicked:
            break
        await _POST_PACER.after_click(reason="post comments load more click")


async def _extract_comments_from_page(
    page: Any, *, limit: int | None
) -> list[dict[str, Any]]:
    raw_comments = await page.evaluate(
        """({ limit }) => {
            const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
            const profilePath = href => {
                if (!href) return '';
                const url = new URL(href, window.location.origin);
                const match = url.pathname.match(/^\\/in\\/([^/?#]+)/);
                return match ? `/in/${match[1]}/` : '';
            };
            const containers = Array.from(document.querySelectorAll('main article, main li, main div'))
                .filter(el => {
                    const text = normalize(el.innerText || el.textContent);
                    return text.length >= 10
                        && text.length <= 2500
                        && /\\bReply\\b/i.test(text)
                        && el.querySelector('a[href*="/in/"]');
                });
            const seen = new Set();
            const comments = [];
            for (const container of containers) {
                const anchor = container.querySelector('a[href*="/in/"]');
                const commenter_profile_url = profilePath(anchor?.href || anchor?.getAttribute('href'));
                if (!commenter_profile_url) continue;
                const text = normalize(container.innerText || container.textContent);
                const key = `${commenter_profile_url}|${text.slice(0, 120)}`;
                if (seen.has(key)) continue;
                seen.add(key);

                const pTags = Array.from(anchor.querySelectorAll('p')).map(p => normalize(p.innerText || p.textContent)).filter(Boolean);
                const commenter_name = pTags[0] || normalize(anchor.innerText || anchor.textContent).split('•')[0].trim() || 'Unknown';
                const commenter_headline = pTags[1] || '';
                const commentTextNode = Array.from(container.querySelectorAll('p, span, div'))
                    .map(node => normalize(node.innerText || node.textContent))
                    .filter(value => value.length > 20 && !/^(Like|Reply|Most relevant|Load more comments)$/i.test(value))
                    .sort((left, right) => right.length - left.length)[0] || text.slice(0, 500);
                const likeMatch = text.match(/Like\\s+(\\d[\\d,]*)/i);
                const replyMatch = text.match(/Reply\\s+(\\d[\\d,]*)/i);
                const timeMatch = text.match(/\\b\\d+\\s*[hdwm]\\b/i);
                comments.push({
                    commenter_profile_url,
                    commenter_name,
                    commenter_headline,
                    comment_text: commentTextNode,
                    relative_timestamp: timeMatch ? timeMatch[0] : '',
                    like_count: likeMatch ? Number(likeMatch[1].replace(/,/g, '')) : 0,
                    reply_count: replyMatch ? Number(replyMatch[1].replace(/,/g, '')) : 0,
                });
                if (limit && comments.length >= limit) break;
            }
            return comments;
        }""",
        {"limit": limit},
    )
    return raw_comments if isinstance(raw_comments, list) else []


async def scrape_post_reactors(
    extractor: Any,
    post_url: str,
    *,
    limit: int | None = 50,
    reaction_type: str | None = None,
) -> dict[str, Any]:
    """Extract visible reactors/likers from one post URL."""
    post = normalize_post_url(post_url)
    limit = _normalize_optional_limit(limit)
    logger.info(
        "Scraping post reactors: %s (limit=%s, reaction_type=%s)",
        post.url,
        limit,
        reaction_type,
    )
    if limit == 0:
        logger.info("Post reactors scrape skipped because limit is 0: %s", post.url)
        result = _base_result(post)
        result["reactors"] = []
        result["reactor_count"] = 0
        return result

    await _navigate_to_post(extractor, post)

    opened = await _open_reactions_dialog(extractor._page)
    reactors: list[dict[str, Any]] = []
    diagnostics: list[dict[str, str]] = []
    if opened:
        await _POST_PACER.after_click(reason="post reactions dialog open click")
        await _scroll_reactions_dialog(extractor._page, limit=limit)
        reactors = await _extract_reactors_from_dialog(
            extractor._page,
            limit=limit,
            reaction_type=reaction_type,
        )
        await _close_dialog(extractor._page)
    else:
        diagnostics.append(
            {
                "type": "reactions_dialog_unavailable",
                "message": "Could not open reactions dialog",
            }
        )

    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for reactor in reactors:
        reactor["post_url"] = post.path
        reactor["scraped_at"] = observed_at

    result = _base_result(post)
    result["reactors"] = reactors
    result["reactor_count"] = len(reactors)
    if diagnostics:
        result["diagnostics"] = diagnostics
    logger.info(
        "Post reactors scrape complete: activity_id=%s reactors=%d diagnostics=%d",
        post.activity_id,
        len(reactors),
        len(diagnostics),
    )
    return result


async def _open_reactions_dialog(page: Any) -> bool:
    opened = await page.evaluate(
        """() => {
            const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
            const candidates = Array.from(document.querySelectorAll('main a, main button, main [role="button"]'));
            const target = candidates.find(el => {
                const text = normalize(el.innerText || el.textContent);
                return /\\b\\d[\\d,]*\\s+reactions?\\b/i.test(text) || /\\band\\s+\\d[\\d,]*\\s+others?\\b/i.test(text);
            });
            if (!target) return false;
            target.click();
            return true;
        }"""
    )
    logger.debug("Reaction dialog open click attempted: %s", opened)
    if not opened:
        return False
    try:
        await page.wait_for_selector('dialog[open], [role="dialog"]', timeout=5000)
        return True
    except Exception:
        logger.debug("Reaction dialog did not appear after click")
        return False


async def _scroll_reactions_dialog(page: Any, *, limit: int | None) -> None:
    max_scrolls = 3 if limit is None else max(1, min(6, (limit // 20) + 1))
    for scroll_index in range(max_scrolls):
        scrolled = await _POST_PACER.scroll_largest_scrollable_in_dialog(
            page,
            reason=f"reaction dialog scroll {scroll_index + 1}/{max_scrolls}",
        )
        logger.debug(
            "Reaction dialog scroll %d/%d: %s",
            scroll_index + 1,
            max_scrolls,
            scrolled,
        )
        if not scrolled:
            break


async def _extract_reactors_from_dialog(
    page: Any,
    *,
    limit: int | None,
    reaction_type: str | None,
) -> list[dict[str, Any]]:
    raw_reactors = await page.evaluate(
        """({ limit, reactionType }) => {
            const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
            const profilePath = href => {
                if (!href) return '';
                const url = new URL(href, window.location.origin);
                const match = url.pathname.match(/^\\/in\\/([^/?#]+)/);
                return match ? `/in/${match[1]}/` : '';
            };
            const dialog = document.querySelector('dialog[open], [role="dialog"]');
            if (!dialog) return [];
            const links = Array.from(dialog.querySelectorAll('a[href*="/in/"]'));
            const seen = new Set();
            const reactors = [];
            for (const link of links) {
                const profile_url = profilePath(link.href || link.getAttribute('href'));
                if (!profile_url || seen.has(profile_url)) continue;
                seen.add(profile_url);
                const row = link.closest('li, article, div') || link;
                const pTags = Array.from(link.querySelectorAll('p')).map(p => normalize(p.innerText || p.textContent)).filter(Boolean);
                const text = normalize(row.innerText || row.textContent);
                reactors.push({
                    reactor_profile_url: profile_url,
                    reactor_name: pTags[0] || normalize(link.innerText || link.textContent).split('•')[0].trim() || 'Unknown',
                    reactor_headline: pTags[1] || '',
                    reaction_type: reactionType || '',
                    row_text: text.slice(0, 500),
                });
                if (limit && reactors.length >= limit) break;
            }
            return reactors;
        }""",
        {"limit": limit, "reactionType": reaction_type},
    )
    return raw_reactors if isinstance(raw_reactors, list) else []


async def _close_dialog(page: Any) -> None:
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_selector(
            'dialog[open], [role="dialog"]',
            state="hidden",
            timeout=3000,
        )
    except Exception:
        logger.debug("Could not confirm dialog close", exc_info=True)
