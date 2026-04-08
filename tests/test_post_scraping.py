from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from linkedin_mcp_server.scraping.post import (
    approximate_timestamp,
    extract_activity_urn,
    normalize_post_url,
    parse_engagement_counts,
    scrape_post_comments,
    scrape_post_details,
    scrape_post_reactors,
)


def test_normalize_post_url_accepts_absolute_url():
    post = normalize_post_url(
        "https://www.linkedin.com/feed/update/urn:li:activity:123/?trackingId=abc"
    )

    assert post.url == "https://www.linkedin.com/feed/update/urn:li:activity:123/"
    assert post.path == "/feed/update/urn:li:activity:123/"
    assert post.activity_urn == "urn:li:activity:123"
    assert post.activity_id == "123"


def test_normalize_post_url_accepts_relative_path():
    post = normalize_post_url("/feed/update/urn:li:activity:456/")

    assert post.url == "https://www.linkedin.com/feed/update/urn:li:activity:456/"
    assert post.activity_id == "456"


def test_normalize_post_url_accepts_bare_activity_urn():
    post = normalize_post_url("urn:li:activity:789")

    assert post.url == "https://www.linkedin.com/feed/update/urn:li:activity:789/"
    assert post.activity_urn == "urn:li:activity:789"


def test_normalize_post_url_accepts_share_and_ugc_post_urns():
    share = normalize_post_url("https://www.linkedin.com/feed/update/urn:li:share:111/")
    ugc = normalize_post_url("/feed/update/urn:li:ugcPost:222/")

    assert share.url == "https://www.linkedin.com/feed/update/urn:li:share:111/"
    assert share.activity_urn == "urn:li:share:111"
    assert share.activity_id == "111"
    assert ugc.url == "https://www.linkedin.com/feed/update/urn:li:ugcPost:222/"
    assert ugc.activity_urn == "urn:li:ugcPost:222"
    assert ugc.activity_id == "222"


def test_normalize_post_url_rejects_non_linkedin_url():
    with pytest.raises(ValueError, match="LinkedIn URL"):
        normalize_post_url("https://example.com/feed/update/urn:li:activity:123/")


def test_normalize_post_url_rejects_non_post_path():
    with pytest.raises(ValueError, match="/feed/update/"):
        normalize_post_url("https://www.linkedin.com/in/person/")


def test_extract_activity_urn():
    assert (
        extract_activity_urn("https://www.linkedin.com/feed/update/urn:li:activity:42/")
        == "urn:li:activity:42"
    )
    assert (
        extract_activity_urn("https://www.linkedin.com/feed/update/urn:li:share:43/")
        == "urn:li:share:43"
    )
    assert extract_activity_urn("no activity") is None


def test_parse_engagement_counts_direct_reaction_count():
    counts = parse_engagement_counts("10 reactions\n2 comments\n3 reposts")

    assert counts == {
        "reaction_count": 10,
        "comment_count": 2,
        "repost_count": 3,
    }


def test_parse_engagement_counts_others_count():
    counts = parse_engagement_counts("Jane and 9 others\n1 comment")

    assert counts["reaction_count"] == 10
    assert counts["comment_count"] == 1
    assert counts["repost_count"] == 0


def test_approximate_timestamp():
    observed_at = datetime(2026, 4, 8, 12, tzinfo=timezone.utc)

    assert approximate_timestamp("2d", observed_at) == "2026-04-06T12:00:00Z"
    assert approximate_timestamp("not a relative time", observed_at) is None


async def test_scrape_post_details_returns_sections_references_and_engagement():
    extractor = MagicMock()
    extractor.extract_page = AsyncMock(
        return_value=SimpleNamespace(
            text="Example post\n10 reactions\n2 comments",
            references=[{"kind": "person", "url": "/in/test/", "text": "Test"}],
            error=None,
        )
    )

    result = await scrape_post_details(
        extractor,
        "https://www.linkedin.com/feed/update/urn:li:activity:123/",
    )

    extractor.extract_page.assert_awaited_once_with(
        "https://www.linkedin.com/feed/update/urn:li:activity:123/",
        section_name="post",
    )
    assert result["activity_id"] == "123"
    assert result["sections"]["post"] == "Example post\n10 reactions\n2 comments"
    assert result["references"]["post"] == [
        {"kind": "person", "url": "/in/test/", "text": "Test"}
    ]
    assert result["engagement"]["reaction_count"] == 10
    assert result["engagement"]["comment_count"] == 2


async def test_scrape_post_comments_limit_zero_skips_navigation():
    extractor = MagicMock()

    result = await scrape_post_comments(extractor, "urn:li:activity:123", limit=0)

    assert result["comments"] == []
    assert result["comment_count"] == 0
    assert not hasattr(extractor, "_page") or not extractor._page.method_calls


async def test_scrape_post_reactors_limit_zero_skips_navigation():
    extractor = MagicMock()

    result = await scrape_post_reactors(extractor, "urn:li:activity:123", limit=0)

    assert result["reactors"] == []
    assert result["reactor_count"] == 0
    assert not hasattr(extractor, "_page") or not extractor._page.method_calls
