"""Incremental revisits: backoff, early stop of paging, count-unchanged skip, minimal writes."""

import json
from datetime import UTC, datetime, timedelta

from crawler.models import ArticleRef, Comment, FetchResult
from crawler.pipeline import RunOptions, SourceRunner, StreamRunner, comments_to_store, priority, revisit_due
from crawler.sources.base import IncrementalPager, Source, StreamItem, StreamSource
from crawler.sources.blick import Blick
from crawler.sources.derstandard import DerStandard
from crawler.sources.taz import Taz

from .test_pipeline import FakeStore

MORNING = datetime(2026, 10, 22, 4, 17, tzinfo=UTC)
EVENING = datetime(2026, 10, 22, 16, 17, tzinfo=UTC)


def _ref(published_ago_h, crawled_ago_h=None, now=MORNING, **kw):
    return ArticleRef(
        "https://x/a",
        "s",
        published_at=now - timedelta(hours=published_ago_h),
        known=crawled_ago_h is not None,
        last_comment_crawl_at=now - timedelta(hours=crawled_ago_h) if crawled_ago_h is not None else None,
        **kw,
    )


def _c(native, at, parent=None):
    return Comment("s", native, f"t{native}", at, parent, 0)


# ------------------------------------------------------------------ revisit policy


def test_new_articles_always_due():
    assert revisit_due(_ref(1), EVENING)


def test_fresh_articles_revisited_every_run_but_not_twice_an_hour():
    assert revisit_due(_ref(10, 12, now=EVENING), EVENING)
    assert not revisit_due(_ref(10, 0.5, now=EVENING), EVENING)


def test_older_articles_only_once_a_day_in_morning_slot():
    assert revisit_due(_ref(30, 24), MORNING)
    assert not revisit_due(_ref(30, 12, now=EVENING), EVENING)  # evening run skips them
    assert not revisit_due(_ref(30, 2), MORNING)  # crawled recently (manual run)


def test_stop_after_three_days():
    assert not revisit_due(_ref(73, 24), MORNING)


def test_priority_prefers_recent_comment_activity():
    quiet = _ref(5, 10)
    busy = _ref(40, 24, last_comment_at=MORNING - timedelta(minutes=30))
    fresh = _ref(2)
    ordered = sorted([quiet, busy, fresh], key=lambda r: priority(r, MORNING), reverse=True)
    assert ordered == [busy, fresh, quiet]


# ------------------------------------------------------------------ what gets written


def test_first_crawl_writes_everything():
    ref = _ref(1)
    comments = [_c("a", MORNING - timedelta(hours=5)), _c("b", MORNING)]
    assert comments_to_store(FetchResult(ref, comments, 2)) == comments


def test_revisit_writes_only_comments_after_last_crawl_minus_overlap():
    ref = _ref(10, 2, known_comment_count=3)
    last = ref.last_comment_crawl_at
    old = _c("old", last - timedelta(minutes=30))
    overlap = _c("overlap", last - timedelta(minutes=5))
    new = _c("new", last + timedelta(minutes=1))
    assert comments_to_store(FetchResult(ref, [old, overlap, new], 4)) == [overlap, new]


def test_unchanged_writes_nothing():
    ref = _ref(10, 2, known_comment_count=3)
    assert comments_to_store(FetchResult(ref, [], 3, complete=False, unchanged=True)) == []


def test_late_moderated_comments_trigger_full_insert_when_complete():
    ref = _ref(10, 2, known_comment_count=2)
    last = ref.last_comment_crawl_at
    # count grew by 2, but only one comment is newer than the last crawl
    comments = [_c("late", last - timedelta(hours=3)), _c("x", last - timedelta(hours=4)), _c("new", MORNING)]
    assert comments_to_store(FetchResult(ref, comments, 4, complete=True)) == comments
    # partial fetch (early stop) never falls back
    assert comments_to_store(FetchResult(ref, comments, 4, complete=False)) == [comments[2]]


# ------------------------------------------------------------------ pager


