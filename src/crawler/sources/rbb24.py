"""rbb24 (rbb24.de, Rundfunk Berlin-Brandenburg): reader comments under news articles.

robots.txt (group ``*``) allows the section pages, article pages and their comment JSON.
Comments are visible to every anonymous visitor (writing needs an ARD account).

* Articles: the section pages (``/``, ``/politik/``, ``/panorama/``, ``/wirtschaft/``, ``/kultur/``,
  ``/sport/``) link the current articles ``/<section>/beitrag/<yyyy>/<mm>/<slug>.html``; only links
  of the current (and, early in a month, the previous) month are taken. The article page holds the
  publication date (JSON-LD), the keywords and ``data-comment-config`` with
  ``totalCountCommentsAndAnswers``, which is the count check on revisits. Articles without a
  comment section are marked (``commentThreadId = "-"``) and not fetched again.
* Comments: ``<article>.commentthreads.jsn?start=0&size=100`` (JSON, newest threads first):
  ``{id, text, author, date, isEditorial, answers: [...]}``. ``date`` is Berlin local time in the
  form ``Oct 24, 2026, 9:43:38 PM``. ``author`` is never read; editorial answers
  (``isEditorial``) are skipped; answers are deduplicated by id (the same answer can be listed
  twice) and get the thread root as parent.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from crawler.config import COMMENT_OVERLAP
from crawler.discovery import parse_page_meta
from crawler.ids import BERLIN, canonicalize_url, ensure_utc
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources.base import IncrementalPager, Source
from crawler.text import clean_text

SITE = "https://www.rbb24.de"
SECTION_PAGES = ("/", "/politik/", "/panorama/", "/wirtschaft/", "/kultur/", "/sport/")
SECTION_NAMES = {
    "politik": "Politik",
    "panorama": "Panorama",
    "wirtschaft": "Wirtschaft",
    "kultur": "Kultur",
    "sport": "Sport",
}
_ARTICLE_RE = re.compile(r"^https://www\.rbb24\.de/([a-z]+)/beitrag/(\d{4})/(\d{2})/[a-z0-9-]+\.html$")
_DATE_RE = re.compile(r"([A-Z][a-z]{2})\s+(\d{1,2}),\s*(\d{4}),?\s+(\d{1,2}):(\d{2}):(\d{2})\s*([AP]M)")
MONTHS = {
    m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)
}
PAGE_SIZE = 100
MAX_PAGES = 10
LINKS_PER_SECTION = 30  # newest article links taken per section page
# Stored as ``commentThreadId`` for articles without a comment section: revisits skip them unread.
NO_COMMENTS = "-"


def parse_date(text: str) -> datetime | None:
    """``Oct 24, 2026, 9:43:38 PM`` (Berlin local time) as UTC."""
    m = _DATE_RE.search(" ".join((text or "").split()))
    if not m or m.group(1) not in MONTHS:
        return None
    mon, day, year, hour, minute, second, ampm = m.groups()
    h = int(hour) % 12 + (12 if ampm == "PM" else 0)
    return ensure_utc(datetime(int(year), MONTHS[mon], int(day), h, int(minute), int(second), tzinfo=BERLIN))


def article_links(page_html: str) -> list[str]:
    soup = BeautifulSoup(page_html, "lxml")
    out: list[str] = []
    for a in soup.select("a[href*='/beitrag/']"):
        url = canonicalize_url(a["href"] if a["href"].startswith("http") else SITE + a["href"])
        if _ARTICLE_RE.match(url) and url not in out:
            out.append(url)
    return out


def comment_config(page_html: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(page_html, "lxml")
    el = soup.select_one("[data-comment-config]")
    if el is None:
        return None
    try:
        return json.loads(html.unescape(el["data-comment-config"]))
    except (TypeError, ValueError):
        return None


def parse_threads(rows: list[dict[str, Any]], source_id: str = "rbb24") -> list[Comment]:
    """Threads and their answers. ``author`` is never read, editorial answers are skipped."""
    out: dict[str, Comment] = {}

    def add(row: dict[str, Any], parent: str | None) -> None:
        cid = str(row.get("id") or "")
        published = parse_date(row.get("date") or "")
        text = clean_text(html.unescape(row.get("text") or ""))
        if cid and published and text and not row.get("isEditorial") and cid not in out:
            out[cid] = Comment(source_id, cid, text, published, native_parent_id=parent)

    for row in rows:
        if not isinstance(row, dict):
            continue
        add(row, None)
        root = str(row.get("id") or "") or None
        stack = list(row.get("answers") or [])
        while stack:
            answer = stack.pop(0)
            if isinstance(answer, dict):
                add(answer, root)
                stack.extend(answer.get("answers") or [])
    return list(out.values())


class Rbb24(Source):
    id = "rbb24"
    name = "rbb24"
    domain = "rbb24.de"
    country = "DE"
    topic = "politics"
    news_sitemap = f"{SITE}/"

    def canonical_url(self, url: str) -> str | None:
        url = canonicalize_url(url)
        return url if _ARTICLE_RE.match(url) else None

    def discover(self, since: datetime, until: datetime) -> list[ArticleRef]:
        months = {(d.year, d.month) for d in (since.astimezone(BERLIN), until.astimezone(BERLIN))}
        refs: dict[str, ArticleRef] = {}
        for path in SECTION_PAGES:
            try:
                links = article_links(self.http.get(SITE + path).text)[:LINKS_PER_SECTION]
            except Exception as exc:  # noqa: BLE001 - one section page must not stop discovery
                self.log.warning("section %s failed: %s", path, type(exc).__name__)
                continue
            for url in links:
                m = _ARTICLE_RE.match(url)
                if m and (int(m.group(2)), int(m.group(3))) in months and url not in refs:
                    section = SECTION_NAMES.get(m.group(1))
                    refs[url] = ArticleRef(
                        url=url, source_id=self.id, topics=[section] if section else [], discovered_url=url
                    )
        return list(refs.values())

    def fetch(self, ref: ArticleRef) -> FetchResult:
        if ref.known and ref.comment_thread_id == NO_COMMENTS:
            return FetchResult(ref, [], ref.known_comment_count, complete=False, unchanged=True)
        page = self.http.get(ref.url).text
        meta = parse_page_meta(BeautifulSoup(page, "lxml"))
        ref.title = ref.title or (meta.title or "")
        ref.published_at = ref.published_at or meta.published_at
        keywords = re.search(r'<meta name="keywords" content="([^"]*)"', page)
        if keywords and not ref.known:
            extra = [k.strip() for k in html.unescape(keywords.group(1)).split(",") if k.strip() and ":" not in k]
            ref.topics = list(dict.fromkeys([*ref.topics, *extra]))
        cfg = comment_config(page)
        if not cfg or not cfg.get("commentsVisible", True):
            return FetchResult(ref, [], 0, thread_id=NO_COMMENTS)
        total = int(cfg.get("totalCountCommentsAndAnswers") or 0)
        if ref.known and total == ref.known_comment_count:
            return FetchResult(ref, [], total, complete=False, unchanged=True)
        if total == 0:
            return FetchResult(ref, [], 0)
        base = ref.url.removesuffix(".html") + ".commentthreads.jsn"
        pager = IncrementalPager(ref, ref.since(COMMENT_OVERLAP), ref.expected_new(total))
        comments: dict[str, Comment] = {}
        complete = True
        for n in range(MAX_PAGES):
            rows = self.http.get_json(base, params={"start": n * PAGE_SIZE, "size": PAGE_SIZE}, allow_404=True) or []
            found = parse_threads(rows if isinstance(rows, list) else [], self.id)
            comments.update((c.native_id, c) for c in found if c.native_id not in comments)
            if len(rows) < PAGE_SIZE:
                break
            if pager.add_page([c.published_at for c in found]):
                complete = False
                break
        else:
            complete = False
        return FetchResult(ref, list(comments.values()), total, complete=complete)

    def rank_new(self, refs: list[ArticleRef], limit: int | None = None) -> list[ArticleRef]:
        # Discovery has no dates; the section pages list the newest articles first.
        return list(refs)
