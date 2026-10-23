"""wallstreet-online.de: the public stock discussion forum (``/diskussion/``).

robots.txt (group ``*``) disallows ad server paths, ``/charts/``, ``/_print/``, ``/_rpc/``,
``/userzentrum/``, ``/realtime``, ``/watchlist/``, ``/portfolio/``, ``/suche?`` and the query
parameters ``?q=`` and ``?orderBy=``. ``/community`` and ``/diskussion/<id>-<page>/<slug>`` are
allowed and readable without login.

Forum threads run for years, so this is a stream source: each run reads the community start page
(the most active threads, in its order), takes the first ``MAX_THREADS`` and reads their newest
page (``-1``; 20 posts, newest first), a second page only while all posts are newer than the
previous run. Posts: ``div.posting[data-id]`` with ``.timestamp`` (``dd.mm.yy hh:mm:ss``, Berlin
time) and ``.postingText``. ``data-nick``/``data-userid``, the avatar/nick block and quotes
(``blockquote``, whose ``cite`` holds "Zitat von <name>") are never read into the text; a quote's
``#beitrag_<id>`` link gives the parent. One thread = one article (canonical thread URL).
"""

from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

from crawler.ids import BERLIN, ensure_utc
from crawler.models import ArticleRef, Comment
from crawler.sources.base import StreamItem, StreamSource
from crawler.text import clean_text, html_to_text

SITE = "https://www.wallstreet-online.de"
_THREAD_RE = re.compile(r"^/diskussion/(\d+)-\d+/([a-z0-9-]+)")
_TS_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})(?::(\d{2}))?")
MAX_THREADS = 15
PAGES_PER_THREAD = 2


def thread_links(html: str) -> list[tuple[str, str]]:
    """``(thread_id, slug)`` of the threads linked on a community page, in page order."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, str] = {}
    for a in soup.select("a[href^='/diskussion/']"):
        m = _THREAD_RE.match(a["href"])
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2)
    return list(out.items())


def parse_timestamp(text: str) -> datetime | None:
    m = _TS_RE.search(text or "")
    if not m:
        return None
    d, mo, y, h, mi, s = m.groups()
    return ensure_utc(datetime(2000 + int(y), int(mo), int(d), int(h), int(mi), int(s or 0), tzinfo=BERLIN))


def parse_thread_page(html: str, source_id: str = "wallstreet") -> tuple[str, list[Comment]]:
    """Thread title and its posts."""
    soup = BeautifulSoup(html, "lxml")
    title = soup.h1.get_text(" ", strip=True) if soup.h1 else ""
    out: list[Comment] = []
    for post in soup.select("div.posting[data-id]"):
        native_id = str(post["data-id"])
        ts = post.select_one(".timestamp")
        body = post.select_one(".postingText")
        if not native_id.isdigit() or ts is None or body is None:
            continue
        parent = None
        for quote in body.select("blockquote"):
            link = quote.select_one("a[href*='#beitrag_']")
            if link is not None and parent is None:
                m = re.search(r"#beitrag_(\d+)", link["href"])
                parent = m.group(1) if m else None
            quote.decompose()
        for el in body.select("script, style, title, cite"):
            el.decompose()
        published = parse_timestamp(ts.get_text(" ", strip=True))
        text = clean_text(html_to_text(str(body)))
        if published and text:
            out.append(Comment(source_id, native_id, text, published, native_parent_id=parent))
    return title, out


class WallstreetOnline(StreamSource):
    id = "wallstreet"
    name = "wallstreet-online.de Forum"
    domain = "wallstreet-online.de"
    country = "DE"
    source_type = "news"
    # "finance" is not a topic of the data contract (SCHEMA.md: politics|tech|tv|general); articles carry "Börse".
    topic = "general"

    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        threads = thread_links(self.http.get(f"{SITE}/community").text)[:MAX_THREADS]
        per_thread: list[list[StreamItem]] = []
        for thread_id, slug in threads:
            url = f"{SITE}/diskussion/{thread_id}-1/{slug}"
            items: list[StreamItem] = []
            try:
                for page in range(1, PAGES_PER_THREAD + 1):
                    title, comments = parse_thread_page(
                        self.http.get(f"{SITE}/diskussion/{thread_id}-{page}/{slug}").text, self.id
                    )
                    ref = ArticleRef(url=url, source_id=self.id, title=title or slug.replace("-", " "),
                                     topics=["Börse"])  # fmt: skip
                    fresh = [c for c in comments if since <= c.published_at <= until]
                    for c in fresh:
                        items.append(StreamItem(ref, c))
                    if not comments or len(fresh) < len(comments):
                        break
            except Exception as exc:  # noqa: BLE001 - one thread must not stop the others
                self.log.warning("thread %s failed: %s", thread_id, type(exc).__name__)
            if items:
                # thread "published" = its oldest post in this sample (pseudo-article date)
                oldest = min(i.comment.published_at for i in items)
                for i in items:
                    i.article.published_at = oldest
                per_thread.append(items)
        # Round robin over threads, so one very busy thread does not take the whole cap.
        out: list[StreamItem] = []
        while len(out) < max_items and any(per_thread):
            for items in per_thread:
                if items and len(out) < max_items:
                    out.append(items.pop(0))
        return out
