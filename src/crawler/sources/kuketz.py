"""Kuketz-Forum (kuketz-forum.de, Discourse): IT security, privacy and digital politics.

robots.txt (group ``*``) disallows ``/admin/``, ``/auth/``, ``/session``, ``/search``, ``/my``,
``/g``, ``/badges``, the topic/category RSS feeds and URLs with ``api_key``; the public JSON
endpoint ``/posts.json`` (latest posts of the whole forum) is allowed and needs no login.

Stream source: ``/posts.json`` returns the newest 50 posts; older ones are paged with
``?before=<post id>`` until the previous run. Per post we read only ``id``, ``topic_id``,
``topic_title``, ``topic_slug``, ``post_number``, ``reply_to_post_number``, ``created_at`` and
``cooked`` (the rendered HTML). ``username``, ``name``, ``display_username``, ``avatar_template``,
``user_id``, ``user_title`` and the flair fields are never read. Quotes (``aside.quote``, whose title
holds the quoted user) and ``@mentions`` (``a.mention``) are removed. Only regular posts
(``post_type == 1``) that are not hidden or deleted are kept. One topic = one article.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from crawler.ids import parse_datetime
from crawler.models import ArticleRef, Comment
from crawler.sources.base import StreamItem, StreamSource
from crawler.text import clean_text, html_to_text

SITE = "https://www.kuketz-forum.de"
MAX_PAGES = 6


def cooked_text(cooked: str) -> str:
    soup = BeautifulSoup(cooked or "", "lxml")
    for el in soup.select("aside.quote, aside.onebox, a.mention, a.mention-group, img, .lightbox-wrapper, script"):
        el.decompose()
    return clean_text(html_to_text(str(soup)))


def parse_posts(rows: list[dict[str, Any]], source_id: str = "kuketz") -> list[tuple[ArticleRef, Comment]]:
    by_number = {(r.get("topic_id"), r.get("post_number")): r.get("id") for r in rows}
    out: list[tuple[ArticleRef, Comment]] = []
    for r in rows:
        if r.get("post_type", 1) != 1 or r.get("hidden") or r.get("deleted_at") or r.get("user_deleted"):
            continue
        published = parse_datetime(r.get("created_at"))
        text = cooked_text(r.get("cooked") or "")
        if not r.get("id") or not r.get("topic_id") or not published or not text:
            continue
        parent_id = (
            by_number.get((r.get("topic_id"), r.get("reply_to_post_number"))) if r.get("reply_to_post_number") else None
        )
        ref = ArticleRef(
            url=f"{SITE}/t/{int(r['topic_id'])}",
            source_id=source_id,
            title=clean_text(r.get("topic_title") or ""),
            topics=["Datenschutz & IT-Sicherheit"],
        )
        out.append(
            (
                ref,
                Comment(
                    source_id, str(r["id"]), text, published, native_parent_id=str(parent_id) if parent_id else None
                ),
            )
        )
    return out


class Kuketz(StreamSource):
    id = "kuketz"
    name = "Kuketz-Forum"
    domain = "kuketz-forum.de"
    country = "DE"
    source_type = "news"  # forum topics as articles (the data contract has no forum type)
    topic = "tech"

    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        if max_items <= 0:
            return []
        rows: dict[int, dict[str, Any]] = {}
        before: int | None = None
        for _ in range(MAX_PAGES):
            data = self.http.get_json(f"{SITE}/posts.json", params={"before": before} if before else None) or {}
            page = [r for r in data.get("latest_posts") or [] if isinstance(r, dict) and r.get("id")]
            if not page:
                break
            for r in page:
                rows.setdefault(int(r["id"]), r)
            times = [t for r in page if (t := parse_datetime(r.get("created_at")))]
            if not times or min(times) < since:
                break
            before = min(int(r["id"]) for r in page)
        items: list[StreamItem] = []
        for ref, c in parse_posts(sorted(rows.values(), key=lambda r: -int(r["id"])), self.id):
            if since <= c.published_at <= until:
                items.append(StreamItem(ref, c))
        # topic "published" = its oldest post in this sample (pseudo-article date)
        oldest: dict[str, datetime] = {}
        for i in items:
            oldest[i.article.id] = min(oldest.get(i.article.id, i.comment.published_at), i.comment.published_at)
        for i in items:
            i.article.published_at = oldest[i.article.id]
        return items[:max_items]
