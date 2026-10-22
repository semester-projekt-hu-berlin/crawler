from datetime import UTC, datetime

from crawler.budget import RESERVE_WRITES, Budget, Limits, build_update, commit_usage, counters_for_today
from crawler.firestore import WriteResult, decode_fields
from crawler.pipeline import RunOptions, SourceRunner

from .test_limits import CappedSource
from .test_pipeline import FakeStore

NOON = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)  # 12:00 Berlin
LIMITS = Limits(total_writes=60000, total_reads=200000, crawler_writes=25000, crawler_reads=60000)


def _doc(day, crawler_w=0, analysis_w=0, crawler_r=0):
    return {"day": day, "writes": {"crawler": crawler_w, "analysis": analysis_w, "api": 0},
            "reads": {"crawler": crawler_r, "analysis": 0, "api": 0}}  # fmt: skip


def test_day_rollover_resets_counters():
    writes, reads = counters_for_today(_doc("2026-10-21", 24000, 30000), "2026-10-22")
    assert writes == {"crawler": 0, "analysis": 0, "api": 0} and sum(reads.values()) == 0
    writes, _ = counters_for_today(_doc("2026-10-22", 100, 200), "2026-10-22")
    assert writes["crawler"] == 100 and writes["analysis"] == 200
    assert counters_for_today(None, "2026-10-22")[0]["crawler"] == 0


def _budget(doc):
    w, r = counters_for_today(doc, "2026-10-22")
    return Budget("2026-10-22", w, r, LIMITS)


def test_remaining_is_min_of_total_and_crawler_share():
    assert _budget(_doc("2026-10-22", 5000, 10000)).writes_left == 20000  # crawler share
    assert _budget(_doc("2026-10-22", 5000, 50000)).writes_left == 5000  # total limit
    assert _budget(_doc("2026-10-22", 25000)).exhausted
    assert _budget(_doc("2026-10-22", 1000, 59000)).exhausted
    assert not _budget(_doc("2026-10-21", 25000, 59000)).exhausted  # yesterday's numbers do not count


def test_stop_condition_keeps_reserve():
    b = _budget(_doc("2026-10-22", 24000))  # 1000 left
    assert not b.should_stop(used_writes=1000 - RESERVE_WRITES - 1, used_reads=0)
    assert b.should_stop(used_writes=1000 - RESERVE_WRITES, used_reads=0)


def test_update_same_day_increments_new_day_resets():
    store = FakeStore()
    same = build_update(store, 300, 40, NOON, _doc("2026-10-22", 100))
    transforms = {t["fieldPath"]: int(t["increment"]["integerValue"]) for t in same.body["updateTransforms"]}
    assert transforms == {"writes.crawler": 300, "reads.crawler": 40}
    new = build_update(store, 300, 40, NOON, _doc("2026-10-21", 100, 5000))
    fields = decode_fields(new.body["update"]["fields"])
    assert fields["day"] == "2026-10-22"
    assert fields["writes"] == {"crawler": 300, "analysis": 0, "api": 0}
    assert "updateTransforms" not in new.body


class BudgetStore(FakeStore):
    """FakeStore with updateTime preconditions; the first commit loses against another component."""

    def __init__(self, doc):
        super().__init__()
        self.docs["meta/budget"] = doc
        self.version = 1
        self.conflicts = 1

    def get_with_update_time(self, path):
        return self.docs.get(path), f"v{self.version}" if path in self.docs else None

    def batch_write(self, writes):
        writes = list(writes)
        pre = writes[0].body.get("currentDocument", {})
        if "updateTime" in pre and self.conflicts:
            self.conflicts -= 1
            self.docs["meta/budget"]["writes"]["analysis"] += 7  # concurrent analysis update
            self.version += 1
            res = WriteResult()
            res.already_exists.extend(writes)
            return res
        res = super().batch_write([w for w in writes])
        self.version += 1
        return res


def test_commit_usage_retries_on_conflict_and_adds_counts():
    store = BudgetStore(_doc("2026-10-22", 100, 50))
    assert commit_usage(store, 300, 40, now=NOON)
    doc = store.docs["meta/budget"]
    assert doc["writes"]["crawler"] == 400 and doc["writes"]["analysis"] == 57 and doc["reads"]["crawler"] == 40


def test_runner_stops_starting_articles_when_budget_used():
    store = FakeStore()
    budget = _budget(_doc("2026-10-22", 25000 - RESERVE_WRITES - 30))  # 30 writes available for the run
    src = CappedSource(n_new=10, comments_per_article=20)
    stats = SourceRunner(src, store, RunOptions(budget=budget), None).run()
    # first article: 20 comment writes; second: 20 more -> 40 >= 30, third is not started
    assert stats.articles_crawled == 2


def test_run_is_skipped_when_budget_exhausted():
    from crawler.ids import berlin_day
    from crawler.pipeline import run

    store = FakeStore()
    store.docs["meta/budget"] = _doc(berlin_day(datetime.now(UTC)), 25000)
    summary = run(["taz"], RunOptions(), store)
    assert summary.skipped_budget and summary.sources == []
    assert store.write_count == 0  # nothing written, not even the counters
