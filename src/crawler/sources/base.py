"""Common interface for all comment sources."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from crawler.discovery import parse_news_sitemap
from crawler.http import PoliteSession
from crawler.ids import BERLIN, berlin_day
from crawler.models import ArticleRef, Comment, FetchResult


class Source(ABC):
    """One news site. Subclasses implement discovery and comment fetching.

    Adapters must only return data that is publicly visible without login and
    must never return author information (names, IDs, avatars, profile URLs).
    """

    id: ClassVar[str]
    name: ClassVar[str]
    domain: ClassVar[str]
    country: ClassVar[str]
    news_sitemap: ClassVar[str]
    # "news" (comments under articles) or "social" (public posts, grouped into pseudo-articles).
    source_type: ClassVar[str] = "news"

    def __init__(self, http: PoliteSession) -> None:
        self.http = http
        self.log = logging.getLogger(f"crawler.sources.{self.id}")

    def source_doc(self) -> dict:
        return {"name": self.name, "domain": self.domain, "country": self.country, "sourceType": self.source_type}

    @abstractmethod
    def canonical_url(self, url: str) -> str | None:
        """Stable permalink of an article, built from the source's native article ID.

        Slugs in article URLs change when editors change a headline; the ID-based
        permalink (which redirects to the current URL) keeps ``articleId`` stable.
        Returns ``None`` for URLs that are not articles of this source.
        """

    def accept_url(self, url: str) -> bool:
        """Filter for discovered URLs (e.g. skip live tickers or other languages)."""
        return True

    def discover(self, since: datetime, until: datetime) -> list[ArticleRef]:
        """Articles published in ``[since, until]`` according to the news sitemap, newest first."""
        resp = self.http.get(self.news_sitemap)
        refs: list[ArticleRef] = []
        seen: set[str] = set()
        for entry in parse_news_sitemap(resp.content):
            url = self.canonical_url(entry.url) if self.accept_url(entry.url) else None
            if url is None or url in seen:
                continue
            if entry.published_at is None or not (since <= entry.published_at <= until):
                continue
            seen.add(url)
            refs.append(
                ArticleRef(
                    url=url,
                    source_id=self.id,
                    title=entry.title,
                    published_at=entry.published_at,
                    topics=entry.keywords,
                )
            )
        refs.sort(key=lambda r: r.published_at, reverse=True)
        return refs

    @abstractmethod
    def fetch(self, ref: ArticleRef) -> FetchResult:
        """Fetch all currently visible comments of an article.

        ``ref.known`` tells whether the article already exists in Firestore; adapters
        may then skip fetching article metadata again.
        """


class IncrementalPager:
    """Decides when to stop paging through a newest-first comment listing on a revisit.

    Replies to old threads can show up on any page, so "this page holds only old
    comments" alone is not enough. With a known comment count we stop as soon as we
    have seen as many genuinely new comments as the count grew; without a count we
    stop at the first page that holds only comments older than the cutoff.
    """

    def __init__(self, ref: ArticleRef, since: datetime | None, expected_new: int | None) -> None:
        self.ref = ref
        self.since = since
        self.expected_new = expected_new
        self.found_new = 0

    def add_page(self, published: list[datetime]) -> bool:
        """Register the timestamps of one page; return True if paging should stop."""
        if self.since is None:  # first crawl: fetch everything
            return False
        self.found_new += sum(1 for t in published if self.ref.is_new_comment(t))
        if self.expected_new is not None:
            return self.found_new >= self.expected_new
        return not any(t >= self.since for t in published)


@dataclass
class StreamItem:
    """One comment/post of a stream source together with the (pseudo-)article it belongs to."""

    article: ArticleRef
    comment: Comment


class StreamSource(ABC):
    """A source that lists new comments/posts directly (newest first), without visiting articles.

    Examples: a site-wide comment feed (WordPress REST API) or public social media timelines.
    Each run asks for everything published since the previous run (minus a small overlap),
    so there are no per-article revisits at all. Social posts are grouped into one
    pseudo-article per day (and instance), see ``day_article``.
    """

    id: ClassVar[str]
    name: ClassVar[str]
    domain: ClassVar[str]
    country: ClassVar[str]
    source_type: ClassVar[str] = "social"
    # Items published up to this long before the previous run are fetched again (late
    # moderation, clock skew). Stored ones are filtered with a Firestore read, never re-sent.
    overlap: ClassVar[timedelta] = timedelta(minutes=10)

    def __init__(self, http: PoliteSession) -> None:
        self.http = http
        self.log = logging.getLogger(f"crawler.sources.{self.id}")

    def source_doc(self) -> dict:
        return {"name": self.name, "domain": self.domain, "country": self.country, "sourceType": self.source_type}

    @abstractmethod
    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        """All items published in ``[since, until]`` (at most ``max_items``)."""

    def day_article(self, base_url: str, title: str, published: datetime, topics: list[str]) -> ArticleRef:
        """Pseudo-article grouping all posts of one Berlin day (``?day=YYYY-MM-DD``)."""
        day = berlin_day(published)
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=BERLIN).astimezone(UTC)
        sep = "&" if "?" in base_url else "?"
        return ArticleRef(
            url=f"{base_url}{sep}day={day}",
            source_id=self.id,
            title=f"{title} · {day}",
            published_at=start,
            topics=topics,
        )
