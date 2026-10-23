"""iphone-ticker.de and ifun.de: German Apple/app blogs (same publisher, same WordPress theme).

robots.txt (group ``*``) of both sites disallows ``/wp-admin/``, ``/wp-includes/``, login, plugins,
a few site paths and ``/*?`` (every URL with a query string, e.g. ``?p=<id>`` shortlinks and
``?paged=``). The main feed ``/feed/`` and each article's comment feed ``<article>/feed/``
(``<wfw:commentRss>``) are allowed; the REST API is not used.

* Articles: main feed ``/feed/`` (title, link, ``pubDate``, categories) incl. ``<slash:comments>``,
  the article's comment count. An unchanged count means no request at all on a revisit.
* Comments: the article's comment feed (the newest 25 comments, ~25 KB). ``dc:creator`` and the
  item title ("Von: <name>") are never read. A reply starts with "Als Antwort auf <a href=
  "…#comment-<parent>">Name</a>." – the parent id is taken from the link and the whole sentence
  (with the name) is removed.
* If the feed does not reach back far enough (first crawl of a busy article, or more new comments
  than the feed holds), the comments are read from the article page instead, which holds all of
  them (``article.comment#comment-<n>``, ``.comment-content``, ``time[datetime]``; replies in the
  ``ul.children`` following their parent). ``.comment-author`` and the reply links (which repeat the
  name) are never read.

Paging the comment feed (``?paged=2``) would be covered by ``Disallow: /*?``, so it is not used.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup, Tag
from lxml import etree

from crawler.config import COMMENT_OVERLAP
from crawler.discovery import clean_title
from crawler.ids import canonicalize_url, parse_datetime
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources.base import Source
from crawler.text import clean_text, html_to_text

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "wfw": "http://wellformedweb.org/CommentAPI/",
    "slash": "http://purl.org/rss/1.0/modules/slash/",
}
_COMMENT_ANCHOR = re.compile(r"#comment-(\d+)")


def _rss_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return parsedate_to_datetime(text.strip()).astimezone(UTC)
    except (TypeError, ValueError):
        return parse_datetime(text)


def _xml_root(xml: bytes):  # noqa: ANN202
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    return etree.fromstring(xml, parser=parser)


def parse_article_feed(xml: bytes, source_id: str) -> list[ArticleRef]:
    """Main feed items with comment count (``slash:comments``) and categories as topics."""
    root = _xml_root(xml)
    refs: list[ArticleRef] = []
    if root is None:
        return refs
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        count = (item.findtext("slash:comments", namespaces=NS) or "").strip()
        refs.append(
            ArticleRef(
                url=canonicalize_url(link),
                source_id=source_id,
                title=clean_title(item.findtext("title") or ""),
                published_at=_rss_date(item.findtext("pubDate")),
                topics=[c.text.strip() for c in item.findall("category") if c.text and c.text.strip()],
                listed_comment_count=int(count) if count.isdigit() else None,
                discovered_url=link,
            )
        )
    return refs


def _reply_parent(soup: BeautifulSoup) -> str | None:
    """Remove the "Als Antwort auf <a>Name</a>." paragraph; return the parent comment id."""
    for p in soup.find_all("p"):
        if p.get_text(" ", strip=True).startswith("Als Antwort auf"):
            link = p.find("a", href=_COMMENT_ANCHOR)
            match = _COMMENT_ANCHOR.search(link["href"]) if link is not None else None
            p.decompose()
            return match.group(1) if match else None
    return None


def parse_comment_feed(xml: bytes, source_id: str) -> list[Comment]:
    """Comments of one article's comment feed. Author (``dc:creator``, item title) is not read."""
    root = _xml_root(xml)
    out: list[Comment] = []
    if root is None:
        return out
    for item in root.iter("item"):
        anchor = _COMMENT_ANCHOR.search(item.findtext("link") or "") or _COMMENT_ANCHOR.search(
            item.findtext("guid") or ""
        )
        published = _rss_date(item.findtext("pubDate"))
        html = item.findtext("content:encoded", namespaces=NS) or item.findtext("description") or ""
        if anchor is None or published is None:
            continue
        soup = BeautifulSoup(html, "lxml")
        parent = _reply_parent(soup)
        text = clean_text(html_to_text(str(soup)))
        if text:
            out.append(Comment(source_id, anchor.group(1), text, published, native_parent_id=parent))
    return out


