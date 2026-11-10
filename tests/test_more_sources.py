"""Offline tests for iphone-ticker.de/ifun.de, Bluesky Jetstream, the extra WordPress REST sources,
ComputerBase and wallstreet-online (fixtures inline, no network)."""

import json
from datetime import UTC, datetime, timedelta

from bs4 import BeautifulSoup

from crawler.ids import comment_id
from crawler.models import ArticleRef, Comment
from crawler.sources import SOURCES
from crawler.sources.appblogs import IphoneTicker, parse_article_comments, parse_article_feed, parse_comment_feed
from crawler.sources.bluesky import parse_posts
from crawler.sources.computerbase import ComputerBase, last_page, parse_atom, thread_path
from crawler.sources.computerbase import parse_posts as cb_posts
from crawler.sources.jetstream import BlueskyJetstream, german_post, subscribe_url
from crawler.sources.mastodon import INSTANCES
from crawler.sources.tichyseinblick import ApolloNews, WordPressComments, in_sample
from crawler.sources.wallstreet import WallstreetOnline, parse_thread_page, parse_timestamp, thread_links

NAMES = ("Max Muster", "maxm", "Erika", "did:plc:", "Kampfkater", "Myzel24", "schrieb")


def _no_names(comments):
    for c in comments:
        for n in NAMES:
            assert n not in c.text, (n, c.text)


# ------------------------------------------------------------------ iphone-ticker.de / ifun.de

MAIN_FEED = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"
 xmlns:wfw="http://wellformedweb.org/CommentAPI/" xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:slash="http://purl.org/rss/1.0/modules/slash/"><channel>
<item><title>iPhone Duo: Lange Schlangen</title>
<link>https://www.iphone-ticker.de/iphone-duo-lange-schlangen-289045/</link>
<dc:creator><![CDATA[Redakteur]]></dc:creator>
<pubDate>Fri, 23 Oct 2026 05:00:00 +0000</pubDate>
<category><![CDATA[News]]></category><category><![CDATA[iPhone]]></category>
<wfw:commentRss>https://www.iphone-ticker.de/iphone-duo-lange-schlangen-289045/feed/</wfw:commentRss>
<slash:comments>3</slash:comments></item>
<item><title>Ohne Kommentare</title><link>https://www.iphone-ticker.de/ohne-kommentare-289046/</link>
<pubDate>Fri, 23 Oct 2026 06:00:00 +0000</pubDate><slash:comments>0</slash:comments></item>
<item><title>Seite</title><link>https://www.iphone-ticker.de/kontakt/</link>
<pubDate>Fri, 23 Oct 2026 06:00:00 +0000</pubDate></item>
</channel></rss>"""

COMMENT_FEED = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"
 xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
<item><title>Von: Max Muster</title>
<link>https://www.iphone-ticker.de/iphone-duo-lange-schlangen-289045/#comment-102</link>
<dc:creator><![CDATA[Max Muster]]></dc:creator>
<pubDate>Fri, 23 Oct 2026 08:00:00 +0000</pubDate>
<description><![CDATA[Als Antwort auf Erika. In Dresden auch]]></description>
<content:encoded><![CDATA[<p>Als Antwort auf <a href="https://www.iphone-ticker.de/iphone-duo-lange-schlangen-289045/#comment-101">Erika</a>.</p>
<p>In Dresden auch problemlos gewesen.</p>]]></content:encoded></item>
<item><title>Von: Erika</title>
<link>https://www.iphone-ticker.de/iphone-duo-lange-schlangen-289045/#comment-101</link>
<dc:creator><![CDATA[Erika]]></dc:creator>
<pubDate>Fri, 23 Oct 2026 07:00:00 +0000</pubDate>
<content:encoded><![CDATA[<p>Die Schlange in Berlin war riesig, das Duo ist zu gro&#223;.</p>]]></content:encoded></item>
</channel></rss>"""

