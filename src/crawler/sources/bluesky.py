"""Bluesky: German-language posts about tracked entities from the public AppView search.

``api.bsky.app/xrpc/app.bsky.feed.searchPosts`` is public; its robots.txt says
"Crawling the public parts of the API is allowed. HTTP 429 ("backoff") status codes are
used for rate-limiting. Up to a handful concurrent requests should be ok."

German Bluesky produces far more posts than we need or want to pay for (~100 per minute), and
only about one random post in eight names a tracked person, party, company or app. So each run
spends ``TARGETED_SHARE`` of its budget on **entity searches** (``lang=de``, ``sort=latest``,
``since``/``until``): the names of the most-mentioned tracked entities (from Firestore) plus a
static list of parties, politicians, companies, broadcasters and shows. Every query gets the same
share of the budget (newest posts first), so one busy name cannot use it up; what quiet queries
leave over is handed out in further rounds. The rest is a small **time-spread sample** of all
German posts (query "die", the most frequent German word; the newest posts of every hour), which
keeps an unbiased view of what else is discussed.

Not stored: authors who asked not to be shown to logged-out users (label
``!no-unauthenticated``), posts that only repeat the headline of the page they link to (news
bots), posts without content or in another language (``quality.py``), and more than
``PER_AUTHOR`` posts of one account per run (bots and very active accounts would otherwise
dominate; the account id is only held in memory for this count). Stored: text (mentions removed),
indexing time, reply structure. No handles, DIDs or display names.

Besides the searches, each run reads the public custom feed "Deutsche Politik"
(``public.api.bsky.app/xrpc/app.bsky.feed.getFeed``, ~300-400 German politics posts a day,
newest first, paged by cursor until the previous run) with the same filters and the
same comment ids, so a post found by the feed and a search is stored once.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from crawler import quality
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
FEED_PER_RUN = 100  # of max_items, reserved for the feed (it delivers 50-70 per run); the rest goes to the searches
FEED_PAGE = 100
FEED_MAX_PAGES = 6
QUERY = "die"
SAMPLE_PER_WINDOW = 100
WINDOW = timedelta(hours=1)
OPT_OUT_LABEL = "!no-unauthenticated"
# Posts of one account stored per run (all searches and the feed together).
PER_AUTHOR = 3


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


def link_preview(record: dict[str, Any]) -> tuple[str, str, list[str]]:
    """``(title, description, urls)`` of the link card and the link facets of a post."""
    embed = record.get("embed") or {}
    external = embed.get("external") or (embed.get("media") or {}).get("external") or {}
    urls = [
        str(feature["uri"])
        for facet in record.get("facets") or []
        for feature in facet.get("features") or []
        if feature.get("uri")
    ]
    if external.get("uri"):
        urls.append(str(external["uri"]))
    return str(external.get("title") or ""), str(external.get("description") or ""), urls


def reject_reason(record: dict[str, Any], text: str) -> str | None:
    """Why a post is not stored (``None``: keep): no content, not German, or just a link's headline."""
    reason = quality.reject_reason(text)
    if reason is None and quality.repeats_link_card(text, *link_preview(record)):
        reason = "link_card"
    return reason


def author_of(uri: str) -> str:
    """The account part of a post URI (``at://<did>/app.bsky.feed.post/<rkey>``); never stored."""
    return uri.split("/")[2] if uri.startswith("at://") else ""


def parse_posts(
    posts: list[dict[str, Any]], source_id: str = "bluesky", filtered: Counter[str] | None = None
) -> list[Comment]:
    """Posts as comments. Posts dropped by ``reject_reason`` are counted in ``filtered``."""
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
        reason = reject_reason(record, text)
        if reason is not None:
            if filtered is not None:
                filtered[reason] += 1
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


