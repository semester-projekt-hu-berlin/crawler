"""Data classes passed between source adapters, pipeline and Firestore writer.

They deliberately have **no author fields**: adapters never extract user names,
user IDs, avatars or profile links, so they cannot leak into storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from crawler.ids import article_id, berlin_day, comment_id


@dataclass
class ArticleRef:
    """An article found during discovery (sitemap) or loaded from Firestore for a revisit."""

    url: str  # canonical URL
    source_id: str
    title: str = ""
    published_at: datetime | None = None
    topics: list[str] = field(default_factory=list)
    # State from Firestore (set when the article already exists there).
    known: bool = False
    last_comment_crawl_at: datetime | None = None
    comment_count: int = 0
    # Newest stored comment and the comment total the source reported at the last change.
    last_comment_at: datetime | None = None
    known_comment_count: int | None = None
    # Source-native id of the comment thread (Blick: Viafoura container id), saves a page fetch.
    comment_thread_id: str | None = None

    @property
    def id(self) -> str:
        return article_id(self.url)

    def since(self, overlap: timedelta) -> datetime | None:
        """Comments older than this were stored by an earlier crawl (``None`` on the first crawl)."""
        if not self.known or self.last_comment_crawl_at is None:
            return None
        return self.last_comment_crawl_at - overlap

    def expected_new(self, reported_total: int | None) -> int | None:
        """How many comments the source gained since the last change (``None`` if unknown)."""
        if reported_total is None or self.known_comment_count is None or not self.known:
            return None
        return max(0, reported_total - self.known_comment_count)

    def is_new_comment(self, published_at: datetime) -> bool:
        """Published strictly after the previous crawl, i.e. not counted in ``known_comment_count``."""
        return self.last_comment_crawl_at is None or published_at > self.last_comment_crawl_at


@dataclass
class Comment:
    source_id: str
    native_id: str
    text: str
    published_at: datetime
    native_parent_id: str | None = None
    upvotes: int | None = None

    @property
    def id(self) -> str:
        return comment_id(self.source_id, self.native_id)

    @property
    def parent_id(self) -> str | None:
        return comment_id(self.source_id, self.native_parent_id) if self.native_parent_id else None

    @property
    def day(self) -> str:
        return berlin_day(self.published_at)


@dataclass
class FetchResult:
    """Result of fetching one article: (possibly enriched) metadata plus all visible comments."""

    article: ArticleRef
    comments: list[Comment]
    # Total number of visible comments the source reports.
    reported_total: int | None = None
    # True if every visible comment was fetched (no early stop of paging).
    complete: bool = True
    # True if the source's comment count did not change since the last crawl; nothing was fetched.
    unchanged: bool = False
    # Native thread id discovered while fetching (stored as ``commentThreadId``).
    thread_id: str | None = None