ARTICLE_HTML = """<html><body><div id="comments"><div class="commentlist">
<li class="comment depth-1" id="li-comment-101"><div id="comment-block"><div id="comment-metadaten">
 <span class="comment-author">Erika</span>
 <span class="comment-date"><a href="#comment-101"><time pubdate datetime="2026-10-23T09:00:00+02:00">23.10.2026</time></a></span></div>
 <article id="comment-101" class="comment"><div class="comment-content"><p>Die Schlange war riesig.</p>
 <div class="reply" style="display:none;"><a class="comment-reply-link" aria-label="Auf Erika antworten">Antworten</a></div></div></article>
 <div class="reply"><a class="comment-reply-link" data-replyto="Auf Erika antworten">Antworten</a></div></div></li>
<ul class="children">
<li class="comment depth-2" id="li-comment-102"><div id="comment-block"><div id="comment-metadaten">
 <span class="comment-author">Max Muster</span>
 <span class="comment-date"><time datetime="2026-10-23T10:00:00+02:00">x</time></span></div>
 <article id="comment-102" class="comment"><div class="comment-content"><p>In Dresden auch.</p></div></article></div></li>
<ul class="children"><li class="comment depth-3" id="li-comment-103"><div id="comment-block"><div id="comment-metadaten">
 <span class="comment-author">maxm</span><span class="comment-date"><time datetime="2026-10-23T11:00:00+02:00">x</time></span></div>
 <article id="comment-103" class="comment"><div class="comment-content"><p>+1</p></div></article></div></li></ul>
</ul>
<li class="comment depth-1" id="li-comment-104"><div id="comment-block"><div id="comment-metadaten">
 <span class="comment-author">Erika</span><span class="comment-date"><time datetime="2026-10-23T12:00:00+02:00">x</time></span></div>
 <article id="comment-104" class="comment"><div class="comment-content"><p>Neuer Thread.</p></div></article></div></li>
</div></div></body></html>"""


def test_appblog_feed_has_counts_and_categories():
    refs = parse_article_feed(MAIN_FEED, "iphoneticker")
    assert refs[0].url == "https://www.iphone-ticker.de/iphone-duo-lange-schlangen-289045/"
    assert refs[0].listed_comment_count == 3 and refs[1].listed_comment_count == 0
    assert refs[0].topics == ["News", "iPhone"]
    t = IphoneTicker(http=None)
    assert t.canonical_url(refs[0].url) == refs[0].url
    assert t.canonical_url("https://www.iphone-ticker.de/kontakt/") is None
    assert t.topic == "tech" and t.source_doc()["topic"] == "tech"


def test_appblog_comment_feed_drops_author_and_reply_name():
    comments = parse_comment_feed(COMMENT_FEED, "iphoneticker")
    assert [c.native_id for c in comments] == ["102", "101"]
    assert comments[0].text == "In Dresden auch problemlos gewesen."
    assert comments[0].native_parent_id == "101" and comments[1].native_parent_id is None
    assert comments[1].text == "Die Schlange in Berlin war riesig, das Duo ist zu groß."
    assert comments[0].published_at == datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
    _no_names(comments)


def test_appblog_article_html_all_comments_with_parents():
    comments = parse_article_comments(ARTICLE_HTML, "ifun")
    assert [(c.native_id, c.native_parent_id) for c in comments] == [
        ("101", None), ("102", "101"), ("103", "102"), ("104", None),
    ]  # fmt: skip
    assert comments[0].text == "Die Schlange war riesig."  # hidden reply link ("Auf Erika antworten") removed
    assert comments[0].published_at == datetime(2026, 10, 23, 7, 0, tzinfo=UTC)
    _no_names(comments)


class _PageHttp:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get(self, url, **kw):
        self.urls.append(url)
        body = self.pages[url]

        class R:
            content = body if isinstance(body, bytes) else body.encode()
            text = body.decode() if isinstance(body, bytes) else body

        return R()


