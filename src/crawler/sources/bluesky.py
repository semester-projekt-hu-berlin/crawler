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

Besides the searches, each run reads the public custom feed "Deutsche Politik"
(``public.api.bsky.app/xrpc/app.bsky.feed.getFeed``, ~300-400 German politics posts a day,
newest first, paged by cursor until the previous run) with the same opt-out filter and the
same comment ids, so a post found by the feed and a search is stored once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from crawler.ids import parse_datetime
from crawler.models import Comment
from crawler.sources.base import StreamItem, StreamSource
from crawler.text import clean_text

API = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
FEED_API = "https://public.api.bsky.app/xrpc/app.bsky.feed.getFeed"
# Custom feed "Deutsche Politik" (feed generator record; the DID is the feed's, not a poster's, and is
# never stored: the pseudo-article URL below does not contain it).
POLITICS_FEED = "at://did:plc:xcprh522pngo62qeonh2jj5u/app.bsky.feed.generator/aaaown2rdgu6m"
POLITICS_FEED_URL = "https://bsky.app/?feed=deutsche-politik"
FEED_PER_RUN = 150  # of max_items, reserved for the feed (~300/day); the rest goes to the searches
FEED_PAGE = 100
FEED_MAX_PAGES = 6
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


def _german(record: dict[str, Any]) -> bool:
    langs = record.get("langs")
    return not langs or any(str(lang).lower().split("-")[0] == "de" for lang in langs)


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


# Targeted searches: German politics, companies, tech, broadcasters and TV shows (plain words also
# match hashtags; "#tag" queries are rejected by the API with 400). Extended at run
# time with the most-mentioned allowlisted entities from Firestore (see ``prepare``).
STATIC_QUERIES = (
    "Bundestag",
    "Bundesregierung",
    "Koalition",
    "Merz",
    "AfD",
    "SPD",
    "CDU",
    "CSU",
    "Grüne",
    "Linke",
    "BSW",
    "FDP",
    "Wahl",
    "Klingbeil",
    "Söder",
    "Weidel",
    "Reiche",
    "Pistorius",
    "Bas",
    "Wadephul",
    "Dobrindt",
    "Bundesverfassungsgericht",
    "Rente",
    "Bürgergeld",
    "Migration",
    "Ukraine",
    "Trump",
    "EU",
    "Volkswagen",
    "Siemens",
    "Deutsche Bank",
    "Telekom",
    "Lufthansa",
    "Bahn",
    "BASF",
    "SAP",
    "Bayer",
    "Mercedes",
    "BMW",
    "Tesla",
    "Amazon",
    "Google",
    "Apple",
    "Meta",
    "OpenAI",
    "Microsoft",
    "Nvidia",
    "KI",
    "Tagesschau",
    "ZDF",
    "ARD",
    "RTL",
    "ProSieben",
    "Netflix",
    "Markus Lanz",
    "Lanz",
    "Maischberger",
    "hart aber fair",
    "Illner",
    "Miosga",
    "Tatort",
    "heute-show",
    "Böhmermann",
    "Polizeiruf",
    "#Bundestag",
    "#Lanz",
    "#Maischberger",
    "#Tatort",
    "#hartaberfair",
    "#Illner",
    "#Miosga",
)
QUERIES_PER_RUN = 60
TARGETED_PAGES = 3  # x 100 posts per query and run (time-based paging)
TARGETED_SHARE = 0.75  # of max_items; the rest is the time-spread sample