def test_pager_stops_when_count_growth_is_found():
    ref = _ref(10, 2, known_comment_count=5)
    last = ref.last_comment_crawl_at
    pager = IncrementalPager(ref, ref.since(timedelta(minutes=10)), expected_new=2)
    assert not pager.add_page([last + timedelta(minutes=1), last - timedelta(hours=1)])
    assert pager.add_page([last - timedelta(hours=2), last + timedelta(minutes=5)])


def test_pager_without_count_stops_at_first_old_page():
    ref = _ref(10, 2)
    last = ref.last_comment_crawl_at
    pager = IncrementalPager(ref, ref.since(timedelta(minutes=10)), expected_new=None)
    assert not pager.add_page([last + timedelta(minutes=1)])
    assert pager.add_page([last - timedelta(hours=1), last - timedelta(hours=2)])


def test_pager_never_stops_on_first_crawl():
    pager = IncrementalPager(_ref(1), None, None)
    assert not pager.add_page([MORNING - timedelta(days=1)])


# ------------------------------------------------------------------ adapters


class ScriptedHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get_json(self, url, params=None, headers=None, **kw):
        self.urls.append((url, params))
        return self.responses.pop(0)

    def get(self, url, **kw):
        self.urls.append((url, None))
        return self.responses.pop(0)


def _ds_forum(total):
    return {"data": {"getForumByContextUri": {"id": "F", "totalPostingCount": total, "stickyPostings": []}}}


def _ds_page(times, has_next):
    edges = [
        {"node": {"id": f"p{t.isoformat()}", "lifecycleStatus": "Published", "text": "x",
                  "history": {"created": t.isoformat()}, "replies": []}}
        for t in times
    ]  # fmt: skip
    info = {"hasNextPage": has_next, "nextCursor": "c" if has_next else ""}
    return {"data": {"getForumRootPostingsV2": {"edges": edges, "pageInfo": info}}}


def test_derstandard_unchanged_count_costs_one_request():
    ref = _ref(10, 2, known_comment_count=7)
    http = ScriptedHttp([_ds_forum(7)])
    result = DerStandard(http).fetch(ref)
    assert result.unchanged and result.comments == [] and len(http.urls) == 1


def test_derstandard_stops_paging_once_new_comments_found():
    ref = _ref(10, 2, known_comment_count=10)
    last = ref.last_comment_crawl_at
    pages = [
        _ds_page([last + timedelta(minutes=20), last + timedelta(minutes=10)], True),
        _ds_page([last - timedelta(hours=1)], True),
        _ds_page([last - timedelta(hours=3)], False),
    ]
    http = ScriptedHttp([_ds_forum(12), *pages])
    result = DerStandard(http).fetch(ref)
    assert len(http.urls) == 2  # forum info + first page only
    assert not result.complete and len(result.comments) == 2


def test_derstandard_first_crawl_reads_all_pages():
    ref = _ref(1)
    http = ScriptedHttp([_ds_forum(2), _ds_page([MORNING], True), _ds_page([MORNING - timedelta(hours=1)], False)])
    ds = DerStandard(http)
    ref.known = True  # skip the article page fetch in this test, but no crawl state yet
    result = ds.fetch(ref)
    assert len(http.urls) == 3 and result.complete and len(result.comments) == 2


def test_blick_revisit_with_known_thread_skips_article_page(fixture_path):
    data = json.loads(fixture_path("blick_comments.json").read_text())
    ref = _ref(10, 2, known_comment_count=data["container"]["total_visible_content"], comment_thread_id="cid")
    blick = Blick(ScriptedHttp([data["container"]]))
    blick._site_uuid = "site"
    result = blick.fetch(ref)
    assert result.unchanged
    assert [u for u, _ in blick.http.urls] == [
        "https://livecomments.viafoura.co/v4/livecomments/site/contentcontainer/id"
    ]


def test_taz_unchanged_when_count_equal(fixture_path):
    class Resp:
        text = fixture_path("taz_article.html").read_text()

    ref = _ref(10, 2, known_comment_count=3)
    result = Taz(ScriptedHttp([Resp()])).fetch(ref)
    assert result.unchanged and result.reported_total == 3 and result.comments == []


