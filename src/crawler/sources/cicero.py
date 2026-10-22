"""Cicero (cicero.de): Drupal comments rendered into the public article page.

Each comment is an ``<article class="comment" id="comment-<n>">`` with a
``data-comment-timestamp`` (epoch seconds) and, in two ``field--name-comment-body``
blocks, the subject (a permalink) and the body. Replies are wrapped in
``<div class="indented">`` right after their parent. The ``<h6>`` with the user name is
never read. Articles come from ``/rss.xml``. robots.txt (group ``*``) only disallows
Drupal internals (``/core/``, ``/admin/``, ``/comment/reply/``, ``/user/...``, ``/search/``).
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from crawler.ids import parse_datetime
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources.base import Source
from crawler.text import clean_text, html_to_text

_ARTICLE_RE = re.compile(r"^https://www\.cicero\.de/[a-z-]+/[A-Za-z0-9-]{10,}$")
MAX_PAGES = 20


def _parent_id(art: Tag) -> str | None:
    """Drupal threading: a reply sits in ``div.indented`` that directly follows its parent's wrapper."""
    indented = art.find_parent("div", class_="indented")
    if indented is None:
        return None
    prev = indented.find_previous_sibling()
    while prev is not None:
        parent_art = prev if prev.name == "article" else prev.find("article", id=re.compile(r"^comment-\d+$"))
        if parent_art is not None:
            arts = prev.find_all("article", id=re.compile(r"^comment-\d+$")) if prev.name != "article" else [prev]
            return arts[-1]["id"].removeprefix("comment-") if arts else None
        prev = prev.find_previous_sibling()
    return None


def parse_comments(soup: BeautifulSoup, source_id: str = "cicero") -> list[Comment]:
    out: list[Comment] = []
    for art in soup.select("article.comment[id^='comment-']"):
        native_id = art["id"].removeprefix("comment-")
        mark = art.find(attrs={"data-comment-timestamp": True})
        published = parse_datetime(int(mark["data-comment-timestamp"])) if mark else None
        parts = []
        for field in art.select(".field--name-comment-body"):
            parts.append(clean_text(html_to_text(str(field))).strip())
        subject, body = (parts + ["", ""])[:2]
        if subject.endswith("...") and body.startswith("..."):
            text = subject[:-3].rstrip() + " " + body[3:].lstrip()  # subject is the start of the body
        else:
            text = "\n".join(p for p in (subject, body) if p)
        text = clean_text(text)
        if native_id.isdigit() and published and text:
            out.append(Comment(source_id, native_id, text, published, native_parent_id=_parent_id(art)))
    return out


class Cicero(Source):
    id = "cicero"
    name = "Cicero"
    domain = "cicero.de"
    country = "DE"
    news_sitemap = "https://www.cicero.de/rss.xml"
    feed_type = "rss"

    def canonical_url(self, url: str) -> str | None:
        return url if _ARTICLE_RE.match(url) else None

    def fetch(self, ref: ArticleRef) -> FetchResult:
        comments: dict[str, Comment] = {}
        for page in range(MAX_PAGES):
            resp = self.http.get(ref.url, params={"page": page} if page else None)
            soup = BeautifulSoup(resp.text, "lxml")
            new = [c for c in parse_comments(soup, self.id) if c.native_id not in comments]
            if (
                page == 0
                and ref.known
                and len(new) == ref.known_comment_count
                and not soup.select_one("a[href*='page=1']")
            ):
                return FetchResult(ref, [], len(new), complete=False, unchanged=True)
            if not new:
                break
            comments.update((c.native_id, c) for c in new)
            if not soup.select_one(f"a[href*='page={page + 1}']"):
                break
        return FetchResult(ref, list(comments.values()), len(comments))
