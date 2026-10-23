"""Caschys Blog (stadt-bremerhaven.de): German tech blog, WordPress comments in the article page.

The WordPress REST API is switched off (404) and robots.txt disallows ``/*/feed/`` (which
includes the site-wide comment feed ``/comments/feed/``), so we read the comments from the
public article pages: ``li.comment`` with ``div#comment-<n>``, ``<time datetime>`` and
``.comment-body``; replies are nested in ``ul.children``. ``cite.fn`` (user name) and the
avatar are never read. Articles come from the main feed ``/feed/`` (not covered by
``/*/feed/``). Other disallowed paths: search, ``?replytocom=``, embeds, previews.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from crawler.ids import parse_datetime
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources.base import Source
from crawler.text import clean_text, html_to_text

_ARTICLE_RE = re.compile(r"^https://stadt-bremerhaven\.de/[a-z0-9-]{8,}/$")


def parse_comments(soup: BeautifulSoup, source_id: str = "caschy") -> list[Comment]:
    out: list[Comment] = []
    for li in soup.select("li.comment"):
        box = li.find("div", id=re.compile(r"^comment-\d+$"))
        if box is None:
            continue
        native_id = box["id"].removeprefix("comment-")
        time_el = box.find("time", attrs={"datetime": True})
        body = box.select_one(".comment-body")
        published = parse_datetime(time_el["datetime"]) if time_el else None
        text = clean_text(html_to_text(str(body))) if body else ""
        parent_li = li.find_parent("li", class_="comment")
        parent = None
        if isinstance(parent_li, Tag):
            pbox = parent_li.find("div", id=re.compile(r"^comment-\d+$"))
            parent = pbox["id"].removeprefix("comment-") if pbox else None
        if published and text:
            out.append(Comment(source_id, native_id, text, published, native_parent_id=parent))
    return out


class Caschy(Source):
    id = "caschy"
    name = "Caschys Blog"
    domain = "stadt-bremerhaven.de"
    country = "DE"
    topic = "tech"
    news_sitemap = "https://stadt-bremerhaven.de/feed/"
    feed_type = "rss"

    def canonical_url(self, url: str) -> str | None:
        return url if _ARTICLE_RE.match(url) else None

    def fetch(self, ref: ArticleRef) -> FetchResult:
        resp = self.http.get(ref.url)
        comments = parse_comments(BeautifulSoup(resp.text, "lxml"), self.id)
        unchanged = ref.known and ref.known_comment_count == len(comments)
        return FetchResult(ref, [] if unchanged else comments, len(comments), unchanged=unchanged)