class Bluesky(StreamSource):
    id = "bluesky"
    name = "Bluesky (deutschsprachig: Stichprobe, Themensuche, Feed Deutsche Politik)"
    domain = "bsky.app"
    country = "DE"
    source_type = "social"
    topic = "general"

    def __init__(self, http):  # noqa: ANN001
        super().__init__(http)
        self.entity_queries: list[str] = []

    def prepare(self, store) -> None:  # noqa: ANN001
        """Add the most-mentioned allowlisted entities of the last 7 days to the query list."""
        try:
            rows = store.run_query(
                "entities", [("allowlisted", "EQUAL", True)], order_by=[("mentions7d", "DESCENDING")], limit=30,
                select=["name"],
            )  # fmt: skip
            self.entity_queries = [f.get("name") for _, f in rows if f.get("name")]
        except Exception as exc:  # noqa: BLE001 - best effort
            self.log.warning("entity query failed: %s", exc)

    def queries(self, now: datetime) -> list[str]:
        seen: set[str] = set()
        merged = [q for q in [*self.entity_queries, *STATIC_QUERIES] if not (q.lower() in seen or seen.add(q.lower()))]
        if len(merged) <= QUERIES_PER_RUN:
            return merged
        # rotate: every run (twice a day) starts at a different offset
        offset = (now.toordinal() * 2 + (now.hour >= 12)) * QUERIES_PER_RUN % len(merged)
        return (merged + merged)[offset : offset + QUERIES_PER_RUN]

    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        feed = self.politics_feed(since, until, min(FEED_PER_RUN, max_items))
        search = self.search(since, until, max(0, max_items - FEED_PER_RUN))
        items = {i.comment.native_id: i for i in feed}
        for item in search:
            items.setdefault(item.comment.native_id, item)
        return list(items.values())[:max_items]

    def politics_feed(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        """Posts of the "Deutsche Politik" feed published in ``[since, until]``, newest first."""
        items: list[StreamItem] = []
        cursor: str | None = None
        for _ in range(FEED_MAX_PAGES):
            if len(items) >= max_items:
                break
            params: dict[str, Any] = {"feed": POLITICS_FEED, "limit": FEED_PAGE}
            if cursor:
                params["cursor"] = cursor
            try:
                data = self.http.get_json(FEED_API, params=params) or {}
            except Exception as exc:  # noqa: BLE001 - the searches still run
                self.log.warning("politics feed failed: %s", type(exc).__name__)
                break
            posts = [row.get("post") or {} for row in data.get("feed") or [] if not row.get("reason")]
            german = [p for p in posts if _german(p.get("record") or {})]
            for c in parse_posts(german, self.id):
                if since <= c.published_at <= until and len(items) < max_items:
                    ref = self.day_article(POLITICS_FEED_URL, "Bluesky · Feed „Deutsche Politik“", c.published_at,
                                           ["Bluesky", "Politik"])  # fmt: skip
                    items.append(StreamItem(ref, c))
            times = [t for p in posts if (t := parse_datetime(p.get("indexedAt")))]
            cursor = data.get("cursor")
            if not cursor or not times or min(times) < since:
                break
        return items

    def search(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        """Targeted searches (``TARGETED_SHARE`` of ``max_items``) plus the time-spread sample."""
        items: dict[str, StreamItem] = {}
        if max_items <= 0:
            return []
        targeted_cap = int(max_items * TARGETED_SHARE)
        for query in self.queries(until):
            if len(items) >= targeted_cap:
                break
            # Unauthenticated search does not allow cursor paging (403), so page by time:
            # the next request asks for posts older than the oldest one seen.
            upper = until
            for _ in range(TARGETED_PAGES):
                params = {"q": query, "lang": "de", "sort": "latest", "limit": SAMPLE_PER_WINDOW,
                          "since": _iso(since), "until": _iso(upper)}  # fmt: skip
                try:
                    data = self.http.get_json(API, params=params) or {}
                except Exception as exc:  # noqa: BLE001 - one failing query must not stop the source
                    self.log.warning("search failed for %r: %s", query, exc)
                    break
                posts = data.get("posts") or []
                comments = parse_posts(posts, self.id)
                for c in comments:
                    if c.native_id not in items and since <= c.published_at <= until:
                        base = "https://bsky.app/search?" + urlencode({"q": query, "lang": "de"})
                        ref = self.day_article(base, f"Bluesky · Suche „{query}“", c.published_at, ["Bluesky", query])
                        items[c.native_id] = StreamItem(ref, c)
                times = [t for p in posts if (t := parse_datetime(p.get("indexedAt")))]
                if len(posts) < SAMPLE_PER_WINDOW or not times or min(times) >= upper:
                    break
                upper = min(times)
        for item in self.sample(since, until, max_items - len(items)):
            items.setdefault(item.comment.native_id, item)
        return list(items.values())[:max_items]

    def sample(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        """Time-spread sample: the newest posts of each hour (all topics)."""
        windows: list[tuple[datetime, datetime]] = []
        end = until
        while end > since and len(windows) * SAMPLE_PER_WINDOW < max_items:
            windows.append((max(since, end - WINDOW), end))
            end -= WINDOW
        per_window = max(1, min(SAMPLE_PER_WINDOW, max_items // max(1, len(windows))))
        items: list[StreamItem] = []
        for start, stop in windows:
            data = self.http.get_json(
                API,
                params={
                    "q": QUERY,
                    "lang": "de",
                    "sort": "latest",
                    "limit": per_window,
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
