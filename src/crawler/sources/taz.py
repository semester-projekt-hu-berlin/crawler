"""taz (taz.de): comments are rendered server-side into the public article page.

Each comment is an ``<li class="comment" data-cid="...">``; replies are nested
``<li>`` elements inside their parent. We read only the ``data-cid``, the
``<time datetime>`` and the ``.message-text-container``; author name, avatar and
profile link elements are never touched.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from crawler.discovery import parse_page_meta
from crawler.ids import parse_datetime
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources.base import Source
from crawler.text import clean_text, html_to_text

_ARTICLE_ID_RE = re.compile(r"^https://taz\.de/(?:[^?#]*/)?!(\d+)/?$")
# article:tag values that are feed/categorisation markers rather than topics.
_IGNORED_TAGS = {"GNS"}


def parse_comments(soup: BeautifulSoup, source_id: str = "taz") -> list[Comment]:
    out: list[Comment] = []
    for li in soup.select("li.comment[data-cid]"):
        native_id = li.get("data-cid", "").strip()
        text_el = li.select_one(".message-text-container")
        time_el = li.find("time", attrs={"datetime": True})
        if not native_id or text_el is None or time_el is None:
            continue
        published = parse_datetime(time_el["datetime"])
        text = clean_text(html_to_text(str(text_el)))
        if not published or not text:
            continue
        parent_li = li.find_parent("li", attrs={"data-cid": True})
        parent: str | None = None
        if isinstance(parent_li, Tag):
            parent = parent_li.get("data-cid") or None
        out.append(Comment(source_id, native_id, text, published, native_parent_id=parent, upvotes=None))
    return out


def parse_tags(soup: BeautifulSoup) -> list[str]:
    tags: list[str] = []
    for meta in soup.find_all("meta", attrs={"property": "article:tag"}):
        value = (meta.get("content") or "").strip()
        if value and value not in _IGNORED_TAGS and value not in tags:
            tags.append(value)
    return tags


class Taz(Source):
    id = "taz"
    name = "taz"
    domain = "taz.de"
    country = "DE"
    news_sitemap = "https://taz.de/sitemap-google-news.xml"

    def canonical_url(self, url: str) -> str | None:
        m = _ARTICLE_ID_RE.match(url)
        return f"https://taz.de/!{m.group(1)}/" if m else None

    def fetch(self, ref: ArticleRef) -> FetchResult:
        # The permalink /!<id>/ answers with a redirect to the slug URL; revisits go straight to
        # the stored slug URL (commentThreadId), which saves one request per revisit.
        resp = self.http.get(ref.comment_thread_id or ref.url)
        page_url = getattr(resp, "url", None)
        thread_id = page_url if isinstance(page_url, str) and page_url.startswith("https://taz.de/") else None
        soup = BeautifulSoup(resp.text, "lxml")
        if not ref.known:
            meta = parse_page_meta(soup)
            ref.title = meta.title or ref.title
            ref.published_at = meta.published_at or ref.published_at
            ref.topics = (meta.topics or []) + [t for t in parse_tags(soup) if t not in (meta.topics or [])]
        comments = parse_comments(soup, self.id)
        # All comments are in the page anyway (no cheaper count endpoint, and taz ignores
        # If-Modified-Since); the pipeline filters out already stored ones before writing.
        unchanged = ref.known and ref.known_comment_count == len(comments)
        return FetchResult(ref, [] if unchanged else comments, len(comments), unchanged=unchanged, thread_id=thread_id)
