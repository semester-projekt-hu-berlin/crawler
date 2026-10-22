"""Common interface for all comment sources."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar

from crawler.discovery import parse_news_sitemap
from crawler.http import PoliteSession
from crawler.models import ArticleRef, FetchResult


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

    def __init__(self, http: PoliteSession) -> None:
        self.http = http
        self.log = logging.getLogger(f"crawler.sources.{self.id}")

    def source_doc(self) -> dict:
        return {"name": self.name, "domain": self.domain, "country": self.country}

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
