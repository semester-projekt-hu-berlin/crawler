"""Static configuration shared by all components of the crawler."""

from __future__ import annotations

import json
import os
from datetime import timedelta

from crawler import __version__

USER_AGENT = os.environ.get(
    "CRAWLER_USER_AGENT",
    f"MeinungspulsBot/{__version__} "
    "(+https://github.com/semester-projekt-hu-berlin/crawler; HU Berlin student project)",
)
# Product token used for robots.txt group matching ("User-agent: MeinungspulsBot").
ROBOTS_AGENT = "MeinungspulsBot"

# Politeness: minimum delay between two requests to the same host (seconds).
# A larger Crawl-delay from robots.txt always wins.
MIN_DELAY_PER_HOST = float(os.environ.get("CRAWLER_MIN_DELAY", "1.5"))
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

# Articles are revisited for new comments for this many days after publication.
REVISIT_DAYS = 3.0
# Revisit policy: articles up to 24 h old are revisited at most once per MIN_REVISIT_MINUTES,
# older ones (up to REVISIT_DAYS) only in the daily slot, i.e. in runs starting before
# DAILY_SLOT_END_HOUR_UTC (with the 04:17/16:17 UTC schedule: the morning run).
MIN_REVISIT_MINUTES = 60
FRESH_HOURS = 24
DAILY_SLOT_END_HOUR_UTC = 12
# On a revisit, comments published up to this long before the previous crawl are re-checked.
COMMENT_OVERLAP = timedelta(minutes=10)

FIRESTORE_PROJECT = os.environ.get("FIRESTORE_PROJECT", "meinungspuls-hu")
FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE", "(default)")


# Per-source caps (per run; the crawl runs twice a day). Why: Firestore cost, the analysis
# capacity (2 runs x 150 min/day on 2 vCPUs) and the German focus of the project. Uncapped
# sources are simply not listed.
#   max_new_articles_per_run: new articles taken per run, most relevant first (revisits of
#                             articles already being tracked are not limited, so threads stay complete)
#   max_comments_per_run:     once this many new comments were stored in a run, no further
#                             *new* articles are started (the current article is always finished)
#   strict:                   1 = the comment budget also stops revisits of tracked articles
SOURCE_LIMITS: dict[str, dict[str, int]] = {
    # Austria/Switzerland kept small on purpose (German focus): ~300/day and ~150/day.
    "derstandard": {"max_new_articles_per_run": 2, "max_comments_per_run": 150, "strict": 1},
    "blick": {"max_new_articles_per_run": 2, "max_comments_per_run": 75, "strict": 1},
    # small sources, capped only as a safety net for the Firestore budget
    "cicero": {"max_comments_per_run": 600},
    "achgut": {"max_comments_per_run": 400},
    # stream sources: max posts per run (Bluesky: targeted searches + sample, ~4,000/day)
    "bluesky": {"max_comments_per_run": 2000},
}
LIMIT_KEYS = ("max_new_articles_per_run", "max_comments_per_run", "strict")

# Sources switched off entirely, e.g. CRAWLER_DISABLED_SOURCES=derstandard,blick
DISABLED_SOURCES = {s.strip().lower() for s in os.environ.get("CRAWLER_DISABLED_SOURCES", "").split(",") if s.strip()}


def source_limits(overrides: list[str] | None = None) -> dict[str, dict[str, int]]:
    """``SOURCE_LIMITS`` merged with ``CRAWLER_SOURCE_LIMITS`` (JSON, same shape) and CLI overrides.

    CLI overrides look like ``derstandard.max_comments_per_run=3000``; a value of ``0`` removes
    that cap, ``derstandard=off`` removes all caps of a source.
    """
    limits = {k: dict(v) for k, v in SOURCE_LIMITS.items()}
    env = os.environ.get("CRAWLER_SOURCE_LIMITS", "").strip()
    if env:
        for sid, values in json.loads(env).items():
            limits.setdefault(sid, {}).update({k: int(v) for k, v in values.items()})
    for item in overrides or []:
        key, _, value = item.partition("=")
        sid, _, field = key.strip().partition(".")
        if not field:
            if value.strip().lower() in ("off", "none", "0"):
                limits.pop(sid, None)
                continue
            raise ValueError(f"invalid source limit {item!r}")
        if field not in LIMIT_KEYS:
            raise ValueError(f"unknown limit {field!r}; known: {', '.join(LIMIT_KEYS)}")
        limits.setdefault(sid, {})[field] = int(value)
    return {sid: {k: v for k, v in vals.items() if v > 0} for sid, vals in limits.items() if any(vals.values())}
