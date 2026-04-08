from unittest.mock import AsyncMock, MagicMock

import pytest

from linkedin_mcp_server.scraping.connection_request import (
    click_add_note_button,
    click_profile_connect_action,
    click_shadow_send_without_note,
    dismiss_connection_confirmation,
    profile_has_pending_state,
)


@pytest.fixture(autouse=True)
def no_connection_pacing(monkeypatch):
    monkeypatch.setattr(
        "linkedin_mcp_server.scraping.connection_request._CONNECTION_PACER.after_click",
        AsyncMock(return_value=0.0),
    )


def _page_with_evaluate(value):
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=value)
    return page


async def test_click_profile_connect_action_returns_direct_click():
    page = _page_with_evaluate(
        {"status": "clicked_direct", "name": "Jane Doe", "degree": "2nd"}
    )

    result = await click_profile_connect_action(page)

    assert result["status"] == "clicked_direct"
    assert result["name"] == "Jane Doe"
    page.evaluate.assert_awaited_once()


async def test_click_profile_connect_action_returns_pending():
    page = _page_with_evaluate(
        {"status": "pending", "name": "Jane Doe", "degree": "2nd"}
    )

    result = await click_profile_connect_action(page)

    assert result["status"] == "pending"


async def test_click_profile_connect_action_handles_eval_error():
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=RuntimeError("dom changed"))

    result = await click_profile_connect_action(page)

    assert result["status"] == "error"
    assert "dom changed" in result["message"]


async def test_click_profile_connect_action_handles_unexpected_result():
    page = _page_with_evaluate({"source": "root", "text": "not action data"})

    result = await click_profile_connect_action(page)

    assert result["status"] == "error"


async def test_click_profile_connect_action_handles_unknown_status():
    page = _page_with_evaluate({"status": "sent_somehow", "name": "Jane Doe"})

    result = await click_profile_connect_action(page)

    assert result["status"] == "error"
    assert result["message"] == "Unknown status"


async def test_click_shadow_send_without_note_returns_true():
    page = _page_with_evaluate(True)

    assert await click_shadow_send_without_note(page) is True


async def test_click_add_note_button_returns_true():
    page = _page_with_evaluate(True)

    assert await click_add_note_button(page) is True


async def test_click_shadow_send_without_note_returns_false_on_error():
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=RuntimeError("no shadow root"))

    assert await click_shadow_send_without_note(page) is False


async def test_profile_has_pending_state():
    page = _page_with_evaluate(True)

    assert await profile_has_pending_state(page) is True


async def test_dismiss_connection_confirmation_returns_true():
    page = _page_with_evaluate(True)
    page.keyboard.press = AsyncMock()

    assert await dismiss_connection_confirmation(page) is True
    page.keyboard.press.assert_awaited_once_with("Escape")


async def test_dismiss_connection_confirmation_falls_back_to_keyboard_on_error():
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=RuntimeError("shadow changed"))
    page.keyboard.press = AsyncMock()

    assert await dismiss_connection_confirmation(page) is False
    page.keyboard.press.assert_awaited_once_with("Escape")
