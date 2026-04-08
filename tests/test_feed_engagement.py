from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from linkedin_mcp_server.workflows.feed_engagement import (
    FEED_URL,
    MAX_COMMENT_LIMIT,
    MAX_FEED_POST_LIMIT,
    MAX_REACTOR_LIMIT,
    collect_feed_engagement,
    search_feed_posts,
    _extract_visible_feed_items,
    _wait_for_feed_hydration,
)


def raw_feed_item(
    post_url: str,
    *,
    post_text: str = "Legal AI is changing litigation workflows.",
    raw_text: str | None = None,
    author_name: str = "Jane Lawyer",
    author_headline: str = "Attorney at Example LLP",
    is_promoted: bool = False,
):
    return {
        "post_url": post_url,
        "post_text": post_text,
        "raw_text": raw_text
        or f"{author_name}\n{author_headline}\n{post_text}\n12 reactions\n3 comments\n1 repost",
        "author_name": author_name,
        "author_headline": author_headline,
        "author_profile_url": "https://www.linkedin.com/in/jane-lawyer/",
        "is_promoted": is_promoted,
    }


def make_extractor(raw_items, *, page_url=FEED_URL):
    page = SimpleNamespace()
    page.url = page_url
    page.wait_for_selector = AsyncMock()
    page.wait_for_function = AsyncMock()
    page.evaluate = AsyncMock(return_value=raw_items)
    page.locator = lambda _selector: SimpleNamespace(
        count=AsyncMock(return_value=1),
        inner_text=AsyncMock(return_value=""),
    )
    page.mouse = SimpleNamespace(wheel=AsyncMock())
    extractor = SimpleNamespace()
    extractor._page = page
    extractor._navigate_to_page = AsyncMock()
    extractor.get_post_details = AsyncMock(
        side_effect=lambda post_url: {"post_url": post_url, "details": True}
    )
    extractor.get_post_comments = AsyncMock(
        side_effect=lambda post_url, limit: {"post_url": post_url, "limit": limit}
    )
    extractor.get_post_reactors = AsyncMock(
        side_effect=lambda post_url, limit, reaction_type=None: {
            "post_url": post_url,
            "limit": limit,
            "reaction_type": reaction_type,
        }
    )
    return extractor


@pytest.mark.asyncio
async def test_search_feed_posts_filters_keywords_counts_and_dedupes():
    extractor = make_extractor(
        [
            raw_feed_item(
                "https://www.linkedin.com/feed/update/urn:li:activity:1/?trackingId=abc"
            ),
            raw_feed_item(
                "https://www.linkedin.com/feed/update/urn:li:activity:1/",
                post_text="Duplicate legal post",
            ),
            raw_feed_item(
                "https://www.linkedin.com/feed/update/urn:li:activity:2/",
                post_text="Generic sales update",
                raw_text="Generic sales update\n1 reaction",
            ),
        ]
    )

    result = await search_feed_posts(
        extractor,
        keywords=["legal", "law"],
        max_posts=10,
        scrolls=0,
        min_reactions=5,
        min_comments=2,
    )

    assert result["diagnostics"] == []
    assert [post["activity_id"] for post in result["posts"]] == ["1"]
    assert result["posts"][0]["matched_keywords"] == ["legal"]
    assert result["posts"][0]["reaction_count"] == 12
    assert result["posts"][0]["comment_count"] == 3
    assert result["posts"][0]["repost_count"] == 1


@pytest.mark.asyncio
async def test_search_feed_posts_excludes_promoted_by_default():
    extractor = make_extractor(
        [
            raw_feed_item(
                "https://www.linkedin.com/feed/update/urn:li:activity:3/",
                raw_text="Promoted\nLegal platform\n9 reactions",
                is_promoted=True,
            )
        ]
    )

    hidden = await search_feed_posts(
        extractor,
        keywords=["legal"],
        scrolls=0,
        include_promoted=False,
    )
    visible = await search_feed_posts(
        extractor,
        keywords=["legal"],
        scrolls=0,
        include_promoted=True,
    )

    assert hidden["posts"] == []
    assert len(visible["posts"]) == 1
    assert visible["posts"][0]["is_promoted"] is True


@pytest.mark.asyncio
async def test_collect_feed_engagement_enriches_discovered_posts():
    extractor = make_extractor(
        [raw_feed_item("https://www.linkedin.com/feed/update/urn:li:activity:4/")]
    )

    result = await collect_feed_engagement(
        extractor,
        keywords=["legal"],
        max_posts=1,
        scrolls=0,
        include_comments=True,
        include_reactors=True,
        comment_limit=5,
        reactor_limit=7,
        reaction_type="Like",
        delay_range=(0, 0),
    )

    assert len(result["posts"]) == 1
    post_url = "https://www.linkedin.com/feed/update/urn:li:activity:4/"
    extractor.get_post_details.assert_awaited_once_with(post_url)
    extractor.get_post_comments.assert_awaited_once_with(post_url, limit=5)
    extractor.get_post_reactors.assert_awaited_once_with(
        post_url, limit=7, reaction_type="Like"
    )
    assert result["diagnostics"] == []


