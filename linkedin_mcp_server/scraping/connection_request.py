"""Profile top-card connection request helpers.

These helpers port the behavior from the Veridis browser-console connection
script into the shared Patchright browser flow.  The DOM-dependent logic is
kept here so ``LinkedInExtractor.connect_with_person`` can remain a small
orchestrator with a text-based fallback.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, TypedDict

from patchright.async_api import Page

from linkedin_mcp_server.scraping.browser_pacing import BrowserPacer

logger = logging.getLogger(__name__)
_CONNECTION_PACER = BrowserPacer(logger_name=__name__)

ConnectClickStatus = Literal[
    "already_connected",
    "pending",
    "clicked_direct",
    "clicked_more",
    "no_profile_section",
    "no_more_button",
    "connect_not_found",
    "error",
]

_CONNECT_CLICK_STATUSES = {
    "already_connected",
    "pending",
    "clicked_direct",
    "clicked_more",
    "no_profile_section",
    "no_more_button",
    "connect_not_found",
    "error",
}


class ConnectClickResult(TypedDict, total=False):
    """Result returned by the top-card connect click helper."""

    status: ConnectClickStatus
    name: str
    degree: str
    message: str


async def click_profile_connect_action(page: Page) -> ConnectClickResult:
    """Click direct Connect or More -> Connect in the visible profile top card.

    The evaluated JavaScript intentionally mirrors the local Veridis
    ``send-connection-request.js`` flow: scroll to the top, find a visible
    profile heading, scope action buttons to that profile section, detect
    pending/1st-degree state, then click either a direct Invite button or the
    More-menu Connect item.
    """
    logger.info("Inspecting profile top card for connection action")
    try:
        result: Any = await page.evaluate(
            """async () => {
                const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                const ignoredHeading = /notification|similar|Featured|Activity|About|Experience|Education|Skills|People|Award|Explore|Ad Options/i;

                window.scrollTo(0, 0);
                await wait(600);

                const nameEl = Array.from(document.querySelectorAll('h1, h2')).find(h => {
                    const text = normalize(h.textContent);
                    return text.length > 0 && text.length < 80 && !!h.offsetParent && !ignoredHeading.test(text);
                });
                const name = normalize(nameEl?.textContent) || 'Unknown';
                const profileSection = nameEl?.closest('section');
                if (!profileSection) {
                    return { status: 'no_profile_section', name, message: 'No profile section found' };
                }

                const getProfileEls = () => Array.from(
                    profileSection.querySelectorAll('button, a, [role="button"]')
                ).filter(el => !!el.offsetParent);

                const els = getProfileEls();
                const degreeEl = Array.from(profileSection.querySelectorAll('p, span'))
                    .find(el => /(?:^|·\\s*)(1st|2nd|3rd)\\b/i.test(normalize(el.textContent)) && !!el.offsetParent);
                const degreeMatch = normalize(degreeEl?.textContent).match(/(?:^|·\\s*)(1st|2nd|3rd)\\b/i);
                const degree = degreeMatch ? degreeMatch[1].toLowerCase() : '';

                if (degree === '1st') {
                    return { status: 'already_connected', name, degree };
                }

                const isPending = els.some(el => {
                    const aria = normalize(el.getAttribute('aria-label')).toLowerCase();
                    const text = normalize(el.textContent).toLowerCase();
                    return aria.includes('pending') || text === 'pending';
                });
                if (isPending) {
                    return { status: 'pending', name, degree };
                }

                const directConnectEl = els.find(el => {
                    const aria = normalize(el.getAttribute('aria-label')).toLowerCase();
                    return aria.includes('invite') && aria.includes('connect');
                });
                if (directConnectEl) {
                    directConnectEl.click();
                    await wait(300);
                    return { status: 'clicked_direct', name, degree };
                }

                const moreBtn = Array.from(profileSection.querySelectorAll('button'))
                    .find(button => normalize(button.textContent) === 'More');
                if (!moreBtn) {
                    return { status: 'no_more_button', name, degree };
                }

                moreBtn.click();
                await wait(1500);

                const menuItems = Array.from(document.querySelectorAll('[role="menuitem"]'));
                if (menuItems.some(el => normalize(el.textContent) === 'Pending')) {
                    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
                    return { status: 'pending', name, degree };
                }

                const connectItem = menuItems.find(el => normalize(el.textContent) === 'Connect');
                if (!connectItem) {
                    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
                    return { status: 'connect_not_found', name, degree };
                }

                connectItem.click();
                await wait(300);
                return { status: 'clicked_more', name, degree };
            }"""
        )
    except Exception as exc:
        logger.debug("Profile top-card connection helper failed", exc_info=True)
        return {"status": "error", "name": "Unknown", "message": str(exc)}

    if not isinstance(result, dict) or "status" not in result:
        logger.debug("Unexpected top-card helper result: %r", result)
        return {"status": "error", "name": "Unknown", "message": "Unexpected result"}
    if result["status"] not in _CONNECT_CLICK_STATUSES:
        logger.debug("Unknown top-card helper status: %r", result)
        return {"status": "error", "name": "Unknown", "message": "Unknown status"}

    logger.info(
        "Profile top-card connection action result: status=%s name=%s degree=%s",
        result.get("status"),
        result.get("name"),
        result.get("degree", ""),
    )
    if result.get("status") in {"clicked_direct", "clicked_more"}:
        await _CONNECTION_PACER.after_click(reason="profile connect action")
    return result  # type: ignore[return-value]


async def click_shadow_send_without_note(page: Page) -> bool:
    """Click LinkedIn's standard or shadow-DOM ``Send without a note`` button."""
    logger.info("Looking for Send without a note button")
    try:
        clicked = await page.evaluate(
            """async () => {
                const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                const roots = root => {
                    const result = [root];
                    for (const host of root.querySelectorAll('*')) {
                        if (host.shadowRoot) result.push(...roots(host.shadowRoot));
                    }
                    return result;
                };
                const findButton = root => Array.from(root.querySelectorAll('button, [role="button"]')).find(el => {
                    const aria = normalize(el.getAttribute('aria-label')).toLowerCase();
                    const text = normalize(el.textContent).toLowerCase();
                    return aria === 'send without a note' || text === 'send without a note';
                });
                const deadline = Date.now() + 4000;
                while (Date.now() < deadline) {
                    for (const root of roots(document)) {
                        const button = findButton(root);
                        if (button) {
                            button.click();
                            await wait(1000);
                            return true;
                        }
                    }
                    await wait(150);
                }
                return false;
            }"""
        )
        logger.info("Send without a note button clicked=%s", bool(clicked))
        if clicked:
            await _CONNECTION_PACER.after_click(reason="send without note click")
        return bool(clicked)
    except Exception:
        logger.debug("Shadow-DOM send button lookup failed", exc_info=True)
        return False


