"""Static configuration shared by all components of the crawler."""

from __future__ import annotations

import os

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
# Do not revisit an article if its comments were crawled less than this many minutes ago.
MIN_REVISIT_MINUTES = 60
# Comments published up to this many minutes before the last crawl are re-checked
# (moderation queues can delay the visibility of a comment).
COMMENT_OVERLAP_MINUTES = 60

FIRESTORE_PROJECT = os.environ.get("FIRESTORE_PROJECT", "meinungspuls-hu")
FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE", "(default)")
