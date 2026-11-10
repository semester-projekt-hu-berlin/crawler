"""GoingElectric (goingelectric.de/forum, phpBB): Germany's large electric car forum.

robots.txt (group ``*``) disallows ``index.php``, ``memberlist.php``, ``search.php``, ``ucp.php``,
``posting.php`` and other phpBB tools, every URL with ``sid=`` or ``hilit=``, and ``download.php``.
The Atom feed ``/forum/feed`` and ``viewtopic.php?t=<id>&start=<n>`` are allowed and readable
without login. Links on the pages carry session ids (``sid=``); they are never followed, all URLs
are built from topic/post ids.

Stream source. Each run reads the feed (the 10 newest posts with their topic and subforum) and
then the topics they belong to plus the topics stored during the last days (revisits, from
Firestore): the last page of each topic (``start=99999``, phpBB clamps it to the last page) and
earlier pages while they hold only new posts. Posts: ``div.post#p<id>``, ``.post-timestamp``
(``So 25. Okt 2026, 21:08``, Berlin time), ``.content``. The user name, profile card, signature
and quotes (``blockquote``, "<name> hat geschrieben:") are never read into the text; a quote's
``p=<id>`` becomes ``parentId``. One topic = one article.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from bs4 import BeautifulSoup
from lxml import etree

from crawler.ids import BERLIN, ensure_utc, parse_datetime
from crawler.models import ArticleRef, Comment
from crawler.sources.base import StreamItem, StreamSource
from crawler.text import clean_text, html_to_text, remove_quotes

FORUM = "https://www.goingelectric.de/forum"
FEED = f"{FORUM}/feed"
ATOM = "{http://www.w3.org/2005/Atom}"
PER_PAGE = 10  # phpBB posts per page on this board
PAGES_PER_TOPIC = 2
MAX_TOPICS = 15
REVISIT_DAYS = 3
_POST_RE = re.compile(r"[?&]p=(\d+)")
_TOPIC_RE = re.compile(r"[?&]t=(\d+)")
_START_RE = re.compile(r"[?&]start=(\d+)")
MONTHS = {
    "jan": 1,
    "feb": 2,
    "mär": 3,
    "mrz": 3,
    "apr": 4,
    "mai": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "okt": 10,
    "nov": 11,
    "dez": 12,
}
_DATE_RE = re.compile(r"(\d{1,2})\.\s*([A-Za-zä]{3})[a-zä]*\.?\s+(\d{4}),?\s+(\d{1,2}):(\d{2})")


class FeedPost(NamedTuple):
    post_id: str
    topic_title: str
    forum: str
    published: datetime | None


def topic_url(topic_id: str, start: int | None = None) -> str:
    return f"{FORUM}/viewtopic.php?t={topic_id}" + (f"&start={start}" if start is not None else "")


def parse_german_date(text: str) -> datetime | None:
    """``So 25. Okt 2026, 21:08`` (Berlin local time) as UTC."""
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    day, mon, year, hour, minute = m.groups()
    month = MONTHS.get(mon.lower()[:3])
    if month is None:
        return None
    return ensure_utc(datetime(int(year), month, int(day), int(hour), int(minute), tzinfo=BERLIN))


def parse_feed(xml: bytes) -> list[FeedPost]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    root = etree.fromstring(xml, parser=parser)
    out: list[FeedPost] = []
    if root is None:
        return out
    for entry in root.iter(f"{ATOM}entry"):
        link = entry.find(f"{ATOM}link")
        m = _POST_RE.search((link.get("href") if link is not None else "") or entry.findtext(f"{ATOM}id") or "")
        if not m:
            continue
        title = " ".join((entry.findtext(f"{ATOM}title") or "").split())
        forum, _, topic = title.partition(" • ")
        topic = topic.removeprefix("Re: ").strip() if topic else forum
        cat = entry.find(f"{ATOM}category")
        forum = (cat.get("label") if cat is not None else None) or (forum if topic != forum else "")
        out.append(FeedPost(m.group(1), topic, forum, parse_datetime(entry.findtext(f"{ATOM}published"))))
    return out


def parse_topic_page(html: str, source_id: str = "goingelectric") -> tuple[str | None, int | None, str, list[Comment]]:
    """``(topic id, start of this page, topic title, posts)`` of one topic page."""
    soup = BeautifulSoup(html, "lxml")
    canonical = soup.select_one("link[rel=canonical]")
    href = canonical["href"] if canonical is not None else ""
    tm, sm = _TOPIC_RE.search(href), _START_RE.search(href)
    h1 = soup.select_one("h1.topic-title, h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    out: list[Comment] = []
    for post in soup.select("div.post[id^=p]"):
        native_id = post["id"].removeprefix("p")
        ts = post.select_one(".post-timestamp")
        body = post.select_one(".post-content .content") or post.select_one(".content")
        if not native_id.isdigit() or ts is None or body is None:
            continue
        for badge in ts.select("span"):
            badge.decompose()
        cites = [m.group(1) for a in remove_quotes(body) if (m := _POST_RE.search(a.get("cite") or ""))]
        parent = cites[0] if cites else None
        for el in body.select("cite, .post-signature, script, style"):
            el.decompose()
        published = parse_german_date(ts.get_text(" ", strip=True))
        text = clean_text(html_to_text(str(body)))
        if published and text:
            out.append(Comment(source_id, native_id, text, published, native_parent_id=parent))
    return (tm.group(1) if tm else None), (int(sm.group(1)) if sm else 0 if tm else None), title, out


class GoingElectric(StreamSource):
    id = "goingelectric"
    name = "GoingElectric Elektroauto-Forum"
    domain = "goingelectric.de"
    country = "DE"
    source_type = "news"  # forum topics as articles (the data contract has no forum type)
    topic = "tech"

    def __init__(self, http) -> None:  # noqa: ANN001
        super().__init__(http)
        self.revisit_topics: list[tuple[str, str]] = []  # (topic id, title) stored in the last days

    def prepare(self, store) -> None:  # noqa: ANN001
        """Topics stored during the last ``REVISIT_DAYS`` days are read again (their newest page)."""
        try:
            rows = store.run_query(
                "articles",
                [
                    ("sourceId", "EQUAL", self.id),
                    ("publishedAt", "GREATER_THAN_OR_EQUAL", datetime.now(UTC) - timedelta(days=REVISIT_DAYS)),
                ],
                order_by=[("publishedAt", "DESCENDING")],
                limit=MAX_TOPICS,
                select=["url", "title"],
            )
            for _, fields in rows:
                m = _TOPIC_RE.search(fields.get("url") or "")
                if m:
                    self.revisit_topics.append((m.group(1), fields.get("title") or ""))
        except Exception as exc:  # noqa: BLE001 - best effort
            self.log.warning("revisit query failed: %s", exc)

    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        if max_items <= 0:
            return []
        feed = [p for p in parse_feed(self.http.get(FEED).content) if p.published is None or p.published >= since]
        topics: dict[str, tuple[str, str]] = {}  # topic id -> (title, forum)
        pages: dict[str, tuple[int, str, list[Comment]]] = {}
        for post in feed:
            if len(topics) >= MAX_TOPICS:
                break
            try:
                tid, start, title, comments = parse_topic_page(
                    self.http.get(f"{FORUM}/viewtopic.php?p={post.post_id}").text, self.id
                )
            except Exception as exc:  # noqa: BLE001
                self.log.warning("post %s failed: %s: %s", post.post_id, type(exc).__name__, str(exc)[:160])
                continue
            if tid and tid not in topics:
                topics[tid] = (post.topic_title or title, post.forum)
                pages[tid] = (start or 0, title, comments)
        for tid, title in self.revisit_topics:
            if tid not in topics and len(topics) < MAX_TOPICS * 2:
                topics[tid] = (title, "")
        per_topic: list[list[StreamItem]] = []
        for tid, (title, forum) in topics.items():
            items: list[StreamItem] = []
            try:
                if tid in pages:
                    start, page_title, comments = pages[tid]
                else:
                    _, start, page_title, comments = parse_topic_page(
                        self.http.get(topic_url(tid, 99999)).text, self.id
                    )
                    start = start or 0
                ref = ArticleRef(
                    url=topic_url(tid),
                    source_id=self.id,
                    title=title or page_title,
                    topics=["Elektromobilität", *([forum] if forum else [])],
                )
                for n in range(PAGES_PER_TOPIC):
                    fresh = [c for c in comments if since <= c.published_at <= until]
                    items = [StreamItem(ref, c) for c in fresh] + items
                    if n + 1 >= PAGES_PER_TOPIC or not comments or len(fresh) < len(comments) or start <= 0:
                        break
                    start = max(0, start - PER_PAGE)
                    _, _, _, comments = parse_topic_page(self.http.get(topic_url(tid, start)).text, self.id)
            except Exception as exc:  # noqa: BLE001 - one topic must not stop the others
                self.log.warning("topic %s failed: %s", tid, type(exc).__name__)
            if items:
                ref.published_at = min(i.comment.published_at for i in items)
                items.sort(key=lambda i: i.comment.published_at, reverse=True)
                per_topic.append(items)
        out: list[StreamItem] = []
        while len(out) < max_items and any(per_topic):
            for items in per_topic:
                if items and len(out) < max_items:
                    out.append(items.pop(0))
        return out