# ------------------------------------------------------------------ pipeline writes


class CountingSource(Source):
    id = "fake"
    name = "Fake"
    domain = "fake.example"
    country = "DE"
    news_sitemap = "https://fake.example/news.xml"

    def __init__(self, script):
        super().__init__(http=None)
        self.script = script

    def canonical_url(self, url):
        return url

    def discover(self, since, until):
        return [ArticleRef(url="https://fake.example/a/1", source_id=self.id, title="A",
                           published_at=datetime.now(UTC) - timedelta(hours=3))]  # fmt: skip

    def fetch(self, ref):
        comments, total = self.script.pop(0)
        if ref.known and ref.known_comment_count == total:
            return FetchResult(ref, [], total, complete=False, unchanged=True)
        return FetchResult(ref, comments, total)


def test_unchanged_revisit_sends_no_writes_and_changed_one_sends_only_new():
    now = datetime.now(UTC)
    first = [_c("1", now - timedelta(hours=3)), _c("2", now - timedelta(hours=2.5))]
    store = FakeStore()
    src = CountingSource([(first, 2), (first, 2), ([*first, _c("3", now + timedelta(minutes=1))], 3)])
    opts = RunOptions(max_articles=10, since_hours=48)

    SourceRunner(src, store, opts, None).run()
    article_path = next(p for p in store.docs if p.startswith("articles/"))
    article = store.docs[article_path]
    assert article["knownCommentCount"] == 2 and article["lastCommentAt"] == first[1].published_at

    article["lastCommentCrawlAt"] = now - timedelta(hours=2)  # due again
    before = store.write_count
    stats = SourceRunner(src, store, opts, None).run()
    assert stats.articles_unchanged == 1
    assert store.write_count == before  # nothing written at all

    before = store.write_count
    stats = SourceRunner(src, store, opts, None).run()
    assert stats.comments_new == 1 and stats.comments_existing == 0
    assert store.write_count - before == 2  # one comment insert + one article update
    assert article["commentCount"] == 3 and article["knownCommentCount"] == 3


# ------------------------------------------------------------------ stream sources


class FakeStream(StreamSource):
    id = "stream"
    name = "Stream"
    domain = "stream.example"
    country = "DE"

    def __init__(self, batches):
        super().__init__(http=None)
        self.batches = batches
        self.windows = []

    def fetch_since(self, since, until, max_items):
        self.windows.append(since)
        out = []
        for c in self.batches.pop(0):
            ref = self.day_article("https://stream.example/local", "Stream", c.published_at, [])
            out.append(StreamItem(ref, c))
        return out


def test_stream_runner_inserts_once_and_never_resends_overlap():
    now = datetime.now(UTC)
    a = _c("a", now - timedelta(minutes=30))
    b = _c("b", now - timedelta(minutes=5))
    c = _c("c", now + timedelta(minutes=1))
    store = FakeStore()
    src = FakeStream([[a, b], [b, c]])
    opts = RunOptions(since_hours=48)

    s1 = StreamRunner(src, store, opts, last_crawl_at=None).run()
    assert s1.comments_new == 2 and s1.articles_new == 1
    article = next(v for k, v in store.docs.items() if k.startswith("articles/"))
    assert article["commentCount"] == 2 and article["sourceType"] == "social"

    before = store.write_count
    s2 = StreamRunner(src, store, opts, last_crawl_at=now).run()
    # window starts at the previous crawl minus the overlap
    assert src.windows[1] == now - src.overlap
    assert s2.comments_new == 1 and s2.comments_existing == 1
    assert store.write_count - before == 2  # comment c + article update; b was filtered by a read
    assert article["commentCount"] == 3


def test_stream_runner_drops_duplicates_within_a_run():
    now = datetime.now(UTC)
    a = _c("a", now - timedelta(minutes=3))
    store = FakeStore()
    stats = StreamRunner(FakeStream([[a, a]]), store, RunOptions(), last_crawl_at=None).run()
    assert stats.error is None and stats.comments_new == 1