@pytest.mark.asyncio
async def test_collect_feed_engagement_applies_hard_caps(monkeypatch):
    extractor = make_extractor([])

    monkeypatch.setattr(
        "linkedin_mcp_server.workflows.feed_engagement._FEED_PACER.scroll_page",
        AsyncMock(),
    )

    result = await collect_feed_engagement(
        extractor,
        max_posts=999,
        comment_limit=999,
        reactor_limit=999,
        scrolls=999,
        include_comments=False,
        include_reactors=True,
        delay_range=(0, 0),
    )

    assert result["limits"]["posts"] == MAX_FEED_POST_LIMIT
    assert result["limits"]["scrolls"] == 30
    assert result["limits"]["comments_per_post"] == MAX_COMMENT_LIMIT
    assert result["limits"]["reactors_per_post"] == MAX_REACTOR_LIMIT


@pytest.mark.asyncio
async def test_extract_visible_feed_items_uses_browser_evaluate():
    page = SimpleNamespace(
        evaluate=AsyncMock(
            return_value=[
                {
                    "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:5/",
                    "activity_id": "5",
                    "author_name": "Synthetic Lawyer",
                    "author_headline": "General Counsel",
                    "author_degree": "1st",
                    "author_timestamp": "1d",
                    "author_profile_url": "https://www.linkedin.com/in/synthetic-lawyer/",
                    "post_text": "Legal AI post",
                    "raw_text": "Legal AI post\n5 reactions",
                    "reaction_types": ["Like"],
                    "is_promoted": False,
                }
            ]
        )
    )

    result = await _extract_visible_feed_items(page)

    assert result[0]["activity_id"] == "5"
    assert result[0]["author_degree"] == "1st"
    assert result[0]["reaction_types"] == ["Like"]
    script = page.evaluate.await_args.args[0]
    assert '[data-testid="mainFeed"]' in script
    assert '[componentkey*="urn:li:activity"]' in script
    assert 'urn%3Ali%3Aactivity' in script
    assert 'activity-' in script
    assert '[data-testid="expandable-text-box"]' in script


@pytest.mark.asyncio
async def test_search_feed_posts_waits_for_feed_hydration():
    extractor = make_extractor(
        [raw_feed_item("https://www.linkedin.com/feed/update/urn:li:activity:6/")]
    )

    await search_feed_posts(extractor, max_posts=1, scrolls=0)

    extractor._page.wait_for_function.assert_awaited_once()
    script = extractor._page.wait_for_function.await_args.args[0]
    assert '/feed/update/urn:li:activity:' in script
    assert 'Start a post' not in script


@pytest.mark.asyncio
async def test_search_feed_posts_reuses_current_feed_page():
    extractor = make_extractor(
        [raw_feed_item("https://www.linkedin.com/feed/update/urn:li:activity:7/")]
    )

    await search_feed_posts(extractor, max_posts=1, scrolls=0)

    extractor._navigate_to_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_feed_posts_navigates_when_not_on_feed_home():
    extractor = make_extractor(
        [raw_feed_item("https://www.linkedin.com/feed/update/urn:li:activity:8/")],
        page_url="https://www.linkedin.com/in/example/",
    )

    await search_feed_posts(extractor, max_posts=1, scrolls=0)

    extractor._navigate_to_page.assert_awaited_once_with(FEED_URL)


@pytest.mark.asyncio
async def test_search_feed_posts_diagnoses_empty_visible_feed():
    extractor = make_extractor([])
    extractor._page.evaluate.side_effect = [
        [],
        {
            "url": FEED_URL,
            "title": "Feed | LinkedIn",
            "body_length": 835,
            "main_present": True,
            "main_feed_present": False,
            "feed_root_link_count": 0,
            "feed_root_link_buckets": {},
            "feed_root_button_buckets": {},
            "listitem_attribute_names": [],
            "listitem_data_value_markers": {
                "activity": 0,
                "urn": 0,
                "numeric_long": 0,
            },
            "contains_reactions_text": False,
            "contains_comments_text": False,
            "contains_loading_text": False,
            "contains_no_posts_text": False,
            "activity_link_count": 0,
            "activity_element_count": 0,
            "activity_urn_count": 0,
            "activity_url_count": 0,
            "article_count": 0,
            "listitem_count": 0,
        },
    ]

    result = await search_feed_posts(extractor, max_posts=1, scrolls=0)

    assert result["posts"] == []
    assert result["diagnostics"][0]["stage"] == "discover_feed_posts"
    assert result["diagnostics"][0]["error_type"] == "NoVisibleFeedItemsError"
    assert result["diagnostics"][0]["snapshot"]["body_length"] == 835


@pytest.mark.asyncio
async def test_feed_hydration_wait_is_best_effort():
    page = SimpleNamespace(
        wait_for_function=AsyncMock(side_effect=TimeoutError("not hydrated"))
    )

    await _wait_for_feed_hydration(page)

    page.wait_for_function.assert_awaited_once()
