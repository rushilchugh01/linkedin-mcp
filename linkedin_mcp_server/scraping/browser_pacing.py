"""Conservative browser interaction pacing helpers.

This module centralizes bounded waits and scroll pacing used by scraping
workflows. It is not an anti-detection layer; callers should keep limits small
and use these helpers for UI stability, polite rate limiting, and clearer logs.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

logger = logging.getLogger(__name__)

ACTION_DELAY_MIN_SECONDS = 5.0
ACTION_DELAY_MAX_SECONDS = 15.0


def random_action_delay_seconds(rng: random.Random | None = None) -> float:
    """Return the default bounded delay after browser actions."""
    generator = rng or random.Random()
    return generator.uniform(ACTION_DELAY_MIN_SECONDS, ACTION_DELAY_MAX_SECONDS)


class BrowserPacer:
    """Small helper for bounded browser interaction pacing."""

    def __init__(
        self,
        *,
        rng: random.Random | None = None,
        logger_name: str | None = None,
    ) -> None:
        self._rng = rng or random.Random()
        self._logger = logging.getLogger(logger_name) if logger_name else logger

    def delay_seconds(self, min_seconds: float, max_seconds: float) -> float:
        """Return a bounded delay in seconds for the configured RNG."""
        minimum = max(0.0, float(min_seconds))
        maximum = max(0.0, float(max_seconds))
        if maximum < minimum:
            maximum = minimum
        if maximum == 0:
            return 0.0
        return self._rng.uniform(minimum, maximum)

    async def pause(
        self,
        min_seconds: float = 1.0,
        max_seconds: float = 2.0,
        *,
        reason: str = "browser pacing",
    ) -> float:
        """Sleep for a bounded delay and return the actual delay used."""
        delay = self.delay_seconds(min_seconds, max_seconds)
        if delay <= 0:
            self._logger.debug("%s: skipped pacing delay", reason)
            return 0.0
        self._logger.debug("%s: pacing delay %.2fs", reason, delay)
        await asyncio.sleep(delay)
        return delay

    async def between_navigation(
        self,
        min_seconds: float = 2.0,
        max_seconds: float = 5.0,
        *,
        reason: str = "between navigation steps",
    ) -> float:
        """Pause between page-level workflow steps."""
        return await self.pause(min_seconds, max_seconds, reason=reason)

    async def after_click(
        self,
        min_seconds: float | None = None,
        max_seconds: float | None = None,
        *,
        reason: str = "after click",
    ) -> float:
        """Pause after a click or click-like DOM action."""
        if min_seconds is None or max_seconds is None:
            min_seconds = ACTION_DELAY_MIN_SECONDS if min_seconds is None else min_seconds
            max_seconds = ACTION_DELAY_MAX_SECONDS if max_seconds is None else max_seconds
        return await self.pause(min_seconds, max_seconds, reason=reason)

    async def before_scroll(
        self,
        min_seconds: float = 0.25,
        max_seconds: float = 0.75,
        *,
        reason: str = "before scroll",
    ) -> float:
        """Pause before a scroll action."""
        return await self.pause(min_seconds, max_seconds, reason=reason)

    async def scroll_page(
        self,
        page: Any,
        *,
        delta_y: int = 900,
        min_seconds: float = 2.0,
        max_seconds: float = 4.0,
        reason: str = "page scroll",
    ) -> None:
        """Scroll the page viewport, then pause for rendering/network work."""
        await self.before_scroll(0.1, 0.3, reason=f"{reason} prepare")
        try:
            await page.mouse.wheel(0, delta_y)
            self._logger.debug(
                "%s: scrolled page via mouse wheel delta_y=%d", reason, delta_y
            )
        except Exception:
            self._logger.debug(
                "%s: mouse wheel failed; falling back to window.scrollBy",
                reason,
                exc_info=True,
            )
            await page.evaluate(
                "(deltaY) => window.scrollBy({ top: deltaY, behavior: 'smooth' })",
                delta_y,
            )
        await self.pause(min_seconds, max_seconds, reason=f"{reason} settle")

    async def scroll_largest_scrollable_in_dialog(
        self,
        page: Any,
        *,
        min_seconds: float = 2.0,
        max_seconds: float = 4.0,
        reason: str = "dialog scroll",
    ) -> bool:
        """Scroll the largest scrollable element in the active dialog to bottom."""
        scrolled = await page.evaluate(
            """() => {
                const dialog = document.querySelector('dialog[open], [role="dialog"]');
                if (!dialog) return false;
                const candidates = [dialog, ...Array.from(dialog.querySelectorAll('*'))]
                    .filter(el => el.scrollHeight > el.clientHeight + 20);
                const target = candidates.sort((a, b) => b.scrollHeight - a.scrollHeight)[0] || dialog;
                target.scrollTop = target.scrollHeight;
                return true;
            }"""
        )
        self._logger.debug("%s: scrolled=%s", reason, scrolled)
        if scrolled:
            await self.pause(min_seconds, max_seconds, reason=f"{reason} settle")
        return bool(scrolled)

    async def hover_visible_area(
        self,
        page: Any,
        *,
        x_ratio: float = 0.5,
        y_ratio: float = 0.5,
        min_seconds: float = 0.1,
        max_seconds: float = 0.3,
        reason: str = "visible area hover",
    ) -> None:
        """Move the pointer to a bounded viewport point for visible-run stability."""
        viewport = getattr(page, "viewport_size", None) or {}
        width = int(viewport.get("width") or 1280)
        height = int(viewport.get("height") or 720)
        x = max(1, min(width - 1, int(width * x_ratio)))
        y = max(1, min(height - 1, int(height * y_ratio)))
        await page.mouse.move(x, y)
        self._logger.debug("%s: moved pointer to x=%d y=%d", reason, x, y)
        await self.pause(min_seconds, max_seconds, reason=f"{reason} settle")
