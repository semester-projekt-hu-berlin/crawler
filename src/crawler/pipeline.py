"""Crawl orchestration: discovery -> revisit selection -> comment fetching -> Firestore."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import IO, Any

from crawler.config import (
    COMMENT_OVERLAP,
    DAILY_SLOT_END_HOUR_UTC,
    FRESH_HOURS,
    MIN_REVISIT_MINUTES,
    REVISIT_DAYS,
)
from crawler.firestore import Firestore, Write
from crawler.http import PoliteSession, RobotsDisallowed
from crawler.logging_setup import log_event
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources import SOURCES, Source
from crawler.sources.base import StreamItem, StreamSource

log = logging.getLogger(__name__)

# Article fields the revisit logic needs (the query reads only these).
ARTICLE_STATE_FIELDS = [
    "url",
    "title",
    "publishedAt",
    "topics",
    "lastCommentCrawlAt",
    "commentCount",
    "lastCommentAt",
    "knownCommentCount",
    "commentThreadId",
]


@dataclass
class SourceStats:
    source: str
    discovered: int = 0
    revisit_candidates: int = 0
    articles_crawled: int = 0
    articles_new: int = 0
    articles_skipped_recent: int = 0
    articles_unchanged: int = 0
    articles_partial: int = 0
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
    # Stream sources: max posts/comments per source and run.
    max_items: int = 3000
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
    http_bytes: int = 0
    firestore_writes: int = 0
    firestore_reads: int = 0

    def totals(self) -> dict[str, int]:
        keys = (
            "articles_crawled",
            "articles_new",
            "articles_unchanged",
            "comments_new",
            "comments_existing",
            "articles_failed",
        )
        return {k: sum(getattr(s, k) for s in self.sources) for k in keys}

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["totals"] = self.totals()
        return d

    def to_markdown(self) -> str:
        head = (
            "| source | discovered | revisit | crawled | new articles | unchanged | comments seen | new comments "
            "| failed | seconds | error |\n|---|---|---|---|---|---|---|---|---|---|---|\n"
        )
        rows = "".join(
            f"| {s.source} | {s.discovered} | {s.revisit_candidates} | {s.articles_crawled} "
            f"| {s.articles_new} | {s.articles_unchanged} "
            f"| {s.comments_seen} | {s.comments_new} | {s.articles_failed + s.comments_failed} "
            f"| {s.duration_s:.0f} | {s.error or ''} |\n"
            for s in self.sources
        )
        mode = " (dry run)" if self.dry_run else ""
        return (
            f"### Crawl run {self.started_at}{mode}\n\n{head}{rows}\n"
            f"HTTP requests: {self.http_requests} ({self.http_bytes / 1e6:.1f} MB on the wire), "
            f"Firestore writes: {self.firestore_writes}, Firestore document reads: {self.firestore_reads}\n"
        )


def revisit_due(ref: ArticleRef, now: datetime) -> bool:
    """Back off revisits as articles age: most comments arrive in the first hours.

    * never crawled: always
    * older than REVISIT_DAYS: never
    * up to FRESH_HOURS old: if the last crawl is at least MIN_REVISIT_MINUTES ago
    * older: only in the daily slot (runs starting before DAILY_SLOT_END_HOUR_UTC) and at most
      once per day. The slot also bounds articles whose last crawl found no change (their
      ``lastCommentCrawlAt`` is not rewritten, to save writes) to one cheap count check a day.
    """
    if not ref.known or ref.last_comment_crawl_at is None:
        return True
    age = now - (ref.published_at or now)
    if age > timedelta(days=REVISIT_DAYS):
        return False
    gap = now - ref.last_comment_crawl_at
    if age <= timedelta(hours=FRESH_HOURS):
        return gap >= timedelta(minutes=MIN_REVISIT_MINUTES - 5)
    return now.hour < DAILY_SLOT_END_HOUR_UTC and gap >= timedelta(hours=12)


def priority(ref: ArticleRef, now: datetime) -> datetime:
    """Most recent activity first: the newer of publication and newest stored comment."""
    times = [t for t in (ref.published_at, ref.last_comment_at) if t is not None]
    return max(times) if times else now


def comments_to_store(result: FetchResult) -> list[Comment]:
    """Comments that are not stored yet and must be written.

    First crawl: all. Revisit: those published after the previous crawl minus a small
    overlap. If the full list was fetched but fewer new comments turned up than the
    source's count grew (comments released late from moderation keep their original
    timestamp), all fetched comments are sent; the ``exists: false`` precondition
    then skips the stored ones.
    """
    ref = result.article
    since = ref.since(COMMENT_OVERLAP)
    if result.unchanged:
        return []
    if since is None:
        return result.comments
    fresh = [c for c in result.comments if c.published_at >= since]
    expected = ref.expected_new(result.reported_total)
    if result.complete and expected is not None:
        found = sum(1 for c in fresh if ref.is_new_comment(c.published_at))
        if found < expected:
            return result.comments
    return fresh


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


def article_doc(
    ref: ArticleRef,
    crawled_at: datetime,
    comment_count: int,
    result: FetchResult,
    last_comment_at: datetime | None,
    source_type: str = "news",
) -> dict[str, Any]:
    doc = {
        "url": ref.url,
        "sourceId": ref.source_id,
        "title": ref.title,
        "publishedAt": ref.published_at or crawled_at,
        "crawledAt": crawled_at,
        "lastCommentCrawlAt": crawled_at,
        "commentCount": comment_count,
        "topics": ref.topics[:20],
        "lastCommentAt": last_comment_at,
        "knownCommentCount": result.reported_total,
        "sourceType": source_type,
    }
    if result.thread_id:
        doc["commentThreadId"] = result.thread_id
    return doc


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
            since = now - timedelta(days=REVISIT_DAYS)
            try:
                rows = self.store.run_query(
                    "articles",
                    [("sourceId", "EQUAL", src.id), ("publishedAt", "GREATER_THAN_OR_EQUAL", since)],
                    order_by=[("publishedAt", "DESCENDING")],
                    limit=2000,
                    select=ARTICLE_STATE_FIELDS,
                )
            except RuntimeError as exc:  # e.g. missing composite index
                self._ev("revisit.query_failed", logging.WARNING, error=str(exc)[:300])
                rows = []
            self.stats.revisit_candidates = len(rows)
            for doc_id, fields in rows:
                ref = by_id.get(doc_id) or ArticleRef(url=fields.get("url", ""), source_id=src.id)
                self._mark_known(ref, fields)
                if ref.url and ref.id == doc_id:
                    by_id[doc_id] = ref
            # Discovered articles the query did not return (normally the genuinely new ones).
            unseen = [aid for aid, r in by_id.items() if not r.known]
            for path, fields in self.store.batch_get([f"articles/{aid}" for aid in unseen]).items():
                if fields:
                    self._mark_known(by_id[path.split("/", 1)[1]], fields)
        refs = sorted(by_id.values(), key=lambda r: priority(r, now), reverse=True)
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
        ref.last_comment_at = fields.get("lastCommentAt")
        known_count = fields.get("knownCommentCount")
        ref.known_comment_count = int(known_count) if known_count is not None else None
        ref.comment_thread_id = fields.get("commentThreadId")

    # -- storing

    def store_result(self, result: FetchResult, crawled_at: datetime) -> Write | None:
        """Write new comments; return the article write if anything changed (None in dry-run)."""
        ref = result.article
        # One batch must not contain the same document twice (paged APIs can repeat items).
        to_write = list({c.id: c for c in comments_to_store(result)}.values())
        if result.unchanged:
            self.stats.articles_unchanged += 1
        if not result.complete:
            self.stats.articles_partial += 1
        if self.store is None:
            if self.opts.out is not None:
                for c in to_write:
                    row = comment_doc(c, ref.id, crawled_at) | {"id": c.id, "articleUrl": ref.url}
                    self.opts.out.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")
            self.stats.comments_new += len(to_write)
            self.stats.articles_new += 0 if ref.known else 1
            return None
        writes = [
            self.store.create_write(f"comments/{c.id}", comment_doc(c, ref.id, crawled_at), tag=c) for c in to_write
        ]
        res = self.store.batch_write(writes) if writes else None
        inserted = [w.tag for w in res.ok] if res else []
        existing = len(res.already_exists) if res else 0
        if res:
            self.stats.comments_new += len(inserted)
            self.stats.comments_existing += existing
            self.stats.comments_failed += len(res.failed)
            if res.failed:
                self._ev(
                    "comments.write_failed",
                    logging.ERROR,
                    article=ref.id,
                    failed=len(res.failed),
                    example=str(res.failed[0][1])[:200],
                )
                # Do not advance lastCommentCrawlAt, so the failed comments are retried next run.
                return None
        newest = max([c.published_at for c in inserted], default=None)
        last_comment_at = max([t for t in (ref.last_comment_at, newest) if t is not None], default=None)
        if not ref.known:
            # (existing > 0 only if an earlier run stored comments but died before the article write.)
            return self.store.create_write(
                f"articles/{ref.id}",
                article_doc(
                    ref, crawled_at, len(inserted) + existing, result, last_comment_at, self.source.source_type
                ),
                tag=("article", True),
            )
        # Known article: write only if something changed.
        update: dict[str, Any] = {}
        if result.reported_total is not None and result.reported_total != ref.known_comment_count:
            update["knownCommentCount"] = result.reported_total
        if inserted:
            update["lastCommentAt"] = last_comment_at
        if result.thread_id and result.thread_id != ref.comment_thread_id:
            update["commentThreadId"] = result.thread_id
        if not update:
            return None
        update["lastCommentCrawlAt"] = crawled_at
        return self.store.update_write(
            f"articles/{ref.id}",
            update,
            increments={"commentCount": len(inserted)} if inserted else None,
            tag=("article", False),
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


class StreamRunner:
    """Runs a :class:`StreamSource`: everything since the previous run, grouped by (pseudo-)article."""

    def __init__(self, source: StreamSource, store: Firestore | None, opts: RunOptions,
                 last_crawl_at: datetime | None) -> None:  # fmt: skip
        self.source = source
        self.store = store
        self.opts = opts
        self.last_crawl_at = last_crawl_at
        self.stats = SourceStats(source.id)
        self.log = logging.getLogger(f"crawler.run.{source.id}")

    def _ev(self, event: str, level: int = logging.INFO, **fields: Any) -> None:
        log_event(self.log, event, level, source=self.source.id, **fields)

    def window(self, now: datetime) -> datetime:
        since = now - timedelta(hours=self.opts.since_hours)
        if self.last_crawl_at is not None:
            since = max(since, self.last_crawl_at - self.source.overlap)
        return since

    def drop_stored(self, items: list[StreamItem]) -> list[StreamItem]:
        """Items from the overlap window may already be stored: check with a read, never re-insert."""
        if self.store is None or self.last_crawl_at is None:
            return items
        old = [i for i in items if i.comment.published_at <= self.last_crawl_at]
        if not old:
            return items
        found = self.store.batch_get([f"comments/{i.comment.id}" for i in old])
        stored = {path.split("/", 1)[1] for path, doc in found.items() if doc is not None}
        self.stats.comments_existing += len(stored)
        return [i for i in items if i.comment.id not in stored]

    def run(self) -> SourceStats:
        t0 = time.monotonic()
        try:
            now = datetime.now(UTC)
            since = self.window(now)
            items = self.source.fetch_since(since, now, self.opts.max_items)
            self.stats.comments_seen = len(items)
            self._ev("source.start", since=since.isoformat(timespec="seconds"), items=len(items))
            self.store_items(self.drop_stored(items), now)
        except Exception as exc:  # noqa: BLE001
            self.stats.error = f"{type(exc).__name__}: {exc}"[:300]
            self._ev("source.failed", logging.ERROR, error=self.stats.error)
        self.stats.duration_s = time.monotonic() - t0
        self._ev("source.done", **{k: v for k, v in asdict(self.stats).items() if k != "source"})
        return self.stats

    def store_items(self, items: list[StreamItem], crawled_at: datetime) -> None:
        by_article: dict[str, tuple[ArticleRef, list[Comment]]] = {}
        seen: set[str] = set()
        for item in items:
            # Paged feeds can return an item twice when new items shift the pages.
            if item.comment.id in seen:
                continue
            seen.add(item.comment.id)
            ref, comments = by_article.setdefault(item.article.id, (item.article, []))
            comments.append(item.comment)
        self.stats.articles_crawled = len(by_article)
        if self.store is None:
            for ref, comments in by_article.values():
                if self.opts.out is not None:
                    for c in comments:
                        row = comment_doc(c, ref.id, crawled_at) | {"id": c.id, "articleUrl": ref.url}
                        self.opts.out.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")
                self.stats.comments_new += len(comments)
            return
        existing = self.store.batch_get([f"articles/{aid}" for aid in by_article])
        article_writes: list[Write] = []
        for aid, (ref, comments) in by_article.items():
            res = self.store.batch_write(
                [self.store.create_write(f"comments/{c.id}", comment_doc(c, aid, crawled_at), tag=c) for c in comments]
            )
            inserted = [w.tag for w in res.ok]
            self.stats.comments_new += len(inserted)
            self.stats.comments_existing += len(res.already_exists)
            self.stats.comments_failed += len(res.failed)
            if res.failed:
                self._ev("comments.write_failed", logging.ERROR, article=aid, failed=len(res.failed))
            doc = existing.get(f"articles/{aid}")
            if not inserted and doc is not None:
                continue
            newest = max(c.published_at for c in comments)
            if doc is None:
                # already_exists > 0 only if an earlier run stored comments but failed before the article write
                fake = FetchResult(ref, comments, None)
                count = len(inserted) + len(res.already_exists)
                article_writes.append(self.store.create_write(
                    f"articles/{aid}",
                    article_doc(ref, crawled_at, count, fake, newest, self.source.source_type),
                    tag=("article", True),
                ))  # fmt: skip
            else:
                newest = max(c.published_at for c in inserted)
                last = max(t for t in (doc.get("lastCommentAt"), newest) if t is not None)
                article_writes.append(self.store.update_write(
                    f"articles/{aid}", {"lastCommentCrawlAt": crawled_at, "lastCommentAt": last},
                    increments={"commentCount": len(inserted)}, tag=("article", False),
                ))  # fmt: skip
        if article_writes:
            res = self.store.batch_write(article_writes)
            self.stats.articles_new += sum(1 for w in res.ok if w.tag == ("article", True))


def sync_sources(
    store: Firestore, sources: list[Source | StreamSource]
) -> tuple[list[Source | StreamSource], dict[str, dict[str, Any] | None]]:
    """Create/refresh ``sources/{id}`` docs; return the enabled sources and the docs."""
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
            changed = {k: v for k, v in s.source_doc().items() if doc.get(k) != v}
            if changed:
                writes.append(store.update_write(f"sources/{s.id}", changed))
            if doc.get("enabled", True):
                enabled.append(s)
            else:
                log_event(log, "source.disabled", source=s.id)
    if writes:
        store.batch_write(writes)
    return enabled, docs


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
    docs: dict[str, dict[str, Any] | None] = {}
    if store is not None:
        sources, docs = sync_sources(store, sources)
    deadline = time.monotonic() + opts.time_budget_s if opts.time_budget_s else None
    runners: list[SourceRunner | StreamRunner] = []
    for s in sources:
        if isinstance(s, StreamSource):
            doc = docs.get(f"sources/{s.id}") or {}
            runners.append(StreamRunner(s, store, opts, doc.get("lastCrawlAt")))
        else:
            runners.append(SourceRunner(s, store, opts, deadline))
    # One worker per source: sources live on different hosts, each host is still rate limited.
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(runners)))) as pool:
        summary.sources = list(pool.map(lambda r: r.run(), runners))
    if store is not None:
        finalize(store, summary.sources, len(SOURCES))
        summary.firestore_writes = store.write_count
        summary.firestore_reads = store.read_count
    summary.http_requests = http.request_count
    summary.http_bytes = http.bytes_received
    summary.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
    return summary
