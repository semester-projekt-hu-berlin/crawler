"""Bluesky Jetstream: a bounded live sample of German-language posts from the public firehose.

Jetstream (``wss://jetstream<n>.<region>.bsky.network/subscribe``) is Bluesky's official public
JSON version of the firehose; no login, no key. Its robots.txt answers 404, i.e. no
restrictions (RFC 9309), which we check before every connection like for any other URL.

Each run listens for ``JETSTREAM_LIVE_SECONDS`` (default 300 s) to
``wantedCollections=app.bsky.feed.post`` and keeps ``commit.operation == "create"`` records with
``"de"`` in ``record.langs`` until the per-run cap (``SOURCE_LIMITS``) is reached. Replaying the
whole 12 h between two runs (``cursor``) would mean downloading the complete firehose for that
time, so we do not. Uncompressed JSON is used (``compress=true`` needs Jetstream's zstd
dictionary).

Robustness: connect and receive timeouts; a stalled or closed connection fails over to the next
instance and resumes from the last event (``cursor`` = ``time_us``). Errors never propagate:
whatever was collected is returned.

Privacy: the author DID is only held in memory, to drop authors who opted out of logged-out
visibility (label ``!no-unauthenticated``, checked in batches of 25 with the public
``app.bsky.actor.getProfiles``; fail closed) and to build the post URI. The URI is only used as
the native id, which is hashed into the comment id exactly like the Bluesky search source does
(``id_source = "bluesky"``), so a post found by both sources is stored once. Stored: text with
mention facets removed, server receive time, reply structure within the sample.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from crawler.config import USER_AGENT
from crawler.models import Comment
from crawler.sources.base import StreamItem, StreamSource
from crawler.sources.bluesky import OPT_OUT_LABEL, post_text

HOSTS = (
    "jetstream2.us-east.bsky.network",
    "jetstream1.us-east.bsky.network",
    "jetstream1.us-west.bsky.network",
    "jetstream2.us-west.bsky.network",
)
COLLECTION = "app.bsky.feed.post"
LIVE_SECONDS = float(os.environ.get("JETSTREAM_LIVE_SECONDS", "300"))
OPEN_TIMEOUT = 15.0
# No event at all for this long: the connection is considered stalled (the firehose has ~50 events/s).
RECV_TIMEOUT = 30.0
PROFILES_API = "https://api.bsky.app/xrpc/app.bsky.actor.getProfiles"
PROFILES_BATCH = 25
ID_SOURCE = "bluesky"  # same comment ids as the Bluesky search source


def _default_connect(url: str, **kwargs: Any):  # noqa: ANN202
    from websockets.sync.client import connect

    return connect(url, **kwargs)


def subscribe_url(host: str, cursor: int | None = None) -> str:
    params: dict[str, Any] = {"wantedCollections": COLLECTION}
    if cursor:
        params["cursor"] = cursor
    return f"wss://{host}/subscribe?{urlencode(params)}"


def german_post(event: dict[str, Any]) -> tuple[str, str, dict[str, Any], int] | None:
    """``(did, rkey, record, time_us)`` for a new German post, else ``None``."""
    commit = event.get("commit") or {}
    if event.get("kind") != "commit" or commit.get("operation") != "create":
        return None
    if commit.get("collection") != COLLECTION:
        return None
    record = commit.get("record") or {}
    langs = record.get("langs") or []
    if not isinstance(langs, list) or not any(str(lang).lower().split("-")[0] == "de" for lang in langs):
        return None
    did, rkey, time_us = event.get("did"), commit.get("rkey"), event.get("time_us")
    if not did or not rkey or not isinstance(time_us, int):
        return None
    return did, rkey, record, time_us


class BlueskyJetstream(StreamSource):
    id = "bluesky_jetstream"
    name = "Bluesky (deutschsprachig: Live-Stichprobe via Jetstream)"
    domain = "bsky.app (Jetstream)"
    country = "DE"
    source_type = "social"
    topic = "general"
    hosts = HOSTS

    def __init__(self, http, connect: Callable[..., Any] | None = None, live_seconds: float | None = None) -> None:  # noqa: ANN001
        super().__init__(http)
        self._connect = connect or _default_connect
        self.live_seconds = LIVE_SECONDS if live_seconds is None else live_seconds

    # -- listening

    def listen(self, max_posts: int) -> list[tuple[str, str, dict[str, Any], int]]:
        """German posts seen during the live window (at most ``max_posts``). Never raises."""
        posts: dict[tuple[str, str], tuple[str, str, dict[str, Any], int]] = {}
        deadline = time.monotonic() + self.live_seconds
        cursor: int | None = None
        events = 0
        for host in self.hosts:
            if time.monotonic() >= deadline or len(posts) >= max_posts:
                break
            url = subscribe_url(host, cursor)
            try:
                if not self.http.robots.can_fetch(f"https://{host}/subscribe?wantedCollections={COLLECTION}"):
                    self.log.warning("robots.txt disallows %s", host)
                    continue
                with self._connect(
                    url,
                    open_timeout=OPEN_TIMEOUT,
                    close_timeout=5,
                    additional_headers={"User-Agent": USER_AGENT},
                    max_size=2**22,
                ) as ws:
                    while len(posts) < max_posts:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        raw = ws.recv(timeout=min(RECV_TIMEOUT, remaining))
                        events += 1
                        try:
                            event = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        if isinstance(event.get("time_us"), int):
                            cursor = event["time_us"]
                        post = german_post(event)
                        if post is not None:
                            posts.setdefault((post[0], post[1]), post)
            except TimeoutError:
                if time.monotonic() < deadline - 1:
                    self.log.warning("jetstream %s stalled, failing over", host)
                    continue
            except Exception as exc:  # noqa: BLE001 - network trouble must never fail the run
                self.log.warning("jetstream %s failed (%s), failing over", host, type(exc).__name__)
                continue
            break  # window over or cap reached
        self.log.info("jetstream: %d events, %d German posts", events, len(posts))
        return list(posts.values())

    # -- opt-out check

    def opted_in(self, dids: list[str]) -> set[str]:
        """Authors that exist and did not opt out of logged-out visibility. Fails closed per batch."""
        ok: set[str] = set()
        for i in range(0, len(dids), PROFILES_BATCH):
            batch = dids[i : i + PROFILES_BATCH]
            try:
                data = self.http.get_json(PROFILES_API, params={"actors": batch}) or {}
            except Exception as exc:  # noqa: BLE001 - drop this batch (fail closed)
                self.log.warning("profile lookup failed for a batch (%s); its posts are skipped", type(exc).__name__)
                continue
            for profile in data.get("profiles") or []:
                if any(label.get("val") == OPT_OUT_LABEL for label in profile.get("labels") or []):
                    continue
                if profile.get("did"):
                    ok.add(profile["did"])
        return ok

    # -- StreamSource

    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        # The live window lies after ``until`` (run start), so ``since``/``until`` do not apply.
        if max_items <= 0:
            return []
        posts = self.listen(max_items)
        allowed = self.opted_in(sorted({p[0] for p in posts}))
        uris = {f"at://{did}/{COLLECTION}/{rkey}" for did, rkey, _, _ in posts if did in allowed}
        items: list[StreamItem] = []
        for did, rkey, record, time_us in posts:
            uri = f"at://{did}/{COLLECTION}/{rkey}"
            if uri not in uris:
                continue
            text = post_text(record)
            if not text:
                continue
            parent = ((record.get("reply") or {}).get("parent") or {}).get("uri")
            published = datetime.fromtimestamp(time_us / 1e6, UTC)
            comment = Comment(
                self.id,
                uri,
                text,
                published,
                native_parent_id=parent if parent in uris else None,
                id_source=ID_SOURCE,
            )
            ref = self.day_article(
                "https://bsky.app/?feed=jetstream-de", "Bluesky · Live-Stichprobe (Jetstream)", published, ["Bluesky"]
            )
            items.append(StreamItem(ref, comment))
        return items[:max_items]