def test_appblog_fetch_feed_html_fallback_and_unchanged():
    url = "https://www.iphone-ticker.de/iphone-duo-lange-schlangen-289045/"
    http = _PageHttp({url + "feed/": COMMENT_FEED, url: ARTICLE_HTML})
    src = IphoneTicker(http)
    # first crawl, 4 comments listed but the feed holds 2 -> the article page is read
    res = src.fetch(ArticleRef(url, "iphoneticker", listed_comment_count=4))
    assert http.urls == [url + "feed/", url] and len(res.comments) == 4 and res.reported_total == 4
    # feed covers everything -> no page request
    http.urls.clear()
    res = src.fetch(ArticleRef(url, "iphoneticker", listed_comment_count=2))
    assert http.urls == [url + "feed/"] and len(res.comments) == 2
    # revisit: feed reaches back past the previous crawl -> feed is enough
    http.urls.clear()
    ref = ArticleRef(url, "iphoneticker", listed_comment_count=40, known=True, known_comment_count=39,
                     last_comment_crawl_at=datetime(2026, 10, 23, 7, 30, tzinfo=UTC))  # fmt: skip
    src.fetch(ref)
    assert http.urls == [url + "feed/"]
    # unchanged count from the main feed: no request at all; zero comments: none either
    http.urls.clear()
    ref = ArticleRef(url, "iphoneticker", listed_comment_count=39, known=True, known_comment_count=39)
    assert src.fetch(ref).unchanged
    assert src.fetch(ArticleRef(url, "iphoneticker", listed_comment_count=0)).comments == []
    assert http.urls == []


def test_appblog_rank_new_prefers_discussed():
    a = ArticleRef("a", "ifun", published_at=datetime(2026, 10, 23, tzinfo=UTC), listed_comment_count=2)
    b = ArticleRef("b", "ifun", published_at=datetime(2026, 10, 22, tzinfo=UTC), listed_comment_count=50)
    assert [r.url for r in IphoneTicker(http=None).rank_new([a, b])] == ["b", "a"]


# ------------------------------------------------------------------ Bluesky Jetstream

DID = "did:plc:abcdefghijklmnop"


def _event(rkey, text="Die Bahn ist wieder zu spät", langs=("de",), op="create", t=1792772235000000, reply=None,
           did=DID):  # fmt: skip
    record = {"$type": "app.bsky.feed.post", "text": text, "langs": list(langs), "createdAt": "2020-01-01T00:00:00Z"}
    if reply:
        record["reply"] = {"parent": {"uri": reply}, "root": {"uri": reply}}
    return json.dumps({"did": did, "time_us": t, "kind": "commit",
                       "commit": {"operation": op, "collection": "app.bsky.feed.post", "rkey": rkey,
                                  "record": record}})  # fmt: skip


class _FakeWS:
    def __init__(self, messages, end_exc):
        self.messages = list(messages)
        self.end_exc = end_exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def recv(self, timeout=None):
        if self.messages:
            return self.messages.pop(0)
        raise self.end_exc


class _Robots:
    def can_fetch(self, url):
        return True


class _ProfileHttp:
    def __init__(self, hidden=(), fail=False):
        self.robots = _Robots()
        self.hidden = set(hidden)
        self.fail = fail
        self.calls = []

    def get_json(self, url, params=None, **kw):
        self.calls.append(params["actors"])
        if self.fail:
            raise RuntimeError("down")
        return {"profiles": [{"did": d, "handle": "max.bsky.social",
                              "labels": [{"val": "!no-unauthenticated"}] if d in self.hidden else []}
                             for d in params["actors"]]}  # fmt: skip


def test_jetstream_filter():
    assert german_post(json.loads(_event("1"))) is not None
    assert german_post(json.loads(_event("1", langs=("de-AT",)))) is not None
    assert german_post(json.loads(_event("1", langs=("en",)))) is None
    assert german_post(json.loads(_event("1", op="delete"))) is None
    assert german_post({"kind": "identity", "did": DID}) is None
    assert "cursor=123" in subscribe_url("jetstream2.us-east.bsky.network", 123)


