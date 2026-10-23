"""feddit.org: the largest German-language Lemmy instance (a Reddit alternative), public API.

``GET /api/v3/comment/list?type_=Local&sort=New&limit=50&page=N`` lists the newest comments
in the instance's own (mostly German) communities, newest first, without login. (``type_=All``
is dominated by English communities from other instances: 1 of 50 comments was German.) We keep comments that
are German (``language_id`` = de, or "undetermined" with German-looking text), not deleted
or removed, and not written by bot accounts. The comment's post becomes the article (title,
link ``https://feddit.org/post/<id>``, community as topic). The ``creator`` object (user
name, id, avatar) is never read.

robots.txt (group ``*``): only ``/login``, ``/settings``, ``/create_*``, ``/inbox``,
``/admin``, ``/search/`` etc. are disallowed, and ``Crawl-delay: 60`` is set, which the
crawler obeys (one request per minute, ~15 requests per run).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from crawler.ids import parse_datetime
from crawler.models import ArticleRef, Comment
from crawler.sources.base import StreamItem, StreamSource
from crawler.text import clean_text

BASE = "https://feddit.org"
PAGE_LIMIT = 50
MAX_PAGES = 15  # at Crawl-delay 60 s: at most 15 minutes
LANG_DE = 32  # feddit.org /api/v3/site all_languages: "de"
LANG_UNDETERMINED = 0
_GERMAN_WORDS = re.compile(
    r"\b(und|der|die|das|nicht|ist|ich|ein|eine|auch|mit|sich|auf|für|dass|wie|aber|wenn|noch|nur|schon)\b",
    re.IGNORECASE,
)
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_QUOTE = re.compile(r"^\s*>+\s?", re.MULTILINE)


def looks_german(text: str) -> bool:
    words = max(1, len(text.split()))
    hits = len(_GERMAN_WORDS.findall(text))
    return hits >= 2 and hits / words >= 0.05 or bool(re.search(r"[äöüß]", text)) and hits >= 1


def markdown_text(md: str) -> str:
    text = _MD_LINK.sub(lambda m: m.group(1), md or "")
    text = _MD_QUOTE.sub("", text)
    return clean_text(text.replace("**", "").replace("__", ""))


def parse_views(views: list[dict[str, Any]], source_id: str = "feddit") -> list[tuple[ArticleRef, Comment]]:
    out: list[tuple[ArticleRef, Comment]] = []
    for v in views:
        c, post, community = v.get("comment") or {}, v.get("post") or {}, v.get("community") or {}
        if c.get("deleted") or c.get("removed") or (v.get("creator") or {}).get("bot_account"):
            continue
        text = markdown_text(c.get("content") or "")
        lang = c.get("language_id")
        if not text or not (lang == LANG_DE or (lang == LANG_UNDETERMINED and looks_german(text))):
            continue
        published = parse_datetime(c.get("published"))
        if not c.get("id") or not post.get("id") or not published:
            continue
        path = [p for p in str(c.get("path") or "").split(".") if p and p != "0"]
        parent = path[-2] if len(path) >= 2 else None
        ref = ArticleRef(
            url=f"{BASE}/post/{post['id']}",
            source_id=source_id,
            title=clean_text(post.get("name") or ""),
            published_at=parse_datetime(post.get("published")) or published,
            topics=[t for t in (community.get("title"), community.get("name")) if t][:1],
        )
        out.append((ref, Comment(source_id, str(c["id"]), text, published, native_parent_id=parent,
                                 upvotes=int((v.get("counts") or {}).get("upvotes") or 0))))  # fmt: skip
    return out


class Feddit(StreamSource):
    id = "feddit"
    name = "feddit.org (Lemmy)"
    domain = "feddit.org"
    country = "DE"
    source_type = "social"
    topic = "general"

    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        items: list[StreamItem] = []
        for page in range(1, MAX_PAGES + 1):
            data = self.http.get_json(
                f"{BASE}/api/v3/comment/list",
                params={"type_": "Local", "sort": "New", "limit": PAGE_LIMIT, "page": page},
            )
            views = (data or {}).get("comments") or []
            if not views:
                break
            for ref, c in parse_views(views, self.id):
                if since <= c.published_at <= until:
                    items.append(StreamItem(ref, c))
            oldest = parse_datetime((views[-1].get("comment") or {}).get("published"))
            if oldest is None or oldest < since or len(items) >= max_items:
                break
        return items[:max_items]