# Entity searches: parties, politicians, companies, apps, broadcasters and TV shows (plain words
# also match hashtags; "#tag" queries are rejected by the API with 400). Only names of tracked
# entities: topic words ("KI", "Rente", "Wahl", "Bahn", "Migration") and ambiguous surnames
# ("Reiche", "Bas") returned mostly posts without any tracked entity (measured 9 Nov 2026: 19-44 %
# of their posts linked an entity, against 80-100 % for the names below). Extended at run time with
# the most-mentioned allowlisted entities from Firestore (see ``prepare``).
STATIC_QUERIES = (
    # institutions
    "Bundestag",
    "Bundesregierung",
    "Koalition",
    "Bundesverfassungsgericht",
    "Bundeswehr",
    "EU",
    # parties
    "AfD",
    "SPD",
    "CDU",
    "CSU",
    "Grüne",
    "Linke",
    "BSW",
    "FDP",
    # politicians (all Bundestag parties)
    "Merz",
    "Klingbeil",
    "Söder",
    "Dobrindt",
    "Wadephul",
    "Pistorius",
    "Katherina Reiche",
    "Bärbel Bas",
    "Linnemann",
    "Spahn",
    "Weidel",
    "Chrupalla",
    "Höcke",
    "Banaszak",
    "Habeck",
    "Reichinnek",
    "Schwerdtner",
    "Wagenknecht",
    "Aiwanger",
    "Trump",
    "Putin",
    "Musk",
    # companies and apps
    "Volkswagen",
    "Siemens",
    "Deutsche Bank",
    "Telekom",
    "Lufthansa",
    "Deutsche Bahn",
    "Rheinmetall",
    "BASF",
    "SAP",
    "Bayer",
    "Mercedes",
    "BMW",
    "Tesla",
    "Amazon",
    "Google",
    "Apple",
    "OpenAI",
    "ChatGPT",
    "Microsoft",
    "Nvidia",
    "WhatsApp",
    "TikTok",
    # broadcasters and shows
    "Tagesschau",
    "ZDF",
    "ARD",
    "RTL",
    "ProSieben",
    "Netflix",
    "Markus Lanz",
    "Maischberger",
    "hart aber fair",
    "Illner",
    "Miosga",
    "Böhmermann",
)
QUERIES_PER_RUN = 60
SEARCH_PAGE = 100  # the API's maximum
# Every query gets the same share of the search budget per round, but at least this many posts.
MIN_PER_QUERY = 20
# Rounds over the query list: what quiet queries leave over goes to the busier ones (older posts).
TARGETED_ROUNDS = 3
TARGETED_SHARE = 0.9  # of the search budget; the rest is the time-spread sample


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
        self._per_author: Counter[str] = Counter()
        items: dict[str, StreamItem] = {}
        self.politics_feed(since, until, min(FEED_PER_RUN, max_items), items)
        self.search(since, until, max_items, items)
        return list(items.values())[:max_items]

    def _take(self, items: dict[str, StreamItem], ref_for, comment: Comment) -> bool:  # noqa: ANN001
        """Add a post unless it is already there or its author reached ``PER_AUTHOR``."""
        if comment.native_id in items:
            return False
        author = author_of(comment.native_id)
        if self._per_author[author] >= PER_AUTHOR:
            self.filtered["per_author"] += 1
            return False
        self._per_author[author] += 1
        items[comment.native_id] = StreamItem(ref_for(comment), comment)
        return True

    def politics_feed(self, since: datetime, until: datetime, max_items: int, items: dict[str, StreamItem]) -> None:
        """Posts of the "Deutsche Politik" feed published in ``[since, until]``, newest first."""

        def ref_for(c: Comment):  # noqa: ANN202
            return self.day_article(
                POLITICS_FEED_URL, "Bluesky · Feed „Deutsche Politik“", c.published_at, ["Bluesky", "Politik"]
            )

        cursor: str | None = None
        taken = 0
        for _ in range(FEED_MAX_PAGES):
            if taken >= max_items:
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
            for c in parse_posts(german, self.id, self.filtered):
                if since <= c.published_at <= until and taken < max_items:
                    taken += self._take(items, ref_for, c)
            times = [t for p in posts if (t := parse_datetime(p.get("indexedAt")))]
            cursor = data.get("cursor")
            if not cursor or not times or min(times) < since:
                break

    def search(self, since: datetime, until: datetime, max_items: int, items: dict[str, StreamItem]) -> None:
        """Entity searches (``TARGETED_SHARE`` of the room left in ``items``) plus the time-spread sample."""
        budget = max_items - min(FEED_PER_RUN, max_items)  # the feed's share stays reserved
        if budget <= 0:
            return
        targeted_cap = len(items) + int(budget * TARGETED_SHARE)
        sample_cap = budget - int(budget * TARGETED_SHARE)
        # Unauthenticated search does not allow cursor paging (403), so page by time: the next
        # request of a query asks for posts older than the oldest one seen. Posts of a page that
        # exceed the query's share wait in ``pending`` for the next round (no second request).
        upper = dict.fromkeys(self.queries(until), until)
        pending: dict[str, list[Comment]] = {q: [] for q in upper}
        exhausted: set[str] = set()
        for _ in range(TARGETED_ROUNDS):
            open_queries = [q for q in upper if pending[q] or q not in exhausted]
            for n, query in enumerate(open_queries):
                room = targeted_cap - len(items)
                if room <= 0:
                    break
                quota = min(room, max(MIN_PER_QUERY, -(-room // (len(open_queries) - n))))
                if len(pending[query]) < quota and query not in exhausted:
                    params = {"q": query, "lang": "de", "sort": "latest", "limit": SEARCH_PAGE,
                              "since": _iso(since), "until": _iso(upper[query])}  # fmt: skip
                    try:
                        posts = (self.http.get_json(API, params=params) or {}).get("posts") or []
                    except Exception as exc:  # noqa: BLE001 - one failing query must not stop the source
                        self.log.warning("search failed for %r: %s", query, exc)
                        posts = []
                    pending[query] += [
                        c for c in parse_posts(posts, self.id, self.filtered) if since <= c.published_at <= until
                    ]
                    times = [t for p in posts if (t := parse_datetime(p.get("indexedAt")))]
                    if len(posts) < SEARCH_PAGE or not times or min(times) >= upper[query]:
                        exhausted.add(query)  # nothing older in the window
                    else:
                        upper[query] = min(times)
                base = "https://bsky.app/search?" + urlencode({"q": query, "lang": "de"})
                title = f"Bluesky · Suche „{query}“"

                def ref_for(c: Comment, base: str = base, title: str = title, query: str = query):  # noqa: ANN202
                    return self.day_article(base, title, c.published_at, ["Bluesky", query])

                taken = 0
                while pending[query] and taken < quota:  # newest first
                    taken += self._take(items, ref_for, pending[query].pop(0))
            if len(items) >= targeted_cap:
                break
        for item in self.sample(since, until, sample_cap):
            if len(items) >= max_items:
                break
            self._take(items, lambda _c, ref=item.article: ref, item.comment)

    def sample(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        """Time-spread sample: the newest posts of each hour (all topics)."""
        if max_items <= 0:
            return []
        windows: list[tuple[datetime, datetime]] = []
        end = until
        while end > since and len(windows) < max_items:
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
                    # some more than needed: part of the posts is dropped by the filters
                    "limit": min(SAMPLE_PER_WINDOW, per_window * 2),
                    "since": _iso(start),
                    "until": _iso(stop),
                },
            )
            comments = parse_posts((data or {}).get("posts") or [], self.id, self.filtered)
            for c in comments[:per_window]:
                ref = self.day_article(
                    "https://bsky.app/search?q=die&lang=de", "Bluesky · deutschsprachige Stichprobe",
                    c.published_at, ["Bluesky"],
                )  # fmt: skip
                items.append(StreamItem(ref, c))
        return items[:max_items]
