from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from linkedin_mcp_server.workflows.company_engagement import (
    MAX_COMMENT_LIMIT,
    MAX_POST_LIMIT,
    MAX_REACTOR_LIMIT,
    collect_company_engagement,
)


def extracted(*references, error=None):
    return SimpleNamespace(
        text="Company posts", references=list(references), error=error
    )


@pytest.fixture
def mock_extractor():
    extractor = SimpleNamespace()
    extractor.extract_page = AsyncMock(
        return_value=extracted(
            {
                "kind": "feed_post",
                "url": "/feed/update/urn:li:activity:1/",
                "text": "Post 1",
            },
            {
                "kind": "feed_post",
                "url": "/feed/update/urn:li:activity:2/",
                "text": "Post 2",
            },
        )
    )
    extractor.get_post_details = AsyncMock(
        side_effect=lambda post_url: {"post_url": post_url, "text": "details"}
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
async def test_collect_company_engagement_discovers_and_enriches_posts(mock_extractor):
    result = await collect_company_engagement(
        mock_extractor,
        "testcorp",
        limit=2,
        include_comments=True,
        comment_limit=5,
        delay_range=(0, 0),
    )

    mock_extractor.extract_page.assert_awaited_once_with(
        "https://www.linkedin.com/company/testcorp/posts/",
        section_name="posts",
    )
    assert result["post_urls"] == [
        "https://www.linkedin.com/feed/update/urn:li:activity:1/",
        "https://www.linkedin.com/feed/update/urn:li:activity:2/",
    ]
    assert len(result["posts"]) == 2
    assert mock_extractor.get_post_details.await_count == 2
    assert mock_extractor.get_post_comments.await_count == 2
    mock_extractor.get_post_reactors.assert_not_awaited()
    assert result["diagnostics"] == []


@pytest.mark.asyncio
async def test_collect_company_engagement_can_include_reactors(mock_extractor):
    result = await collect_company_engagement(
        mock_extractor,
        "https://www.linkedin.com/company/testcorp/",
        limit=1,
        include_comments=False,
        include_reactors=True,
        reactor_limit=7,
        reaction_type="Like",
        delay_range=(0, 0),
    )

    mock_extractor.extract_page.assert_awaited_once_with(
        "https://www.linkedin.com/company/testcorp/posts/",
        section_name="posts",
    )
    mock_extractor.get_post_comments.assert_not_awaited()
    mock_extractor.get_post_reactors.assert_awaited_once_with(
        "https://www.linkedin.com/feed/update/urn:li:activity:1/",
        limit=7,
        reaction_type="Like",
    )
    assert result["posts"][0]["reactors"]["reaction_type"] == "Like"


@pytest.mark.asyncio
async def test_collect_company_engagement_applies_hard_caps(mock_extractor):
    result = await collect_company_engagement(
        mock_extractor,
        "testcorp",
        limit=999,
        comment_limit=999,
        reactor_limit=999,
        include_reactors=True,
        delay_range=(0, 0),
    )

    assert result["limits"] == {
        "posts": MAX_POST_LIMIT,
        "comments_per_post": MAX_COMMENT_LIMIT,
        "reactors_per_post": MAX_REACTOR_LIMIT,
    }


@pytest.mark.asyncio
async def test_collect_company_engagement_keeps_partial_results(mock_extractor):
    mock_extractor.get_post_details = AsyncMock(side_effect=RuntimeError("boom"))

    result = await collect_company_engagement(
        mock_extractor,
        "testcorp",
        limit=1,
        include_comments=False,
        delay_range=(0, 0),
    )

    assert len(result["posts"]) == 1
    assert result["posts"][0]["diagnostics"][0]["stage"] == "get_post_details"
    assert result["diagnostics"][0]["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_collect_company_engagement_returns_diagnostic_when_no_feed_posts():
    extractor = SimpleNamespace(
        extract_page=AsyncMock(
            return_value=extracted({"kind": "company", "url": "/company/testcorp/"})
        )
    )

    result = await collect_company_engagement(
        extractor,
        "testcorp",
        delay_range=(0, 0),
    )

    assert result["posts"] == []
    assert result["diagnostics"][0]["error_type"] == "NoFeedPostReferences"


@pytest.mark.asyncio
async def test_collect_company_engagement_normalizes_and_dedupes_post_urls():
    extractor = SimpleNamespace()
    extractor.extract_page = AsyncMock(
        return_value=extracted(
            {
                "kind": "feed_post",
                "url": "/feed/update/urn:li:activity:1/?trackingId=abc",
            },
            {
                "kind": "feed_post",
                "url": "https://www.linkedin.com/feed/update/urn:li:activity:1/",
            },
            {
                "kind": "feed_post",
                "url": "/in/not-a-post/",
            },
        )
    )
    extractor.get_post_details = AsyncMock(
        side_effect=lambda post_url: {"post_url": post_url}
    )
    extractor.get_post_comments = AsyncMock(return_value={"comments": []})
    extractor.get_post_reactors = AsyncMock(return_value={"reactors": []})

    result = await collect_company_engagement(
        extractor,
        "testcorp",
        include_comments=False,
        delay_range=(0, 0),
    )

    assert result["post_urls"] == [
        "https://www.linkedin.com/feed/update/urn:li:activity:1/"
    ]
    extractor.get_post_details.assert_awaited_once_with(
        "https://www.linkedin.com/feed/update/urn:li:activity:1/"
    )


@pytest.mark.asyncio
async def test_collect_company_engagement_returns_diagnostic_on_discovery_error():
    extractor = SimpleNamespace(extract_page=AsyncMock(side_effect=ValueError("bad")))

    result = await collect_company_engagement(
        extractor,
        "testcorp",
        delay_range=(0, 0),
    )

    assert result["posts"] == []
    assert result["diagnostics"][0]["stage"] == "discover_company_posts"
    assert result["diagnostics"][0]["error_type"] == "ValueError"
