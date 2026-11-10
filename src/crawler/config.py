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


# Per-source caps. Why: every stored comment costs one write here plus about five reads and two
# writes in the analysis, and the project's Firestore budget is small. The caps follow the measured
# yield of each source (mentions of tracked entities per 100 stored comments, see "Yield per source"
# in the README): sources that mostly produce comments without any tracked entity are cut, high-yield
# sources may take everything their site offers, and the right-leaning, public/centre and
# left-leaning news sources are kept in balance. All sources together must stay at or below
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
DAILY_COMMENT_TARGET = 9_500
DAILY_COMMENT_CAPS: dict[str, int] = {
    # social media
    "bluesky": 1_800,  # 200 "Deutsche Politik" feed + 1,600 search (90 % entity searches, 10 % sample)
    "bluesky_jetstream": 150,  # off by default (DEFAULT_DISABLED_SOURCES)
    "mastodon": 600,
    "feddit": 150,
    # news sites
    "tagesschau": 1_100,
    "tichyseinblick": 1_100,
    "taz": 500,
    "freitag": 100,
    "cicero": 200,
    "achgut": 100,
    "netzpolitik": 30,
    "ruhrbarone": 20,
    "apollo": 300,  # deterministic ~10 % sample (see ApolloNews.sample_share)
    "overton": 300,
    "lawblog": 20,  # off by default
    "br24": 400,  # ~2,000/day available: most-commented stories, newest comments first
    "rbb24": 300,
    # tech / app blogs (PRODUCT entities)
    "caschy": 200,
    "iphoneticker": 180,
    "ifun": 80,
    "appgefahren": 60,
    "computerbase": 300,
    # forums
    "wallstreet": 220,
    "allmystery": 300,
    "finanztip": 60,  # off by default
    "goingelectric": 80,
    "kuketz": 100,
    "bundestag": 40,  # off by default
    # Austria / Switzerland, kept small on purpose (German focus)
    "derstandard": 200,
    "blick": 60,
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


def _id_set(env: str) -> set[str]:
    return {s.strip().lower() for s in os.environ.get(env, "").split(",") if s.strip()}


# Sources that are not part of ``--sources all`` (they still run when named explicitly):
#   bluesky_jetstream  random German posts: 12 % mention a tracked entity (19 mentions per 100
#                      posts); the entity searches of ``bluesky`` deliver 126 per 100, and the
#                      5-minute live window was most of the run time
#   lawblog            the comment feed is almost only foreign-language spam (16 of 17 comments
#                      in a dry run; 5 mentions/day)
#   finanztip          16 mentions per 100 comments (10 a day) for ~20 requests and 80 s per run
#   bundestag          no forum post in 29 runs (14 days) for ~28 requests and 50 s per run
DEFAULT_DISABLED_SOURCES = {"bluesky_jetstream", "lawblog", "finanztip", "bundestag"}
# Switch further sources off with CRAWLER_DISABLED_SOURCES=derstandard,blick; switch default-off
# sources back on with CRAWLER_ENABLED_SOURCES=bundestag.
DISABLED_SOURCES = (DEFAULT_DISABLED_SOURCES | _id_set("CRAWLER_DISABLED_SOURCES")) - _id_set("CRAWLER_ENABLED_SOURCES")


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
