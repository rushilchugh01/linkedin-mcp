import random
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from linkedin_mcp_server.scraping.browser_pacing import (
    ACTION_DELAY_MAX_SECONDS,
    ACTION_DELAY_MIN_SECONDS,
    BrowserPacer,
    random_action_delay_seconds,
)


class FixedRng(random.Random):
    def uniform(self, a: float, b: float) -> float:
        return a + ((b - a) / 2)


def test_delay_seconds_uses_bounded_range():
    pacer = BrowserPacer(rng=FixedRng())

    assert pacer.delay_seconds(2.0, 4.0) == 3.0
    assert pacer.delay_seconds(-1.0, 0.0) == 0.0
    assert pacer.delay_seconds(5.0, 2.0) == 5.0


def test_random_action_delay_seconds_uses_default_bounds():
    delay = random_action_delay_seconds(FixedRng())

    assert delay == ACTION_DELAY_MIN_SECONDS + (
        (ACTION_DELAY_MAX_SECONDS - ACTION_DELAY_MIN_SECONDS) / 2
    )


@pytest.mark.asyncio
async def test_pause_uses_asyncio_sleep(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(
        "linkedin_mcp_server.scraping.browser_pacing.asyncio.sleep", sleep
    )
    pacer = BrowserPacer(rng=FixedRng())

    delay = await pacer.pause(1.0, 3.0, reason="test pause")

    assert delay == 2.0
    sleep.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
async def test_pause_skips_zero_delay(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(
        "linkedin_mcp_server.scraping.browser_pacing.asyncio.sleep", sleep
    )
    pacer = BrowserPacer(rng=FixedRng())

    delay = await pacer.pause(0.0, 0.0, reason="zero pause")

    assert delay == 0.0
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_scroll_largest_scrollable_in_dialog_calls_page_evaluate():
    page = SimpleNamespace(evaluate=AsyncMock(return_value=True))
    pacer = BrowserPacer(rng=FixedRng())

    scrolled = await pacer.scroll_largest_scrollable_in_dialog(
        page,
        min_seconds=0,
        max_seconds=0,
        reason="test dialog scroll",
    )

    assert scrolled is True
    page.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_scroll_page_falls_back_to_evaluate_when_mouse_wheel_fails():
    mouse = SimpleNamespace(wheel=AsyncMock(side_effect=RuntimeError("no wheel")))
    page = SimpleNamespace(mouse=mouse, evaluate=AsyncMock())
    pacer = BrowserPacer(rng=FixedRng())

    await pacer.scroll_page(
        page,
        delta_y=500,
        min_seconds=0,
        max_seconds=0,
        reason="test page scroll",
    )

    mouse.wheel.assert_awaited_once_with(0, 500)
    page.evaluate.assert_awaited_once()
