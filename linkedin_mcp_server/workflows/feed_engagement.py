"""Bounded home-feed post discovery and engagement enrichment workflow."""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

from linkedin_mcp_server.core.utils import detect_rate_limit, handle_modal_close
from linkedin_mcp_server.scraping.browser_pacing import BrowserPacer
from linkedin_mcp_server.scraping.post import (
    normalize_post_url,
    parse_engagement_counts,
)

logger = logging.getLogger(__name__)
_FEED_PACER = BrowserPacer(logger_name=__name__)

FEED_URL = "https://www.linkedin.com/feed/"
DEFAULT_SEARCH_POST_LIMIT = 10
DEFAULT_ENGAGEMENT_POST_LIMIT = 5
DEFAULT_SCROLLS = 10
DEFAULT_COMMENT_LIMIT = 20
DEFAULT_REACTOR_LIMIT = 0
MAX_FEED_POST_LIMIT = 100
MAX_FEED_SCROLLS = 30
MAX_COMMENT_LIMIT = 100
MAX_REACTOR_LIMIT = 250
DEFAULT_DELAY_RANGE_SECONDS = (2.0, 5.0)
_FEED_HOME_RE = re.compile(
    r"^https://(?:[a-z]{2,3}\.)?(?:www\.)?linkedin\.com/feed/?(?:[?#].*)?$"
)


class NoVisibleFeedItemsError(RuntimeError):
    """Raised for diagnostics when feed discovery sees no post containers."""


def _clamp_limit(value: int | None, *, default: int, maximum: int) -> int:
    if value is None:
        value = default
    return max(0, min(int(value), maximum))


def _normalize_keywords(keywords: list[str] | None) -> list[str]:
    if not keywords:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        if not isinstance(keyword, str):
            continue
        clean = " ".join(keyword.casefold().split())
        if not clean or clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)
    return normalized


def _matched_keywords(haystack: str, keywords: list[str]) -> list[str]:
    if not keywords:
        return []
    haystack_normalized = " ".join(haystack.casefold().split())
    matched: list[str] = []
    for keyword in keywords:
        escaped = re.escape(keyword)
        prefix = r"(?<![A-Za-z0-9])" if keyword[0].isalnum() else ""
        suffix = r"(?![A-Za-z0-9])" if keyword[-1].isalnum() else ""
        if re.search(f"{prefix}{escaped}{suffix}", haystack_normalized):
            matched.append(keyword)
    return matched


def _diagnostic(stage: str, error: Exception, *, post_url: str | None = None) -> dict:
    diagnostic: dict[str, str] = {
        "stage": stage,
        "error_type": type(error).__name__,
        "error_message": str(error),
    }
    if post_url:
        diagnostic["post_url"] = post_url
    return diagnostic


ProgressReporter = Callable[[int, int, str], Awaitable[None] | None]


async def _report_progress(
    reporter: ProgressReporter | None,
    *,
    progress: int,
    total: int = 100,
    message: str,
) -> None:
    if reporter is None:
        return
    outcome = reporter(progress, total, message)
    if outcome is not None:
        await outcome


