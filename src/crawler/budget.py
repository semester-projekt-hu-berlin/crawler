"""Daily Firestore budget shared by all components (doc ``meta/budget``, see SCHEMA.md).

``meta/budget = {day, writes: {crawler, analysis, api}, reads: {...}, updatedAt}``; ``day`` is
the Berlin day the counters belong to, a different day means all counters are 0.

* Run start: one read. If the combined writes reached ``DAILY_WRITE_LIMIT`` (or reads
  ``DAILY_READ_LIMIT``), or the crawler's own share is used up, the run is skipped (exit 0).
* During the run: the Firestore client counts its operations; the runners stop starting new
  articles once the remaining budget (minus a reserve for the final writes) is used.
* Run end: the run's counts are added with a single write (optimistic concurrency on
  ``updateTime``; a new day resets the other components' counters to 0).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from crawler.ids import berlin_day
from crawler.logging_setup import log_event

log = logging.getLogger(__name__)

COMPONENTS = ("crawler", "analysis", "api")
DOC = "meta/budget"
# Writes/reads kept back for the end of the run (article flush, finalize, budget update).
RESERVE_WRITES = 200
RESERVE_READS = 100


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass
class Limits:
    total_writes: int = field(default_factory=lambda: _env_int("DAILY_WRITE_LIMIT", 60000))
    total_reads: int = field(default_factory=lambda: _env_int("DAILY_READ_LIMIT", 200000))
    crawler_writes: int = field(default_factory=lambda: _env_int("CRAWLER_DAILY_WRITE_LIMIT", 25000))
    crawler_reads: int = field(default_factory=lambda: _env_int("CRAWLER_DAILY_READ_LIMIT", 60000))


def counters_for_today(doc: dict[str, Any] | None, today: str) -> tuple[dict[str, int], dict[str, int]]:
    """``(writes, reads)`` per component; all zero if the doc belongs to another day."""
    zero = {c: 0 for c in COMPONENTS}
    if not doc or doc.get("day") != today:
        return dict(zero), dict(zero)
    writes = zero | {k: int(v or 0) for k, v in (doc.get("writes") or {}).items()}
    reads = zero | {k: int(v or 0) for k, v in (doc.get("reads") or {}).items()}
    return writes, reads


@dataclass
class Budget:
    day: str
    writes: dict[str, int]
    reads: dict[str, int]
    limits: Limits

    @classmethod
    def load(cls, store, now: datetime | None = None, limits: Limits | None = None) -> Budget:  # noqa: ANN001
        today = berlin_day(now or datetime.now(UTC))
        writes, reads = counters_for_today(store.get(DOC), today)
        return cls(today, writes, reads, limits or Limits())

    # remaining for this crawler run, before any of its own operations
    @property
    def writes_left(self) -> int:
        return max(
            0,
            min(
                self.limits.total_writes - sum(self.writes.values()),
                self.limits.crawler_writes - self.writes["crawler"],
            ),
        )

    @property
    def reads_left(self) -> int:
        return max(
            0,
            min(
                self.limits.total_reads - sum(self.reads.values()),
                self.limits.crawler_reads - self.reads["crawler"],
            ),
        )

    @property
    def exhausted(self) -> bool:
        return self.writes_left <= RESERVE_WRITES or self.reads_left <= RESERVE_READS

    def should_stop(self, used_writes: int, used_reads: int) -> bool:
        """True once this run's own operations reach the remaining budget (minus the reserve)."""
        return used_writes >= self.writes_left - RESERVE_WRITES or used_reads >= self.reads_left - RESERVE_READS

    def summary(self, used_writes: int, used_reads: int) -> dict[str, int | str]:
        return {
            "day": self.day,
            "crawler_writes_today": self.writes["crawler"] + used_writes,
            "crawler_reads_today": self.reads["crawler"] + used_reads,
            "writes_left_today": max(0, self.writes_left - used_writes),
            "reads_left_today": max(0, self.reads_left - used_reads),
        }


def build_update(store, run_writes: int, run_reads: int, now: datetime, current: dict[str, Any] | None):  # noqa: ANN001, ANN201
    """The single write that adds this run's counts (resets other counters on a new day)."""
    today = berlin_day(now)
    if current and current.get("day") == today:
        return store.update_write(
            DOC,
            {"updatedAt": now},
            increments={"writes.crawler": run_writes, "reads.crawler": run_reads},
        )
    zero = {c: 0 for c in COMPONENTS}
    return store.update_write(
        DOC,
        {
            "day": today,
            "writes": zero | {"crawler": run_writes},
            "reads": zero | {"crawler": run_reads},
            "updatedAt": now,
        },
    )


def commit_usage(store, run_writes: int, run_reads: int, now: datetime | None = None, attempts: int = 5) -> bool:  # noqa: ANN001
    """Add this run's usage to ``meta/budget``. Retries if another component wrote in between."""
    now = now or datetime.now(UTC)
    for _ in range(attempts):
        current, update_time = store.get_with_update_time(DOC)
        write = build_update(store, run_writes, run_reads, now, current)
        write.body["currentDocument"] = {"updateTime": update_time} if update_time else {"exists": False}
        result = store.batch_write([write])
        if result.ok:
            return True
        log_event(log, "budget.update_conflict", logging.WARNING, status=str(result.failed[:1])[:200])
    log_event(log, "budget.update_failed", logging.ERROR, writes=run_writes, reads=run_reads)
    return False
