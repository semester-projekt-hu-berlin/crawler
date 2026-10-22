"""Mastodon: public local timelines of German-language instances.

``GET /api/v1/timelines/public?local=true`` is readable without login on the configured
instances (their ``timelines_access.live_feeds.local`` is ``public``) and paged backwards
with ``max_id``. We keep only public, German-language (``language`` = ``de``) original
posts (no boosts) by human accounts that have not opted out of indexing
(``account.noindex``), have not declined full-text indexing (``account.indexable``)
and are not bots. Stored are the text (mentions removed),
the timestamp and the reply structure; no handles, account ids or display names.

Posts are grouped into one pseudo-article per instance and Berlin day.
robots.txt of the instances only disallows ``/media_proxy/``, ``/interact/`` and
``/api/v1/instance/domain_blocks``. Mastodon's API limit (300 requests / 5 min) is far
above our rate.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from crawler.ids import parse_datetime
from crawler.models import Comment
from crawler.sources.base import StreamItem, StreamSource
from crawler.text import clean_text

INSTANCES = ("norden.social", "troet.cafe")
PAGE_LIMIT = 40
MAX_PAGES = 150


def status_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for mention in soup.select("span.h-card, a.mention:not(.hashtag)"):
        mention.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for p in soup.find_all("p"):
        p.insert_after("\n\n")
    return clean_text(soup.get_text())


def keep_status(s: dict[str, Any]) -> bool:
    account = s.get("account") or {}
    return (
        s.get("visibility") == "public"
        and not s.get("reblog")
        and (s.get("language") or "").lower().startswith("de")
        and not account.get("bot")
        and not account.get("noindex")
        and account.get("indexable", True) is not False
    )


def parse_statuses(statuses: list[dict[str, Any]], instance: str, source_id: str = "mastodon") -> list[Comment]:
    ids = {str(s.get("id")) for s in statuses}
    out: list[Comment] = []
    for s in statuses:
        if not keep_status(s):
            continue
        published = parse_datetime(s.get("created_at"))
        text = status_text(s.get("content") or "")
        if not published or not text:
            continue
        parent = s.get("in_reply_to_id")
        out.append(
            Comment(
                source_id,
                f"{instance}:{s['id']}",
                text,
                published,
                # only link replies whose parent is part of the same instance timeline batch
                native_parent_id=f"{instance}:{parent}" if parent and str(parent) in ids else None,
                upvotes=int(s.get("favourites_count") or 0),
            )
        )
    return out


class Mastodon(StreamSource):
    id = "mastodon"
    name = "Mastodon (norden.social, troet.cafe)"
    domain = "norden.social, troet.cafe"
    country = "DE"
    source_type = "social"
    instances = INSTANCES

    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        items: list[StreamItem] = []
        per_instance = max(1, max_items // len(self.instances))
        for instance in self.instances:
            statuses: list[dict[str, Any]] = []
            max_id: str | None = None
            for _ in range(MAX_PAGES):
                params = {"local": "true", "limit": PAGE_LIMIT}
                if max_id:
                    params["max_id"] = max_id
                page = self.http.get_json(f"https://{instance}/api/v1/timelines/public", params=params)
                if not page:
                    break
                statuses.extend(page)
                max_id = str(page[-1]["id"])
                oldest = parse_datetime(page[-1].get("created_at"))
                if oldest is None or oldest < since or len(statuses) >= per_instance * 3:
                    break
            comments = [c for c in parse_statuses(statuses, instance, self.id) if since <= c.published_at <= until]
            for c in comments[:per_instance]:
                ref = self.day_article(
                    f"https://{instance}/public/local", f"{instance} · lokale Timeline", c.published_at, ["Mastodon"]
                )
                items.append(StreamItem(ref, c))
        return items
