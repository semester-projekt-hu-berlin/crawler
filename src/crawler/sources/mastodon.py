"""Mastodon: public local timelines of German-language instances.

``GET /api/v1/timelines/public?local=true`` is readable without login on the configured
instances (their ``timelines_access.live_feeds.local`` is ``public``) and paged backwards
with ``max_id``. We keep only public, German-language (``language`` = ``de``) original
posts (no boosts) by human accounts that have not opted out of indexing
(``account.noindex``), have not declined full-text indexing (``account.indexable``)
and are not bots. Posts that only repeat the headline of their link preview (``card``), posts
without content or in another language (``quality.py``) and more than ``PER_AUTHOR`` posts of one
account per run are dropped (the account id is only held in memory for this count). Stored are
the text (mentions removed), the timestamp and the reply structure; no handles, account ids or
display names.

Posts are grouped into one pseudo-article per instance and Berlin day.
robots.txt of the instances only disallows ``/media_proxy/``, ``/interact/`` and
``/api/v1/instance/domain_blocks``. Mastodon's API limit (300 requests / 5 min) is far
above our rate; an instance that answers 429 anyway is asked once more and then left alone until
the next run.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from crawler import quality
from crawler.ids import parse_datetime
from crawler.models import Comment
from crawler.sources.base import StreamItem, StreamSource
from crawler.text import clean_text

# German instances whose local timeline is public (``timelines_access.live_feeds.local == "public"``)
# and whose robots.txt allows the API. Stored posts/day and mentions of tracked entities per 100
# posts, measured in Firestore for 2-8 Nov 2026.
INSTANCES = (
    "norden.social",  # 110/day, 37
    "troet.cafe",  # 83/day, 44
    "nrw.social",  # 74/day, 27
    "mastodon.de",  # 69/day, 71
    "dresden.network",  # 34/day, 46
    "ruhr.social",  # 33/day, 28
    "social.cologne",  # 29/day, 42
    "digitalcourage.social",  # 28/day, 96
    "berlin.social",  # 27/day, 37
    "social.anoxinon.de",  # 12/day, 43
    "mastodontech.de",  # 12/day, 46
    "muenchen.social",  # 9/day, 38
)
# Removed 9 Nov 2026: det.social (429 in every second run, 4 posts/day), hessen.social (429 in
# every fourth run, 19 mentions per 100 posts), bonn.social (17 per 100), nerdculture.de (mostly
# English, 4 per 100), machteburch.social (1 post/day).
PAGE_LIMIT = 40
MAX_PAGES = 40
# Posts of one account stored per instance and run.
PER_AUTHOR = 3


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


def reject_reason(status: dict[str, Any], text: str) -> str | None:
    """Why a post is not stored (``None``: keep): no content, not German, or just a link's headline."""
    reason = quality.reject_reason(text)
    card = status.get("card") or {}
    if reason is None and quality.repeats_link_card(
        text, str(card.get("title") or ""), str(card.get("description") or ""), [str(card.get("url") or "")]
    ):
        reason = "link_card"
    return reason


def parse_statuses(
    statuses: list[dict[str, Any]],
    instance: str,
    source_id: str = "mastodon",
    filtered: Counter[str] | None = None,
) -> list[Comment]:
    """Statuses as comments (same order). Posts dropped for their content or because their author
    already has ``PER_AUTHOR`` posts in this batch are counted in ``filtered``."""
    ids = {str(s.get("id")) for s in statuses}
    per_author: Counter[str] = Counter()
    filtered = filtered if filtered is not None else Counter()
    out: list[Comment] = []
    for s in statuses:
        if not keep_status(s):
            continue
        published = parse_datetime(s.get("created_at"))
        text = status_text(s.get("content") or "")
        if not published or not text:
            continue
        reason = reject_reason(s, text)
        author = str((s.get("account") or {}).get("id") or "")
        if reason is None and author and per_author[author] >= PER_AUTHOR:
            reason = "per_author"
        if reason is not None:
            filtered[reason] += 1
            continue
        per_author[author] += 1
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
    domain = "troet.cafe, mastodon.de, norden.social, … (12 instances)"
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
                    # one retry only: an instance that is down or rate-limits us is skipped for this run
                    page = self.http.get_json(
                        f"https://{instance}/api/v1/timelines/public", params=params, max_retries=1
                    )
                    if not page:
                        break
                    statuses.extend(page)
                    max_id = str(page[-1]["id"])
                    oldest = parse_datetime(page[-1].get("created_at"))
                    if oldest is None or oldest < since:
                        break
            except Exception as exc:  # noqa: BLE001 - one unreachable instance must not stop the others
                self.log.warning("instance %s failed: %s", instance, exc)
            in_window = [s for s in statuses if (t := parse_datetime(s.get("created_at"))) and since <= t <= until]
            per_instance[instance] = parse_statuses(in_window, instance, self.id, self.filtered)
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
