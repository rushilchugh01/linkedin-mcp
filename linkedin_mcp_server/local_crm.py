"""Local SQLite CRM/observability store for scraped LinkedIn data."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import urlparse, urlunparse

from linkedin_mcp_server.scraping.post import (
    normalize_post_url as _normalize_scraped_post_url,
)

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_LINKEDIN_ORIGIN = "https://www.linkedin.com"


def local_crm_enabled() -> bool:
    """Return whether local CRM recording is enabled for this process."""
    configured = os.getenv("LINKEDIN_LOCAL_CRM", "").strip().lower()
    if configured in _TRUTHY:
        return True
    if configured in _FALSY:
        return False
    return _repo_root().joinpath(".git").exists()


def local_crm_db_path() -> Path:
    """Return the configured local CRM database path."""
    configured = os.getenv("LINKEDIN_LOCAL_CRM_DB", "").strip()
    if configured:
        return Path(configured).expanduser()
    if _repo_root().joinpath(".git").exists():
        return _repo_root() / "data" / "local-crm.sqlite3"
    return Path.home() / ".linkedin-mcp" / "crm.sqlite3"


def record_tool_result(
    tool_name: str,
    arguments: dict[str, Any] | None,
    result: Any,
) -> None:
    """Record a successful tool result into the local CRM store when enabled."""
    if not local_crm_enabled():
        return

    try:
        store = LocalCrmStore(local_crm_db_path())
        store.record_tool_result(tool_name, arguments or {}, result)
    except Exception:
        logger.debug("Local CRM recording failed for tool %s", tool_name, exc_info=True)


class LocalCrmStore:
    """Small SQLite-backed store for local lead-generation observability."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record_tool_result(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
    ) -> None:
        observed_at = _utcnow()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._initialize(conn)
            tool_run_id = self._insert_tool_run(
                conn, tool_name, arguments, result, observed_at
            )
            self._record_payload(conn, tool_name, result, observed_at, tool_run_id)

    def _initialize(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tool_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                result_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS visits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_run_id INTEGER,
                observed_at TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                url TEXT,
                source_url TEXT,
                metadata_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS profiles (
                profile_url TEXT PRIMARY KEY,
                name TEXT,
                headline TEXT,
                connection_status TEXT,
                connection_degree TEXT,
                source_tool TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS companies (
                company_url TEXT PRIMARY KEY,
                name TEXT,
                source_tool TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS posts (
                post_url TEXT PRIMARY KEY,
                activity_urn TEXT,
                activity_id TEXT,
                author_name TEXT,
                author_profile_url TEXT,
                author_headline TEXT,
                post_text TEXT,
                reaction_count INTEGER,
                comment_count INTEGER,
                repost_count INTEGER,
                source_tool TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_url TEXT NOT NULL,
                commenter_profile_url TEXT,
                commenter_name TEXT,
                commenter_headline TEXT,
                comment_text TEXT,
                like_count INTEGER,
                reply_count INTEGER,
                observed_at TEXT NOT NULL,
                approx_timestamp TEXT,
                source_tool TEXT,
                payload_json TEXT NOT NULL,
                UNIQUE(post_url, commenter_profile_url, comment_text)
            );

            CREATE TABLE IF NOT EXISTS reactors (
                post_url TEXT NOT NULL,
                reactor_profile_url TEXT NOT NULL,
                reactor_name TEXT,
                reactor_headline TEXT,
                reaction_type TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                source_tool TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(post_url, reactor_profile_url, reaction_type)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_reactors_post_profile
                ON reactors(post_url, reactor_profile_url);

            CREATE TABLE IF NOT EXISTS profile_post_edges (
                profile_url TEXT NOT NULL,
                post_url TEXT NOT NULL,
                relationship TEXT NOT NULL,
                tool_run_id INTEGER,
                source_tool TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(profile_url, post_url, relationship)
            );

            CREATE TABLE IF NOT EXISTS company_post_edges (
                company_url TEXT NOT NULL,
                post_url TEXT NOT NULL,
                relationship TEXT NOT NULL,
                tool_run_id INTEGER,
                source_tool TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(company_url, post_url, relationship)
            );
            """
        )
        _ensure_column(conn, "visits", "tool_run_id", "INTEGER")
        _ensure_column(conn, "profile_post_edges", "tool_run_id", "INTEGER")
        _ensure_column(conn, "company_post_edges", "tool_run_id", "INTEGER")

    def _insert_tool_run(
        self,
        conn: sqlite3.Connection,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        observed_at: str,
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO tool_runs (
                observed_at, tool_name, arguments_json, result_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                observed_at,
                tool_name,
                _json(arguments),
                _json(_trim_payload(result)),
            ),
        )
        return int(cursor.lastrowid)

    def _record_payload(
        self,
        conn: sqlite3.Connection,
        tool_name: str,
        result: Any,
        observed_at: str,
        tool_run_id: int,
    ) -> None:
        if not isinstance(result, dict):
            return

        self._record_profile_result(conn, tool_name, result, observed_at, tool_run_id)
        company_url = self._record_company_result(
            conn, tool_name, result, observed_at, tool_run_id
        )

        for profile in _iter_profiles(result):
            self._upsert_profile(conn, tool_name, profile, observed_at, tool_run_id)

        for company in _iter_companies(result):
            self._upsert_company(conn, tool_name, company, observed_at, tool_run_id)

        for post in _iter_posts(result):
            post_url = self._upsert_post(conn, tool_name, post, observed_at, tool_run_id)
            if company_url and post_url:
                self._upsert_company_post_edge(
                    conn,
                    tool_name,
                    company_url=company_url,
                    post_url=post_url,
                    relationship="discovered_from_company_page",
                    payload=post,
                    observed_at=observed_at,
                    tool_run_id=tool_run_id,
                )

        for comment in _iter_comments(result):
            self._insert_comment(conn, tool_name, comment, observed_at, tool_run_id)

        for reactor in _iter_reactors(result):
            self._upsert_reactor(conn, tool_name, reactor, observed_at, tool_run_id)

    def _record_profile_result(
        self,
        conn: sqlite3.Connection,
        tool_name: str,
        result: dict[str, Any],
        observed_at: str,
        tool_run_id: int,
    ) -> None:
        profile_url = _normalize_profile_url(str(result.get("url") or ""))
        if not profile_url:
            return
        lines = _section_lines(result, "main_profile")
        connection = result.get("connection") if isinstance(result.get("connection"), dict) else {}
        self._upsert_profile(
            conn,
            tool_name,
            {
                "profile_url": profile_url,
                "name": lines[0] if lines else "",
                "headline": lines[1] if len(lines) > 1 else "",
                "connection_status": connection.get("status") or "",
                "connection_degree": connection.get("degree") or "",
                "payload": _trim_payload(result),
            },
            observed_at,
            tool_run_id,
        )

    def _record_company_result(
        self,
        conn: sqlite3.Connection,
        tool_name: str,
        result: dict[str, Any],
        observed_at: str,
        tool_run_id: int,
    ) -> str:
        company_url = _normalize_company_url(
            str(
                result.get("url")
                or result.get("company_url")
                or result.get("company_posts_url")
                or ""
            )
        )
        if not company_url:
            return ""
        lines = _section_lines(result, "about")
        self._upsert_company(
            conn,
            tool_name,
            {
                "company_url": company_url,
                "name": lines[0] if lines else str(result.get("company_name") or ""),
                "payload": _trim_payload(result),
            },
            observed_at,
            tool_run_id,
        )
        return company_url

    def _upsert_profile(
        self,
        conn: sqlite3.Connection,
        tool_name: str,
        profile: dict[str, Any],
        observed_at: str,
        tool_run_id: int,
    ) -> None:
        profile_url = _normalize_profile_url(str(profile.get("profile_url") or ""))
        if not profile_url:
            return
        conn.execute(
            """
            INSERT INTO profiles (
                profile_url, name, headline, connection_status,
                connection_degree, source_tool, first_seen_at, last_seen_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_url) DO UPDATE SET
                name = COALESCE(NULLIF(excluded.name, ''), profiles.name),
                headline = COALESCE(NULLIF(excluded.headline, ''), profiles.headline),
                connection_status = COALESCE(NULLIF(excluded.connection_status, ''), profiles.connection_status),
                connection_degree = COALESCE(NULLIF(excluded.connection_degree, ''), profiles.connection_degree),
                source_tool = excluded.source_tool,
                last_seen_at = excluded.last_seen_at,
                payload_json = excluded.payload_json
            """,
            (
                profile_url,
                str(profile.get("name") or ""),
                str(profile.get("headline") or ""),
                str(profile.get("connection_status") or ""),
                str(profile.get("connection_degree") or ""),
                tool_name,
                observed_at,
                observed_at,
                _json(profile.get("payload") or profile),
            ),
        )
        self._insert_visit(
            conn,
            tool_name,
            "profile",
            profile_url,
            profile_url,
            None,
            profile,
            observed_at,
            tool_run_id,
        )

    def _upsert_company(
        self,
        conn: sqlite3.Connection,
        tool_name: str,
        company: dict[str, Any],
        observed_at: str,
        tool_run_id: int,
    ) -> None:
        company_url = _normalize_company_url(str(company.get("company_url") or ""))
        if not company_url:
            return
        conn.execute(
            """
            INSERT INTO companies (
                company_url, name, source_tool, first_seen_at, last_seen_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_url) DO UPDATE SET
                name = COALESCE(NULLIF(excluded.name, ''), companies.name),
                source_tool = excluded.source_tool,
                last_seen_at = excluded.last_seen_at,
                payload_json = excluded.payload_json
            """,
            (
                company_url,
                str(company.get("name") or ""),
                tool_name,
                observed_at,
                observed_at,
                _json(company.get("payload") or company),
            ),
        )
        self._insert_visit(
            conn,
            tool_name,
            "company",
            company_url,
            company_url,
            None,
            company,
            observed_at,
            tool_run_id,
        )

    def _upsert_post(
        self,
        conn: sqlite3.Connection,
        tool_name: str,
        post: dict[str, Any],
        observed_at: str,
        tool_run_id: int,
    ) -> str:
        post_url = _normalize_post_url(str(post.get("post_url") or post.get("url") or ""))
        if not post_url:
            return ""
        engagement = post.get("engagement") if isinstance(post.get("engagement"), dict) else {}
        post_text = post.get("post_text")
        if not post_text and isinstance(post.get("sections"), dict):
            post_text = post["sections"].get("post")
        conn.execute(
            """
            INSERT INTO posts (
                post_url, activity_urn, activity_id, author_name,
                author_profile_url, author_headline, post_text, reaction_count,
                comment_count, repost_count, source_tool, first_seen_at,
                last_seen_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_url) DO UPDATE SET
                activity_urn = COALESCE(NULLIF(excluded.activity_urn, ''), posts.activity_urn),
                activity_id = COALESCE(NULLIF(excluded.activity_id, ''), posts.activity_id),
                author_name = COALESCE(NULLIF(excluded.author_name, ''), posts.author_name),
                author_profile_url = COALESCE(NULLIF(excluded.author_profile_url, ''), posts.author_profile_url),
                author_headline = COALESCE(NULLIF(excluded.author_headline, ''), posts.author_headline),
                post_text = COALESCE(NULLIF(excluded.post_text, ''), posts.post_text),
                reaction_count = COALESCE(excluded.reaction_count, posts.reaction_count),
                comment_count = COALESCE(excluded.comment_count, posts.comment_count),
                repost_count = COALESCE(excluded.repost_count, posts.repost_count),
                source_tool = excluded.source_tool,
                last_seen_at = excluded.last_seen_at,
                payload_json = excluded.payload_json
            """,
            (
                post_url,
                str(post.get("activity_urn") or ""),
                str(post.get("activity_id") or ""),
                str(post.get("author_name") or ""),
                _normalize_profile_url(str(post.get("author_profile_url") or "")),
                str(post.get("author_headline") or ""),
                str(post_text or ""),
                _int_or_none(post.get("reaction_count", engagement.get("reaction_count"))),
                _int_or_none(post.get("comment_count", engagement.get("comment_count"))),
                _int_or_none(post.get("repost_count", engagement.get("repost_count"))),
                tool_name,
                observed_at,
                observed_at,
                _json(post.get("payload") or post),
            ),
        )
        author_profile_url = _normalize_profile_url(
            str(post.get("author_profile_url") or "")
        )
        if author_profile_url:
            self._upsert_profile(
                conn,
                tool_name,
                {
                    "profile_url": author_profile_url,
                    "name": post.get("author_name") or "",
                    "headline": post.get("author_headline") or "",
                    "payload": _trim_payload(post),
                },
                observed_at,
                tool_run_id,
            )
            self._upsert_profile_post_edge(
                conn,
                tool_name,
                profile_url=author_profile_url,
                post_url=post_url,
                relationship="author",
                payload=post,
                observed_at=observed_at,
                tool_run_id=tool_run_id,
            )
        self._insert_visit(
            conn,
            tool_name,
            "post",
            post_url,
            post_url,
            None,
            post,
            observed_at,
            tool_run_id,
        )
        return post_url

    def _insert_comment(
        self,
        conn: sqlite3.Connection,
        tool_name: str,
        comment: dict[str, Any],
        observed_at: str,
        tool_run_id: int,
    ) -> None:
        post_url = _normalize_post_url(str(comment.get("post_url") or ""))
        comment_text = str(comment.get("comment_text") or "")
        if not post_url or not comment_text:
            return
        conn.execute(
            """
            INSERT INTO comments (
                post_url, commenter_profile_url, commenter_name, commenter_headline,
                comment_text, like_count, reply_count, observed_at,
                approx_timestamp, source_tool, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_url, commenter_profile_url, comment_text) DO UPDATE SET
                commenter_name = COALESCE(NULLIF(excluded.commenter_name, ''), comments.commenter_name),
                commenter_headline = COALESCE(NULLIF(excluded.commenter_headline, ''), comments.commenter_headline),
                like_count = COALESCE(excluded.like_count, comments.like_count),
                reply_count = COALESCE(excluded.reply_count, comments.reply_count),
                observed_at = excluded.observed_at,
                approx_timestamp = COALESCE(NULLIF(excluded.approx_timestamp, ''), comments.approx_timestamp),
                source_tool = excluded.source_tool,
                payload_json = excluded.payload_json
            """,
            (
                post_url,
                _normalize_profile_url(str(comment.get("commenter_profile_url") or "")),
                str(comment.get("commenter_name") or ""),
                str(comment.get("commenter_headline") or ""),
                comment_text,
                _int_or_none(comment.get("like_count")),
                _int_or_none(comment.get("reply_count")),
                str(comment.get("observed_at") or observed_at),
                str(comment.get("approx_timestamp") or ""),
                tool_name,
                _json(comment),
            ),
        )
        profile_url = _normalize_profile_url(
            str(comment.get("commenter_profile_url") or "")
        )
        if profile_url:
            self._upsert_profile(
                conn,
                tool_name,
                {
                    "profile_url": profile_url,
                    "name": comment.get("commenter_name") or "",
                    "headline": comment.get("commenter_headline") or "",
                    "payload": _trim_payload(comment),
                },
                observed_at,
                tool_run_id,
            )
            self._upsert_profile_post_edge(
                conn,
                tool_name,
                profile_url=profile_url,
                post_url=post_url,
                relationship="commenter",
                payload=comment,
                observed_at=observed_at,
                tool_run_id=tool_run_id,
            )

    def _upsert_reactor(
        self,
        conn: sqlite3.Connection,
        tool_name: str,
        reactor: dict[str, Any],
        observed_at: str,
        tool_run_id: int,
    ) -> None:
        post_url = _normalize_post_url(str(reactor.get("post_url") or ""))
        profile_url = _normalize_profile_url(str(reactor.get("reactor_profile_url") or ""))
        if not post_url or not profile_url:
            return
        reaction_type = str(reactor.get("reaction_type") or "")
        conn.execute(
            """
            INSERT INTO reactors (
                post_url, reactor_profile_url, reactor_name, reactor_headline,
                reaction_type, first_seen_at, last_seen_at, source_tool,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_url, reactor_profile_url) DO UPDATE SET
                reactor_name = COALESCE(NULLIF(excluded.reactor_name, ''), reactors.reactor_name),
                reactor_headline = COALESCE(NULLIF(excluded.reactor_headline, ''), reactors.reactor_headline),
                reaction_type = COALESCE(NULLIF(excluded.reaction_type, ''), reactors.reaction_type),
                last_seen_at = excluded.last_seen_at,
                source_tool = excluded.source_tool,
                payload_json = excluded.payload_json
            """,
            (
                post_url,
                profile_url,
                str(reactor.get("reactor_name") or ""),
                str(reactor.get("reactor_headline") or ""),
                reaction_type,
                observed_at,
                observed_at,
                tool_name,
                _json(reactor),
            ),
        )
        self._upsert_profile(
            conn,
            tool_name,
            {
                "profile_url": profile_url,
                "name": reactor.get("reactor_name") or "",
                "headline": reactor.get("reactor_headline") or "",
                "payload": _trim_payload(reactor),
            },
            observed_at,
            tool_run_id,
        )
        self._upsert_profile_post_edge(
            conn,
            tool_name,
            profile_url=profile_url,
            post_url=post_url,
            relationship="reactor",
            payload=reactor,
            observed_at=observed_at,
            tool_run_id=tool_run_id,
        )

    def _upsert_profile_post_edge(
        self,
        conn: sqlite3.Connection,
        tool_name: str,
        *,
        profile_url: str,
        post_url: str,
        relationship: str,
        payload: dict[str, Any],
        observed_at: str,
        tool_run_id: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO profile_post_edges (
                profile_url, post_url, relationship, tool_run_id, source_tool,
                first_seen_at, last_seen_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_url, post_url, relationship) DO UPDATE SET
                tool_run_id = excluded.tool_run_id,
                source_tool = excluded.source_tool,
                last_seen_at = excluded.last_seen_at,
                payload_json = excluded.payload_json
            """,
            (
                profile_url,
                post_url,
                relationship,
                tool_run_id,
                tool_name,
                observed_at,
                observed_at,
                _json(_trim_payload(payload)),
            ),
        )

    def _upsert_company_post_edge(
        self,
        conn: sqlite3.Connection,
        tool_name: str,
        *,
        company_url: str,
        post_url: str,
        relationship: str,
        payload: dict[str, Any],
        observed_at: str,
        tool_run_id: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO company_post_edges (
                company_url, post_url, relationship, tool_run_id, source_tool,
                first_seen_at, last_seen_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_url, post_url, relationship) DO UPDATE SET
                tool_run_id = excluded.tool_run_id,
                source_tool = excluded.source_tool,
                last_seen_at = excluded.last_seen_at,
                payload_json = excluded.payload_json
            """,
            (
                company_url,
                post_url,
                relationship,
                tool_run_id,
                tool_name,
                observed_at,
                observed_at,
                _json(_trim_payload(payload)),
            ),
        )

    def _insert_visit(
        self,
        conn: sqlite3.Connection,
        tool_name: str,
        entity_type: str,
        entity_key: str,
        url: str | None,
        source_url: str | None,
        metadata: dict[str, Any],
        observed_at: str,
        tool_run_id: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO visits (
                tool_run_id, observed_at, tool_name, entity_type, entity_key, url,
                source_url, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tool_run_id,
                observed_at,
                tool_name,
                entity_type,
                entity_key,
                url,
                source_url,
                _json(metadata),
            ),
        )


def _iter_profiles(payload: Any) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            profile_url = (
                value.get("profile_url")
                or value.get("author_profile_url")
                or value.get("commenter_profile_url")
                or value.get("reactor_profile_url")
                or value.get("url")
            )
            normalized = _normalize_profile_url(str(profile_url or ""))
            if normalized:
                profiles.append(
                    {
                        "profile_url": normalized,
                        "name": value.get("name")
                        or value.get("author_name")
                        or value.get("commenter_name")
                        or value.get("reactor_name")
                        or value.get("text")
                        or "",
                        "headline": value.get("headline")
                        or value.get("author_headline")
                        or value.get("commenter_headline")
                        or value.get("reactor_headline")
                        or value.get("context")
                        or "",
                        "payload": _trim_payload(value),
                    }
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return profiles


def _iter_companies(payload: Any) -> list[dict[str, Any]]:
    companies: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            company_url = (
                value.get("company_url")
                or value.get("company_posts_url")
                or value.get("url")
            )
            normalized = _normalize_company_url(str(company_url or ""))
            if normalized:
                companies.append(
                    {
                        "company_url": normalized,
                        "name": value.get("company_name") or value.get("name") or "",
                        "payload": _trim_payload(value),
                    }
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return companies


def _iter_posts(payload: Any) -> list[dict[str, Any]]:
    posts: list[dict[str, Any]] = []

    def add(value: dict[str, Any]) -> None:
        if _is_post_payload(value) and _normalize_post_url(
            str(value.get("post_url") or value.get("url") or "")
        ):
            posts.append({**value, "payload": _trim_payload(value)})

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("summary"), dict):
                add(value["summary"])
            if isinstance(value.get("details"), dict):
                add(value["details"])
            add(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return posts


def _iter_comments(payload: Any) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("comments"), list):
                for comment in value["comments"]:
                    if isinstance(comment, dict):
                        comments.append(comment)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return comments


def _iter_reactors(payload: Any) -> list[dict[str, Any]]:
    reactors: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("reactors"), list):
                for reactor in value["reactors"]:
                    if isinstance(reactor, dict):
                        reactors.append(reactor)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return reactors


def _is_post_payload(value: dict[str, Any]) -> bool:
    if not isinstance(value, dict):
        return False
    if "comments" in value or "reactors" in value:
        return False
    if any(
        key in value
        for key in (
            "activity_urn",
            "activity_id",
            "author_name",
            "author_profile_url",
            "post_text",
            "reaction_count",
            "comment_count",
            "repost_count",
            "engagement",
        )
    ):
        return True
    sections = value.get("sections")
    return isinstance(sections, dict) and isinstance(sections.get("post"), str)


def _section_lines(result: dict[str, Any], section_name: str) -> list[str]:
    sections = result.get("sections")
    if not isinstance(sections, dict):
        return []
    text = sections.get(section_name)
    if not isinstance(text, str):
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _normalize_profile_url(value: str) -> str:
    path = _normalize_linkedin_path(value)
    if not path or not path.startswith("/in/"):
        return ""
    username = path.split("/", 3)[2]
    return f"{_LINKEDIN_ORIGIN}/in/{username}/"


def _normalize_company_url(value: str) -> str:
    path = _normalize_linkedin_path(value)
    if not path or not path.startswith("/company/"):
        return ""
    slug = path.split("/", 3)[2]
    return f"{_LINKEDIN_ORIGIN}/company/{slug}/"


def _normalize_post_url(value: str) -> str:
    try:
        return _normalize_scraped_post_url(value).url
    except ValueError:
        return ""


def _normalize_linkedin_path(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    if raw.startswith("/"):
        parsed = urlparse(f"{_LINKEDIN_ORIGIN}{raw}")
    else:
        parsed = urlparse(raw)
    if parsed.netloc and parsed.netloc.lower() not in {"linkedin.com", "www.linkedin.com"}:
        return ""
    path = parsed.path
    if not path:
        return ""
    normalized = urlunparse(("", "", path, "", "", ""))
    return normalized if normalized.startswith("/") else f"/{normalized}"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _trim_payload(value: Any, *, max_text_length: int = 5000) -> Any:
    if isinstance(value, str):
        return value[:max_text_length]
    if isinstance(value, list):
        return [_trim_payload(item, max_text_length=max_text_length) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _trim_payload(child, max_text_length=max_text_length)
            for key, child in value.items()
        }
    return value


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]