def parse_article_comments(html: str, source_id: str) -> list[Comment]:
    """All comments from the article page (fallback when the comment feed is not enough)."""
    soup = BeautifulSoup(html, "lxml")
    out: list[Comment] = []
    for art in soup.select("article.comment[id^=comment-]"):
        native_id = art["id"].removeprefix("comment-")
        if not native_id.isdigit():
            continue
        li = art.find_parent("li")
        time_el = li.select_one("time[datetime]") if isinstance(li, Tag) else None
        body = art.select_one(".comment-content")
        if body is None or time_el is None:
            continue
        for el in body.select("div.reply, .comment-reply-link"):
            el.decompose()
        parent = None
        holder = li.parent if isinstance(li, Tag) else None
        if isinstance(holder, Tag) and holder.name == "ul" and "children" in (holder.get("class") or []):
            prev = holder.find_previous_sibling("li")
            if isinstance(prev, Tag) and (prev.get("id") or "").startswith("li-comment-"):
                parent = prev["id"].removeprefix("li-comment-")
        published = parse_datetime(time_el["datetime"])
        text = clean_text(html_to_text(str(body)))
        if published and text:
            out.append(Comment(source_id, native_id, text, published, native_parent_id=parent))
    return out


class AppBlog(Source):
    """WordPress app blog: articles from ``/feed/``, comments from ``<article>/feed/`` (or the page)."""

    country = "DE"
    topic = "tech"
    feed_type = "rss"
    host: str

    def canonical_url(self, url: str) -> str | None:
        url = canonicalize_url(url)
        return url if re.match(rf"^https://{re.escape(self.host)}/[a-z0-9-]+-\d+/$", url) else None

    def discover(self, since: datetime, until: datetime) -> list[ArticleRef]:
        resp = self.http.get(self.news_sitemap)
        refs: dict[str, ArticleRef] = {}
        for ref in parse_article_feed(resp.content, self.id):
            url = self.canonical_url(ref.url)
            if url is None or url in refs or ref.published_at is None or not (since <= ref.published_at <= until):
                continue
            ref.url = url
            refs[url] = ref
        return sorted(refs.values(), key=lambda r: r.published_at, reverse=True)

    def rank_new(self, refs: list[ArticleRef], limit: int | None = None) -> list[ArticleRef]:
        """Most discussed first (comment count from the feed), then newest."""
        return sorted(
            refs,
            key=lambda r: (r.listed_comment_count or 0, r.published_at or datetime.min.replace(tzinfo=UTC)),
            reverse=True,
        )

    def fetch(self, ref: ArticleRef) -> FetchResult:
        total = ref.listed_comment_count
        if ref.known and total is not None and total == ref.known_comment_count:
            return FetchResult(ref, [], total, complete=False, unchanged=True)
        if total == 0:
            return FetchResult(ref, [], 0)
        comments = parse_comment_feed(self.http.get(ref.url + "feed/").content, self.id)
        since = ref.since(COMMENT_OVERLAP)
        covers_all = total is not None and len(comments) >= total
        reaches_cutoff = since is not None and any(c.published_at < since for c in comments)
        if not (covers_all or reaches_cutoff):
            # The feed only holds the newest comments: read the full list from the article page.
            comments = parse_article_comments(self.http.get(ref.url).text, self.id)
            total = max(total or 0, len(comments))
        return FetchResult(ref, comments, total)


class IphoneTicker(AppBlog):
    id = "iphoneticker"
    name = "iphone-ticker.de"
    domain = "iphone-ticker.de"
    host = "www.iphone-ticker.de"
    news_sitemap = "https://www.iphone-ticker.de/feed/"


class Ifun(AppBlog):
    id = "ifun"
    name = "iFun.de"
    domain = "ifun.de"
    host = "www.ifun.de"
    news_sitemap = "https://www.ifun.de/feed/"
