"""Achse des Guten (achgut.com): reader letters ("Leserbriefe") in the public article page.

Comments are WordPress-style ``<li class="comment" id="<n>">`` items with
``.comment-content`` and a ``<time datetime="YYYY-MM-DD">`` (date only). Replies are
nested ``<li>`` inside the parent's ``<ol class="children">``. The ``cite.fn`` with the
user name is never read. Articles come from ``/rss``. robots.txt (group ``*``) disallows
``/wp-admin/``, login and search paths and sets ``Crawl-delay: 10``, which the crawler obeys.

Only dates are published, so a revisit checks the comments of the cutoff's day by reading
them from Firestore and writes only the missing ones.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from crawler.ids import parse_datetime
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources.base import Source
from crawler.text import clean_text, html_to_text

_ARTICLE_RE = re.compile(r"^https://www\.achgut\.com/artikel/[a-z0-9_]+$")


def parse_comments(soup: BeautifulSoup, source_id: str = "achgut") -> list[Comment]:
    out: list[Comment] = []
    for li in soup.select("li.comment[id]"):
        native_id = li["id"]
        body = li.find("article")
        if not native_id.isdigit() or body is None:
            continue
        time_el = body.find("time", attrs={"datetime": True})
        content = body.select_one(".comment-content")
        published = parse_datetime(time_el["datetime"]) if time_el else None
        text = clean_text(html_to_text(str(content))) if content else ""
        parent_li = li.find_parent("li", class_="comment")
        parent = parent_li["id"] if isinstance(parent_li, Tag) and parent_li.get("id", "").isdigit() else None
        if published and text:
            out.append(Comment(source_id, native_id, text, published, native_parent_id=parent))
    return out


class Achgut(Source):
    id = "achgut"
    name = "Achse des Guten"
    domain = "achgut.com"
    country = "DE"
    news_sitemap = "https://www.achgut.com/rss"
    feed_type = "rss"

    def canonical_url(self, url: str) -> str | None:
        return url if _ARTICLE_RE.match(url) else None

    def fetch(self, ref: ArticleRef) -> FetchResult:
        resp = self.http.get(ref.url)
        comments = parse_comments(BeautifulSoup(resp.text, "lxml"), self.id)
        unchanged = ref.known and ref.known_comment_count == len(comments)
        return FetchResult(ref, [] if unchanged else comments, len(comments), unchanged=unchanged, day_resolution=True)
