from unittest.mock import AsyncMock

import pytest

from linkedin_mcp_server.config.schema import AppConfig
from linkedin_mcp_server.server import create_mcp_server


@pytest.fixture
def session_config(monkeypatch):
    config = AppConfig()
    monkeypatch.setattr("linkedin_mcp_server.server.get_config", lambda: config)
    return config


async def test_browser_session_mode_defaults_to_visible_browser(session_config):
    mcp = create_mcp_server()

    result = await mcp.call_tool("browser_session_mode", {})

    assert result.structured_content["status"] == "success"
    assert result.structured_content["headless"] is False
    assert result.structured_content["configured_headless"] is False
    assert result.structured_content["mode"] == "no_headless"
    assert result.structured_content["active_browser"] is False
    assert result.structured_content["requires_close_session"] is False


async def test_browser_session_mode_sets_headless_true_and_false(session_config):
    mcp = create_mcp_server()

    headless = await mcp.call_tool("browser_session_mode", {"headless": True})
    assert headless.structured_content["headless"] is True
    assert headless.structured_content["configured_headless"] is True
    assert headless.structured_content["mode"] == "headless"
    assert headless.structured_content["previous_headless"] is False

    visible = await mcp.call_tool("browser_session_mode", {"headless": False})
    assert visible.structured_content["headless"] is False
    assert visible.structured_content["configured_headless"] is False
    assert visible.structured_content["mode"] == "no_headless"
    assert visible.structured_content["previous_headless"] is True


async def test_browser_session_mode_reports_active_browser_requires_relaunch(
    monkeypatch,
    session_config,
):
    monkeypatch.setattr("linkedin_mcp_server.server.has_active_browser", lambda: True)
    mcp = create_mcp_server()

    result = await mcp.call_tool("browser_session_mode", {"headless": True})

    assert result.structured_content["headless"] is True
    assert result.structured_content["active_browser"] is True
    assert result.structured_content["applied_to_active_browser"] is False
    assert result.structured_content["requires_close_session"] is True
    assert "close_session" in result.structured_content["message"]


async def test_close_session_leaves_browser_mode_for_next_session(
    monkeypatch,
    session_config,
):
    close_browser = AsyncMock()
    monkeypatch.setattr("linkedin_mcp_server.server.close_browser", close_browser)
    mcp = create_mcp_server()

    await mcp.call_tool("browser_session_mode", {"headless": True})
    result = await mcp.call_tool("close_session", {})
    mode = await mcp.call_tool("browser_session_mode", {})

    assert result.structured_content["status"] == "success"
    close_browser.assert_awaited_once()
    assert mode.structured_content["headless"] is True
