"""Crawl orchestration: discovery -> revisit selection -> comment fetching -> Firestore."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import IO, Any

from crawler.config import COMMENT_OVERLAP_MINUTES, MIN_REVISIT_MINUTES, REVISIT_DAYS
from crawler.firestore import Firestore, Write
from crawler.http import PoliteSession, RobotsDisallowed
from crawler.logging_setup import log_event
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources import SOURCES, Source

log = logging.getLogger(__name__)


@dataclass
class SourceStats:
    source: str
    discovered: int = 0
    revisit_candidates: int = 0
    articles_crawled: int = 0
    articles_new: int = 0
    articles_skipped_recent: int = 0
    articles_failed: int = 0
    comments_seen: int = 0
    comments_new: int = 0
    comments_existing: int = 0
    comments_failed: int = 0
    duration_s: float = 0.0
    error: str | None = None


@dataclass
class RunOptions:
    max_articles: int = 500
    since_hours: float = 48.0
    dry_run: bool = False
    time_budget_s: float | None = None
    out: IO[str] | None = None  # dry-run JSONL output (comments incl. text)


@dataclass
class RunSummary:
    started_at: str
    finished_at: str = ""
    dry_run: bool = False
    sources: list[SourceStats] = field(default_factory=list)
    http_requests: int = 0
    firestore_writes: int = 0

    def totals(self) -> dict[str, int]:
        keys = ("articles_crawled", "articles_new", "comments_new", "comments_existing", "articles_failed")
        return {k: sum(getattr(s, k) for s in self.sources) for k in keys}

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["totals"] = self.totals()
        return d

    def to_markdown(self) -> str:
        head = (
            "| source | discovered | revisit | crawled | new articles | comments seen | new comments "
            "| failed | seconds | error |\n|---|---|---|---|---|---|---|---|---|---|\n"
        )
        rows = "".join(
            f"| {s.source} | {s.discovered} | {s.revisit_candidates} | {s.articles_crawled} | {s.articles_new} "
            f"| {s.comments_seen} | {s.comments_new} | {s.articles_failed + s.comments_failed} "
            f"| {s.duration_s:.0f} | {s.error or ''} |\n"
            for s in self.sources
        )
        mode = " (dry run)" if self.dry_run else ""
        return (
            f"### Crawl run {self.started_at}{mode}\n\n{head}{rows}\n"
            f"HTTP requests: {self.http_requests}, Firestore writes: {self.firestore_writes}\n"
        )


def revisit_due(ref: ArticleRef, now: datetime) -> bool:
    """Back off revisits as articles age: most comments arrive in the first hours."""
    if not ref.known or ref.last_comment_crawl_at is None:
        return True
    age = now - (ref.published_at or now)
    if age > timedelta(days=REVISIT_DAYS):
        return False
    if age < timedelta(hours=12):
        min_gap = timedelta(minutes=MIN_REVISIT_MINUTES)
    elif age < timedelta(hours=36):
        min_gap = timedelta(hours=3)
    else:
        min_gap = timedelta(hours=6)
    return now - ref.last_comment_crawl_at >= min_gap - timedelta(minutes=5)


def comments_to_store(result: FetchResult) -> list[Comment]:
    """On revisits only comments newer than the previous crawl (minus an overlap) are written."""
    ref = result.article
    if not ref.known or ref.last_comment_crawl_at is None:
        return result.comments
    cutoff = ref.last_comment_crawl_at - timedelta(minutes=COMMENT_OVERLAP_MINUTES)
    return [c for c in result.comments if c.published_at >= cutoff]


def comment_doc(c: Comment, article_id: str, crawled_at: datetime) -> dict[str, Any]:
    """Firestore document for a new comment (SCHEMA.md ``comments``). No author fields."""
    return {
        "articleId": article_id,
        "sourceId": c.source_id,
        "text": c.text,
        "publishedAt": c.published_at,
        "crawledAt": crawled_at,
        "day": c.day,
        "parentId": c.parent_id,
        "upvotes": c.upvotes,
        "analyzed": False,
        "analyzedAt": None,
        "analysisVersion": None,
    }


def article_doc(ref: ArticleRef, crawled_at: datetime, comment_count: int) -> dict[str, Any]:
    return {
        "url": ref.url,
        "sourceId": ref.source_id,
        "title": ref.title,
        "publishedAt": ref.published_at or crawled_at,
        "crawledAt": crawled_at,
        "lastCommentCrawlAt": crawled_at,
        "commentCount": comment_count,
        "topics": ref.topics[:20],
    }


class SourceRunner:
    def __init__(self, source: Source, store: Firestore | None, opts: RunOptions, deadline: float | None) -> None:
        self.source = source
        self.store = store
        self.opts = opts
        self.deadline = deadline
        self.stats = SourceStats(source.id)
        self.log = logging.getLogger(f"crawler.run.{source.id}")

    def _ev(self, event: str, level: int = logging.INFO, **fields: Any) -> None:
        log_event(self.log, event, level, source=self.source.id, **fields)

    # -- candidate selection

    def candidates(self, now: datetime) -> list[ArticleRef]:
        src = self.source
        discovered = src.discover(now - timedelta(hours=self.opts.since_hours), now + timedelta(minutes=15))
        self.stats.discovered = len(discovered)
        by_id: dict[str, ArticleRef] = {r.id: r for r in discovered}
        if self.store is not None:
            known = self.store.batch_get([f"articles/{aid}" for aid in by_id])
            for path, fields in known.items():
                if fields:
                    self._mark_known(by_id[path.split("/", 1)[1]], fields)
            since = now - timedelta(days=REVISIT_DAYS)
            try:
                rows = self.store.run_query(
                    "articles",
                    [("sourceId", "EQUAL", src.id), ("publishedAt", "GREATER_THAN_OR_EQUAL", since)],
                    order_by=[("publishedAt", "DESCENDING")],
                    limit=2000,
                )
            except RuntimeError as exc:  # e.g. missing composite index
                self._ev("revisit.query_failed", logging.WARNING, error=str(exc)[:300])
                rows = []
            self.stats.revisit_candidates = len(rows)
            for doc_id, fields in rows:
                if doc_id not in by_id:
                    ref = ArticleRef(url=fields.get("url", ""), source_id=src.id)
                    self._mark_known(ref, fields)
                    if ref.url and ref.id == doc_id:
                        by_id[doc_id] = ref
        refs = sorted(by_id.values(), key=lambda r: r.published_at or now, reverse=True)
        due = [r for r in refs if revisit_due(r, now)]
        self.stats.articles_skipped_recent = len(refs) - len(due)
        return due[: self.opts.max_articles]

    @staticmethod
    def _mark_known(ref: ArticleRef, fields: dict[str, Any]) -> None:
        ref.known = True
        ref.title = fields.get("title") or ref.title
        ref.published_at = fields.get("publishedAt") or ref.published_at
        ref.topics = fields.get("topics") or ref.topics
        ref.last_comment_crawl_at = fields.get("lastCommentCrawlAt")
        ref.comment_count = int(fields.get("commentCount") or 0)

    # -- storing

    def store_result(self, result: FetchResult, crawled_at: datetime) -> Write | None:
        """Write new comments; return the article write (or None in dry-run)."""
        ref = result.article
        new_candidates = comments_to_store(result)
        if self.store is None:
            if self.opts.out is not None:
                for c in result.comments:
                    row = comment_doc(c, ref.id, crawled_at) | {"id": c.id, "articleUrl": ref.url}
                    self.opts.out.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")
            self.stats.comments_new += len(new_candidates)
            self.stats.articles_new += 0 if ref.known else 1
            return None
        writes = [
            self.store.create_write(f"comments/{c.id}", comment_doc(c, ref.id, crawled_at), tag=c.id)
            for c in new_candidates
        ]
        res = self.store.batch_write(writes) if writes else None
        inserted = len(res.ok) if res else 0
        existing = len(res.already_exists) if res else 0
        if res:
            self.stats.comments_new += inserted
            self.stats.comments_existing += len(res.already_exists)
            self.stats.comments_failed += len(res.failed)
            if res.failed:
                log_event(
                    self.log,
                    "comments.write_failed",
                    logging.ERROR,
                    article=ref.id,
                    failed=len(res.failed),
                    example=str(res.failed[0][1])[:200],
                )
                # Do not advance lastCommentCrawlAt, so the failed comments are retried next run.
                return None
        if ref.known:
            return self.store.update_write(
                f"articles/{ref.id}",
                {"lastCommentCrawlAt": crawled_at},
                increments={"commentCount": inserted} if inserted else None,
                tag=("article", False),
            )
        # (existing > 0 only if an earlier run stored comments but died before the article write.)
        return self.store.create_write(
            f"articles/{ref.id}", article_doc(ref, crawled_at, inserted + existing), tag=("article", True)
        )

    def flush_articles(self, writes: list[Write]) -> None:
        if not writes or self.store is None:
            return
        res = self.store.batch_write(writes)
        self.stats.articles_new += sum(1 for w in res.ok if w.tag == ("article", True))
        for _, status in res.failed:
            self._ev("article.write_failed", logging.ERROR, status=str(status)[:200])
        writes.clear()

    # -- main loop

    def run(self) -> SourceStats:
        t0 = time.monotonic()
        try:
            now = datetime.now(UTC)
            refs = self.candidates(now)
            log_event(
                self.log,
                "source.start",
                articles=len(refs),
                discovered=self.stats.discovered,
                revisit=self.stats.revisit_candidates,
                skipped_recent=self.stats.articles_skipped_recent,
            )
            pending: list[Write] = []
            for i, ref in enumerate(refs, 1):
                if self.deadline and time.monotonic() > self.deadline:
                    self._ev("source.time_budget_exhausted", logging.WARNING, done=i - 1)
                    break
                crawled_at = datetime.now(UTC)
                try:
                    result = self.source.fetch(ref)
                except RobotsDisallowed:
                    self.stats.articles_failed += 1
                    continue
                except Exception as exc:  # noqa: BLE001 - one broken article must not stop the run
                    self.stats.articles_failed += 1
                    log_event(
                        self.log,
                        "article.fetch_failed",
                        logging.WARNING,
                        url=ref.url,
                        error=f"{type(exc).__name__}: {exc}"[:300],
                    )
                    continue
                self.stats.articles_crawled += 1
                self.stats.comments_seen += len(result.comments)
                write = self.store_result(result, crawled_at)
                if write is not None:
                    pending.append(write)
                if len(pending) >= 100:
                    self.flush_articles(pending)
                log_event(
                    self.log,
                    "article.done",
                    logging.DEBUG,
                    n=i,
                    url=ref.url,
                    known=ref.known,
                    comments=len(result.comments),
                    reported=result.reported_total,
                )
                if i % 25 == 0:
                    self._ev("source.progress", done=i, of=len(refs), comments_new=self.stats.comments_new)
            self.flush_articles(pending)
        except Exception as exc:  # noqa: BLE001
            self.stats.error = f"{type(exc).__name__}: {exc}"[:300]
            self._ev("source.failed", logging.ERROR, error=self.stats.error)
        self.stats.duration_s = time.monotonic() - t0
        self._ev("source.done", **{k: v for k, v in asdict(self.stats).items() if k != "source"})
        return self.stats


def sync_sources(store: Firestore, sources: list[Source]) -> list[Source]:
    """Create/refresh ``sources/{id}`` docs and return the enabled sources."""
    docs = store.batch_get([f"sources/{s.id}" for s in sources])
    writes: list[Write] = []
    enabled: list[Source] = []
    for s in sources:
        doc = docs.get(f"sources/{s.id}")
        if doc is None:
            writes.append(
                store.create_write(
                    f"sources/{s.id}", s.source_doc() | {"enabled": True, "commentCount": 0, "lastCrawlAt": None}
                )
            )
            enabled.append(s)
        else:
            writes.append(store.update_write(f"sources/{s.id}", s.source_doc()))
            if doc.get("enabled", True):
                enabled.append(s)
            else:
                log_event(log, "source.disabled", source=s.id)
    store.batch_write(writes)
    return enabled


def finalize(store: Firestore, stats: list[SourceStats], all_source_count: int) -> None:
    now = datetime.now(UTC)
    writes = [
        store.update_write(
            f"sources/{s.source}",
            {"lastCrawlAt": now},
            increments={"commentCount": s.comments_new} if s.comments_new else None,
        )
        for s in stats
        if s.error is None
    ]
    writes.append(
        store.update_write(
            "meta/stats",
            {"lastCrawlAt": now, "sources": all_source_count},
            increments={"comments": sum(s.comments_new for s in stats), "articles": sum(s.articles_new for s in stats)},
        )
    )
    res = store.batch_write(writes)
    for _, status in res.failed:
        log_event(log, "finalize.write_failed", logging.ERROR, status=str(status)[:200])


def run(source_ids: list[str], opts: RunOptions, store: Firestore | None) -> RunSummary:
    summary = RunSummary(started_at=datetime.now(UTC).isoformat(timespec="seconds"), dry_run=opts.dry_run)
    http = PoliteSession()
    sources = [SOURCES[sid](http) for sid in source_ids]
    if store is not None:
        sources = sync_sources(store, sources)
    deadline = time.monotonic() + opts.time_budget_s if opts.time_budget_s else None
    runners = [SourceRunner(s, store, opts, deadline) for s in sources]
    # One worker per source: sources live on different hosts, each host is still rate limited.
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(runners)))) as pool:
        summary.sources = list(pool.map(lambda r: r.run(), runners))
    if store is not None:
        finalize(store, summary.sources, len(SOURCES))
        summary.firestore_writes = store.write_count
    summary.http_requests = http.request_count
    summary.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
    return summary
