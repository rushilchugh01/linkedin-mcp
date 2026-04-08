"""Utility functions for scraping operations."""

import asyncio
import logging
import random

from patchright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .exceptions import RateLimitError

logger = logging.getLogger(__name__)


async def _core_pace(reason: str) -> None:
    """Short random delay after click actions (avoids circular import with scraping)."""
    delay = random.uniform(5.0, 15.0)
    logger.debug("core pace %.1fs: %s", delay, reason)
    await asyncio.sleep(delay)

SCROLL_PAUSE_MIN_SECONDS = 2.0
SCROLL_PAUSE_MAX_SECONDS = 4.0


def _scroll_pause_bounds(
    pause_time: float | None,
    min_pause_time: float,
    max_pause_time: float,
) -> tuple[float, float]:
    minimum = max(SCROLL_PAUSE_MIN_SECONDS, float(min_pause_time))
    maximum = max(SCROLL_PAUSE_MAX_SECONDS, float(max_pause_time), minimum)
    if pause_time is not None:
        logger.debug(
            "Ignoring fixed scroll pause_time=%s; using randomized %.1f-%.1fs pause",
            pause_time,
            minimum,
            maximum,
        )
    return minimum, maximum


async def _sleep_after_scroll(
    *,
    min_pause_time: float = SCROLL_PAUSE_MIN_SECONDS,
    max_pause_time: float = SCROLL_PAUSE_MAX_SECONDS,
    reason: str,
) -> float:
    delay = random.uniform(min_pause_time, max_pause_time)
    logger.debug("%s: randomized post-scroll pause %.2fs", reason, delay)
    await asyncio.sleep(delay)
    return delay


async def detect_rate_limit(page: Page) -> None:
    """Detect if LinkedIn has rate-limited or security-challenged the session.

    Checks (in order):
    1. URL contains /checkpoint or /authwall (security challenge)
    2. Body text contains rate-limit phrases on error-shaped pages (throttling)

    The body-text heuristic only runs on pages without a ``<main>`` element
    and with short body text (<2000 chars), since real rate-limit pages are
    minimal error pages.  This avoids false positives from profile content
    that happens to contain phrases like "slow down" or "try again later".

    Raises:
        RateLimitError: If any rate-limiting or security challenge is detected
    """
    # Check URL for security challenges
    current_url = page.url
    if "linkedin.com/checkpoint" in current_url or "authwall" in current_url:
        raise RateLimitError(
            "LinkedIn security checkpoint detected. "
            "You may need to verify your identity or wait before continuing.",
            suggested_wait_time=30,
        )

    # Check for rate limit messages — only on error-shaped pages.
    # Real rate-limit pages have no <main> element and short body text.
    # Normal LinkedIn pages (profiles, jobs) have <main> and long content
    # that may incidentally contain phrases like "slow down".
    try:
        has_main = await page.locator("main").count() > 0
        if has_main:
            return  # Normal page with content, skip body text heuristic

        body_text = await page.locator("body").inner_text(timeout=1000)
        if body_text and len(body_text) < 2000:
            body_lower = body_text.lower()
            if any(
                phrase in body_lower
                for phrase in [
                    "too many requests",
                    "rate limit",
                    "slow down",
                    "try again later",
                ]
            ):
                raise RateLimitError(
                    "Rate limit message detected on page.",
                    suggested_wait_time=30,
                )
    except RateLimitError:
        raise
    except PlaywrightTimeoutError:
        pass


