"""Scrape recent connections from the LinkedIn connections page.

Navigate to ``/mynetwork/invite-connect/connections/``, scroll until
the visible connection dates exceed a caller-defined cutoff, then
return structured connection data.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

from linkedin_mcp_server.core.utils import detect_rate_limit, handle_modal_close
from linkedin_mcp_server.scraping.browser_pacing import BrowserPacer

logger = logging.getLogger(__name__)

CONNECTIONS_URL = "https://www.linkedin.com/mynetwork/invite-connect/connections/"
_PACER = BrowserPacer(logger_name=__name__)

# Date parsing: absolute format "Connected on April 8, 2026"
_ABSOLUTE_DATE_RE = re.compile(
    r"Connected\s+on\s+(\w+)\s+(\d{1,2}),?\s+(\d{4})", re.IGNORECASE
)

# Date parsing: relative formats
_RELATIVE_RE = re.compile(
    r"Connected\s+(\d+)\s+(day|week|month|year)s?\s+ago", re.IGNORECASE
)

_MONTH_MAP = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

MAX_SCROLLS = 50
MAX_DAYS = 365


def _clamp(value: int | None, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    return max(minimum, min(int(value), maximum))


def parse_connection_date(text: str, *, today: date | None = None) -> date | None:
    """Parse a LinkedIn connection date string to a Python date.

    Handles both absolute (``Connected on April 8, 2026``) and relative
    (``Connected 2 days ago``) formats.  Returns ``None`` when the text
    is unparseable.
    """
    today = today or date.today()

    # Try absolute format first
    match = _ABSOLUTE_DATE_RE.search(text)
    if match:
        month_name = match.group(1).lower()
        day_num = int(match.group(2))
        year = int(match.group(3))
        month_num = _MONTH_MAP.get(month_name)
        if month_num:
            try:
                return date(year, month_num, day_num)
            except ValueError:
                logger.debug("Invalid absolute date components: %s", text)
                return None

    # Try relative format
    match = _RELATIVE_RE.search(text)
    if match:
        quantity = int(match.group(1))
        unit = match.group(2).lower()
        if unit == "day":
            return today - timedelta(days=quantity)
        if unit == "week":
            return today - timedelta(weeks=quantity)
        if unit == "month":
            return today - timedelta(days=quantity * 30)
        if unit == "year":
            return today - timedelta(days=quantity * 365)

    return None


# --------------------------------------------------------------------------
# In-page JS extraction
# --------------------------------------------------------------------------

_EXTRACT_CONNECTIONS_JS = """() => {
    const normalize = v => (v || '').replace(/\\s+/g, ' ').trim();

    // Find all connection card containers. Each card has an <a href="/in/...">
    // for the name (not the avatar) plus a <p> with "Connected on ..." text.
    const profileLinks = Array.from(
        document.querySelectorAll('a[href*="/in/"]')
    );

    const seen = new Set();
    const results = [];

    for (const link of profileLinks) {
        const href = (link.getAttribute('href') || '').split('?')[0];
        if (!href || seen.has(href)) continue;

        // Skip avatar-only links (those that contain a <figure> but no <p>)
        const pTags = link.querySelectorAll('p');
        if (pTags.length === 0) continue;

        // Extract name from first <p>
        const nameEl = pTags[0];
        const name = normalize(nameEl?.textContent || '');
        if (!name) continue;

        // Extract headline from second <p> (wrapped in a div)
        const headlineEl = pTags.length >= 2 ? pTags[1] : null;
        const headline = normalize(headlineEl?.textContent || '');

        // Extract username from href
        const usernameMatch = href.match(/\\/in\\/([^/]+)/);
        const username = usernameMatch ? usernameMatch[1] : '';

        // Find "Connected on ..." text - it's a <p> sibling outside the link
        // Walk up to the link's parent container and find the date <p>
        let dateText = '';
        let container = link.parentElement;
        // Walk up a few levels to find the card container
        for (let i = 0; i < 3 && container; i++) {
            const dateParagraphs = container.querySelectorAll('p');
            for (const p of dateParagraphs) {
                const txt = normalize(p.textContent || '');
                if (/^Connected\\s+(on|\\d)/i.test(txt)) {
                    dateText = txt;
                    break;
                }
            }
            if (dateText) break;
            container = container.parentElement;
        }

        // Extract profile URN from the Message link in the same card
        let profileUrn = '';
        const cardContainer = link.closest('[componentkey^="auto-component-"]') || container;
        if (cardContainer) {
            const messageLink = cardContainer.querySelector('a[href*="profileUrn="]');
            if (messageLink) {
                const msgHref = messageLink.getAttribute('href') || '';
                const urnMatch = msgHref.match(/profileUrn=([^&]+)/);
                if (urnMatch) {
                    try {
                        profileUrn = decodeURIComponent(urnMatch[1]);
                    } catch {
                        profileUrn = urnMatch[1];
                    }
                }
            }
        }

        seen.add(href);
        results.push({
            name: name,
            username: username,
            profile_url: href.endsWith('/') ? href : href + '/',
            headline: headline,
            connected_date_raw: dateText,
            profile_urn: profileUrn,
        });
    }

    return results;
}"""


async def _extract_visible_connections(page: Any) -> list[dict[str, Any]]:
    """Run JS extraction on the current page and return raw connection dicts."""
    try:
        return await page.evaluate(_EXTRACT_CONNECTIONS_JS)
    except Exception as error:
        logger.exception("Connection extraction JS failed: %s", error)
        return []


async def _click_load_more(page: Any) -> bool:
    """Click the 'Load more' button if present. Returns True if clicked."""
    try:
        buttons = page.locator("button").filter(has_text=re.compile(r"^Load more$", re.IGNORECASE))
        count = await buttons.count()
        if count > 0:
            await buttons.first.click()
            logger.debug("Clicked 'Load more' button")
            return True
    except Exception as error:
        logger.debug("Load more click failed (non-fatal): %s", error)
    return False


# --------------------------------------------------------------------------
# Main orchestrator
# --------------------------------------------------------------------------


async def scrape_recent_connections(
    extractor: Any,
    *,
    days: int = 10,
    max_scrolls: int = 30,
) -> dict[str, Any]:
    """Scrape recent connections from the LinkedIn connections page.

    Args:
        extractor: An authenticated LinkedInExtractor instance.
        days: How many days back to include (default: 10, max: 365).
        max_scrolls: Safety limit on scroll iterations (default: 30, max: 50).

    Returns:
        Dict with url, connections list, total_found, cutoff_date, and diagnostics.
    """
    days = _clamp(days, default=10, minimum=1, maximum=MAX_DAYS)
    max_scrolls = _clamp(max_scrolls, default=30, minimum=1, maximum=MAX_SCROLLS)

    today = date.today()
    cutoff_date = today - timedelta(days=days)
    diagnostics: list[dict[str, Any]] = []

    logger.info(
        "scrape_recent_connections: days=%d cutoff=%s max_scrolls=%d",
        days,
        cutoff_date.isoformat(),
        max_scrolls,
    )

    # Navigate to connections page
    await extractor._navigate_to_page(CONNECTIONS_URL)
    await detect_rate_limit(extractor._page)
    await handle_modal_close(extractor._page)

    # Wait for the connection list to render
    try:
        await extractor._page.wait_for_selector(
            'a[href*="/in/"]', timeout=10000
        )
    except Exception:
        logger.debug("No connection links found within timeout")
        diagnostics.append({
            "stage": "wait_for_connections",
            "error_type": "Timeout",
            "error_message": "No connection links found on the page within 10s",
        })

    # Scroll loop: extract after each scroll, check dates
    all_connections: dict[str, dict[str, Any]] = {}
    past_cutoff = False

    for scroll_index in range(max_scrolls + 1):
        # Extract currently visible connections
        visible = await _extract_visible_connections(extractor._page)
        new_count = 0

        for conn in visible:
            url = conn.get("profile_url", "")
            if url and url not in all_connections:
                # Parse the date
                parsed = parse_connection_date(
                    conn.get("connected_date_raw", ""), today=today
                )
                conn["connected_date"] = parsed.isoformat() if parsed else None
                all_connections[url] = conn
                new_count += 1

                # Check if this connection is past the cutoff
                if parsed and parsed < cutoff_date:
                    past_cutoff = True

        logger.debug(
            "scrape_recent_connections: scroll=%d visible=%d new=%d total=%d past_cutoff=%s",
            scroll_index,
            len(visible),
            new_count,
            len(all_connections),
            past_cutoff,
        )

        # Stop conditions
        if past_cutoff:
            logger.info(
                "scrape_recent_connections: stopping at scroll %d — reached cutoff date %s",
                scroll_index,
                cutoff_date.isoformat(),
            )
            break

        if scroll_index >= max_scrolls:
            logger.info(
                "scrape_recent_connections: stopping — reached max_scrolls=%d",
                max_scrolls,
            )
            break

        # Try clicking "Load more" first, fall back to scrolling
        clicked = await _click_load_more(extractor._page)
        if clicked:
            await _PACER.pause(2.0, 4.0, reason="after Load more click")
        else:
            await _PACER.scroll_page(
                extractor._page,
                reason=f"connections scroll {scroll_index + 1}/{max_scrolls}",
            )

        # Check if no new connections were loaded (page might be exhausted)
        if new_count == 0 and scroll_index > 0:
            # Give it one more try
            recheck = await _extract_visible_connections(extractor._page)
            recheck_urls = {c.get("profile_url", "") for c in recheck}
            if recheck_urls.issubset(set(all_connections.keys())):
                logger.info(
                    "scrape_recent_connections: no new connections after scroll; stopping"
                )
                break

    # Filter to only connections within the date range
    filtered: list[dict[str, Any]] = []
    for conn in all_connections.values():
        parsed_date = conn.get("connected_date")
        if parsed_date is None:
            # Include connections with unparseable dates (conservative)
            filtered.append(conn)
            continue
        conn_date = date.fromisoformat(parsed_date)
        if conn_date >= cutoff_date:
            filtered.append(conn)

    # Sort by date descending (most recent first), unparseable last
    def sort_key(c: dict[str, Any]) -> str:
        d = c.get("connected_date")
        return d if d else "0000-00-00"

    filtered.sort(key=sort_key, reverse=True)

    result: dict[str, Any] = {
        "url": CONNECTIONS_URL,
        "connections": filtered,
        "total_found": len(all_connections),
        "total_within_range": len(filtered),
        "cutoff_date": cutoff_date.isoformat(),
        "days": days,
        "diagnostics": diagnostics,
    }

    logger.info(
        "scrape_recent_connections: completed total=%d within_range=%d cutoff=%s",
        len(all_connections),
        len(filtered),
        cutoff_date.isoformat(),
    )

    return result
