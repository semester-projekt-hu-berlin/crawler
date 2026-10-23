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

# German instances whose local timeline is public (``timelines_access.live_feeds.local == "public"``)
# and whose robots.txt allows the API. Usable posts/day (German, not opted out) measured 23 Oct 2026.
INSTANCES = (
    "troet.cafe",  # ~620
    "mastodon.de",  # ~290
    "norden.social",  # ~170
    "nrw.social",  # ~130
    "ruhr.social",  # ~120
    "berlin.social",  # ~70
    "social.cologne",  # ~65
    "muenchen.social",  # ~55
    "digitalcourage.social",  # ~55
    "hessen.social",  # ~45
    "dresden.network",  # ~40
)
PAGE_LIMIT = 40
MAX_PAGES = 40


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
    name = "Mastodon (deutschsprachige Instanzen)"
    domain = "troet.cafe, mastodon.de, norden.social, … (11 instances)"
    country = "DE"
    topic = "general"
    source_type = "social"
    instances = INSTANCES

    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        per_instance: dict[str, list[Comment]] = {}
        for instance in self.instances:
            statuses: list[dict[str, Any]] = []
            max_id: str | None = None
            try:
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
                    if oldest is None or oldest < since:
                        break
            except Exception as exc:  # noqa: BLE001 - one unreachable instance must not stop the others
                self.log.warning("instance %s failed: %s", instance, exc)
            per_instance[instance] = [
                c for c in parse_statuses(statuses, instance, self.id) if since <= c.published_at <= until
            ]
        total = sum(len(v) for v in per_instance.values())
        items: list[StreamItem] = []
        for instance, comments in per_instance.items():
            # over the cap: thin every instance evenly (keeps the spread over time and instances)
            if total > max_items > 0:
                step = total / max_items
                comments = [comments[int(i * step)] for i in range(int(len(comments) / step))]
            for c in comments:
                ref = self.day_article(
                    f"https://{instance}/public/local", f"{instance} · lokale Timeline", c.published_at, ["Mastodon"]
                )
                items.append(StreamItem(ref, c))
        return items
