"""Finanztip Community (finanztip.de/community, WoltLab Suite forum): personal finance and provider experiences.

robots.txt (group ``*``): **``Crawl-delay: 3``** (obeyed), disallows search, member list, posting
and a few TYPO3 paths; ``/community/forum/thread-list-rss-feed/`` and the thread pages
``/community/forum/thema/<id>-<slug>/`` are allowed and readable without login.

Stream source. Each run reads the thread feeds (RSS, the 20 most recently active threads with the
time of their last post): board 14 "Erfahrungen mit Anbietern" first, then the whole forum. Threads
with posts since the previous run are read: ``li[id^=post]`` / ``article[data-post-id]`` with
``<woltlab-core-date-time date="ISO">`` and ``.messageText``. The user box (``itemprop=author``),
``dc:creator``, signatures, ``@mentions`` (``a.userMention``) and quotes (``blockquote.quoteBox``,
"Zitat von <name>") are never read into the text; the quote's ``postID`` becomes ``parentId``.

Anonymous thread pages are served from a CDN cache (``Age`` up to several hours) that ignores the
query string, so ``?pageNo=N`` always returns page 1 (20 posts). Only page 1 of a thread is used:
new threads are covered completely up to their 20th post, later replies of long threads are not.
Because of the cache lag the source re-reads a 12-hour overlap (already stored posts are filtered
with a Firestore read, never written twice). Source topic ``general`` (no finance topic in SCHEMA.md).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import ClassVar, NamedTuple
from urllib.parse import unquote

from bs4 import BeautifulSoup
from lxml import etree

from crawler.discovery import clean_title
from crawler.ids import parse_datetime
from crawler.models import ArticleRef, Comment
from crawler.sources.base import StreamItem, StreamSource
from crawler.text import clean_text, html_to_text, remove_quotes

SITE = "https://www.finanztip.de/community"
FEEDS = (
    f"{SITE}/forum/thread-list-rss-feed/14/",  # Erfahrungen mit Anbietern
    f"{SITE}/forum/thread-list-rss-feed/",
)
NS = {"slash": "http://purl.org/rss/1.0/modules/slash/"}
_THREAD_RE = re.compile(r"/community/forum/thema/(\d+)(?:-[^/?#]*)?/?")
_POST_ID_RE = re.compile(r"postID=(\d+)")
MAX_THREADS = 20


class FeedThread(NamedTuple):
    thread_id: str
    url: str
    title: str
    last_post_at: datetime | None
    replies: int | None


def parse_feed(xml: bytes) -> list[FeedThread]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    root = etree.fromstring(xml, parser=parser)
    out: list[FeedThread] = []
    if root is None:
        return out
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        m = _THREAD_RE.search(link)
        if not m:
            continue
        try:
            last = parsedate_to_datetime((item.findtext("pubDate") or "").strip()).astimezone(UTC)
        except (TypeError, ValueError):
            last = None
        replies = (item.findtext("slash:comments", namespaces=NS) or "").strip()
        out.append(
            FeedThread(
                m.group(1),
                link.split("?", 1)[0],
                clean_title(item.findtext("title") or ""),
                last,
                int(replies) if replies.isdigit() else None,
            )
        )
    return out


def thread_url(thread_id: str) -> str:
    """ID-based thread URL (redirects to the slug URL), stable if the title changes."""
    return f"{SITE}/forum/thema/{thread_id}/"


def parse_thread_page(html: str, source_id: str = "finanztip") -> tuple[str, list[str], list[Comment]]:
    """Thread title, board names (breadcrumbs) and the posts of the page."""
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.select_one("h1.contentTitle, h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    boards = [
        a.get_text(" ", strip=True)
        for a in soup.select(".breadcrumbs a[href*='/forum/board/']")
        if a.get_text(strip=True)
    ]
    out: list[Comment] = []
    for art in soup.select("article[data-post-id]"):
        native_id = str(art["data-post-id"])
        date = art.select_one("woltlab-core-date-time[date]")
        body = art.select_one(".messageText")
        if not native_id.isdigit() or date is None or body is None or art.get("data-is-deleted") == "1":
            continue
        cites = [m.group(1) for a in remove_quotes(body) if (m := _POST_ID_RE.search(unquote(a.get("cite") or "")))]
        parent = cites[0] if cites else None
        for el in body.select("a.userMention, .messageSignature, script, style, noscript"):
            el.decompose()
        published = parse_datetime(date["date"])
        text = clean_text(html_to_text(str(body)))
        if published and text:
            out.append(Comment(source_id, native_id, text, published, native_parent_id=parent))
    return title, list(dict.fromkeys(boards)), out


class Finanztip(StreamSource):
    id = "finanztip"
    name = "Finanztip Community-Forum"
    domain = "finanztip.de"
    country = "DE"
    source_type = "news"  # forum threads as articles (the data contract has no forum type)
    topic = "general"
    # The CDN serves thread pages up to several hours old: re-read a longer window.
    overlap: ClassVar[timedelta] = timedelta(hours=12)

    def threads(self, since: datetime) -> list[FeedThread]:
        seen: dict[str, FeedThread] = {}
        for feed in FEEDS:
            try:
                for t in parse_feed(self.http.get(feed).content):
                    if t.thread_id not in seen and (t.last_post_at is None or t.last_post_at >= since):
                        seen[t.thread_id] = t
            except Exception as exc:  # noqa: BLE001 - the other feed may still work
                self.log.warning("feed %s failed: %s", feed, type(exc).__name__)
        return list(seen.values())[:MAX_THREADS]

    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        if max_items <= 0:
            return []
        per_thread: list[list[StreamItem]] = []
        for t in self.threads(since):
            try:
                title, boards, comments = parse_thread_page(self.http.get(t.url).text, self.id)
            except Exception as exc:  # noqa: BLE001 - one thread must not stop the others
                self.log.warning("thread %s failed: %s", t.thread_id, type(exc).__name__)
                continue
            fresh = sorted(
                (c for c in comments if since <= c.published_at <= until), key=lambda c: c.published_at, reverse=True
            )
            if not fresh:
                continue
            first = min(c.published_at for c in comments)
            ref = ArticleRef(
                url=thread_url(t.thread_id),
                source_id=self.id,
                title=t.title or title,
                published_at=first,
                topics=["Finanzen", *boards[-1:]],
            )
            per_thread.append([StreamItem(ref, c) for c in fresh])
        out: list[StreamItem] = []
        while len(out) < max_items and any(per_thread):
            for items in per_thread:
                if items and len(out) < max_items:
                    out.append(items.pop(0))
        return out
