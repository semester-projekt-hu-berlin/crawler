"""DER STANDARD (derstandard.at): public forum GraphQL API.

The article page loads its forum ("Postings") from ``capi.ds.at/forum-serve-graphql``
via GET requests with Apollo *persisted queries* (the server only accepts known
query hashes, not free-form queries). We issue exactly the requests the public
website makes for anonymous readers:

1. ``GetForumInfo(contextUri)`` -> forum id + total posting count
2. ``ThreadsByForumQuery(id, first: Max, nextCursor)`` -> root postings with nested
   replies, paginated by cursor.

If derStandard redeploys its forum frontend the hashes may change; they can be
overridden with the env vars ``DERSTANDARD_HASH_FORUMINFO`` / ``DERSTANDARD_HASH_THREADS``
(look them up in the browser dev tools, network tab, filter ``forum-serve-graphql``).
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

from bs4 import BeautifulSoup

from crawler.config import COMMENT_OVERLAP
from crawler.discovery import parse_page_meta
from crawler.ids import parse_datetime
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources.base import IncrementalPager, Source
from crawler.text import clean_text

GRAPHQL_URL = "https://capi.ds.at/forum-serve-graphql/v1/"
HASH_FORUMINFO = os.environ.get(
    "DERSTANDARD_HASH_FORUMINFO", "88adea55fbddc38bedd177c9107e457d5cf0f38d2fcd0976c8024dfa31779751"
)
HASH_THREADS = os.environ.get(
    "DERSTANDARD_HASH_THREADS", "d5a2376ac61344341ffe4dcd17f814dba7ea80fbacfb5806c8d1f9c2072a3fa6"
)
# Sections preferred when the source is capped (their RSS feeds tell which stories belong to them).
PRIORITY_SECTIONS = ("international", "wirtschaft", "web")
# Only stories about Germany, the EU or the USA are taken (German focus of the project).
RELEVANT_RE = re.compile(
    r"\b(Deutschland|deutsch\w*|Berlin|Bundestag|Bundesregierung|Merz|Scholz|AfD|CDU|CSU|SPD|Grüne\w*|FDP|"
    r"EU|EU-\w+|Europ\w*|Brüssel|von der Leyen|USA|US-\w+|Trump|Washington|Amerika\w*|Midterms?)\b",
    re.IGNORECASE,
)
SECTION_RSS = "https://www.derstandard.at/rss/{}"
RANK_POOL_FACTOR = 4
_RSS_STORY_RE = re.compile(r"<link>https://www\.derstandard\.at/story/(\d+)")
_STORY_RE = re.compile(r"^https://www\.derstandard\.at/story/(\d+)(?:/|$)")
MAX_PAGES = 200
PERSISTED_RETRIES = 4


class PersistedQueryError(RuntimeError):
    pass


def _reaction(node: dict[str, Any], name: str) -> int | None:
    for feature in (node.get("reactions") or {}).get("aggregated") or []:
        if feature.get("name") == name:
            try:
                return int(feature.get("value"))
            except (TypeError, ValueError):
                return None
    return None


def parse_postings(nodes: list[dict[str, Any]], source_id: str = "derstandard") -> list[Comment]:
    """Flatten a list of (root) posting nodes with nested ``replies`` into comments.

    Only ``id``, ``title``, ``text``, ``history.created``, reactions and the tree
    structure are read. The ``author`` and ``legacy`` objects (user names / IDs) are ignored.
    """
    out: list[Comment] = []
    seen: set[str] = set()

    def walk(node: dict[str, Any], parent: str | None) -> None:
        native_id = node.get("id")
        if not native_id or native_id in seen:
            return
        seen.add(native_id)
        status = node.get("lifecycleStatus")
        published = parse_datetime((node.get("history") or {}).get("created"))
        title = (node.get("title") or "").strip()
        body = (node.get("text") or "").strip()
        text = clean_text(f"{title}\n{body}" if title and body else title or body)
        if status in (None, "Published") and published and text:
            out.append(
                Comment(
                    source_id=source_id,
                    native_id=str(native_id),
                    text=text,
                    published_at=published,
                    native_parent_id=parent,
                    upvotes=_reaction(node, "positive"),
                )
            )
        # Replies of deleted postings are still shown and keep the deleted posting as parent.
        for reply in node.get("replies") or []:
            walk(reply, str(native_id))

    for node in nodes:
        walk(node, None)
    return out


class DerStandard(Source):
    id = "derstandard"
    name = "DER STANDARD"
    domain = "derstandard.at"
    country = "AT"
    topic = "general"
    news_sitemap = "https://www.derstandard.at/sitemaps/news.xml"

    def __init__(self, http):  # noqa: ANN001
        super().__init__(http)
        # Forum info fetched while ranking, reused by fetch() (saves a request per kept article).
        self._forum_cache: dict[str, dict[str, Any] | None] = {}

    def priority_story_ids(self) -> set[str]:
        ids: set[str] = set()
        for section in PRIORITY_SECTIONS:
            try:
                resp = self.http.get(SECTION_RSS.format(section))
            except Exception:  # noqa: BLE001 - ranking is best effort
                continue
            ids.update(_RSS_STORY_RE.findall(resp.text))
        return ids

    def forum_info(self, url: str) -> dict[str, Any] | None:
        if url not in self._forum_cache:
            data = self._query("GetForumInfo", HASH_FORUMINFO, {"contextUri": url})
            self._forum_cache[url] = data.get("getForumByContextUri")
        return self._forum_cache[url]

    def rank_new(self, refs: list[ArticleRef], limit: int | None = None) -> list[ArticleRef]:
        """Priority sections first; among the newest of them the most active forums.

        To keep ranking cheap, posting counts are fetched only for the newest
        ``RANK_POOL_FACTOR * limit`` priority-section candidates (the forum info is cached
        and reused by ``fetch``). The rest follows by recency.
        """
        refs = [r for r in refs if RELEVANT_RE.search(r.title or "")]
        if not refs:
            return refs
        preferred_ids = self.priority_story_ids()
        newest_first = sorted(refs, key=lambda r: r.published_at or datetime.min.replace(tzinfo=UTC), reverse=True)
        preferred = [r for r in newest_first if r.url.rsplit("/", 1)[-1] in preferred_ids]
        others = [r for r in newest_first if r.url.rsplit("/", 1)[-1] not in preferred_ids]
        pool = preferred[: RANK_POOL_FACTOR * limit] if limit else preferred
        for ref in pool:
            try:
                forum = self.forum_info(ref.url)
            except Exception:  # noqa: BLE001 - ranking is best effort
                forum = None
            ref.listed_comment_count = int((forum or {}).get("totalPostingCount") or 0)
        pool.sort(key=lambda r: r.listed_comment_count or 0, reverse=True)
        return pool + preferred[len(pool) :] + others

    def canonical_url(self, url: str) -> str | None:
        m = _STORY_RE.match(url)
        # Live tickers (/jetzt/livebericht/...) have a different forum model and are skipped.
        return f"https://www.derstandard.at/story/{m.group(1)}" if m else None

    def _query(self, operation: str, sha: str, variables: dict[str, Any]) -> dict[str, Any]:
        params = {
            "operationName": operation,
            "variables": json.dumps(variables, separators=(",", ":")),
            "extensions": json.dumps({"persistedQuery": {"version": 1, "sha256Hash": sha}}, separators=(",", ":")),
        }
        # Individual backend nodes occasionally answer PersistedQueryNotFound (their query cache is cold);
        # a retry a few seconds later normally succeeds. Persistent failures mean the hash changed.
        msg = ""
        for attempt in range(PERSISTED_RETRIES):
            # Apollo's CSRF protection requires a non-simple content type even on GET.
            data = self.http.get_json(GRAPHQL_URL, params=params, headers={"Content-Type": "application/json"})
            if not data.get("errors"):
                return data.get("data") or {}
            msg = "; ".join(str(e.get("message")) for e in data["errors"])
            if "persisted" not in msg.lower():
                raise RuntimeError(f"{operation}: {msg}")
            time.sleep(3 * (attempt + 1))
        raise PersistedQueryError(
            f"{operation}: {msg} after {PERSISTED_RETRIES} attempts (hash changed? set DERSTANDARD_HASH_*)"
        )

    def _enrich(self, ref: ArticleRef) -> None:
        resp = self.http.get(ref.url)
        meta = parse_page_meta(BeautifulSoup(resp.text, "lxml"))
        ref.title = meta.title or ref.title
        ref.published_at = meta.published_at or ref.published_at
        if meta.topics:
            ref.topics = meta.topics

    def fetch(self, ref: ArticleRef) -> FetchResult:
        if not ref.known:
            self._enrich(ref)
        forum = self.forum_info(ref.url)
        if not forum or not forum.get("id"):
            return FetchResult(ref, [], 0)
        total = int(forum.get("totalPostingCount") or 0)
        if ref.known and ref.known_comment_count == total:
            return FetchResult(ref, [], total, complete=False, unchanged=True)
        nodes: list[dict[str, Any]] = list(forum.get("stickyPostings") or [])
        complete = True
        if total:
            # Root postings come newest first ("ByTime", descending); replies are nested in their root.
            pager = IncrementalPager(ref, ref.since(COMMENT_OVERLAP), ref.expected_new(total))
            cursor = ""
            for _ in range(MAX_PAGES):
                data = self._query(
                    "ThreadsByForumQuery",
                    HASH_THREADS,
                    {"id": forum["id"], "sortOrder": "ByTime", "first": "Max", "nextCursor": cursor},
                )
                page = data.get("getForumRootPostingsV2") or {}
                page_nodes = [edge["node"] for edge in page.get("edges") or [] if edge.get("node")]
                nodes.extend(page_nodes)
                info = page.get("pageInfo") or {}
                cursor = info.get("nextCursor") or ""
                if not info.get("hasNextPage") or not cursor:
                    break
                if pager.add_page([t for n in page_nodes for t in subtree_times(n)]):
                    complete = False
                    break
        return FetchResult(ref, parse_postings(nodes, self.id), total, complete=complete)


def subtree_times(node: dict[str, Any]) -> list[datetime]:
    """Creation times of a posting and all its replies (deleted ones included)."""
    out: list[datetime] = []
    stack = [node]
    while stack:
        n = stack.pop()
        t = parse_datetime((n.get("history") or {}).get("created"))
        if t:
            out.append(t)
        stack.extend(n.get("replies") or [])
    return out