def test_jetstream_failover_cap_optout_and_ids():
    first = [_event("1"), "not json", _event("2", langs=("en",)), _event("3", reply=f"at://{DID}/app.bsky.feed.post/1")]
    second = [_event("4", did="did:plc:hidden", t=1792772236000000), _event("5", t=1792772237000000),
              _event("6", t=1792772238000000)]  # fmt: skip
    urls = []

    def connect(url, **kw):
        urls.append(url)
        if len(urls) == 1:
            return _FakeWS(first, ConnectionResetError("reset"))
        if len(urls) == 2:
            raise OSError("unreachable")
        return _FakeWS(second, TimeoutError())

    http = _ProfileHttp(hidden={"did:plc:hidden"})
    src = BlueskyJetstream(http, connect=connect, live_seconds=60)
    items = src.fetch_since(datetime.now(UTC) - timedelta(hours=12), datetime.now(UTC), max_items=4)
    # failover: 1st host reset, 2nd unreachable, 3rd resumes from the last cursor
    assert len(urls) == 3 and "cursor=" not in urls[0] and "cursor=1792772235000000" in urls[2]
    texts = [i.comment for i in items]
    # cap 4 German posts collected (1, 3, 4, 5); post 4's author opted out
    assert [c.native_id.rsplit("/", 1)[1] for c in texts] == ["1", "3", "5"]
    reply = texts[1]
    assert reply.parent_id == texts[0].id
    # same comment id as the Bluesky search source -> stored once
    uri = f"at://{DID}/app.bsky.feed.post/1"
    search = parse_posts([{"uri": uri, "indexedAt": "2026-10-22T11:27:49Z", "record": {"text": "ein Post"}}])[0]
    assert texts[0].id == search.id == comment_id("bluesky", uri)
    assert texts[0].source_id == "bluesky_jetstream" and texts[0].upvotes is None
    assert items[0].article.url.startswith("https://bsky.app/?feed=jetstream-de&day=")
    for c in texts:
        assert DID not in c.text and "did:" not in c.text


def test_jetstream_fails_closed_and_never_raises():
    def connect(url, **kw):
        return _FakeWS([_event("1")], ConnectionResetError())

    src = BlueskyJetstream(_ProfileHttp(fail=True), connect=connect, live_seconds=5)
    assert src.fetch_since(datetime.now(UTC), datetime.now(UTC), 100) == []  # profile lookup failed -> drop

    def broken(url, **kw):
        raise OSError("no network")

    assert (
        BlueskyJetstream(_ProfileHttp(), connect=broken, live_seconds=5).fetch_since(
            datetime.now(UTC), datetime.now(UTC), 100
        )
        == []
    )


def test_comment_id_namespace_default_unchanged():
    c = Comment("mastodon", "x:1", "t", datetime.now(UTC), native_parent_id="x:0")
    assert c.id == comment_id("mastodon", "x:1") and c.parent_id == comment_id("mastodon", "x:0")


# ------------------------------------------------------------------ Mastodon instances


def test_mastodon_instances():
    for host in ("troet.cafe", "mastodon.de", "norden.social", "mastodontech.de", "social.anoxinon.de"):
        assert host in INSTANCES
    assert "swiss.social" not in INSTANCES and "wien.rocks" not in INSTANCES
    # dropped 9 Nov 2026: rate-limited (429) or hardly any tracked entity in their posts
    for host in ("det.social", "hessen.social", "bonn.social", "nerdculture.de", "machteburch.social"):
        assert host not in INSTANCES
    assert len(set(INSTANCES)) == len(INSTANCES)


# ------------------------------------------------------------------ WordPress REST (Apollo & co.)


class _WpHttp:
    def __init__(self, n):
        self.rows = [{"id": i, "post": 7, "parent": 0, "date_gmt": "2026-10-23T08:00:00",
                      "content": {"rendered": f"<p>Kommentar {i}</p>"}, "author_name": "Max Muster"}
                     for i in range(1, n + 1)]  # fmt: skip

    def get_json(self, url, params=None, **kw):
        if url.endswith("/comments"):
            start = (params["page"] - 1) * params["per_page"]
            return self.rows[start : start + params["per_page"]]
        return [{"id": 7, "link": "https://apollo-news.net/x/", "title": {"rendered": "Titel"},
                 "date_gmt": "2026-10-23T06:00:00"}]  # fmt: skip


def test_apollo_deterministic_sample():
    since, until = datetime(2026, 10, 23, tzinfo=UTC), datetime(2026, 10, 24, tzinfo=UTC)
    items = ApolloNews(_WpHttp(1000)).fetch_since(since, until, 10_000)
    assert 60 <= len(items) <= 140  # ~10 %
    again = ApolloNews(_WpHttp(1000)).fetch_since(since, until, 10_000)
    assert [i.comment.id for i in items] == [i.comment.id for i in again]  # same sample every run
    assert items[0].article.url == "https://apollo-news.net/?p=7"
    _no_names([i.comment for i in items])
    c = Comment("x", "1", "t", since)
    assert in_sample(c, 1.0) and not in_sample(c, 0.0)
    assert WordPressComments.sample_share == 1.0
    for sid in ("apollo", "overton", "appgefahren", "lawblog"):
        assert sid in SOURCES


