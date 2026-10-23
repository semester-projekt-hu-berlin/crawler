"""Blick (blick.ch, German-language edition): public Viafoura comments API.

Blick embeds Viafoura. The article page contains the container id
(``<vf-conversations vf-container-id="...">``); the widget then reads the comments
anonymously from ``livecomments.viafoura.co``:

1. ``GET /v4/livecomments/{site}/contentcontainer/id?container_id=...``
   -> content container UUID + visible comment count
2. ``GET /v4/livecomments/{site}?container_id=...&limit=100&reply_limit=100&sorted_by=newest``
   -> first page of top-level comments with replies (flat list, ``parent_uuid`` links them)
3. ``GET /v4/livecomments/{site}/{container_uuid}/comments?starting_from={last top-level uuid}``
   -> further pages while ``more_available``.

Only ``content_uuid``, ``parent_uuid``, ``content``, ``date_created``, ``state`` and
``total_likes`` are read; ``actor_uuid`` (the user) is ignored.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from crawler.config import COMMENT_OVERLAP
from crawler.discovery import parse_page_meta
from crawler.ids import parse_datetime
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources.base import IncrementalPager, Source
from crawler.text import clean_text

API = "https://livecomments.viafoura.co/v4/livecomments"
SITE_LOOKUP = "https://tyrion.viafoura.co/v3/sections/sites/www.blick.ch"
DEFAULT_SITE_UUID = "00000000-0000-4000-8000-acd0dfe4436a"
PAGE_LIMIT = 100
PRIORITY_SECTIONS = ("politik", "schweiz", "ausland", "wirtschaft", "sport")
REVISIT_PAGE_LIMIT = 20
MAX_PAGES = 50
_ARTICLE_RE = re.compile(r"^https://www\.blick\.ch/(?!fr/)(?:[a-z0-9-]+/)+[a-z0-9-]+/([a-z0-9]{6,8})$")
_CONTAINER_RE = re.compile(r'vf-container-id="([0-9a-fA-F-]{36})"')


def parse_contents(contents: list[dict[str, Any]], container_uuid: str, source_id: str = "blick") -> list[Comment]:
    out: list[Comment] = []
    for c in contents:
        native_id = c.get("content_uuid")
        if not native_id or c.get("state") not in (None, "visible"):
            continue
        published = parse_datetime(c.get("date_created") or c.get("time"))
        text = clean_text(c.get("content") or "")
        if not published or not text:
            continue
        parent = c.get("parent_uuid")
        if not parent or parent in (container_uuid, c.get("content_container_uuid")):
            parent = None
        likes = c.get("total_likes")
        out.append(
            Comment(
                source_id=source_id,
                native_id=str(native_id),
                text=text,
                published_at=published,
                native_parent_id=str(parent) if parent else None,
                upvotes=int(likes) if isinstance(likes, int) else None,
            )
        )
    return out


def find_container_id(html: str) -> str | None:
    m = _CONTAINER_RE.search(html)
    return m.group(1) if m else None


class Blick(Source):
    id = "blick"
    name = "Blick"
    domain = "blick.ch"
    country = "CH"
    topic = "general"
    news_sitemap = "https://www.blick.ch/news.xml"

    def __init__(self, http):  # noqa: ANN001
        super().__init__(http)
        self._site_uuid: str | None = None

    def canonical_url(self, url: str) -> str | None:
        m = _ARTICLE_RE.match(url)
        return f"https://www.blick.ch/{m.group(1)}" if m else None

    def rank_new(self, refs: list[ArticleRef], limit: int | None = None) -> list[ArticleRef]:
        """Priority sections (from the discovered URL path) first, then newest."""

        def key(ref: ArticleRef) -> tuple:
            path = (ref.discovered_url or "").removeprefix("https://www.blick.ch/")
            section = path.split("/", 1)[0]
            return (section in PRIORITY_SECTIONS, ref.published_at)

        return sorted(refs, key=key, reverse=True)

    def site_uuid(self) -> str:
        if self._site_uuid is None:
            try:
                data = self.http.get_json(SITE_LOOKUP)
                self._site_uuid = data.get("uuid") or DEFAULT_SITE_UUID
            except Exception:  # noqa: BLE001 - fall back to the known value
                self._site_uuid = DEFAULT_SITE_UUID
        return self._site_uuid

    def fetch(self, ref: ArticleRef) -> FetchResult:
        container_id = ref.comment_thread_id
        if not ref.known or not container_id:
            # Article page only on the first crawl (metadata + Viafoura container id).
            resp = self.http.get(ref.url)
            if not ref.known:
                meta = parse_page_meta(BeautifulSoup(resp.text, "lxml"))
                ref.title = meta.title or ref.title
                ref.published_at = meta.published_at or ref.published_at
                if meta.topics:
                    ref.topics = meta.topics
            container_id = find_container_id(resp.text)
        if not container_id:
            return FetchResult(ref, [], 0)
        site = self.site_uuid()
        info = self.http.get_json(
            f"{API}/{site}/contentcontainer/id", params={"container_id": container_id}, allow_404=True
        )
        if not isinstance(info, dict) or not info.get("content_container_uuid"):
            return FetchResult(ref, [], 0, thread_id=container_id)
        total = int(info.get("total_visible_content") or 0)
        if ref.known and ref.known_comment_count == total:
            return FetchResult(ref, [], total, complete=False, unchanged=True, thread_id=container_id)
        if total == 0:
            return FetchResult(ref, [], 0, thread_id=container_id)
        cc_uuid = info["content_container_uuid"]
        since = ref.since(COMMENT_OVERLAP)
        expected = ref.expected_new(total)
        pager = IncrementalPager(ref, since, expected)
        # Revisits usually need only the newest few threads: start with smaller pages.
        limit = PAGE_LIMIT if since is None else min(PAGE_LIMIT, max(REVISIT_PAGE_LIMIT, 2 * (expected or 0)))
        params = {"limit": limit, "reply_limit": PAGE_LIMIT, "sorted_by": "newest"}
        page = self.http.get_json(f"{API}/{site}", params={**params, "container_id": container_id})
        contents: list[dict[str, Any]] = list(page.get("contents") or [])
        complete = True
        for _ in range(MAX_PAGES):
            if not page.get("more_available"):
                break
            if pager.add_page(_times(page.get("contents") or [])):
                complete = False
                break
            top_level = [c for c in page.get("contents") or [] if c.get("parent_uuid") == cc_uuid]
            if not top_level:
                break
            page = self.http.get_json(
                f"{API}/{site}/{cc_uuid}/comments",
                params={**params, "starting_from": top_level[-1]["content_uuid"]},
            )
            contents.extend(page.get("contents") or [])
        return FetchResult(
            ref, parse_contents(contents, cc_uuid, self.id), total, complete=complete, thread_id=container_id
        )


def _times(contents: list[dict[str, Any]]) -> list[datetime]:
    return [t for c in contents if (t := parse_datetime(c.get("date_created") or c.get("time")))]
