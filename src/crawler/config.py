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
# Hosts that asked for (or deserve) a slower pace than MIN_DELAY_PER_HOST (seconds).
HOST_MIN_DELAY: dict[str, float] = {
    "www.appgefahren.de": 5.0,
}
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


# Per-source caps. Why: Firestore cost, the analysis capacity (it can process ~14,700 new comments
# a day within its Firestore share, and a backlog plus new analysis features need headroom) and
# the German focus of the project. All sources together must stay at or below
# DAILY_COMMENT_TARGET new comments a day.
#
# DAILY_COMMENT_CAPS: new comments per source and day. The crawl runs RUNS_PER_DAY times a day,
# so the per-run cap ``max_comments_per_run`` is the daily cap divided by RUNS_PER_DAY.
#   Stream sources (comment feeds, social media, forums read as streams): hard cap per run.
#   Article sources: once the per-run cap is reached no further *new* article is started; with
#   ``strict`` the cap also stops revisits of tracked articles. Without ``strict``, revisits of
#   articles already tracked continue (complete threads), so the cap is a ceiling for new work,
#   and the real volume of these sources is what the measurements in the README show.
RUNS_PER_DAY = 2
DAILY_COMMENT_TARGET = 11_000
DAILY_COMMENT_CAPS: dict[str, int] = {
    # social media
    "bluesky": 2_300,  # 2,000 search (targeted + sample) + 300 "Deutsche Politik" feed
    "bluesky_jetstream": 700,
    "mastodon": 800,
    "feddit": 250,
    # news sites
    "tagesschau": 1_100,
    "tichyseinblick": 1_100,
    "taz": 300,
    "freitag": 60,
    "cicero": 150,
    "achgut": 100,
    "netzpolitik": 30,
    "ruhrbarone": 20,
    "apollo": 300,  # deterministic ~10 % sample (see ApolloNews.sample_share)
    "overton": 250,
    "lawblog": 60,
    "br24": 600,  # ~2,000/day available: most-commented stories, newest comments first
    "rbb24": 250,
    # tech / app blogs (PRODUCT entities)
    "caschy": 220,
    "iphoneticker": 250,
    "ifun": 150,
    "appgefahren": 100,
    "computerbase": 400,
    # forums
    "wallstreet": 300,
    "allmystery": 300,
    "finanztip": 200,
    "goingelectric": 150,
    "kuketz": 100,
    "bundestag": 150,
    # Austria / Switzerland, kept small on purpose (German focus)
    "derstandard": 200,
    "blick": 100,
}
# Caps besides the comment cap:
#   max_new_articles_per_run: new articles taken per run, most relevant first (revisits of
#                             articles already being tracked are not limited, so threads stay complete)
#   strict:                   1 = the comment cap also stops revisits of tracked articles
EXTRA_LIMITS: dict[str, dict[str, int]] = {
    "derstandard": {"max_new_articles_per_run": 2, "strict": 1},
    "blick": {"max_new_articles_per_run": 2, "strict": 1},
    "computerbase": {"max_new_articles_per_run": 6, "strict": 1},
    "br24": {"max_new_articles_per_run": 4, "strict": 1},
    "rbb24": {"max_new_articles_per_run": 40, "strict": 1},
}
SOURCE_LIMITS: dict[str, dict[str, int]] = {
    sid: {"max_comments_per_run": cap // RUNS_PER_DAY} | EXTRA_LIMITS.get(sid, {})
    for sid, cap in DAILY_COMMENT_CAPS.items()
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