# ------------------------------------------------------------------ ComputerBase

CB_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Nvidia DLSS 5: Metro</title>
<link rel="alternate" type="text/html" href="https://www.computerbase.de/news/gaming/metro-dlss-5-nr.99971/" />
<id>tag:computerbase.de,2026:artikel-99971</id><published>2026-10-23T16:33:00+02:00</published>
<updated>2026-10-23T16:33:00+02:00</updated><category term="Gaming"/></entry></feed>"""

CB_ARTICLE = """<div class="article-view__meta-right"><a class="article__comments-link js-thread-link"
 href="/forum/threads/nvidia-dlss-5-metro.2280699/">25 Kommentare</a></div>
<a href="/forum/threads/anderes-thema.123/">anders</a>"""

CB_THREAD = """<html><body><div class="pageNav"><a href="/forum/threads/nvidia-dlss-5-metro.2280699/page-2">2</a></div>
<article class="message" data-author="Redaktion" data-content="post-1"><time datetime="2026-10-23T16:00:00+0200">x</time>
 <div class="bbWrapper">Teaser <b>Zur News: <a href="/news/x">x</a></b></div></article>
<article class="message" data-author="Max Muster" data-content="post-2"><time datetime="2026-10-23T16:36:16+0200">x</time>
 <div class="bbWrapper">DLSS5 kann nicht alles retten.</div></article>
<article class="message" data-author="maxm" data-content="post-3"><time datetime="2026-10-23T17:00:00+0200">x</time>
 <div class="bbWrapper"><blockquote class="bbCodeBlock bbCodeBlock--quote" data-quote="Max Muster" data-source="post: 2">
 <div class="bbCodeBlock-title">Max Muster schrieb:</div><div class="bbCodeBlock-content">DLSS5 kann nicht alles retten.</div></blockquote>
 <a class="username" data-user-id="9">@Erika</a> Sehe ich anders.</div></article>
</body></html>"""


def test_computerbase_parsing():
    refs = parse_atom(CB_ATOM)
    assert refs[0].url == "https://www.computerbase.de/news/gaming/metro-dlss-5-nr.99971/"
    assert refs[0].published_at == datetime(2026, 10, 23, 14, 33, tzinfo=UTC) and refs[0].topics == ["Gaming"]
    assert ComputerBase(http=None).canonical_url(refs[0].url) == refs[0].url
    assert thread_path(CB_ARTICLE) == "/forum/threads/nvidia-dlss-5-metro.2280699/"
    soup = BeautifulSoup(CB_THREAD, "lxml")
    assert last_page(soup) == 2
    posts = cb_posts(soup, skip_first=True)
    assert [(c.native_id, c.native_parent_id) for c in posts] == [("2", None), ("3", "2")]
    assert posts[1].text == "Sehe ich anders."
    _no_names(posts)


def test_computerbase_nested_quotes_do_not_break_the_thread():
    # a quote inside a quote: reading the inner one after the outer was removed raised AttributeError
    # (the whole article was lost, 1-2 per run)
    html = """<article data-content="post-7"><time datetime="2026-11-04T10:00:00+0100"></time>
      <div class="bbWrapper"><blockquote data-source="post: 5"><a class="username">Max Muster</a>
      <blockquote data-source="post: 3">ganz alt</blockquote>alt</blockquote>Stimmt so nicht.</div></article>"""
    posts = cb_posts(BeautifulSoup(html, "lxml"))
    assert [(c.native_id, c.native_parent_id, c.text) for c in posts] == [("7", "5", "Stimmt so nicht.")]
    _no_names(posts)


def test_computerbase_fetch_reads_newest_page_first_and_stores_thread():
    url = "https://www.computerbase.de/news/gaming/metro-dlss-5-nr.99971/"
    base = "https://www.computerbase.de/forum/threads/nvidia-dlss-5-metro.2280699/"
    page2 = CB_THREAD.replace('data-content="post-', 'data-content="post-10').replace("2026-10-23", "2026-10-24")
    http = _PageHttp({url: CB_ARTICLE, base: CB_THREAD, base + "page-2": page2})
    res = ComputerBase(http).fetch(ArticleRef(url, "computerbase"))
    assert http.urls == [url, base, base + "page-2"]
    assert res.thread_id == "/forum/threads/nvidia-dlss-5-metro.2280699/"
    assert {c.native_id for c in res.comments} == {"2", "3", "101", "102", "103"}  # page 2 has no teaser
    # revisit: thread id known, newest page holds only old posts -> stop there
    http.urls.clear()
    ref = ArticleRef(url, "computerbase", known=True, comment_thread_id=res.thread_id,
                     last_comment_crawl_at=datetime(2026, 10, 25, tzinfo=UTC))  # fmt: skip
    ComputerBase(http).fetch(ref)
    assert http.urls == [base, base + "page-2"]


# ------------------------------------------------------------------ wallstreet-online

WO_COMMUNITY = """<a href="/diskussion/1313540-1/biontech-riese#neuster_beitrag">a</a>
<a href="/diskussion/1313540-1/biontech-riese#beitrag_1">a</a><a href="/diskussion/976618-1/silber#neuster_beitrag">b</a>
<a href="/community/foren.html">x</a>"""

WO_THREAD = """<html><body><h1>BioNTech - Ein deutscher Biotech-Riese erwacht</h1>
<div class="posting" data-id="80297138" data-nick="Myzel24" data-userid="920373">
 <div class="postingHead"><div class="nick">Myzel24</div><div class="timestamp"> 23.10.26 18:14:58 </div></div>
 <div class="postingText bodyText"><p>Wenn man die Charts vergleicht, sieht man das Potenzial.</p></div></div>
