"""Data classes passed between source adapters, pipeline and Firestore writer.

They deliberately have **no author fields**: adapters never extract user names,
user IDs, avatars or profile links, so they cannot leak into storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from crawler.ids import article_id, berlin_day, comment_id


@dataclass
class ArticleRef:
    """An article found during discovery (sitemap) or loaded from Firestore for a revisit."""

    url: str  # canonical URL
    source_id: str
    title: str = ""
    published_at: datetime | None = None
    topics: list[str] = field(default_factory=list)
    # Set when the article already exists in Firestore.
    known: bool = False
    last_comment_crawl_at: datetime | None = None
    comment_count: int = 0

    @property
    def id(self) -> str:
        return article_id(self.url)


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
    # Total number of comments the source reports (for logging only).
    reported_total: int | None = None