async def scroll_to_bottom(
    page: Page,
    pause_time: float | None = None,
    max_scrolls: int = 10,
    min_pause_time: float = SCROLL_PAUSE_MIN_SECONDS,
    max_pause_time: float = SCROLL_PAUSE_MAX_SECONDS,
) -> None:
    """Scroll to the bottom of the page to trigger lazy loading.

    Args:
        page: Patchright page object
        pause_time: Deprecated fixed pause; ignored in favor of 2-4s randomized pauses
        max_scrolls: Maximum number of scroll attempts
        min_pause_time: Minimum randomized post-scroll pause in seconds
        max_pause_time: Maximum randomized post-scroll pause in seconds
    """
    min_pause_time, max_pause_time = _scroll_pause_bounds(
        pause_time, min_pause_time, max_pause_time
    )
    for i in range(max_scrolls):
        previous_height = await page.evaluate("document.body.scrollHeight")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await _sleep_after_scroll(
            min_pause_time=min_pause_time,
            max_pause_time=max_pause_time,
            reason=f"page scroll {i + 1}/{max_scrolls}",
        )

        new_height = await page.evaluate("document.body.scrollHeight")
        if new_height == previous_height:
            logger.debug("Reached bottom after %d scrolls", i + 1)
            break


async def scroll_job_sidebar(
    page: Page,
    pause_time: float | None = None,
    max_scrolls: int = 10,
    min_pause_time: float = SCROLL_PAUSE_MIN_SECONDS,
    max_pause_time: float = SCROLL_PAUSE_MAX_SECONDS,
) -> None:
    """Scroll the job search sidebar to load all job cards.

    LinkedIn renders job search results in a scrollable sidebar container,
    not the main page body. This function finds that container by locating
    a job card link and walking up to its scrollable ancestor, then scrolls
    it iteratively until no new content loads.

    Args:
        page: Patchright page object
        pause_time: Deprecated fixed pause; ignored in favor of 2-4s randomized pauses
        max_scrolls: Maximum number of scroll attempts
        min_pause_time: Minimum randomized post-scroll pause in seconds
        max_pause_time: Maximum randomized post-scroll pause in seconds
    """
    min_pause_time, max_pause_time = _scroll_pause_bounds(
        pause_time, min_pause_time, max_pause_time
    )
    # Wait for at least one job card link to render before scrolling
    try:
        await page.wait_for_selector('a[href*="/jobs/view/"]', timeout=5000)
    except PlaywrightTimeoutError:
        logger.debug("No job card links found, skipping sidebar scroll")
        return

    scrolled = await page.evaluate(
        """async ({pauseTime, maxScrolls}) => {
            const link = document.querySelector('a[href*="/jobs/view/"]');
            if (!link) return -2;

            let container = link.parentElement;
            while (container && container !== document.body) {
                const style = window.getComputedStyle(container);
                const overflowY = style.overflowY;
                if ((overflowY === 'auto' || overflowY === 'scroll')
                    && container.scrollHeight > container.clientHeight) {
                    break;
                }
                container = container.parentElement;
            }

            if (!container || container === document.body) {
                return -1;
            }

            let scrollCount = 0;
            for (let i = 0; i < maxScrolls; i++) {
                const prevHeight = container.scrollHeight;
                container.scrollTop = container.scrollHeight;
                const pauseSeconds = pauseMin + Math.random() * (pauseMax - pauseMin);
                await new Promise(r => setTimeout(r, pauseSeconds * 1000));
                if (container.scrollHeight === prevHeight) break;
                scrollCount++;
            }
            return scrollCount;
        }""",
        {
            "pauseMin": min_pause_time,
            "pauseMax": max_pause_time,
            "maxScrolls": max_scrolls,
        },
    )
    if scrolled == -2:
        logger.debug("Job card link disappeared before evaluate, skipping scroll")
    elif scrolled == -1:
        logger.debug("No scrollable container found for job sidebar")
    elif scrolled:
        logger.debug("Scrolled job sidebar %d times", scrolled)
    else:
        logger.debug("Job sidebar container found but no new content loaded")


async def handle_modal_close(page: Page) -> bool:
    """Close any popup modals that might be blocking content.

    Returns:
        True if a modal was closed, False otherwise
    """
    try:
        close_button = page.locator(
            'button[aria-label="Dismiss"], '
            'button[aria-label="Close"], '
            "button.artdeco-modal__dismiss"
        ).first

        if await close_button.is_visible(timeout=1000):
            await close_button.click()
            await _core_pace(reason="modal close click")
            logger.debug("Closed modal")
            return True
    except PlaywrightTimeoutError:
        pass
    except Exception as e:
        logger.debug("Error closing modal: %s", e)

    return False