<div class="posting" data-id="80297045" data-nick="Kampfkater1969">
 <div class="postingHead"><div class="timestamp">23.10.26 17:01</div></div>
 <div class="postingText bodyText"><blockquote><div><cite>Zitat von Kampfkater1969:
 <a class="internalLink" href="/diskussion/1313540-1/biontech-riese#beitrag_80296796"></a></cite>
 <p>Altes Zitat</p></div></blockquote><p>Sehe ich auch so.</p></div></div>
</body></html>"""


def test_wallstreet_parsing():
    assert thread_links(WO_COMMUNITY) == [("1313540", "biontech-riese"), ("976618", "silber")]
    assert parse_timestamp("23.10.26 18:14:58") == datetime(2026, 10, 23, 16, 14, 58, tzinfo=UTC)
    title, posts = parse_thread_page(WO_THREAD)
    assert title.startswith("BioNTech")
    assert [(c.native_id, c.native_parent_id) for c in posts] == [("80297138", None), ("80297045", "80296796")]
    assert posts[1].text == "Sehe ich auch so."
    _no_names(posts)


def test_wallstreet_fetch_round_robin_and_window():
    pages = {"https://www.wallstreet-online.de/community": WO_COMMUNITY}
    for tid, slug in (("1313540", "biontech-riese"), ("976618", "silber")):
        pages[f"https://www.wallstreet-online.de/diskussion/{tid}-1/{slug}"] = WO_THREAD.replace("80297", tid)
        pages[f"https://www.wallstreet-online.de/diskussion/{tid}-2/{slug}"] = "<html></html>"
    http = _PageHttp(pages)
    until = datetime(2026, 10, 23, 18, tzinfo=UTC)
    items = WallstreetOnline(http).fetch_since(datetime(2026, 10, 23, 14, tzinfo=UTC), until, 3)
    # all posts of page 1 are new -> page 2 is read too; the cap is filled round robin over threads
    assert sum(u.endswith("-2/biontech-riese") or u.endswith("-2/silber") for u in http.urls) == 2
    assert [i.article.url.rsplit("/", 1)[1] for i in items] == ["biontech-riese", "silber", "biontech-riese"]
    # older posts than the window: only the newest post is kept and page 2 is not requested
    http.urls.clear()
    items = WallstreetOnline(http).fetch_since(datetime(2026, 10, 23, 15, 30, tzinfo=UTC), until, 10)
    assert len(items) == 2 and not any("-2/" in u for u in http.urls)
