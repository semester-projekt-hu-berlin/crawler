"""ComputerBase (computerbase.de): reader comments on news, which live in the XenForo forum.

robots.txt (group ``*``) disallows ``/api/``, ``/forum/posts/``, ``/forum/members/``,
``/forum/whats-new/``, ``/forum/search/``, ``/forum/goto/`` and a few more forum and shop paths.
The news feed ``/rss/news.xml`` (Atom), news pages and ``/forum/threads/<slug>.<id>/`` with
``page-<n>`` are allowed.

* Articles: Atom feed ``/rss/news.xml`` (~50 newest news).
* Comments: the news page links its comment thread (``a.js-thread-link``); the thread URL is
  stored as ``commentThreadId`` so revisits go straight to the thread. Posts are
  ``article[data-content="post-<id>"]`` with ``time[datetime]`` and ``.bbWrapper``. The first
  post of the thread is the news teaser and is skipped. ``data-author``, user links and
  quotes (``blockquote``, which carry "<name> schrieb:") are never read into the text; a quote's
  ``data-source="post: <id>"`` gives the parent. ``@Name`` mentions (``a.username``) are removed.
* Threads are read newest page first; a revisit stops at the first page with only older posts,
  a first crawl reads at most ``MAX_PAGES`` pages (newest ~300 posts).
"""

from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup
from lxml import etree

from crawler.config import COMMENT_OVERLAP
from crawler.discovery import clean_title
from crawler.ids import canonicalize_url, parse_datetime
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.sources.base import IncrementalPager, Source
from crawler.text import clean_text, html_to_text, remove_quotes

SITE = "https://www.computerbase.de"
ATOM = "{http://www.w3.org/2005/Atom}"
_NEWS_RE = re.compile(r"^https://www\.computerbase\.de/news/[a-z0-9-]+/[a-z0-9.-]+\.\d+/$")
_THREAD_RE = re.compile(r"^/forum/threads/[a-z0-9-]+\.\d+/$")
_PAGE_RE = re.compile(r"/page-(\d+)$")
MAX_PAGES = 15


def parse_atom(xml: bytes, source_id: str = "computerbase") -> list[ArticleRef]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    root = etree.fromstring(xml, parser=parser)
    refs: list[ArticleRef] = []
    if root is None:
        return refs
    for entry in root.iter(f"{ATOM}entry"):
        link = entry.find(f"{ATOM}link[@rel='alternate']")
        if link is None:
            link = entry.find(f"{ATOM}link")
        href = (link.get("href") if link is not None else "") or ""
        published = parse_datetime(entry.findtext(f"{ATOM}published") or entry.findtext(f"{ATOM}updated"))
        if not href:
            continue
        refs.append(
            ArticleRef(
                url=canonicalize_url(href),
                source_id=source_id,
                title=clean_title(entry.findtext(f"{ATOM}title") or ""),
                published_at=published,
                topics=[c.get("term") for c in entry.findall(f"{ATOM}category") if c.get("term")],
                discovered_url=href,
            )
        )
    return refs


def thread_path(article_html: str) -> str | None:
    soup = BeautifulSoup(article_html, "lxml")
    for a in soup.select("a.js-thread-link[href], a[href*='/forum/threads/']"):
        href = a["href"].split("#", 1)[0].removeprefix(SITE)
        if _THREAD_RE.match(href) and ("js-thread-link" in (a.get("class") or []) or "Kommentar" in a.get_text()):
            return href
    return None


def last_page(soup: BeautifulSoup) -> int:
    pages = [int(m.group(1)) for a in soup.select(".pageNav a[href]") if (m := _PAGE_RE.search(a["href"].rstrip("/")))]
    return max(pages, default=1)


def parse_posts(soup: BeautifulSoup, source_id: str = "computerbase", skip_first: bool = False) -> list[Comment]:
    out: list[Comment] = []
    for i, post in enumerate(soup.select("article[data-content^='post-']")):
        if skip_first and i == 0:
            continue  # thread starter = news teaser
        native_id = post["data-content"].removeprefix("post-")
        time_el = post.select_one("time[datetime]")
        body = post.select_one(".bbWrapper")
        if not native_id.isdigit() or time_el is None or body is None:
            continue
        parent = None
        # remove_quotes reads the attributes first: a quote nested in another one is gone once the
        # outer quote is removed (reading it afterwards raised AttributeError and lost the article)
        for attrs in remove_quotes(body):
            src = re.search(r"post:\s*(\d+)", attrs.get("data-source") or "")
            if src and parent is None:
                parent = src.group(1)
        for el in body.select("a.username, .bbCodeBlock-title, script, noscript"):
            el.decompose()
        published = parse_datetime(time_el["datetime"])
        text = clean_text(html_to_text(str(body)))
        if published and text:
            out.append(Comment(source_id, native_id, text, published, native_parent_id=parent))
    return out


class ComputerBase(Source):
    id = "computerbase"
    name = "ComputerBase"
    domain = "computerbase.de"
    country = "DE"
    topic = "tech"
    news_sitemap = f"{SITE}/rss/news.xml"

    def canonical_url(self, url: str) -> str | None:
        url = canonicalize_url(url)
        return url if _NEWS_RE.match(url) else None

    def discover(self, since: datetime, until: datetime) -> list[ArticleRef]:
        refs: dict[str, ArticleRef] = {}
        for ref in parse_atom(self.http.get(self.news_sitemap).content, self.id):
            url = self.canonical_url(ref.url)
            if url is None or url in refs or ref.published_at is None or not (since <= ref.published_at <= until):
                continue
            ref.url = url
            refs[url] = ref
        return sorted(refs.values(), key=lambda r: r.published_at, reverse=True)

    def fetch(self, ref: ArticleRef) -> FetchResult:
        thread = ref.comment_thread_id
        if not thread:
            thread = thread_path(self.http.get(ref.url).text)
            if thread is None:
                return FetchResult(ref, [], 0)
        base = SITE + thread
        first = BeautifulSoup(self.http.get(base).text, "lxml")
        last = last_page(first)
        pager = IncrementalPager(ref, ref.since(COMMENT_OVERLAP), None)
        comments: dict[str, Comment] = {}
        complete = True
        for n, page in enumerate(range(last, 0, -1)):
            if n >= MAX_PAGES:
                complete = False
                break
            soup = first if page == 1 else BeautifulSoup(self.http.get(f"{base}page-{page}").text, "lxml")
            found = parse_posts(soup, self.id, skip_first=page == 1)
            comments.update((c.native_id, c) for c in found)
            if pager.add_page([c.published_at for c in found]) and page > 1:
                complete = False
                break
        return FetchResult(ref, list(comments.values()), None, complete=complete, thread_id=thread)
