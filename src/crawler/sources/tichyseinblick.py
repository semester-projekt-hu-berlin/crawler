"""Tichys Einblick (tichyseinblick.de): site-wide comment feed of the public WordPress REST API.

``/wp-json/wp/v2/comments`` lists the approved comments of all articles, newest first,
and accepts ``after=`` (site-local time). One request returns 100 comments, so a whole
day of comments costs a handful of requests instead of one request per article. Article
title/link/date come from ``/wp-json/wp/v2/posts?include=...``. We request only
``id, post, parent, date_gmt, content`` (``_fields``), so author name, avatar and author
id never even reach the crawler. robots.txt (group ``*``) does not restrict ``/wp-json/``.

Comments are pre-moderated and keep their original timestamp when released, so this
source uses a long overlap window; comments already stored are filtered with a cheap
Firestore read before writing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import urlsplit

from crawler.ids import BERLIN, parse_datetime
from crawler.models import ArticleRef, Comment
from crawler.sources.base import StreamItem, StreamSource
from crawler.text import clean_text, html_to_text

PER_PAGE = 100


def parse_wp_comments(rows: list[dict[str, Any]], source_id: str = "tichyseinblick") -> list[tuple[int, Comment]]:
    out: list[tuple[int, Comment]] = []
    for row in rows:
        published = parse_datetime((row.get("date_gmt") or "") + "+00:00") if row.get("date_gmt") else None
        html = (row.get("content") or {}).get("rendered") or ""
        text = clean_text(html_to_text(html))
        if not row.get("id") or not row.get("post") or not published or not text:
            continue
        parent = row.get("parent") or None
        out.append(
            (
                int(row["post"]),
                Comment(source_id, str(row["id"]), text, published, native_parent_id=str(parent) if parent else None),
            )
        )
    return out


class WordPressComments(StreamSource):
    """Site-wide WordPress REST comment feed (``/wp-json/wp/v2/comments``), newest first.

    Subclasses set ``site`` (``https://host``). ``_fields`` asks the server to leave out author
    data; sites that ignore ``_fields`` still send it, but the parser never reads it.
    """

    site: ClassVar[str]
    source_type = "news"
    overlap: ClassVar[timedelta] = timedelta(hours=12)

    @property
    def api(self) -> str:
        return f"{self.site}/wp-json/wp/v2"

    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        after = since.astimezone(BERLIN).replace(tzinfo=None).isoformat(timespec="seconds")
        fields = "id,post,parent,date_gmt,content"
        pairs: list[tuple[int, Comment]] = []
        page = 1
        while len(pairs) < max_items:
            rows = self.http.get_json(
                f"{self.api}/comments",
                params={"per_page": PER_PAGE, "page": page, "after": after, "_fields": fields},
                allow_404=True,
            )
            if not isinstance(rows, list) or not rows:
                break
            pairs.extend(parse_wp_comments(rows, self.id))
            if len(rows) < PER_PAGE:
                break
            page += 1
        pairs = [(p, c) for p, c in pairs if since <= c.published_at <= until][:max_items]
        posts = self._posts(sorted({p for p, _ in pairs}))
        items: list[StreamItem] = []
        for post_id, comment in pairs:
            ref = posts.get(post_id)
            if ref is not None:
                items.append(StreamItem(ref, comment))
        return items

    def _posts(self, ids: list[int]) -> dict[int, ArticleRef]:
        out: dict[int, ArticleRef] = {}
        for i in range(0, len(ids), PER_PAGE):
            chunk = ids[i : i + PER_PAGE]
            rows = self.http.get_json(
                f"{self.api}/posts",
                params={
                    "include": ",".join(map(str, chunk)),
                    "per_page": PER_PAGE,
                    "_fields": "id,link,title,date_gmt",
                },
            )
            for row in rows if isinstance(rows, list) else []:
                link = row.get("link") or ""
                section = [p for p in urlsplit(link).path.split("/") if p][:1] if self.section_from_path else []
                out[int(row["id"])] = ArticleRef(
                    # WordPress shortlink: stable even if the slug changes.
                    url=f"{self.site}/?p={int(row['id'])}",
                    source_id=self.id,
                    title=clean_text(html_to_text((row.get("title") or {}).get("rendered") or "")),
                    published_at=parse_datetime((row.get("date_gmt") or "") + "+00:00"),
                    topics=[s.replace("-", " ").title() for s in section],
                )
        return out

    # first URL path segment is a section (Tichys Einblick), not a date or slug
    section_from_path: ClassVar[bool] = False


class TichysEinblick(WordPressComments):
    id = "tichyseinblick"
    name = "Tichys Einblick"
    domain = "tichyseinblick.de"
    country = "DE"
    topic = "politics"
    site = "https://www.tichyseinblick.de"
    section_from_path = True


class Netzpolitik(WordPressComments):
    id = "netzpolitik"
    name = "netzpolitik.org"
    domain = "netzpolitik.org"
    country = "DE"
    topic = "tech"
    site = "https://netzpolitik.org"


class Ruhrbarone(WordPressComments):
    id = "ruhrbarone"
    name = "Ruhrbarone"
    domain = "ruhrbarone.de"
    country = "DE"
    topic = "politics"
    site = "https://www.ruhrbarone.de"
