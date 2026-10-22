"""Per-source caps: config parsing, ranking and the runner's budget logic."""

from datetime import UTC, datetime, timedelta

import pytest

from crawler.config import SOURCE_LIMITS, source_limits
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.pipeline import RunOptions, SourceRunner
from crawler.sources.base import Source
from crawler.sources.blick import Blick
from crawler.sources.derstandard import DerStandard

from .test_pipeline import FakeStore

NOW = datetime.now(UTC)


def test_defaults_cap_only_derstandard_and_blick():
    limits = source_limits([])
    assert set(limits) == {"derstandard", "blick"} == set(SOURCE_LIMITS)
    assert all(v > 0 for vals in limits.values() for v in vals.values())


def test_cli_and_env_overrides(monkeypatch):
    monkeypatch.setenv("CRAWLER_SOURCE_LIMITS", '{"taz": {"max_comments_per_run": 50}}')
    limits = source_limits(
        ["derstandard.max_comments_per_run=3000", "blick=off", "derstandard.max_new_articles_per_run=0"]
    )
    assert limits["derstandard"] == {"max_comments_per_run": 3000}
    assert "blick" not in limits
    assert limits["taz"] == {"max_comments_per_run": 50}
    with pytest.raises(ValueError):
        source_limits(["derstandard.unknown=1"])


class CappedSource(Source):
    id = "capped"
    name = "Capped"
    domain = "capped.example"
    country = "DE"
    news_sitemap = "https://capped.example/news.xml"

    def __init__(self, n_new, comments_per_article):
        super().__init__(http=None)
        self.n_new = n_new
        self.per_article = comments_per_article
        self.fetched = []

    def canonical_url(self, url):
        return url

    def discover(self, since, until):
        return [
            ArticleRef(url=f"https://capped.example/{i}", source_id=self.id, title=str(i),
                       published_at=NOW - timedelta(minutes=10 * i))
            for i in range(self.n_new)
        ]  # fmt: skip

    def rank_new(self, refs, limit=None):
        return sorted(refs, key=lambda r: int(r.url.rsplit("/", 1)[-1]) % 3)  # deterministic "relevance"

    def fetch(self, ref):
        self.fetched.append(ref.url)
        comments = [
            Comment(self.id, f"{ref.url}#{j}", "t", NOW - timedelta(minutes=j)) for j in range(self.per_article)
        ]
        return FetchResult(ref, comments, len(comments))


def test_max_new_articles_takes_most_relevant():
    src = CappedSource(n_new=9, comments_per_article=1)
    opts = RunOptions(source_limits={"capped": {"max_new_articles_per_run": 3}})
    stats = SourceRunner(src, FakeStore(), opts, None).run()
    assert stats.articles_crawled == 3 and stats.articles_capped == 6
    assert [int(u.rsplit("/", 1)[-1]) % 3 for u in src.fetched] == [0, 0, 0]


def test_comment_budget_stops_new_articles_but_finishes_current_one():
    src = CappedSource(n_new=6, comments_per_article=40)
    opts = RunOptions(source_limits={"capped": {"max_comments_per_run": 100}})
    stats = SourceRunner(src, FakeStore(), opts, None).run()
    # 40 + 40 + 40 = 120 >= 100: the third article is completed, then no new article is started
    assert stats.articles_crawled == 3 and stats.comments_new == 120 and stats.articles_capped == 3


def test_revisits_are_not_capped():
    store = FakeStore()
    src = CappedSource(n_new=4, comments_per_article=5)
    SourceRunner(src, store, RunOptions(), None).run()  # uncapped first run tracks 4 articles
    for path, doc in store.docs.items():
        if path.startswith("articles/"):
            doc["lastCommentCrawlAt"] = NOW - timedelta(hours=2)
    src.fetched.clear()
    opts = RunOptions(source_limits={"capped": {"max_new_articles_per_run": 1, "max_comments_per_run": 1}})
    stats = SourceRunner(src, store, opts, None).run()
    assert len(src.fetched) == 4 and stats.articles_capped == 0


class _Resp:
    def __init__(self, text):
        self.text = text


class _DsHttp:
    def __init__(self, preferred_ids, counts):
        self.preferred = preferred_ids
        self.counts = counts
        self.forum_calls = 0

    def get(self, url, **kw):
        if "/rss/inland" in url:
            return _Resp(
                "".join(f"<link>https://www.derstandard.at/story/{i}/x?ref=rss</link>" for i in self.preferred)
            )
        return _Resp("<rss></rss>")

    def get_json(self, url, params=None, **kw):
        import json

        self.forum_calls += 1
        story = json.loads(params["variables"])["contextUri"].rsplit("/", 1)[-1]
        return {"data": {"getForumByContextUri": {"id": story, "totalPostingCount": self.counts.get(story, 0)}}}


def _ds_ref(i, minutes_ago):
    return ArticleRef(
        f"https://www.derstandard.at/story/{i}", "derstandard", published_at=NOW - timedelta(minutes=minutes_ago)
    )


def test_derstandard_ranks_priority_sections_then_activity():
    http = _DsHttp(preferred_ids={"1", "2", "3"}, counts={"1": 5, "2": 500, "3": 50, "4": 9999})
    ds = DerStandard(http)
    refs = [_ds_ref(1, 10), _ds_ref(2, 20), _ds_ref(3, 30), _ds_ref(4, 5)]
    ranked = ds.rank_new(refs, limit=1)
    assert [r.url[-1] for r in ranked] == ["2", "3", "1", "4"]  # section first, then posting count
    assert http.forum_calls == 3  # only the priority pool (4 x limit) is counted
    # forum info is cached and reused by fetch(): no second GetForumInfo
    assert ds.forum_info(refs[1].url)["totalPostingCount"] == 500 and http.forum_calls == 3


def test_blick_ranks_priority_sections_first():
    b = Blick(http=None)
    refs = [
        ArticleRef(
            "https://www.blick.ch/a1", "blick", published_at=NOW, discovered_url="https://www.blick.ch/people-tv/x/a1"
        ),
        ArticleRef(
            "https://www.blick.ch/a2",
            "blick",
            published_at=NOW - timedelta(hours=1),
            discovered_url="https://www.blick.ch/politik/x/a2",
        ),
    ]
    assert [r.url for r in b.rank_new(refs, 1)] == ["https://www.blick.ch/a2", "https://www.blick.ch/a1"]
