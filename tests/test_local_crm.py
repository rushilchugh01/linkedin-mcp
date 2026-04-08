import json
import sqlite3

import linkedin_mcp_server.local_crm as crm

LocalCrmStore = crm.LocalCrmStore
local_crm_db_path = crm.local_crm_db_path
local_crm_enabled = crm.local_crm_enabled
record_tool_result = crm.record_tool_result


def _fetch_all(db_path, query: str):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query).fetchall()]


def _feed_engagement_payload():
    post_url = "https://www.linkedin.com/feed/update/urn:li:share:123/"
    return {
        "feed_url": "https://www.linkedin.com/feed/",
        "keywords": ["legal ai"],
        "discovered_posts": [
            {
                "post_url": post_url,
                "activity_urn": "urn:li:share:123",
                "activity_id": "123",
                "author_name": "Author Person",
                "author_profile_url": "https://www.linkedin.com/in/author/?miniProfileUrn=abc",
                "author_headline": "General Counsel",
                "post_text": "Legal AI changes contract review.",
                "reaction_count": 5,
                "comment_count": 1,
                "repost_count": 0,
                "matched_keywords": ["legal ai"],
            }
        ],
        "references": {
            "posts": [
                {
                    "kind": "profile",
                    "url": "/in/reference-profile/?trk=feed",
                    "text": "Reference Person",
                    "context": "Legal AI founder",
                }
            ]
        },
        "posts": [
            {
                "post_url": post_url,
                "summary": {
                    "post_url": post_url,
                    "activity_urn": "urn:li:share:123",
                    "activity_id": "123",
                    "author_name": "Author Person",
                    "author_profile_url": "https://www.linkedin.com/in/author/",
                    "author_headline": "General Counsel",
                    "post_text": "Legal AI changes contract review.",
                    "reaction_count": 5,
                    "comment_count": 1,
                    "repost_count": 0,
                },
                "details": {
                    "url": post_url,
                    "post_url": "/feed/update/urn:li:share:123/",
                    "activity_urn": "urn:li:share:123",
                    "activity_id": "123",
                    "sections": {"post": "Legal AI changes contract review."},
                    "engagement": {
                        "reaction_count": 5,
                        "comment_count": 1,
                        "repost_count": 0,
                    },
                },
                "comments": {
                    "post_url": "/feed/update/urn:li:share:123/",
                    "comments": [
                        {
                            "post_url": "/feed/update/urn:li:share:123/",
                            "commenter_profile_url": "/in/commenter/",
                            "commenter_name": "Comment Person",
                            "commenter_headline": "Legal Ops",
                            "comment_text": "This is useful for playbooks.",
                            "like_count": 2,
                            "reply_count": 1,
                        }
                    ],
                },
                "reactors": {
                    "post_url": "/feed/update/urn:li:share:123/",
                    "reactors": [
                        {
                            "post_url": "/feed/update/urn:li:share:123/",
                            "reactor_profile_url": "/in/reactor/",
                            "reactor_name": "React Person",
                            "reactor_headline": "Compliance",
                            "reaction_type": "Like",
                        }
                    ],
                },
            }
        ],
        "diagnostics": [],
    }


def test_local_crm_defaults_off_when_unset_outside_repo(monkeypatch, tmp_path):
    monkeypatch.delenv("LINKEDIN_LOCAL_CRM", raising=False)
    monkeypatch.delenv("LINKEDIN_LOCAL_CRM_DB", raising=False)
    monkeypatch.setattr(crm, "_repo_root", lambda: tmp_path / "outside-repo")

    assert local_crm_enabled() is False
    assert local_crm_db_path().name == "crm.sqlite3"


def test_record_tool_result_noops_when_disabled(monkeypatch, tmp_path):
    db_path = tmp_path / "crm.sqlite3"
    monkeypatch.delenv("LINKEDIN_LOCAL_CRM", raising=False)
    monkeypatch.setenv("LINKEDIN_LOCAL_CRM_DB", str(db_path))
    monkeypatch.setattr(crm, "_repo_root", lambda: tmp_path / "outside-repo")

    record_tool_result("feed_engagement", {}, _feed_engagement_payload())

    assert not db_path.exists()