async def click_add_note_button(page: Page) -> bool:
    """Click LinkedIn's standard or shadow-DOM ``Add a note`` button."""
    logger.info("Looking for Add a note button")
    try:
        clicked = await page.evaluate(
            """async () => {
                const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                const roots = root => {
                    const result = [root];
                    for (const host of root.querySelectorAll('*')) {
                        if (host.shadowRoot) result.push(...roots(host.shadowRoot));
                    }
                    return result;
                };
                const findButton = root => Array.from(root.querySelectorAll('button, [role="button"]')).find(el => {
                    const aria = normalize(el.getAttribute('aria-label')).toLowerCase();
                    const text = normalize(el.textContent).toLowerCase();
                    return aria === 'add a note'
                        || aria === 'add note'
                        || text === 'add a note'
                        || text === 'add note';
                });
                const deadline = Date.now() + 3000;
                while (Date.now() < deadline) {
                    for (const root of roots(document)) {
                        const button = findButton(root);
                        if (button) {
                            button.click();
                            await wait(500);
                            return true;
                        }
                    }
                    await wait(150);
                }
                return false;
            }"""
        )
        logger.info("Add a note button clicked=%s", bool(clicked))
        if clicked:
            await _CONNECTION_PACER.after_click(reason="add note click")
        return bool(clicked)
    except Exception:
        logger.debug("Add a note button lookup failed", exc_info=True)
        return False


async def dismiss_connection_confirmation(page: Page) -> bool:
    """Dismiss a standard or shadow-DOM connection confirmation surface."""
    logger.info("Dismissing connection confirmation surface if present")
    try:
        clicked = await page.evaluate(
            """async () => {
                const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                const roots = root => {
                    const result = [root];
                    for (const host of root.querySelectorAll('*')) {
                        if (host.shadowRoot) result.push(...roots(host.shadowRoot));
                    }
                    return result;
                };
                const findDismiss = root => Array.from(root.querySelectorAll('button, [role="button"]')).find(el => {
                    const aria = normalize(el.getAttribute('aria-label')).toLowerCase();
                    const text = normalize(el.textContent).toLowerCase();
                    return aria.includes('dismiss')
                        || aria.includes('close')
                        || text === 'cancel'
                        || text === 'not now';
                });
                for (const root of roots(document)) {
                    const button = findDismiss(root);
                    if (button) {
                        button.click();
                        await wait(300);
                        return true;
                    }
                }
                document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
                await wait(300);
                return false;
            }"""
        )
        try:
            await page.keyboard.press("Escape")
        except Exception:
            logger.debug("Keyboard Escape dismiss fallback failed", exc_info=True)
        logger.info("Connection confirmation dismiss clicked=%s", bool(clicked))
        if clicked:
            await _CONNECTION_PACER.after_click(reason="connection confirmation dismiss click")
        return bool(clicked)
    except Exception:
        logger.debug("Connection confirmation dismiss failed", exc_info=True)
        try:
            await page.keyboard.press("Escape")
        except Exception:
            logger.debug("Keyboard Escape dismiss fallback failed", exc_info=True)
        return False


async def profile_has_pending_state(page: Page) -> bool:
    """Return whether the visible profile top card now exposes Pending state."""
    try:
        pending = await page.evaluate(
            """() => {
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                const ignoredHeading = /notification|similar|Featured|Activity|About|Experience|Education|Skills|People|Award|Explore|Ad Options/i;
                const nameEl = Array.from(document.querySelectorAll('h1, h2')).find(h => {
                    const text = normalize(h.textContent);
                    return text.length > 0 && text.length < 80 && !!h.offsetParent && !ignoredHeading.test(text);
                });
                const profileSection = nameEl?.closest('section');
                if (!profileSection) return false;
                return Array.from(profileSection.querySelectorAll('button, a, [role="button"]'))
                    .filter(el => !!el.offsetParent)
                    .some(el => {
                        const aria = normalize(el.getAttribute('aria-label')).toLowerCase();
                        const text = normalize(el.textContent);
                        return aria.includes('pending') || text === 'Pending';
                    });
            }"""
        )
        logger.info("Profile pending verification result=%s", bool(pending))
        return bool(pending)
    except Exception:
        logger.debug("Pending-state verification failed", exc_info=True)
        return False
