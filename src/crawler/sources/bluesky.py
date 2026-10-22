"""Bluesky: a time-spread sample of German-language posts from the public AppView search.

``api.bsky.app/xrpc/app.bsky.feed.searchPosts`` is public; its robots.txt says
"Crawling the public parts of the API is allowed. HTTP 429 ("backoff") status codes are
used for rate-limiting. Up to a handful concurrent requests should be ok."

German Bluesky produces far more posts than we need or want to pay for (~100 per
minute), so each run takes a **sample**: the time since the previous run is split into
hourly windows and the newest ``SAMPLE_PER_WINDOW`` posts of each window are read
(``lang=de``, ``sort=latest``, ``since``/``until``). The search needs a query term; we
use the most frequent German word ("die"), which matches the large majority of German
posts. Authors who asked not to be shown to logged-out users (label
``!no-unauthenticated``) are skipped. Stored: text (mentions removed), indexing time,
reply structure. No handles, DIDs or display names.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from crawler.ids import parse_datetime
from crawler.models import Comment
from crawler.sources.base import StreamItem, StreamSource
from crawler.text import clean_text

API = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
QUERY = "die"
SAMPLE_PER_WINDOW = 100
WINDOW = timedelta(hours=1)
OPT_OUT_LABEL = "!no-unauthenticated"


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def post_text(record: dict[str, Any]) -> str:
    """Post text with mention facets cut out (facets index UTF-8 bytes)."""
    raw = (record.get("text") or "").encode("utf-8")
    cuts = []
    for facet in record.get("facets") or []:
        if any(f.get("$type", "").endswith("#mention") for f in facet.get("features") or []):
            idx = facet.get("index") or {}
            cuts.append((int(idx.get("byteStart", 0)), int(idx.get("byteEnd", 0))))
    for start, end in sorted(cuts, reverse=True):
        raw = raw[:start] + raw[end:]
    return clean_text(raw.decode("utf-8", errors="ignore"))


def parse_posts(posts: list[dict[str, Any]], source_id: str = "bluesky") -> list[Comment]:
    uris = {p.get("uri") for p in posts}
    out: list[Comment] = []
    for p in posts:
        author = p.get("author") or {}
        if any(label.get("val") == OPT_OUT_LABEL for label in author.get("labels") or []):
            continue
        record = p.get("record") or {}
        # indexedAt is set by the server; createdAt is client-controlled.
        published = parse_datetime(p.get("indexedAt")) or parse_datetime(record.get("createdAt"))
        text = post_text(record)
        if not p.get("uri") or not published or not text:
            continue
        parent = ((record.get("reply") or {}).get("parent") or {}).get("uri")
        out.append(
            Comment(
                source_id,
                p["uri"],
                text,
                published,
                native_parent_id=parent if parent in uris else None,
                upvotes=int(p.get("likeCount") or 0),
            )
        )
    return out


class Bluesky(StreamSource):
    id = "bluesky"
    name = "Bluesky (Stichprobe, deutschsprachig)"
    domain = "bsky.app"
    country = "DE"
    source_type = "social"

    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        windows: list[tuple[datetime, datetime]] = []
        end = until
        while end > since and len(windows) * SAMPLE_PER_WINDOW < max_items:
            windows.append((max(since, end - WINDOW), end))
            end -= WINDOW
        items: list[StreamItem] = []
        for start, stop in windows:
            data = self.http.get_json(
                API,
                params={
                    "q": QUERY,
                    "lang": "de",
                    "sort": "latest",
                    "limit": SAMPLE_PER_WINDOW,
                    "since": _iso(start),
                    "until": _iso(stop),
                },
            )
            for c in parse_posts((data or {}).get("posts") or [], self.id):
                ref = self.day_article(
                    "https://bsky.app/search?q=die&lang=de", "Bluesky · deutschsprachige Stichprobe",
                    c.published_at, ["Bluesky"],
                )  # fmt: skip
                items.append(StreamItem(ref, c))
        return items
