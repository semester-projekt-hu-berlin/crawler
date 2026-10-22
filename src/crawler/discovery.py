"""Article discovery and article page metadata helpers.

All supported sources publish a Google News sitemap (listed in their robots.txt)
containing the articles of the last ~48 hours with title and publication date.
That is exactly what the crawler needs, so we read it directly instead of
pulling in a full article extraction framework.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup
from lxml import etree

from crawler.ids import canonicalize_url, parse_datetime

NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}


@dataclass
class SitemapEntry:
    url: str
    title: str
    published_at: datetime | None
    keywords: list[str]


def parse_news_sitemap(xml: bytes) -> list[SitemapEntry]:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    root = etree.fromstring(xml, parser=parser)
    entries: list[SitemapEntry] = []
    if root is None:
        return entries
    for url_el in root.findall("sm:url", NS):
        loc = (url_el.findtext("sm:loc", default="", namespaces=NS) or "").strip()
        if not loc:
            continue
        title = (url_el.findtext("news:news/news:title", default="", namespaces=NS) or "").strip()
        published = parse_datetime(url_el.findtext("news:news/news:publication_date", default=None, namespaces=NS))
        kw = url_el.findtext("news:news/news:keywords", default="", namespaces=NS) or ""
        keywords = [k.strip() for k in kw.split(",") if k.strip()]
        entries.append(SitemapEntry(canonicalize_url(loc), title, published, keywords))
    return entries


@dataclass
class PageMeta:
    canonical_url: str | None = None
    title: str | None = None
    published_at: datetime | None = None
    topics: list[str] | None = None


def _iter_jsonld(soup: BeautifulSoup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, dict):
                if "@graph" in item and isinstance(item["@graph"], list):
                    stack.extend(item["@graph"])
                yield item


def _types(item: dict) -> set[str]:
    t = item.get("@type")
    return set(t) if isinstance(t, list) else {t}


def parse_page_meta(soup: BeautifulSoup) -> PageMeta:
    """Extract canonical URL, headline, date and section path (breadcrumb) from an article page."""
    meta = PageMeta()
    link = soup.find("link", rel="canonical")
    if link and link.get("href"):
        meta.canonical_url = canonicalize_url(link["href"])
    og_title = soup.find("meta", property="og:title")
    breadcrumb: list[str] = []
    for item in _iter_jsonld(soup):
        types = _types(item)
        if types & {"NewsArticle", "Article", "ReportageNewsArticle", "OpinionNewsArticle"}:
            meta.title = meta.title or item.get("headline")
            meta.published_at = meta.published_at or parse_datetime(item.get("datePublished"))
        elif "BreadcrumbList" in types and not breadcrumb:
            elements = sorted(item.get("itemListElement") or [], key=lambda e: e.get("position", 0))
            for el in elements:
                name = el.get("name")
                if not name and isinstance(el.get("item"), dict):
                    name = el["item"].get("name")
                if name:
                    breadcrumb.append(str(name).strip())
    if not meta.title and og_title and og_title.get("content"):
        meta.title = og_title["content"].strip()
    if not meta.published_at:
        tag = soup.find("meta", property="article:published_time")
        if tag:
            meta.published_at = parse_datetime(tag.get("content"))
    # Drop the root ("Home", "Startseite", site name) and the article itself.
    topics = breadcrumb[1:]
    if topics and meta.title and topics[-1].strip() == meta.title.strip().rstrip("​"):
        topics = topics[:-1]
    if topics and len(topics[-1]) > 60:  # article headline used as last crumb
        topics = topics[:-1]
    meta.topics = topics
    return meta


DC = "http://purl.org/dc/elements/1.1/"


def parse_rss(xml: bytes) -> list[SitemapEntry]:
    """RSS 2.0 items as :class:`SitemapEntry` (``pubDate`` or ``dc:date``; categories as keywords)."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    root = etree.fromstring(xml, parser=parser)
    entries: list[SitemapEntry] = []
    if root is None:
        return entries
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        published = None
        pub = item.findtext("pubDate")
        if pub:
            try:
                published = parsedate_to_datetime(pub.strip()).astimezone(UTC)
            except (TypeError, ValueError):
                published = None
        if published is None:
            published = parse_datetime(item.findtext(f"{{{DC}}}date"))
        keywords = [c.text.strip() for c in item.findall("category") if c.text and c.text.strip()]
        subject = item.findtext(f"{{{DC}}}subject") or ""
        keywords += [k.strip() for k in subject.split(",") if k.strip() and k.strip() not in keywords]
        title = clean_title(item.findtext("title") or "")
        entries.append(SitemapEntry(canonicalize_url(link), title, published, keywords))
    return entries


def clean_title(text: str) -> str:
    return " ".join(html.unescape(text).split())
