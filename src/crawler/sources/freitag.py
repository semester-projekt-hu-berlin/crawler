"""der Freitag (freitag.de): comments via the site's public Plone REST API.

The article page loads its comments from ``<article url>/@comments`` (JSON, anonymous
``view_comments`` permission). Each item has ``comment_id``, ``in_reply_to``,
``creation_date`` and ``text.data`` (HTML). ``author_name`` / ``author_username`` /
``author_image`` are ignored. Articles come from the Google News sitemap
(``/@@google-news-rss``, listed in robots.txt). robots.txt only disallows the login
endpoints under ``/acl_users/``.
"""

from __future__ import annotations

import re
from typing import Any

from crawler.ids import parse_datetime
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources.base import Source
from crawler.text import clean_text, html_to_text

_ARTICLE_RE = re.compile(r"^https://www\.freitag\.de/autoren/[a-z0-9-]+/[a-z0-9-]+$")
MAX_PAGES = 40


def parse_items(items: list[dict[str, Any]], source_id: str = "freitag") -> list[Comment]:
    out: list[Comment] = []
    for item in items:
        native_id = str(item.get("comment_id") or "")
        published = parse_datetime(item.get("creation_date"))
        body = item.get("text") or {}
        raw = body.get("data") if isinstance(body, dict) else str(body)
        text = clean_text(html_to_text(raw or ""))
        if not native_id or not published or not text:
            continue
        parent = item.get("in_reply_to")
        out.append(Comment(source_id, native_id, text, published, native_parent_id=str(parent) if parent else None))
    return out


class Freitag(Source):
    id = "freitag"
    name = "der Freitag"
    domain = "freitag.de"
    country = "DE"
    topic = "politics"
    news_sitemap = "https://www.freitag.de/@@google-news-rss"

    def canonical_url(self, url: str) -> str | None:
        return url if _ARTICLE_RE.match(url) else None

    def fetch(self, ref: ArticleRef) -> FetchResult:
        url: str | None = f"{ref.url}/@comments"
        items: list[dict[str, Any]] = []
        total: int | None = None
        for _ in range(MAX_PAGES):
            if url is None:
                break
            data = self.http.get_json(url, headers={"Accept": "application/json"}, allow_404=True)
            if not isinstance(data, dict):
                break
            if total is None:
                total = int(data.get("items_total") or 0)
                if ref.known and total == ref.known_comment_count:
                    return FetchResult(ref, [], total, complete=False, unchanged=True)
            items.extend(data.get("items") or [])
            url = (data.get("batching") or {}).get("next")
        return FetchResult(ref, parse_items(items, self.id), total or 0)