def test_local_crm_records_feed_profiles_posts_text_and_edges(tmp_path):
    db_path = tmp_path / "crm.sqlite3"
    store = LocalCrmStore(db_path)

    store.record_tool_result(
        "feed_engagement",
        {"keywords": ["legal ai"]},
        _feed_engagement_payload(),
    )
    store.record_tool_result(
        "feed_engagement",
        {"keywords": ["legal ai"]},
        _feed_engagement_payload(),
    )

    profiles = _fetch_all(db_path, "SELECT profile_url, name FROM profiles")
    posts = _fetch_all(db_path, "SELECT post_url, post_text FROM posts")
    post_payloads = _fetch_all(db_path, "SELECT payload_json FROM posts")
    comments = _fetch_all(
        db_path,
        "SELECT post_url, commenter_profile_url, comment_text, like_count, reply_count FROM comments",
    )
    reactors = _fetch_all(
        db_path,
        "SELECT post_url, reactor_profile_url, reaction_type FROM reactors",
    )
    edges = _fetch_all(
        db_path,
        """
        SELECT profile_url, post_url, relationship
        FROM profile_post_edges
        ORDER BY relationship, profile_url
        """,
    )
    tool_runs = _fetch_all(db_path, "SELECT tool_name FROM tool_runs")

    assert len(tool_runs) == 2
    assert len(profiles) == 4
    assert len(posts) == 1
    assert len(comments) == 1
    assert len(reactors) == 1
    assert len(edges) == 3
    assert {profile["profile_url"] for profile in profiles} == {
        "https://www.linkedin.com/in/author/",
        "https://www.linkedin.com/in/commenter/",
        "https://www.linkedin.com/in/reference-profile/",
        "https://www.linkedin.com/in/reactor/",
    }
    assert "commenter_profile_url" not in json.loads(post_payloads[0]["payload_json"])
    assert posts == [
        {
            "post_url": "https://www.linkedin.com/feed/update/urn:li:share:123/",
            "post_text": "Legal AI changes contract review.",
        }
    ]
    assert comments == [
        {
            "post_url": "https://www.linkedin.com/feed/update/urn:li:share:123/",
            "commenter_profile_url": "https://www.linkedin.com/in/commenter/",
            "comment_text": "This is useful for playbooks.",
            "like_count": 2,
            "reply_count": 1,
        }
    ]
    assert reactors == [
        {
            "post_url": "https://www.linkedin.com/feed/update/urn:li:share:123/",
            "reactor_profile_url": "https://www.linkedin.com/in/reactor/",
            "reaction_type": "Like",
        }
    ]
    assert edges == [
        {
            "profile_url": "https://www.linkedin.com/in/author/",
            "post_url": "https://www.linkedin.com/feed/update/urn:li:share:123/",
            "relationship": "author",
        },
        {
            "profile_url": "https://www.linkedin.com/in/commenter/",
            "post_url": "https://www.linkedin.com/feed/update/urn:li:share:123/",
            "relationship": "commenter",
        },
        {
            "profile_url": "https://www.linkedin.com/in/reactor/",
            "post_url": "https://www.linkedin.com/feed/update/urn:li:share:123/",
            "relationship": "reactor",
        },
    ]