def _summarize_raw_feed_items(
    raw_items: list[dict[str, Any]],
    *,
    keywords: list[str],
    max_posts: int,
    min_reactions: int,
    min_comments: int,
    include_promoted: bool,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        raw_url = raw_item.get("post_url")
        if not isinstance(raw_url, str) or not raw_url:
            continue
        try:
            post = normalize_post_url(raw_url)
        except ValueError:
            logger.debug("Skipping invalid feed post URL: %r", raw_url)
            continue
        if post.url in seen:
            continue

        raw_text = str(raw_item.get("raw_text") or "")
        post_text = str(raw_item.get("post_text") or raw_text)
        author_name = str(raw_item.get("author_name") or "")
        author_headline = str(raw_item.get("author_headline") or "")
        haystack = f"{post_text} {author_name} {author_headline} {raw_text}"
        matched = _matched_keywords(haystack, keywords)
        if keywords and not matched:
            continue

        engagement = parse_engagement_counts(raw_text)
        if engagement["reaction_count"] < min_reactions:
            continue
        if engagement["comment_count"] < min_comments:
            continue

        is_promoted = bool(raw_item.get("is_promoted"))
        if is_promoted and not include_promoted:
            continue

        seen.add(post.url)
        summaries.append(
            {
                "post_url": post.url,
                "activity_id": post.activity_id,
                "activity_urn": post.activity_urn,
                "author_name": author_name,
                "author_profile_url": raw_item.get("author_profile_url") or "",
                "author_headline": author_headline,
                "author_degree": raw_item.get("author_degree") or "",
                "author_timestamp": raw_item.get("author_timestamp") or "",
                "post_text": post_text,
                "reaction_count": engagement["reaction_count"],
                "comment_count": engagement["comment_count"],
                "repost_count": engagement["repost_count"],
                "reaction_types": list(raw_item.get("reaction_types") or []),
                "matched_keywords": matched,
                "is_promoted": is_promoted,
            }
        )
        if len(summaries) >= max_posts:
            break
    return summaries


async def _extract_visible_feed_items(page: Any) -> list[dict[str, Any]]:
    return await page.evaluate(
        """() => {
            const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
            const directText = element => element ? normalize(element.textContent || element.innerText || '') : '';
            const feed = document.querySelector('[data-testid="mainFeed"]') || document.querySelector('main') || document.body;
            const activityLinkSelector = [
                'a[href*="/feed/update/urn:li:"]',
                'a[href*="urn%3Ali%3Aactivity"]',
                'a[href*="urn%3Ali%3Ashare"]',
                'a[href*="urn%3Ali%3AugcPost"]',
                'a[href*="urn:li:activity"]',
                'a[href*="urn:li:share"]',
                'a[href*="urn:li:ugcPost"]',
                'a[href*="activity-"]',
            ].join(', ');
            const activityElementSelector = [
                'article',
                '[role="listitem"]',
                '[data-urn*="urn:li:activity"], [data-urn*="urn:li:share"], [data-urn*="urn:li:ugcPost"]',
                '[data-id*="urn:li:activity"], [data-id*="urn:li:share"], [data-id*="urn:li:ugcPost"]',
                '[data-activity-urn*="urn:li:activity"], [data-activity-urn*="urn:li:share"], [data-activity-urn*="urn:li:ugcPost"]',
                '[componentkey*="urn:li:activity"], [componentkey*="urn:li:share"], [componentkey*="urn:li:ugcPost"]',
            ].join(', ');
            const candidateSet = new Set(Array.from(feed.querySelectorAll(activityElementSelector)));
            for (const link of Array.from(feed.querySelectorAll(activityLinkSelector))) {
                let item = link.closest(activityElementSelector);
                let parent = link.parentElement;
                let depth = 0;
                while (!item && parent && depth < 8) {
                    const textLength = normalize(parent.innerText || parent.textContent || '').length;
                    if (textLength > 50) item = parent;
                    parent = parent.parentElement;
                    depth += 1;
                }
                candidateSet.add(item || link);
            }
            const candidates = Array.from(candidateSet);
            const reactionMap = {
                'like-consumption-ring-small': 'Like',
                'praise-consumption-ring-small': 'Celebrate',
                'empathy-consumption-ring-small': 'Love',
                'interest-consumption-ring-small': 'Insightful',
                'support-consumption-ring-small': 'Support',
                'entertainment-consumption-ring-small': 'Funny',
            };
            const postUrnFromValue = value => {
                if (!value) return '';
                const values = [String(value)];
                try {
                    values.push(decodeURIComponent(String(value)));
                } catch {
                    // Keep the raw value when LinkedIn stores a partial encoded URL.
                }
                for (const candidate of values) {
                    const urnMatch = candidate.match(/urn:li:(activity|share|ugcPost):(\\d+)/i);
                    if (urnMatch) return `urn:li:${urnMatch[1]}:${urnMatch[2]}`;
                    const activityUrlMatch = candidate.match(/activity-(\\d{10,})/i);
                    if (activityUrlMatch) return `urn:li:activity:${activityUrlMatch[1]}`;
                }
                return '';
            };
            const activityFromElement = item => {
                const attrs = ['href', 'componentkey', 'data-urn', 'data-id', 'data-activity-urn'];
                const elements = [
                    item,
                    ...Array.from(item.querySelectorAll(
                        `${activityLinkSelector}, [componentkey*="urn:li:activity"], ` +
                        '[componentkey*="urn:li:share"], [componentkey*="urn:li:ugcPost"], ' +
                        '[data-urn*="urn:li:activity"], [data-urn*="urn:li:share"], [data-urn*="urn:li:ugcPost"], ' +
                        '[data-id*="urn:li:activity"], [data-id*="urn:li:share"], [data-id*="urn:li:ugcPost"], ' +
                        '[data-activity-urn*="urn:li:activity"], [data-activity-urn*="urn:li:share"], ' +
                        '[data-activity-urn*="urn:li:ugcPost"]'
                    )),
                ];
                for (const element of elements) {
                    for (const attr of attrs) {
                        const value = attr === 'href' ? element.href : element.getAttribute?.(attr);
                        const postUrn = postUrnFromValue(value);
                        if (postUrn) return postUrn;
                    }
                }
                return '';
            };
            const cleanName = value => normalize(value).replace(/\\s*•\\s*(1st|2nd|3rd\\+?).*$/i, '').trim();
            const rows = [];
            for (const item of candidates) {
                const postLink = item.matches?.(activityLinkSelector)
                    ? item
                    : Array.from(item.querySelectorAll(activityLinkSelector))[0];
                const activityUrn = activityFromElement(item);
                const activityId = (activityUrn.match(/:(\\d+)$/) || [])[1] || '';
                const postUrl = activityUrn
                    ? `https://www.linkedin.com/feed/update/${activityUrn}/`
                    : (postLink?.href || '');
                if (!postUrl) continue;
                const rawText = normalize(item.innerText || item.textContent || '');
                if (rawText.length < 20) continue;

                const authorLink = Array.from(item.querySelectorAll('a[href*="/in/"], a[href*="/company/"]'))
                    .find(link => !link.href.includes('/feed/update/') && link.querySelectorAll('p').length >= 1)
                    || Array.from(item.querySelectorAll('a[href*="/in/"], a[href*="/company/"]'))
                        .find(link => !link.href.includes('/feed/update/'));
                const pTags = authorLink ? Array.from(authorLink.querySelectorAll('p')) : [];
                const nameP = pTags[0];
                const visibleName = nameP?.querySelector('span[aria-hidden="true"]');
                const rawName = directText(visibleName || nameP || authorLink);
                const degreeMatch = rawName.match(/•\\s*(1st|2nd|3rd\\+?)/i);
                const authorHeadline = pTags.length >= 3
                    ? directText(pTags[2])
                    : (pTags.length >= 2 ? directText(pTags[1]) : '');
                const authorTimestamp = pTags.length >= 4 ? directText(pTags[3]).replace('•', '').trim() : '';
                const textBox = item.querySelector(
                    '[data-testid="expandable-text-box"], .feed-shared-update-v2__description, .update-components-text'
                );
                let postText = normalize(textBox ? (textBox.innerText || textBox.textContent || '') : '');
                if (!postText) {
                    const paragraphs = Array.from(item.querySelectorAll('p'))
                        .filter(p => directText(p).length > 50 && !p.closest('a[href*="/in/"], a[href*="/company/"]'))
                        .sort((a, b) => directText(b).length - directText(a).length);
                    postText = paragraphs[0] ? directText(paragraphs[0]) : '';
                }
                const reactionSpan = Array.from(item.querySelectorAll('span'))
                    .find(span => /^\\d[\\d,]*\\s*reactions?$/i.test(directText(span)));
                const reactionsLink = reactionSpan ? reactionSpan.closest('a') : null;
                const reactionTypes = reactionsLink
                    ? Array.from(reactionsLink.querySelectorAll('svg[id]'))
                        .map(svg => reactionMap[svg.id] || svg.id)
                        .filter(Boolean)
                    : [];

                rows.push({
                    post_url: postUrl,
                    activity_id: activityId,
                    activity_urn: activityUrn,
                    raw_text: rawText,
                    post_text: postText || rawText.slice(0, 5000),
                    author_name: cleanName(rawName),
                    author_headline: authorHeadline,
                    author_degree: degreeMatch ? degreeMatch[1] : '',
                    author_timestamp: authorTimestamp,
                    author_profile_url: authorLink ? authorLink.href.split('?')[0] : '',
                    reaction_types: reactionTypes,
                    is_promoted: /\\bPromoted\\b/i.test(rawText),
                });
            }
            return rows;
        }"""
    )


def _is_feed_home_url(url: str | None) -> bool:
    return bool(url and _FEED_HOME_RE.match(url))


async def _ensure_feed_page(extractor: Any) -> None:
    current_url = str(getattr(extractor._page, "url", "") or "")
    if _is_feed_home_url(current_url):
        logger.debug("search_feed_posts: using existing feed page url=%s", current_url)
        return
    await extractor._navigate_to_page(FEED_URL)


async def _wait_for_feed_hydration(page: Any) -> None:
    wait_for_function = getattr(page, "wait_for_function", None)
    if not callable(wait_for_function):
        return
    try:
        await wait_for_function(
            """() => {
                const body = document.body?.innerText || '';
                const html = document.body?.innerHTML || '';
                const hasActivityElement = !!document.querySelector(
                    [
                        'a[href*="/feed/update/urn:li:activity:"]',
                        'a[href*="/feed/update/urn:li:share:"]',
                        'a[href*="/feed/update/urn:li:ugcPost:"]',
                        'a[href*="urn%3Ali%3Aactivity"]',
                        'a[href*="urn%3Ali%3Ashare"]',
                        'a[href*="urn%3Ali%3AugcPost"]',
                        'a[href*="activity-"]',
                        '[data-urn*="urn:li:activity"], [data-urn*="urn:li:share"], [data-urn*="urn:li:ugcPost"]',
                        '[data-id*="urn:li:activity"], [data-id*="urn:li:share"], [data-id*="urn:li:ugcPost"]',
                        '[data-activity-urn*="urn:li:activity"], [data-activity-urn*="urn:li:share"], [data-activity-urn*="urn:li:ugcPost"]',
                        '[componentkey*="urn:li:activity"], [componentkey*="urn:li:share"], [componentkey*="urn:li:ugcPost"]',
                    ].join(', ')
                );
                const hasActivityUrn = /urn(?::|%3A)li(?::|%3A)(activity|share|ugcPost)(?::|%3A)\\d+/i.test(html);
                const hasActivityUrl = /activity-\\d{10,}/i.test(html);
                const terminalEmptyState = /\\bNo posts\\b/i.test(body);
                const terminalErrorState = /\\bSomething went wrong\\b/i.test(body);
                return hasActivityElement || hasActivityUrn || hasActivityUrl || terminalEmptyState || terminalErrorState;
            }""",
            timeout=15000,
        )
    except Exception as error:
        logger.debug("Feed hydration wait completed without visible posts: %s", error)


async def _feed_snapshot(page: Any) -> dict[str, Any]:
    try:
        snapshot = await page.evaluate(
            """() => {
                const html = document.body?.innerHTML || '';
                const text = document.body?.innerText || '';
                const mainFeed = document.querySelector('[data-testid="mainFeed"]');
                const feedRoot = mainFeed || document.querySelector('main') || document.body;
                const activityPattern = /urn(?::|%3A)li(?::|%3A)(activity|share|ugcPost)(?::|%3A)\\d+/gi;
                const linkBuckets = {};
                const bump = key => { linkBuckets[key] = (linkBuckets[key] || 0) + 1; };
                for (const link of Array.from(feedRoot.querySelectorAll('a[href]'))) {
                    let url;
                    try {
                        url = new URL(link.href);
                    } catch {
                        bump('invalid');
                        continue;
                    }
                    if (!/linkedin\\.com$/i.test(url.hostname)) {
                        bump('external');
                    } else if (url.pathname.startsWith('/feed/update/')) {
                        bump('feed_update');
                    } else if (/activity-\\d{10,}/i.test(url.href)) {
                        bump('activity_url');
                    } else if (url.pathname.startsWith('/posts/')) {
                        bump('posts');
                    } else if (url.pathname.startsWith('/in/')) {
                        bump('profile');
                    } else if (url.pathname.startsWith('/company/')) {
                        bump('company');
                    } else if (url.pathname.startsWith('/jobs/')) {
                        bump('jobs');
                    } else {
                        bump('other_linkedin');
                    }
                }
                const attributeNames = new Set();
                const dataValueMarkers = {
                    activity: 0,
                    urn: 0,
                    numeric_long: 0,
                };
                for (const item of Array.from(feedRoot.querySelectorAll('[role="listitem"]')).slice(0, 20)) {
                    for (const attr of Array.from(item.attributes || [])) {
                        attributeNames.add(attr.name);
                        if (!attr.name.startsWith('data-')) continue;
                        if (/activity/i.test(attr.value)) dataValueMarkers.activity += 1;
                        if (/urn/i.test(attr.value)) dataValueMarkers.urn += 1;
                        if (/\\d{10,}/.test(attr.value)) dataValueMarkers.numeric_long += 1;
                    }
                }
                const buttonBuckets = {};
                const bumpButton = key => { buttonBuckets[key] = (buttonBuckets[key] || 0) + 1; };
                for (const button of Array.from(feedRoot.querySelectorAll('button, [role="button"]'))) {
                    const label = `${button.getAttribute('aria-label') || ''} ${button.textContent || ''}`;
                    if (/comment/i.test(label)) bumpButton('comment');
                    else if (/repost/i.test(label)) bumpButton('repost');
                    else if (/react|like|celebrate|support|love|insightful|funny/i.test(label)) bumpButton('react');
                    else if (/send|share/i.test(label)) bumpButton('send_share');
                    else if (/follow/i.test(label)) bumpButton('follow');
                    else if (/more|menu/i.test(label)) bumpButton('menu');
                    else bumpButton('other');
                }
                return {
                    url: location.href,
                    title: document.title,
                    body_length: text.length,
                    main_present: !!document.querySelector('main'),
                    main_feed_present: !!mainFeed,
                    feed_root_link_count: feedRoot.querySelectorAll('a[href]').length,
                    feed_root_link_buckets: linkBuckets,
                    feed_root_button_buckets: buttonBuckets,
                    listitem_attribute_names: Array.from(attributeNames).sort(),
                    listitem_data_value_markers: dataValueMarkers,
                    contains_reactions_text: /\\b(reactions?|others?)\\b/i.test(text),
                    contains_comments_text: /\\bcomments?\\b/i.test(text),
                    contains_loading_text: /\\bloading\\b/i.test(text),
                    contains_no_posts_text: /\\bNo posts\\b/i.test(text),
                    activity_link_count: document.querySelectorAll(
                        'a[href*="/feed/update/urn:li:"], ' +
                        'a[href*="urn%3Ali%3Aactivity"], a[href*="urn%3Ali%3Ashare"], ' +
                        'a[href*="urn%3Ali%3AugcPost"], a[href*="urn:li:activity"], ' +
                        'a[href*="urn:li:share"], a[href*="urn:li:ugcPost"], a[href*="activity-"]'
                    ).length,
                    activity_element_count: document.querySelectorAll(
                        '[data-urn*="urn:li:activity"], [data-urn*="urn:li:share"], [data-urn*="urn:li:ugcPost"], ' +
                        '[data-id*="urn:li:activity"], [data-id*="urn:li:share"], [data-id*="urn:li:ugcPost"], ' +
                        '[data-activity-urn*="urn:li:activity"], [data-activity-urn*="urn:li:share"], ' +
                        '[data-activity-urn*="urn:li:ugcPost"], [componentkey*="urn:li:activity"], ' +
                        '[componentkey*="urn:li:share"], [componentkey*="urn:li:ugcPost"]'
                    ).length,
                    activity_urn_count: (html.match(activityPattern) || []).length,
                    activity_url_count: (html.match(/activity-\\d{10,}/gi) || []).length,
                    article_count: document.querySelectorAll('article').length,
                    listitem_count: document.querySelectorAll('[role="listitem"]').length,
                };
            }"""
        )
    except Exception as error:
        logger.debug("Feed snapshot failed: %s", error)
        return {}
    return snapshot if isinstance(snapshot, dict) else {}


async def _no_visible_feed_items_diagnostic(page: Any) -> dict[str, Any]:
    diagnostic = _diagnostic(
        "discover_feed_posts",
        NoVisibleFeedItemsError(
            "No visible feed post items found after scrolling the home feed"
        ),
    )
    snapshot = await _feed_snapshot(page)
    if snapshot:
        diagnostic["snapshot"] = snapshot
    return diagnostic


async def _prepare_feed_page(extractor: Any) -> None:
    await _ensure_feed_page(extractor)
    await detect_rate_limit(extractor._page)
    try:
        await extractor._page.wait_for_selector("main", timeout=5000)
    except Exception:
        logger.debug("No <main> element found on feed")
    await handle_modal_close(extractor._page)
    await _wait_for_feed_hydration(extractor._page)


async def _preload_feed_posts(
    extractor: Any,
    *,
    scrolls: int,
    progress: ProgressReporter | None = None,
) -> None:
    if scrolls <= 0:
        return

    await _prepare_feed_page(extractor)
    for index in range(scrolls):
        progress_value = 5 + int(((index + 1) / max(1, scrolls)) * 35)
        await _report_progress(
            progress,
            progress=progress_value,
            message=f"Preloading feed scroll {index + 1}/{scrolls}",
        )
        await _FEED_PACER.scroll_page(
            extractor._page,
            reason=f"feed preload scroll {index + 1}/{scrolls}",
        )


async def search_feed_posts(
    extractor: Any,
    *,
    keywords: list[str] | None = None,
    max_posts: int | None = DEFAULT_SEARCH_POST_LIMIT,
    scrolls: int | None = DEFAULT_SCROLLS,
    min_reactions: int = 0,
    min_comments: int = 0,
    include_promoted: bool = False,
) -> dict[str, Any]:
    """Discover matching visible posts from the authenticated LinkedIn home feed."""
    post_limit = _clamp_limit(
        max_posts, default=DEFAULT_SEARCH_POST_LIMIT, maximum=MAX_FEED_POST_LIMIT
    )
    scroll_limit = _clamp_limit(
        scrolls, default=DEFAULT_SCROLLS, maximum=MAX_FEED_SCROLLS
    )
    normalized_keywords = _normalize_keywords(keywords)
    diagnostics: list[dict[str, Any]] = []
    raw_items_by_url: dict[str, dict[str, Any]] = {}

    logger.info(
        "search_feed_posts: starting max_posts=%d scrolls=%d keywords=%s "
        "min_reactions=%d min_comments=%d include_promoted=%s",
        post_limit,
        scroll_limit,
        normalized_keywords,
        min_reactions,
        min_comments,
        include_promoted,
    )

    if post_limit == 0:
        return {
            "feed_url": FEED_URL,
            "limits": {"posts": post_limit, "scrolls": scroll_limit},
            "keywords": normalized_keywords,
            "posts": [],
            "diagnostics": diagnostics,
        }

    summaries: list[dict[str, Any]] = []
    try:
        await _prepare_feed_page(extractor)

        for index in range(scroll_limit + 1):
            try:
                visible_items = await _extract_visible_feed_items(extractor._page)
            except Exception as error:
                logger.exception("search_feed_posts: visible feed extraction failed")
                diagnostics.append(_diagnostic("extract_visible_feed_items", error))
                visible_items = []

            for item in visible_items:
                raw_url = item.get("post_url")
                if isinstance(raw_url, str) and raw_url not in raw_items_by_url:
                    raw_items_by_url[raw_url] = item

            summaries = _summarize_raw_feed_items(
                list(raw_items_by_url.values()),
                keywords=normalized_keywords,
                max_posts=post_limit,
                min_reactions=max(0, int(min_reactions)),
                min_comments=max(0, int(min_comments)),
                include_promoted=include_promoted,
            )
            if len(summaries) >= post_limit or index >= scroll_limit:
                logger.info(
                    "search_feed_posts: stopping after scroll_index=%d posts=%d raw_items=%d",
                    index,
                    len(summaries),
                    len(raw_items_by_url),
                )
                break
            await _FEED_PACER.scroll_page(
                extractor._page,
                reason=f"feed search scroll {index + 1}/{scroll_limit}",
            )

        if not raw_items_by_url:
            diagnostics.append(
                await _no_visible_feed_items_diagnostic(extractor._page)
            )
    except Exception as error:
        logger.exception("search_feed_posts: feed discovery failed")
        diagnostics.append(_diagnostic("discover_feed_posts", error))

    result = {
        "feed_url": FEED_URL,
        "limits": {"posts": post_limit, "scrolls": scroll_limit},
        "keywords": normalized_keywords,
        "posts": summaries,
        "diagnostics": diagnostics,
    }
    logger.info(
        "search_feed_posts: completed posts=%d diagnostics=%d",
        len(result["posts"]),
        len(diagnostics),
    )
    return result


async def collect_feed_engagement(
    extractor: Any,
    *,
    keywords: list[str] | None = None,
    max_posts: int | None = DEFAULT_ENGAGEMENT_POST_LIMIT,
    scrolls: int | None = DEFAULT_SCROLLS,
    include_comments: bool = True,
    include_reactors: bool = False,
    comment_limit: int | None = DEFAULT_COMMENT_LIMIT,
    reactor_limit: int | None = DEFAULT_REACTOR_LIMIT,
    reaction_type: str | None = None,
    min_reactions: int = 0,
    min_comments: int = 0,
    include_promoted: bool = False,
    delay_range: tuple[float, float] = DEFAULT_DELAY_RANGE_SECONDS,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    """Discover matching feed posts and enrich them with engagement data."""
    post_limit = _clamp_limit(
        max_posts, default=DEFAULT_ENGAGEMENT_POST_LIMIT, maximum=MAX_FEED_POST_LIMIT
    )
    scroll_limit = _clamp_limit(
        scrolls, default=DEFAULT_SCROLLS, maximum=MAX_FEED_SCROLLS
    )
    capped_comment_limit = _clamp_limit(
        comment_limit, default=DEFAULT_COMMENT_LIMIT, maximum=MAX_COMMENT_LIMIT
    )
    capped_reactor_limit = _clamp_limit(
        reactor_limit, default=DEFAULT_REACTOR_LIMIT, maximum=MAX_REACTOR_LIMIT
    )

    await _report_progress(
        progress,
        progress=1,
        message=(
            f"Preloading feed with {scroll_limit} scrolls"
            if scroll_limit > 0
            else "Preparing feed"
        ),
    )
    await _preload_feed_posts(
        extractor,
        scrolls=scroll_limit,
        progress=progress,
    )
    await _report_progress(
        progress,
        progress=45,
        message="Extracting posts from loaded feed DOM",
    )
    discovery = await search_feed_posts(
        extractor,
        keywords=keywords,
        max_posts=post_limit,
        scrolls=0,
        min_reactions=min_reactions,
        min_comments=min_comments,
        include_promoted=include_promoted,
    )
    result: dict[str, Any] = {
        "feed_url": FEED_URL,
        "limits": {
            "posts": post_limit,
            "scrolls": scroll_limit,
            "comments_per_post": capped_comment_limit,
            "reactors_per_post": capped_reactor_limit,
        },
        "keywords": discovery.get("keywords", []),
        "discovered_posts": discovery.get("posts", []),
        "posts": [],
        "diagnostics": list(discovery.get("diagnostics", [])),
    }

    discovered_posts = list(discovery.get("posts", []))
    logger.info(
        "feed_engagement: enriching discovered_posts=%d include_comments=%s "
        "comment_limit=%d include_reactors=%s reactor_limit=%d reaction_type=%s",
        len(discovered_posts),
        include_comments,
        capped_comment_limit,
        include_reactors,
        capped_reactor_limit,
        reaction_type,
    )

    for index, summary in enumerate(discovered_posts, start=1):
        post_url = summary["post_url"]
        post_result: dict[str, Any] = {
            "post_url": post_url,
            "summary": summary,
            "diagnostics": [],
        }
        logger.info(
            "feed_engagement: enriching post %d/%d %s",
            index,
            len(discovered_posts),
            post_url,
        )
        await _report_progress(
            progress,
            progress=50 + int((index - 1) * 45 / max(1, len(discovered_posts))),
            message=f"Enriching post {index}/{len(discovered_posts)}",
        )

        try:
            post_result["details"] = await extractor.get_post_details(post_url)
        except Exception as error:
            logger.exception("feed_engagement: post details failed for %s", post_url)
            diagnostic = _diagnostic("get_post_details", error, post_url=post_url)
            post_result["diagnostics"].append(diagnostic)
            result["diagnostics"].append(diagnostic)

        if include_comments and capped_comment_limit > 0:
            minimum, maximum = delay_range
            await _FEED_PACER.between_navigation(
                minimum, maximum, reason="feed_engagement before comments"
            )
            try:
                post_result["comments"] = await extractor.get_post_comments(
                    post_url, limit=capped_comment_limit
                )
            except Exception as error:
                logger.exception(
                    "feed_engagement: post comments failed for %s", post_url
                )
                diagnostic = _diagnostic("get_post_comments", error, post_url=post_url)
                post_result["diagnostics"].append(diagnostic)
                result["diagnostics"].append(diagnostic)

        if include_reactors and capped_reactor_limit > 0:
            minimum, maximum = delay_range
            await _FEED_PACER.between_navigation(
                minimum, maximum, reason="feed_engagement before reactors"
            )
            try:
                post_result["reactors"] = await extractor.get_post_reactors(
                    post_url,
                    limit=capped_reactor_limit,
                    reaction_type=reaction_type,
                )
            except Exception as error:
                logger.exception(
                    "feed_engagement: post reactors failed for %s", post_url
                )
                diagnostic = _diagnostic("get_post_reactors", error, post_url=post_url)
                post_result["diagnostics"].append(diagnostic)
                result["diagnostics"].append(diagnostic)

        result["posts"].append(post_result)
        if index < len(discovered_posts):
            minimum, maximum = delay_range
            await _FEED_PACER.between_navigation(
                minimum, maximum, reason="feed_engagement before next post"
            )

    logger.info(
        "feed_engagement: completed posts=%d diagnostics=%d",
        len(result["posts"]),
        len(result["diagnostics"]),
    )
    await _report_progress(progress, progress=100, message="Complete")
    return result
