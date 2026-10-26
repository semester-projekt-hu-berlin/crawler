"""BR24 (br.de, Bayerischer Rundfunk): reader comments from the Coral comment platform.

robots.txt: ``www.br.de`` (group ``*``) allows ``/nachrichten/`` and the news sitemap;
``comments.br24.de/robots.txt`` answers 404, i.e. no restrictions (RFC 9309). The comments are
shown to every anonymous visitor of a story.

* Articles: Google News sitemap ``/nachrichten/newsSitemap`` (title, date, keywords). Short agency
  items (``/nachrichten/meldung/``) have no comment stream and are skipped. The Coral story id is
  the suffix after the comma of the article URL (``…/titel,VZ4flIU``).
* Comments: Coral 9 GraphQL. Raw queries are refused; the embed uses *persisted queries*
  (``GET /api/graphql?query=&id=<id>&variables=<json>``). The ids depend on the deployed Coral
  bundle, so each run reads ``/embed/bootstrap`` (~1 KB): if it names the known bundle the known
  ids are used, otherwise the new ``stream.<hash>.js`` is read and the ids are taken from it
  (``params:{id:"…",name:"AllCommentsTabQuery"}``); on any problem the known ids are the fallback.
  ``StreamQuery`` (~3 KB) returns ``commentCounts.totalPublished`` and is the count check;
  ``AllCommentsTabQuery`` returns the newest 20 threads with replies, ``…PaginationQuery`` the
  next 50 threads by cursor (``CREATED_AT_DESC``).
* Volume: ~2,000 comments a day in total, far more than our share, so new stories are taken most
  commented first (count probe), and a fetch reads at most ``MAX_COMMENTS_PER_FETCH`` comments
  (newest threads first; the pipeline caps the run, see ``config.DAILY_COMMENT_CAPS``).
* Privacy: ``author`` (username, avatar, bio, id) and ``parent.author`` are never read. Quotes
  (``blockquote``) and ``@name`` mentions are removed from the text.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import unquote

from bs4 import BeautifulSoup

from crawler.config import COMMENT_OVERLAP
from crawler.ids import parse_datetime
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources.base import IncrementalPager, Source
from crawler.text import clean_text, html_to_text, remove_quotes

CORAL = "https://comments.br24.de"
GRAPHQL = f"{CORAL}/api/graphql"
BOOTSTRAP = f"{CORAL}/embed/bootstrap"
# Persisted query ids of the Coral bundle deployed on 26 Oct 2026 (stream.<KNOWN_BUNDLE>.js).
KNOWN_BUNDLE = "e17437fa6c9c9a6068afb70790b0d44b"
KNOWN_IDS = {
    "AllCommentsTabQuery": "9c28aa927a6d4ee29ff476337d9563b4",
    "AllCommentsTabContainerPaginationQuery": "fe6db435838489b39afeb993214af799",
    "StreamQuery": "93c86a5a1175c6df31e4149e10d6fa9a",
}
_STORY_RE = re.compile(r"^https://www\.br\.de/nachrichten/([a-z0-9-]+)/[^/,]*,([A-Za-z0-9]{5,12})$")
_BUNDLE_RE = re.compile(r"assets/js/stream\.([0-9a-f]{16,64})\.js")
_ID_RE = re.compile(r'params:\{id:"([0-9a-f]{32})",(?:metadata:\{[^{}]*\},)?name:"([A-Za-z]+)"')
SECTIONS = {
    "bayern": "Bayern",
    "deutschland-welt": "Deutschland & Welt",
    "wirtschaft": "Wirtschaft",
    "kultur": "Kultur",
    "sport": "Sport",
    "wissen": "Wissen",
    "netzwelt": "Netzwelt",
}
PAGE_SIZE = 50
MAX_COMMENTS_PER_FETCH = 100
# New stories whose comment count is probed per run (newest first) to pick the most commented.
PROBE_MAX = 30


def story_id(url: str) -> str | None:
    m = _STORY_RE.match(unquote(url))
    return m.group(2) if m else None


def query_ids_from_bundle(js: str) -> dict[str, str]:
    """Persisted query ids by operation name, as found in a Coral ``stream.<hash>.js`` bundle."""
    return {name: qid for qid, name in _ID_RE.findall(js) if name in KNOWN_IDS}


def _comment(node: dict[str, Any], source_id: str, parent: str | None) -> Comment | None:
    if node.get("deleted") or (node.get("status") not in (None, "APPROVED", "NONE")):
        return None
    soup = BeautifulSoup(node.get("body") or "", "lxml")
    remove_quotes(soup)
    for el in soup.select("script, style"):
        el.decompose()
    text = clean_text(html_to_text(str(soup)))
    published = parse_datetime(node.get("createdAt"))
    if not node.get("id") or not text or published is None:
        return None
    reactions = ((node.get("actionCounts") or {}).get("reaction") or {}).get("total")
    return Comment(
        source_id,
        str(node["id"]),
        text,
        published,
        native_parent_id=parent,
        upvotes=int(reactions) if reactions is not None else None,
    )


def parse_comments(story: dict[str, Any], source_id: str = "br24") -> list[Comment]:
    """All comments of one page of threads (roots plus nested replies). No author fields are read."""
    out: list[Comment] = []

    def walk(node: dict[str, Any], parent: str | None) -> None:
        c = _comment(node, source_id, parent)
        if c is not None:
            out.append(c)
        for edge in (node.get("replies") or {}).get("edges") or []:
            walk(edge.get("node") or {}, str(node.get("id")) if node.get("id") else parent)

    for edge in ((story.get("comments") or {}).get("edges")) or []:
        walk(edge.get("node") or {}, None)
    return out


class BR24(Source):
    id = "br24"
    name = "BR24"
    domain = "br.de"
    country = "DE"
    topic = "politics"
    news_sitemap = "https://www.br.de/nachrichten/newsSitemap"

    def __init__(self, http) -> None:  # noqa: ANN001
        super().__init__(http)
        self._ids: dict[str, str] | None = None

    # -- articles

    def accept_url(self, url: str) -> bool:
        return "/nachrichten/meldung/" not in unquote(url) and story_id(url) is not None

    def canonical_url(self, url: str) -> str | None:
        m = _STORY_RE.match(unquote(url))
        # The slug is not needed: /nachrichten/<section>/<anything>,<id> redirects to the article.
        return f"https://www.br.de/nachrichten/{m.group(1)}/beitrag,{m.group(2)}" if m else None

    def discover(self, since: datetime, until: datetime) -> list[ArticleRef]:
        refs = super().discover(since, until)
        for ref in refs:
            m = _STORY_RE.match(ref.url)
            section = SECTIONS.get(m.group(1)) if m else None
            if section and section not in ref.topics:
                ref.topics = [section, *ref.topics]
        return refs

    def rank_new(self, refs: list[ArticleRef], limit: int | None = None) -> list[ArticleRef]:
        """Most commented first: probe the comment count of the newest ``PROBE_MAX`` new stories."""
        newest = super().rank_new(refs)
        probed = newest[:PROBE_MAX]
        for ref in probed:
            try:
                ref.listed_comment_count = self.count(ref)
            except Exception as exc:  # noqa: BLE001 - unprobed stories just rank last
                self.log.warning("count probe failed for %s: %s", ref.url, type(exc).__name__)
        ranked = sorted(probed, key=lambda r: r.listed_comment_count or 0, reverse=True)
        return [r for r in ranked if r.listed_comment_count] + [
            r for r in [*ranked, *newest[PROBE_MAX:]] if not r.listed_comment_count
        ]

    # -- Coral

    def query_ids(self) -> dict[str, str]:
        """Persisted query ids of the currently deployed Coral bundle (known ids as fallback)."""
        if self._ids is not None:
            return self._ids
        ids = dict(KNOWN_IDS)
        try:
            boot = self.http.get_json(BOOTSTRAP) or {}
            scripts = [a.get("src") or "" for a in (boot.get("assets") or {}).get("js") or []]
            bundle = next((m for s in scripts if (m := _BUNDLE_RE.search(s))), None)
            if bundle is not None and bundle.group(1) != KNOWN_BUNDLE:
                found = query_ids_from_bundle(self.http.get(f"{CORAL}/{bundle.group(0)}").text)
                self.log.info("coral bundle %s: %d query ids found", bundle.group(1), len(found))
                ids.update(found)
        except Exception as exc:  # noqa: BLE001 - keep the known ids
            self.log.warning("coral bootstrap failed (%s), using known query ids", type(exc).__name__)
        self._ids = ids
        return ids

    def _query(self, name: str, variables: dict[str, Any]) -> dict[str, Any]:
        params = {"query": "", "id": self.query_ids()[name], "variables": json.dumps(variables, separators=(",", ":"))}
        data = self.http.get_json(GRAPHQL, params=params) or {}
        if data.get("errors") and not data.get("data"):
            raise RuntimeError(f"coral {name}: {str(data['errors'])[:200]}")
        return data.get("data") or {}

    def count(self, ref: ArticleRef) -> int | None:
        """Published comment count of the story (``None`` if Coral has no such story)."""
        sid = story_id(ref.url)
        if sid is None:
            return None
        story = self._query("StreamQuery", {"storyID": sid, "storyURL": None, "storyMode": None}).get("story")
        return int(((story or {}).get("commentCounts") or {}).get("totalPublished") or 0) if story else None

    def fetch(self, ref: ArticleRef) -> FetchResult:
        sid = story_id(ref.url)
        if sid is None:
            return FetchResult(ref, [], 0)
        total = ref.listed_comment_count if ref.listed_comment_count is not None else self.count(ref)
        if total is None:
            return FetchResult(ref, [], 0)
        if ref.known and total == ref.known_comment_count:
            return FetchResult(ref, [], total, complete=False, unchanged=True)
        if total == 0:
            return FetchResult(ref, [], 0)
        story = (
            self._query(
                "AllCommentsTabQuery",
                {
                    "storyID": sid,
                    "storyURL": None,
                    "commentsOrderBy": "CREATED_AT_DESC",
                    "tag": None,
                    "storyMode": None,
                    "flattenReplies": False,
                    "ratingFilter": None,
                    "refreshStream": False,
                },
            ).get("story")
            or {}
        )
        pager = IncrementalPager(ref, ref.since(COMMENT_OVERLAP), ref.expected_new(total))
        comments: dict[str, Comment] = {}
        complete = True
        while True:
            found = parse_comments(story, self.id)
            comments.update((c.native_id, c) for c in found)
            info = (story.get("comments") or {}).get("pageInfo") or {}
            if not info.get("hasNextPage") or not info.get("endCursor"):
                break
            if pager.add_page([c.published_at for c in found]) or len(comments) >= MAX_COMMENTS_PER_FETCH:
                complete = False
                break
            story = (
                self._query(
                    "AllCommentsTabContainerPaginationQuery",
                    {
                        "count": PAGE_SIZE,
                        "cursor": info["endCursor"],
                        "orderBy": "CREATED_AT_DESC",
                        "storyID": sid,
                        "tag": None,
                        "flattenReplies": False,
                        "ratingFilter": None,
                        "refreshStream": False,
                    },
                ).get("story")
                or {}
            )
        newest = sorted(comments.values(), key=lambda c: c.published_at, reverse=True)
        if len(newest) > MAX_COMMENTS_PER_FETCH:
            newest, complete = newest[:MAX_COMMENTS_PER_FETCH], False
        return FetchResult(ref, newest, total, complete=complete)