def test_local_crm_records_company_engagement_edges(tmp_path):
    db_path = tmp_path / "crm.sqlite3"
    store = LocalCrmStore(db_path)

    store.record_tool_result(
        "company_engagement",
        {"company_name": "exampleco"},
        {
            "company_name": "exampleco",
            "company_posts_url": "https://www.linkedin.com/company/exampleco/posts/",
            "posts": [
                {
                    "post_url": "/feed/update/urn:li:ugcPost:456/",
                    "details": {
                        "post_url": "urn:li:ugcPost:456",
                        "activity_urn": "urn:li:ugcPost:456",
                        "activity_id": "456",
                        "sections": {"post": "Company post about compliance ops."},
                    },
                }
            ],
        },
    )

    companies = _fetch_all(db_path, "SELECT company_url, name FROM companies")
    posts = _fetch_all(db_path, "SELECT post_url, post_text FROM posts")
    company_edges = _fetch_all(
        db_path,
        """
        SELECT company_url, post_url, relationship
        FROM company_post_edges
        """,
    )

    assert companies == [
        {
            "company_url": "https://www.linkedin.com/company/exampleco/",
            "name": "exampleco",
        }
    ]
    assert posts == [
        {
            "post_url": "https://www.linkedin.com/feed/update/urn:li:ugcPost:456/",
            "post_text": "Company post about compliance ops.",
        }
    ]
    assert company_edges == [
        {
            "company_url": "https://www.linkedin.com/company/exampleco/",
            "post_url": "https://www.linkedin.com/feed/update/urn:li:ugcPost:456/",
            "relationship": "discovered_from_company_page",
        }
    ]


def test_local_crm_reactor_type_updates_without_duplicate(tmp_path):
    db_path = tmp_path / "crm.sqlite3"
    store = LocalCrmStore(db_path)
    base_payload = {
        "post_url": "/feed/update/urn:li:activity:789/",
        "reactors": [
            {
                "post_url": "/feed/update/urn:li:activity:789/",
                "reactor_profile_url": "/in/reactor/",
                "reactor_name": "React Person",
                "reaction_type": "",
            }
        ],
    }
    liked_payload = {
        **base_payload,
        "reactors": [{**base_payload["reactors"][0], "reaction_type": "Like"}],
    }

    store.record_tool_result("get_post_reactors", {}, base_payload)
    store.record_tool_result("get_post_reactors", {}, liked_payload)

    reactors = _fetch_all(
        db_path,
        "SELECT reactor_profile_url, reaction_type FROM reactors",
    )

    assert reactors == [
        {
            "reactor_profile_url": "https://www.linkedin.com/in/reactor/",
            "reaction_type": "Like",
        }
    ]


def test_local_crm_records_profile_contact_info_and_preserves_full_payload(tmp_path):
    db_path = tmp_path / "crm.sqlite3"
    store = LocalCrmStore(db_path)
    profile_url = "https://www.linkedin.com/in/person/"
    profile_payload = {
        "url": profile_url,
        "sections": {
            "main_profile": "Person Name\n\n· 1st\n\nGeneral Counsel",
            "contact_info": "Contact info\nEmail\nperson@example.com",
        },
        "connection": {
            "status": "already_connected",
            "degree": "1st",
            "is_connected": True,
            "is_pending": False,
            "is_connectable": False,
        },
        "contact_info": {
            "emails": ["person@example.com"],
            "phones": ["+1 555 123 4567"],
            "profile_urls": [profile_url],
            "websites": [],
            "connected_since": "Mar 26, 2026",
        },
        "references": {
            "contact_info": [
                {
                    "kind": "person",
                    "url": "/in/person/",
                    "text": "Person Name",
                }
            ]
        },
    }

    store.record_tool_result("get_person_profile", {}, profile_payload)
    store.record_tool_result(
        "feed_engagement",
        {},
        {
            "references": {
                "posts": [
                    {
                        "kind": "person",
                        "url": "/in/person/",
                        "text": "Reference Person",
                        "context": "Reference-only headline",
                    }
                ]
            }
        },
    )

    profiles = _fetch_all(
        db_path,
        """
        SELECT name, headline, email, phone, connected_since, contact_info_json,
               payload_priority, payload_json
        FROM profiles
        WHERE profile_url = 'https://www.linkedin.com/in/person/'
        """,
    )

    assert len(profiles) == 1
    profile = profiles[0]
    assert profile["name"] == "Person Name"
    assert profile["headline"] == "· 1st"
    assert profile["email"] == "person@example.com"
    assert profile["phone"] == "+1 555 123 4567"
    assert profile["connected_since"] == "Mar 26, 2026"
    assert profile["payload_priority"] == 100
    assert json.loads(profile["contact_info_json"]) == profile_payload["contact_info"]
    assert json.loads(profile["payload_json"])["url"] == profile_url
