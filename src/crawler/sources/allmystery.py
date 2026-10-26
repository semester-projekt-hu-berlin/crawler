"""Allmystery (allmystery.de): the public "Politik" forum, one long-running thread per party or politician.

robots.txt (group ``*``) disallows ``/bilder/``, ``/mitglieder/``, ``/pn/``, ``/login/``, ``/ng/``,
``/fcgi/`` and the imprint; ``/themen/`` (listing and threads) is allowed and readable without login.

Stream source: each run reads the category listing ``/themen/pr`` (the 50 most recently active
threads with their last-post time and last page) and, for the threads in ``THREADS`` that had
posts since the previous run, the last page of the thread (``/themen/pr<id>-<n>``, page ``n+1``;
``/themen/pr<id>`` is page 1) and earlier pages while they hold only new posts. A few more of
the most active other politics threads fill up the cap. Posts: ``div.post[data-id]`` (global post
id), ``span.date[title]`` (``dd.mm.yyyy hh:mm:ss``, Berlin time), ``.post-content``.
``data-username``, the user box, ``@name`` mentions (``a.atu``) and quotes of other posts
(``blockquote``, whose ``cite`` holds "<name> schrieb:") are never read into the text; a quote's
``data-parent-id`` becomes ``parentId``. One thread = one article; the thread title is the context.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import NamedTuple

from bs4 import BeautifulSoup

from crawler.ids import BERLIN, ensure_utc
from crawler.models import ArticleRef, Comment
from crawler.sources.base import StreamItem, StreamSource
from crawler.text import clean_text, html_to_text, remove_quotes

SITE = "https://www.allmystery.de"
LISTING = f"{SITE}/themen/pr"
# One thread per party / person (thread id -> label used as topic).
THREADS: dict[str, str] = {
    "98768": "AfD",
    "94635": "CDU/CSU",
    "135414": "SPD",
    "37553": "Die Linke",
    "89279": "Grüne",
    "170539": "BSW",
    "172339": "Friedrich Merz",
    "174320": "Björn Höcke",
    "120332": "Donald Trump",
}
EXTRA_THREADS = 5  # most active other threads of the listing
PAGES_PER_THREAD = 3
_PAGE_RE = re.compile(r"^/themen/pr(\d+)(?:-(\d+))?(?:#.*)?$")
_TS_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})(?::(\d{2}))?")


class ThreadInfo(NamedTuple):
    thread_id: str
    title: str
    last_page: int  # URL page index: 0 = /themen/pr<id>, n = /themen/pr<id>-<n>
    last_post_at: datetime | None


def thread_url(thread_id: str, page: int = 0) -> str:
    return f"{SITE}/themen/pr{thread_id}" + (f"-{page}" if page else "")


def parse_listing(html: str) -> list[ThreadInfo]:
    """Threads of a category listing in page order (most recent activity first)."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, ThreadInfo] = {}
    for box in soup.select("div.thread"):
        link = box.select_one("a.thread-title[data-thread]:not(.thread-overlay)") or box.select_one(
            "a.thread-title[data-thread]"
        )
        if link is None:
            continue
        tid = str(link["data-thread"])
        ts = link.get("data-last-ts") or ""
        last_at = datetime.fromtimestamp(int(ts) / 1000, UTC) if ts.isdigit() else None
        last_page = 0
        last = box.select_one(".last-post a[href]")
        m = _PAGE_RE.match(last["href"]) if last is not None else None
        if m and m.group(1) == tid and m.group(2):
            last_page = int(m.group(2))
        if tid.isdigit() and tid not in out:
            out[tid] = ThreadInfo(tid, link.get_text(" ", strip=True), last_page, last_at)
    return list(out.values())


def parse_timestamp(text: str) -> datetime | None:
    m = _TS_RE.search(text or "")
    if not m:
        return None
    d, mo, y, h, mi, s = m.groups()
    return ensure_utc(datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), tzinfo=BERLIN))


def parse_thread_page(html: str, source_id: str = "allmystery") -> tuple[str, list[Comment]]:
    """Thread title and the posts of one page (oldest first)."""
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.select_one("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    out: list[Comment] = []
    for post in soup.select("div.post[data-id]"):
        native_id = str(post["data-id"])
        date = post.select_one("span.date[title]")
        body = post.select_one(".post-content")
        if not native_id.isdigit() or date is None or body is None:
            continue
        parent = next(
            (str(a["data-parent-id"]) for a in remove_quotes(body) if str(a.get("data-parent-id") or "").isdigit()),
            None,
        )
        for el in body.select("a.atu, cite, script, style, .spoiler-title"):
            el.decompose()
        published = parse_timestamp(date["title"])
        text = clean_text(html_to_text(str(body)))
        if published and text:
            out.append(Comment(source_id, native_id, text, published, native_parent_id=parent))
    return title, out


class Allmystery(StreamSource):
    id = "allmystery"
    name = "Allmystery Politik-Forum"
    domain = "allmystery.de"
    country = "DE"
    source_type = "news"  # forum threads as articles (the data contract has no forum type)
    topic = "politics"

    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        if max_items <= 0:
            return []
        listing = parse_listing(self.http.get(LISTING).text)
        active = [t for t in listing if t.last_post_at is None or t.last_post_at >= since]
        fixed = [t for t in active if t.thread_id in THREADS]
        others = [t for t in active if t.thread_id not in THREADS][:EXTRA_THREADS]
        per_thread: list[list[StreamItem]] = []
        for info in fixed + others:
            items: list[StreamItem] = []
            try:
                for page in range(info.last_page, max(-1, info.last_page - PAGES_PER_THREAD), -1):
                    title, comments = parse_thread_page(self.http.get(thread_url(info.thread_id, page)).text, self.id)
                    fresh = [c for c in comments if since <= c.published_at <= until]
                    label = THREADS.get(info.thread_id)
                    ref = ArticleRef(
                        url=thread_url(info.thread_id),
                        source_id=self.id,
                        title=info.title or title,
                        topics=["Politik", *([label] if label else [])],
                    )
                    items = [StreamItem(ref, c) for c in fresh] + items
                    if not comments or len(fresh) < len(comments):
                        break
            except Exception as exc:  # noqa: BLE001 - one thread must not stop the others
                self.log.warning("thread %s failed: %s", info.thread_id, type(exc).__name__)
            if items:
                oldest = min(i.comment.published_at for i in items)
                for i in items:
                    i.article.published_at = i.article.published_at or oldest
                items.sort(key=lambda i: i.comment.published_at, reverse=True)
                per_thread.append(items)
        # Round robin (newest posts first), so one busy thread does not take the whole cap.
        out: list[StreamItem] = []
        while len(out) < max_items and any(per_thread):
            for items in per_thread:
                if items and len(out) < max_items:
                    out.append(items.pop(0))
        return out
