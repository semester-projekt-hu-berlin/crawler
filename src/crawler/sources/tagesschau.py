"""tagesschau (meta.tagesschau.de): the ARD news portal's public comment platform.

Every discussion ("Ihre Meinung zu ...") is a Drupal node ``/id/<n>/<slug>`` that belongs
to a tagesschau.de article. The front page (``/?page=N``, newest first) lists discussions
with their opening time and comment count. Comments are rendered into the node page,
oldest first, ~15 threads per page (``?page=N``), replies nested in their thread with a
link to the parent comment.

We read the comment id, ``<time datetime>``, the ``.comment__content`` text and the
parent link's comment id. The user name, badge and the ``@name`` parent link text are
dropped. ``robots.txt`` only disallows ``*/comment/`` (single comment permalinks, which
we never fetch), admin and user paths.
"""

from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from crawler.ids import parse_datetime
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources.base import Source
from crawler.text import clean_text, html_to_text

BASE = "https://meta.tagesschau.de"
MAX_LISTING_PAGES = 12
MAX_COMMENT_PAGES = 60
_NODE_RE = re.compile(r"^https://meta\.tagesschau\.de/id/(\d+)/[a-z0-9-]+$")
_PARENT_RE = re.compile(r"/comment/(\d+)")
_COUNT_RE = re.compile(r"(\d[\d.]*)\s+Kommentar")


def _int(text: str) -> int | None:
    digits = re.sub(r"\D", "", text or "")
    return int(digits) if digits else None


def parse_listing(soup: BeautifulSoup) -> list[ArticleRef]:
    refs: list[ArticleRef] = []
    for story in soup.select("article.story"):
        link = story.select_one("a.story__link[href^='/id/']")
        if link is None:
            continue
        url = BASE + link["href"].split("?")[0].split("#")[0]
        title_el = link.select_one(".field--name-title") or link
        time_el = story.select_one("footer time[datetime]")
        count_el = story.select_one(".story__count")
        refs.append(
            ArticleRef(
                url=url,
                source_id="tagesschau",
                title=title_el.get_text(" ", strip=True),
                published_at=parse_datetime(time_el["datetime"]) if time_el else None,
                listed_comment_count=_int(count_el.get_text(" ", strip=True)) if count_el else None,
            )
        )
    return refs


def parse_comments(soup: BeautifulSoup, source_id: str = "tagesschau") -> list[Comment]:
    out: list[Comment] = []
    for art in soup.select("article[id^='comment-']"):
        native_id = art["id"].removeprefix("comment-")
        time_el = art.select_one("footer time[datetime]")
        content = art.select_one(".comment__content")
        if not native_id.isdigit() or time_el is None or content is None:
            continue
        parent: str | None = None
        parent_link = content.select_one(".comment__parent_link")
        if isinstance(parent_link, Tag):
            a = parent_link.find("a", href=True)
            m = _PARENT_RE.search(a["href"]) if a else None
            parent = m.group(1) if m else None
            parent_link.decompose()  # contains "@username"
        published = parse_datetime(time_el["datetime"])
        text = clean_text(html_to_text(str(content)))
        if published and text:
            out.append(Comment(source_id, native_id, text, published, native_parent_id=parent, upvotes=None))
    return out


def parse_total(soup: BeautifulSoup) -> int | None:
    m = _COUNT_RE.search(soup.get_text(" ", strip=True))
    return _int(m.group(1)) if m else None


class Tagesschau(Source):
    id = "tagesschau"
    name = "tagesschau (meta.tagesschau.de)"
    domain = "meta.tagesschau.de"
    country = "DE"
    news_sitemap = BASE + "/"

    def canonical_url(self, url: str) -> str | None:
        return url if _NODE_RE.match(url) else None

    def discover(self, since: datetime, until: datetime) -> list[ArticleRef]:
        refs: dict[str, ArticleRef] = {}
        for page in range(MAX_LISTING_PAGES):
            resp = self.http.get(BASE + "/", params={"page": page} if page else None)
            listed = parse_listing(BeautifulSoup(resp.text, "lxml"))
            if not listed:
                break
            for ref in listed:
                if ref.published_at and since <= ref.published_at <= until and self.canonical_url(ref.url):
                    refs.setdefault(ref.url, ref)
            # The front page is not strictly chronological: stop once a whole page is too old.
            newest = max((r.published_at for r in listed if r.published_at), default=None)
            if newest is not None and newest < since:
                break
        return sorted(refs.values(), key=lambda r: r.published_at, reverse=True)

    def fetch(self, ref: ArticleRef) -> FetchResult:
        if ref.known and ref.listed_comment_count is not None and ref.listed_comment_count == ref.known_comment_count:
            return FetchResult(ref, [], ref.listed_comment_count, complete=False, unchanged=True)
        comments: dict[str, Comment] = {}
        total: int | None = None
        for page in range(MAX_COMMENT_PAGES):
            resp = self.http.get(ref.url, params={"page": page} if page else None)
            soup = BeautifulSoup(resp.text, "lxml")
            if page == 0:
                total = parse_total(soup)
                if ref.known and total is not None and total == ref.known_comment_count:
                    return FetchResult(ref, [], total, complete=False, unchanged=True)
            new = [c for c in parse_comments(soup, self.id) if c.native_id not in comments]
            if not new:  # past the last page the site repeats the last page
                break
            comments.update((c.native_id, c) for c in new)
            if not soup.select_one(f"a[href*='page={page + 1}']"):
                break
        return FetchResult(ref, list(comments.values()), total if total is not None else len(comments))
