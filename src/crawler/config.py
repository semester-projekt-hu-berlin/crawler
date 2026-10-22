"""Static configuration shared by all components of the crawler."""

from __future__ import annotations

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
