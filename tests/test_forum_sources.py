"""Offline tests for BR24, rbb24, Allmystery, Finanztip, GoingElectric, Kuketz-Forum, the Bundestag
petitions forum, the Bluesky "Deutsche Politik" feed and the daily caps (fixtures inline, no network)."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from crawler.config import DAILY_COMMENT_CAPS, DAILY_COMMENT_TARGET, RUNS_PER_DAY, SOURCE_LIMITS, source_limits
from crawler.ids import comment_id
from crawler.models import ArticleRef
from crawler.sources import SOURCES
from crawler.sources.allmystery import Allmystery, parse_listing
from crawler.sources.allmystery import parse_thread_page as am_page
from crawler.sources.bluesky import POLITICS_FEED, Bluesky
from crawler.sources.br24 import BR24, KNOWN_IDS, parse_comments, query_ids_from_bundle, story_id
from crawler.sources.bundestag import (
    BundestagPetitionen,
    parse_answers,
    parse_forum_rss,
    parse_petition_list,
    parse_threads,
)
from crawler.sources.finanztip import Finanztip, parse_feed, parse_thread_page
from crawler.sources.goingelectric import GoingElectric, parse_german_date, parse_topic_page
from crawler.sources.goingelectric import parse_feed as ge_feed
from crawler.sources.kuketz import Kuketz, parse_posts
from crawler.sources.rbb24 import Rbb24, article_links, comment_config, parse_date
from crawler.sources.rbb24 import parse_threads as rbb_threads

# user names, handles and ids that appear in the fixtures and must never reach a comment text
NAMES = ("oachkatzl", "KlausJuergen", "Erge58", "Attair", "Africanus", "rainlove", "Uli74", "90 Prozent",
         "SandroMa", "Ingenpeiling", "Flow", "flow", "Nutzer6638448", "Adam Loose", "§§§", "schrieb",
         "geschrieben", "Zitat von", "did:plc:", "max.bsky.social", "Maranrune")  # fmt: skip


def _no_names(comments):
    assert comments
    for c in comments:
        for n in NAMES:
            assert n not in c.text, (n, c.text)
        assert not hasattr(c, "author") and not hasattr(c, "username")


class _Resp:
    def __init__(self, body):
        self.body = body

    @property
    def text(self):
        return self.body.decode() if isinstance(self.body, bytes) else self.body

    @property
    def content(self):
        return self.body if isinstance(self.body, bytes) else self.body.encode()

    def json(self):
        return json.loads(self.text)


class FakeHttp:
    """Maps full URLs (with query string) to bodies; records every request."""

    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def _url(self, url, params):
        return url + ("?" + urlencode(params) if params else "")

    def get(self, url, params=None, **kw):
        full = self._url(url, params)
        self.urls.append(full)
        body = self.pages.get(full)
        if body is None:
            raise RuntimeError(f"unexpected request {full}")
        return _Resp(body if isinstance(body, str | bytes) else json.dumps(body))

    def get_json(self, url, params=None, **kw):
        return self.get(url, params=params).json()


# ------------------------------------------------------------------ daily caps


def test_daily_caps_cover_every_source_and_stay_within_target():
    assert set(DAILY_COMMENT_CAPS) == set(SOURCES)
    assert sum(DAILY_COMMENT_CAPS.values()) <= DAILY_COMMENT_TARGET <= 11_000
    for sid, cap in DAILY_COMMENT_CAPS.items():
        assert SOURCE_LIMITS[sid]["max_comments_per_run"] == cap // RUNS_PER_DAY
    limits = source_limits([])
    assert limits["bluesky"]["max_comments_per_run"] == 1150  # 1,000 search + 150 feed per run
    assert limits["br24"] == {"max_comments_per_run": 300, "max_new_articles_per_run": 4, "strict": 1}
    for sid in ("rbb24", "allmystery", "finanztip", "goingelectric", "kuketz", "bundestag"):
        assert limits[sid]["max_comments_per_run"] <= 150


# ------------------------------------------------------------------ BR24

BR_STORY = {
    "id": "VYJBwtn",
    "commentCounts": {"totalPublished": 4},
    "comments": {
        "edges": [
            {"node": {"id": "c1", "author": {"id": "u1", "username": "oachkatzlschwoaf", "bio": "Hass ist dumm"},
                      "body": "<div>Arme Berliner Polizei. <blockquote>KlausJuergen: Zitat</blockquote></div>",
                      "createdAt": "2026-10-17T16:28:24.708Z", "status": "APPROVED", "deleted": False,
                      "actionCounts": {"reaction": {"total": 70}}, "parent": None,
                      "replies": {"edges": [{"node": {
                          "id": "c2", "author": {"username": "KlausJuergen"},
                          "parent": {"id": "c1", "author": {"username": "oachkatzlschwoaf"}},
                          "body": "<div>@oachkatzlschwoaf Tschüssi. Jüngeres kommt nach.</div>",
                          "createdAt": "2026-10-17T16:30:00.000Z", "status": "APPROVED",
                          "actionCounts": {"reaction": {"total": 3}}, "replies": {"edges": []}}}]}},
             "cursor": "2026-10-17T16:28:24.708Z"},
            {"node": {"id": "c3", "author": {"username": "x"}, "body": "<div>gelöscht</div>", "deleted": True,
                      "createdAt": "2026-10-17T16:00:00.000Z", "replies": {"edges": []}}},
        ],
        "pageInfo": {"endCursor": "2026-10-17T16:00:00.000Z", "hasNextPage": True},
    },
}  # fmt: skip
BR_PAGE2 = {"id": "VYJBwtn", "comments": {"edges": [
    {"node": {"id": "c4", "author": {"username": "Connaisseur"}, "body": "<div>Die Mitte verschwindet.</div>",
              "createdAt": "2026-10-17T15:00:00.000Z", "status": "APPROVED", "replies": {"edges": []}}}],
    "pageInfo": {"endCursor": None, "hasNextPage": False}}}  # fmt: skip
BR_URL = "https://www.br.de/nachrichten/bayern/einigung-von-linke-spd-und-gruenen-bei-sondierungen-in-berlin,VYJBwtn"


def _br_q(qid, variables):
    params = {"query": "", "id": qid, "variables": json.dumps(variables, separators=(",", ":"))}
    return "https://comments.br24.de/api/graphql?" + urlencode(params)


def _br_pages(bundle="e17437fa6c9c9a6068afb70790b0d44b", ids=KNOWN_IDS):
    first = {"storyID": "VYJBwtn", "storyURL": None, "commentsOrderBy": "CREATED_AT_DESC", "tag": None,
             "storyMode": None, "flattenReplies": False, "ratingFilter": None, "refreshStream": False}  # fmt: skip
    nxt = {"count": 50, "cursor": "2026-10-17T16:00:00.000Z", "orderBy": "CREATED_AT_DESC", "storyID": "VYJBwtn",
           "tag": None, "flattenReplies": False, "ratingFilter": None, "refreshStream": False}  # fmt: skip
    return {
        "https://comments.br24.de/embed/bootstrap": {"assets": {"js": [{"src": f"assets/js/stream.{bundle}.js"}]}},
        _br_q(ids["StreamQuery"], {"storyID": "VYJBwtn", "storyURL": None, "storyMode": None}):
            {"data": {"story": {"id": "VYJBwtn", "commentCounts": {"totalPublished": 4}}}},
        _br_q(ids["AllCommentsTabQuery"], first): {"data": {"story": BR_STORY}},
        _br_q(ids["AllCommentsTabContainerPaginationQuery"], nxt): {"data": {"story": BR_PAGE2}},
    }  # fmt: skip


def test_br24_story_ids_and_urls():
    src = BR24(http=None)
    assert story_id(BR_URL) == "VYJBwtn"
    assert src.canonical_url(BR_URL) == "https://www.br.de/nachrichten/bayern/beitrag,VYJBwtn"
    assert not src.accept_url("https://www.br.de/nachrichten/meldung/cdu-will-nicht%2C30081c6b5")
    assert src.accept_url(BR_URL) and src.topic == "politics" and src.country == "DE"


def test_br24_parse_comments_drops_authors_quotes_mentions():
    comments = parse_comments(BR_STORY)
    assert [(c.native_id, c.native_parent_id) for c in comments] == [("c1", None), ("c2", "c1")]
    assert comments[0].text == "Arme Berliner Polizei."
    assert comments[1].text == "Tschüssi. Jüngeres kommt nach."
    assert comments[0].upvotes == 70
    assert comments[0].published_at == datetime(2026, 10, 17, 16, 28, 24, 708000, tzinfo=UTC)
    _no_names(comments)


def test_br24_query_ids_from_bundle():
    js = ('x={params:{id:"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",metadata:{},name:"AllCommentsTabQuery",operationKind:"query"}};'
          'y={params:{id:"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",metadata:{},name:"CreateCommentMutation"}}')  # fmt: skip
    assert query_ids_from_bundle(js) == {"AllCommentsTabQuery": "a" * 32}


def test_br24_fetch_pages_and_unchanged():
    http = FakeHttp(_br_pages())
    src = BR24(http)
    res = src.fetch(ArticleRef(src.canonical_url(BR_URL), "br24"))
    assert [c.native_id for c in res.comments] == ["c2", "c1", "c4"] and res.reported_total == 4 and res.complete
    assert not any("stream." in u for u in http.urls)  # known bundle: no bundle download
    # unchanged count: only the count probe
    http.urls.clear()
    ref = ArticleRef(src.canonical_url(BR_URL), "br24", known=True, last_comment_crawl_at=datetime.now(UTC),
                     known_comment_count=4)  # fmt: skip
    assert src.fetch(ref).unchanged and len(http.urls) == 1


def test_br24_new_bundle_rediscovers_ids():
    ids = {"AllCommentsTabQuery": "1" * 32, "AllCommentsTabContainerPaginationQuery": "2" * 32, "StreamQuery": "3" * 32}
    pages = _br_pages(bundle="f" * 32, ids=ids)
    pages["https://comments.br24.de/assets/js/stream." + "f" * 32 + ".js"] = "".join(
        f'q={{params:{{id:"{v}",metadata:{{}},name:"{k}",operationKind:"query"}}}};' for k, v in ids.items()
    )
    src = BR24(FakeHttp(pages))
    assert src.query_ids() == ids
    assert len(src.fetch(ArticleRef(src.canonical_url(BR_URL), "br24")).comments) == 3


def test_br24_rank_new_prefers_most_commented():
    class CountHttp:
        def get_json(self, url, params=None, **kw):
            sid = json.loads(params["variables"])["storyID"]
            return {"data": {"story": {"commentCounts": {"totalPublished": {"AAAAA1": 3, "BBBBB2": 90}.get(sid, 0)}}}}

    src = BR24(CountHttp())
    src._ids = dict(KNOWN_IDS)
    now = datetime.now(UTC)
    refs = [ArticleRef(f"https://www.br.de/nachrichten/bayern/beitrag,{s}", "br24", published_at=now - timedelta(hours=i))
            for i, s in enumerate(("AAAAA1", "BBBBB2", "CCCCC3"))]  # fmt: skip
    ranked = src.rank_new(refs)
    assert [story_id(r.url) for r in ranked] == ["BBBBB2", "AAAAA1", "CCCCC3"]
    assert ranked[0].listed_comment_count == 90


# ------------------------------------------------------------------ rbb24

RBB_URL = "https://www.rbb24.de/panorama/beitrag/2026/10/anwerbeabkommen-tuerkei-deutschland.html"
RBB_HTML = """<html><head><meta name="keywords" content="Berlin, rbb24:tagesschau, Migration"/>
<script type="application/ld+json">{"@type":"NewsArticle","headline":"Aufbruch in die neue Heimat",
"datePublished":"2026-10-25T07:22:57.000+01:00"}</script></head><body>
<div data-analytics='{"commentcount":3}' data-comment-config='{"initialCountComments":3,"totalCountComments":2,
"totalCountCommentsAndAnswers":3,"commentsEnabled":true,"commentsVisible":true}'></div></body></html>"""
RBB_JSON = [
    {"id": "t1", "text": "Berlin? \r\nEs geht hier wohl um Westberlin.", "author": "Erge58",
     "date": "Oct 25, 2026, 11:59:49 PM", "isEditorial": False, "answers": [
        {"id": "a1", "text": "Was faseln Sie da?", "author": "Attair", "date": "Oct 26, 2026, 7:59:27 AM",
         "isEditorial": False, "answers": []},
        {"id": "a1", "text": "Was faseln Sie da?", "author": "Attair", "date": "Oct 26, 2026, 7:59:27 AM",
         "isEditorial": False, "answers": []},
        {"id": "e1", "text": "Liebe Community, bitte sachlich bleiben.", "author": "rbb|24",
         "date": "Oct 26, 2026, 8:00:00 AM", "isEditorial": True, "answers": []}]},
    {"id": "t2", "text": "Ich verneige mich vor ihrer Lebensleistung!", "author": "Altwestberlin",
     "date": "Oct 25, 2026, 9:38:16 PM", "isEditorial": False, "answers": []},
]  # fmt: skip


def test_rbb24_parsing():
    assert parse_date("Oct 25, 2026, 11:59:49 PM") == datetime(2026, 10, 25, 22, 59, 49, tzinfo=UTC)  # CET
    assert parse_date("Oct 24, 2026, 9:43:38 PM") == datetime(2026, 10, 24, 19, 43, 38, tzinfo=UTC)  # CEST
    assert parse_date("Oct 26, 2026, 12:05:00 AM") == datetime(2026, 10, 25, 23, 5, tzinfo=UTC)
    comments = rbb_threads(RBB_JSON)
    assert [(c.native_id, c.native_parent_id) for c in comments] == [("t1", None), ("a1", "t1"), ("t2", None)]
    assert comments[0].text == "Berlin?\nEs geht hier wohl um Westberlin."
    _no_names(comments)
    assert comment_config(RBB_HTML)["totalCountCommentsAndAnswers"] == 3
    links = article_links('<a href="/politik/beitrag/2026/10/x-y.html">a</a><a href="/politik/thema/x.html">b</a>'
                          '<a href="https://www.rbb24.de/sport/beitrag/2026/09/z.html#top">c</a>')  # fmt: skip
    assert links == ["https://www.rbb24.de/politik/beitrag/2026/10/x-y.html",
                     "https://www.rbb24.de/sport/beitrag/2026/09/z.html"]  # fmt: skip


def test_rbb24_fetch_enriches_and_skips_unchanged():
    jsn = RBB_URL.removesuffix(".html") + ".commentthreads.jsn?start=0&size=100"
    http = FakeHttp({RBB_URL: RBB_HTML, jsn: RBB_JSON})
    src = Rbb24(http)
    ref = ArticleRef(RBB_URL, "rbb24", topics=["Panorama"])
    res = src.fetch(ref)
    assert len(res.comments) == 3 and res.reported_total == 3 and res.complete
    assert res.article.title == "Aufbruch in die neue Heimat"
    assert res.article.published_at == datetime(2026, 10, 25, 6, 22, 57, tzinfo=UTC)
    assert res.article.topics == ["Panorama", "Berlin", "Migration"]
    http.urls.clear()
    known = ArticleRef(RBB_URL, "rbb24", known=True, last_comment_crawl_at=datetime.now(UTC), known_comment_count=3)
    assert src.fetch(known).unchanged and http.urls == [RBB_URL]
    # no comment section -> no JSON request
    http = FakeHttp({RBB_URL: "<html><body>Kein Kommentarbereich</body></html>"})
    assert Rbb24(http).fetch(ArticleRef(RBB_URL, "rbb24")).comments == [] and len(http.urls) == 1


def test_rbb24_discover_takes_current_month_only():
    page = '<a href="/politik/beitrag/2026/10/neu.html">n</a><a href="/politik/beitrag/2025/11/alt.html">a</a>'
    http = FakeHttp({f"https://www.rbb24.de{p}": page for p in ("/", "/politik/", "/panorama/", "/wirtschaft/",
                                                               "/kultur/", "/sport/")})  # fmt: skip
    until = datetime(2026, 10, 26, 12, tzinfo=UTC)
    refs = Rbb24(http).discover(until - timedelta(hours=12), until)
    assert [r.url for r in refs] == ["https://www.rbb24.de/politik/beitrag/2026/10/neu.html"]
    assert refs[0].topics == ["Politik"]


# ------------------------------------------------------------------ Allmystery

AM_LISTING = """<div class="thread"><div class="thread-container">
<a href="/themen/pr98768" class="thread-title toFirstUnreadPost thread-overlay" data-thread="98768" data-last-ts="1793016735000"></a>
<a href="/themen/pr98768" class="thread-title toFirstUnreadPost" data-thread="98768" data-last-ts="1793016735000">AfD</a>
<div class="thread-details short-details">101.627 Beiträge von <a href="/mitglieder/Africanus">Africanus</a></div></div>
<div class="last-post"><a href="/themen/pr98768-5157#id38141401">vor 54 Minuten</a></div></div>
<div class="thread"><div class="thread-container">
<a href="/themen/pr555" class="thread-title" data-thread="555" data-last-ts="1786191496000">Altes Thema</a></div>
<div class="last-post"><a href="/themen/pr555#id1">alt</a></div></div>"""
AM_PAGE = """<html><body><h1>AfD</h1><div class="posts">
<div class="post" data-id="38134755" data-username="Africanus" id="id38134755">
<div class="finfo"><a href="/themen/pr98768-5157#id38134755" class="nu">Africanus</a>
<a class="avatar" href="/mitglieder/Africanus"><img alt="Profil von Africanus"></a></div>
<div class="fbeitrag"><span class="date" id="date_38134755" title="26.10.2026 08:15:05">heute um 08:15</span><br>
<div class="post-content"><a class="atu" href="#">@rainlove</a> <blockquote data-parent-username="rainlove" data-parent-id="38132719"><cite><a class="bnu" href="#">rainlove schrieb:</a></cite>Wird es der Partei schaden?</blockquote>Die neueste Umfrage deutet an, dass es schadet.<br><br>Quelle: <a href="https://www.wahlrecht.de/">wahlrecht.de</a></div></div></div>
<div class="post" data-id="38138835" data-username="Dasdeeeeniz" id="id38138835">
<div class="fbeitrag"><span class="date" title="25.10.2026 19:52:00">gestern um 19:52</span>
<div class="post-content">Zurück zur Apothekerin aus Bielefeld.</div></div></div>
</div></body></html>"""


def test_allmystery_parsing():
    threads = parse_listing(AM_LISTING)
    assert [(t.thread_id, t.title, t.last_page) for t in threads] == [("98768", "AfD", 5157), ("555", "Altes Thema", 0)]
    assert threads[0].last_post_at == datetime.fromtimestamp(1793016735, UTC)
    title, posts = am_page(AM_PAGE)
    assert title == "AfD"
    assert [(c.native_id, c.native_parent_id) for c in posts] == [("38134755", "38132719"), ("38138835", None)]
    assert posts[0].text == "Die neueste Umfrage deutet an, dass es schadet.\n\nQuelle: wahlrecht.de"
    assert posts[0].published_at == datetime(2026, 10, 26, 7, 15, 5, tzinfo=UTC)
    _no_names(posts)


def test_allmystery_fetch_only_active_fixed_threads_and_new_posts():
    http = FakeHttp({"https://www.allmystery.de/themen/pr": AM_LISTING,
                     "https://www.allmystery.de/themen/pr98768-5157": AM_PAGE})  # fmt: skip
    until = datetime(2026, 10, 26, 12, tzinfo=UTC)
    items = Allmystery(http).fetch_since(datetime(2026, 10, 26, 0, tzinfo=UTC), until, 100)
    # only the post of 26 Oct; the older one ends paging (no request for page 5156); inactive thread skipped
    assert [i.comment.native_id for i in items] == ["38134755"]
    assert http.urls == ["https://www.allmystery.de/themen/pr", "https://www.allmystery.de/themen/pr98768-5157"]
    art = items[0].article
    assert art.url == "https://www.allmystery.de/themen/pr98768" and art.title == "AfD" and "AfD" in art.topics


# ------------------------------------------------------------------ Finanztip

FT_FEED = b"""<?xml version="1.0" encoding="UTF-8"?><rss xmlns:slash="http://purl.org/rss/1.0/modules/slash/"
 xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0"><channel>
<item><title>Fonds\xc3\xbcbertrag von Gro\xc3\x9feltern</title>
<link>https://www.finanztip.de/community/forum/thema/47819-fonds%C3%BCbertrag-von-gro%C3%9Feltern/</link>
<slash:comments>2</slash:comments><pubDate>Mon, 26 Oct 2026 09:07:10 +0000</pubDate><dc:creator>Uli74</dc:creator></item>
<item><title>Alt</title><link>https://www.finanztip.de/community/forum/thema/100-alt/</link>
<pubDate>Mon, 19 Oct 2026 09:07:10 +0000</pubDate></item>
</channel></rss>"""
FT_THREAD = """<html><body><nav class="breadcrumbs"><a href="https://www.finanztip.de/community/forum/">Forum</a>
<a href="https://www.finanztip.de/community/forum/board/25-steuern-sparen/">Steuern sparen</a></nav>
<h1 class="contentTitle">Fondsübertrag von Großeltern</h1>
<li id="post653501"><article class="wbbPost message" data-post-id="653501" data-is-deleted="0" data-user-id="32540">
<aside class="messageSidebar member" itemprop="author"><a class="username userLink"><span itemprop="name">Uli74</span></a></aside>
<div class="messageContent"><woltlab-core-date-time date="2026-10-26T07:08:10+00:00">heute</woltlab-core-date-time>
<div class="messageText" itemprop="text"><p>Ich hätte eine Frage zur Fondsübertragung.</p></div>
<div class="messageSignature">Gruß Uli74</div></div></article></li>
<li id="post653549"><article class="wbbPost message" data-post-id="653549" data-is-deleted="0">
<woltlab-core-date-time date="2026-10-26T08:00:00+00:00">heute</woltlab-core-date-time>
<div class="messageText"><blockquote class="quoteBox" cite="https://www.finanztip.de/community/forum/thema/47819-x/?postID=653501#post653501">
<div class="quoteBoxTitle"><a href="#">Zitat von Uli74</a></div><div class="quoteBoxContent"><p>Frage</p></div></blockquote>
<p><a class="userMention userLink" href="#">@Uli74</a> Auf die Familienversicherung achten.</p></div></article></li>
<li id="post653001"><article class="wbbPost message" data-post-id="653001" data-is-deleted="0">
<woltlab-core-date-time date="2026-10-24T08:00:00+00:00">alt</woltlab-core-date-time>
<div class="messageText"><p>Alter Beitrag.</p></div></article></li>
</body></html>"""


def test_finanztip_parsing():
    feed = parse_feed(FT_FEED)
    assert [(t.thread_id, t.replies) for t in feed] == [("47819", 2), ("100", None)]
    assert feed[0].title == "Fondsübertrag von Großeltern"
    title, boards, posts = parse_thread_page(FT_THREAD)
    assert title == "Fondsübertrag von Großeltern" and boards == ["Steuern sparen"]
    assert [(c.native_id, c.native_parent_id) for c in posts] == [
        ("653501", None), ("653549", "653501"), ("653001", None)]  # fmt: skip
    assert posts[1].text == "Auf die Familienversicherung achten."
    _no_names(posts)


def test_finanztip_fetch_board_feed_first_and_window():
    thread = "https://www.finanztip.de/community/forum/thema/47819-fonds%C3%BCbertrag-von-gro%C3%9Feltern/"
    http = FakeHttp({"https://www.finanztip.de/community/forum/thread-list-rss-feed/14/": FT_FEED,
                     "https://www.finanztip.de/community/forum/thread-list-rss-feed/": FT_FEED, thread: FT_THREAD})  # fmt: skip
    until = datetime(2026, 10, 26, 12, tzinfo=UTC)
    items = Finanztip(http).fetch_since(until - timedelta(hours=12), until, 50)
    assert [i.comment.native_id for i in items] == ["653549", "653501"]
    assert http.urls[0].endswith("/thread-list-rss-feed/14/") and http.urls.count(thread) == 1
    assert items[0].article.url == "https://www.finanztip.de/community/forum/thema/47819/"
    assert Finanztip.overlap == timedelta(hours=12)


# ------------------------------------------------------------------ GoingElectric

GE_FEED = """<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom" xml:lang="de">
<entry><author><name><![CDATA[Franky]]></name></author><published>2026-10-26T14:07:17+01:00</published>
<id>https://www.goingelectric.de/forum/viewtopic.php?p=2453007#p2453007</id>
<link href="https://www.goingelectric.de/forum/viewtopic.php?p=2453007#p2453007"/>
<title type="html"><![CDATA[Öffentliche Lade-Infrastruktur • Re: Warum klappt in Frankreich, was in Deutschland nicht klappt?]]></title>
<category term="Öffentliche Lade-Infrastruktur" label="Öffentliche Lade-Infrastruktur"/>
<content type="html"><![CDATA[Text]]></content></entry></feed>""".encode()
GE_PAGE = """<html><head><link rel="canonical" href="https://www.goingelectric.de/forum/viewtopic.php?t=102137&start=30"></head>
<body><h1 class="topic-title"><a href="./viewtopic.php?t=102137&amp;start=30">Warum klappt in Frankreich, was in Deutschland nicht klappt?</a></h1>
<div class="post bg1" id="p2452914"><div class="post-header"><span class="avatar-initial" data-username="Ingenpeiling">Ingenpeiling</span>
<a href="./memberlist.php?mode=viewprofile&amp;u=61370&amp;sid=abc" class="post-username">Ingenpeiling</a>
<div class="post-timestamp post-timestamp-header"><span class="post-badge-neu">Neu</span> Mo 26. Okt 2026, 10:01</div></div>
<div id="post_content2452914"><div class="post-content"><div class="content"><blockquote cite="./viewtopic.php?p=2452908#p2452908"><div><cite><a href="./memberlist.php?u=82800">SandroMa</a> hat geschrieben: <span>So 25. Okt 2026, 21:35</span></cite>Das liegt am Atomstrom.</div></blockquote>

Naja - liegt wohl eher am fehlenden Willen zur Tarifregulierung?</div>
<div class="post-signature">Kia EV3 Exclusive LR</div></div></div></div>
<div class="post bg2" id="p2453007"><div class="post-timestamp">Mo 26. Okt 2026, 14:07</div>
<div class="post-content"><div class="content">Wie sollte das Gerichtsverfahren das verhindert haben?</div></div></div>
</body></html>"""


def test_goingelectric_parsing():
    assert parse_german_date("Mo 26. Okt 2026, 14:07") == datetime(2026, 10, 26, 13, 7, tzinfo=UTC)
    assert parse_german_date("Mi 12. Mär 2025, 08:00") == datetime(2025, 3, 12, 7, 0, tzinfo=UTC)
    feed = ge_feed(GE_FEED)
    assert feed[0].post_id == "2453007" and feed[0].forum == "Öffentliche Lade-Infrastruktur"
    assert feed[0].topic_title == "Warum klappt in Frankreich, was in Deutschland nicht klappt?"
    tid, start, title, posts = parse_topic_page(GE_PAGE)
    assert (tid, start) == ("102137", 30) and title.startswith("Warum klappt")
    assert [(c.native_id, c.native_parent_id) for c in posts] == [("2452914", "2452908"), ("2453007", None)]
    assert posts[0].text == "Naja - liegt wohl eher am fehlenden Willen zur Tarifregulierung?"
    _no_names(posts)


def test_goingelectric_fetch_feed_topics_and_revisits():
    http = FakeHttp({
        "https://www.goingelectric.de/forum/feed": GE_FEED,
        "https://www.goingelectric.de/forum/viewtopic.php?p=2453007": GE_PAGE,
        "https://www.goingelectric.de/forum/viewtopic.php?t=102137&start=20": GE_PAGE.replace("p2452914", "p2452800")
        .replace("p2453007", "p2452801"),
        "https://www.goingelectric.de/forum/viewtopic.php?t=777&start=99999": "<html><head>"
        '<link rel="canonical" href="https://www.goingelectric.de/forum/viewtopic.php?t=777"></head><body>'
        '<div class="post" id="p1"><div class="post-timestamp">Mo 26. Okt 2026, 09:00</div>'
        '<div class="content">Revisit-Beitrag.</div></div></body></html>',
    })  # fmt: skip
    src = GoingElectric(http)
    src.revisit_topics = [("777", "Altes Thema")]
    until = datetime(2026, 10, 26, 14, tzinfo=UTC)
    items = src.fetch_since(until - timedelta(hours=12), until, 50)
    ids = [i.comment.native_id for i in items]
    assert set(ids) == {"2452914", "2453007", "2452800", "2452801", "1"}  # page 3 was all new -> page 2 read too
    assert not any("sid=" in u for u in http.urls)
    arts = {i.article.url for i in items}
    assert arts == {"https://www.goingelectric.de/forum/viewtopic.php?t=102137",
                    "https://www.goingelectric.de/forum/viewtopic.php?t=777"}  # fmt: skip


# ------------------------------------------------------------------ Kuketz-Forum

KZ_POSTS = {"latest_posts": [
    {"id": 102820, "topic_id": 17753, "topic_title": "Wer Digitalisierung ablehnt wird Organspender?",
     "topic_slug": "x", "post_number": 9, "reply_to_post_number": 7, "post_type": 1, "hidden": False,
     "user_deleted": False, "deleted_at": None, "created_at": "2026-10-26T13:04:40.565Z",
     "username": "flow", "name": "Flow", "display_username": "Flow", "avatar_template": "/user_avatar/flow/{size}.png",
     "raw": '[quote="Flow, post:7, topic:17753"]\nBesser man redet\n[/quote]\n\nSoweit stimme ich zu.',
     "cooked": '<aside class="quote no-group" data-username="Flow" data-post="7"><div class="title">Flow:</div>'
               '<blockquote><p>Besser man redet</p></blockquote></aside><p>Soweit stimme ich zu, <a class="mention" href="/u/flow">@Flow</a>.</p>'},
    {"id": 102800, "topic_id": 17753, "topic_title": "Wer Digitalisierung ablehnt wird Organspender?",
     "post_number": 7, "post_type": 1, "created_at": "2026-10-26T10:00:00.000Z", "username": "flow",
     "cooked": "<p>Besser man geht aufeinander zu.</p>"},
    {"id": 102799, "topic_id": 17753, "post_number": 6, "post_type": 3, "created_at": "2026-10-26T09:00:00.000Z",
     "cooked": "<p>closed</p>"},
]}  # fmt: skip


def test_kuketz_parsing_and_fetch():
    pairs = parse_posts(KZ_POSTS["latest_posts"])
    assert [(c.native_id, c.native_parent_id) for _, c in pairs] == [("102820", "102800"), ("102800", None)]
    assert pairs[0][1].text == "Soweit stimme ich zu, ."
    assert pairs[0][0].url == "https://www.kuketz-forum.de/t/17753"
    _no_names([c for _, c in pairs])
    http = FakeHttp({"https://www.kuketz-forum.de/posts.json": KZ_POSTS})
    until = datetime(2026, 10, 26, 14, tzinfo=UTC)
    items = Kuketz(http).fetch_since(datetime(2026, 10, 26, 9, 30, tzinfo=UTC), until, 50)
    assert len(items) == 2 and len(http.urls) == 1  # oldest post older than the window: no further page
    assert items[0].article.published_at == datetime(2026, 10, 26, 10, tzinfo=UTC)


# ------------------------------------------------------------------ Bundestag petitions

BT_LIST = """<table><tbody><tr><td data-label="Titel"><p><strong>Mietrecht</strong> <br /><b>
<a class="to-petdetails" href="/petitionen/_2026/_06/_08/Petition_202289.nc.html" title="Einheitliches Widerspruchsverfahren">x</a></b></p></td>
<td data-label="Id-Nr."><p>202289</p></td><td data-label="Forum"><p>52 Beitr&auml;ge</p></td></tr>
<tr><td data-label="Titel"><p><strong>Rente</strong><a class="to-petdetails" href="/petitionen/_2026/_07/_01/Petition_203000.nc.html" title="Ohne Forum">y</a></p></td>
<td data-label="Forum"><p>0 Beitr&auml;ge</p></td></tr></tbody></table>"""
BT_THREADS = """<div class="discussion-branch module-content-wrap discussion-868214"><div class="row"><div class="col-md-8">
<p class="">Nutzer18343<span> | </span><span class="date">26.10.2026</span><span> - </span><span class="time">09:17 </span></p>
<div><h3 id="h3_868214"><a href="#">Verstehe ich nicht ...</a></h3></div></div>
<div class="col-md-4"><p class="author">Das Anthropische Prinzip <span> | </span>
<a class="text letzter-beitrag" href="#">26.10.2026 - 11:00 </a></p></div></div></div>
<div class="discussion-branch discussion-868184"><div class="col-md-8"><p>Petent<span> | </span><span class="date">14.09.2026</span>
<span class="time">08:32 </span></p><h3 id="h3_868184"><a href="#">Mietrecht</a></h3></div>
<div class="col-md-4"><p class="author">Maranrune <span> | </span><a class="text letzter-beitrag" href="#">12.10.2026 - 17:58 </a></p></div></div>"""
BT_ANSWERS = """<div class="col-md-12 antwortb" id="a_870971"><p class="author">Adam Loose <span> | </span>
<span class="date">26.10.2026</span><span> - </span><span class="time">11:00 </span></p>
<div class="beitragtext-870971"><p>@ Nutzer6638448 | gestern - 22:04<br/><blockquote><strong>Zitat: </strong>von §§§869255§§§ <br/>Nein, bitte nicht.</blockquote><br/>Und wieder soll ein Gesetz einen Einzelfall lösen.</p></div>
<div class="discussion-branch--comment-feedback"><button class="btn" data-href="/petitionen/_2026/_06/_08/Petition_202289/forum/Beitrag_868214/Beitrag_869255/Beitrag_870971.newbeitrag.$$$.antwort.870971.html">antworten</button></div></div>
<div class="col-md-12 antwortb" id="a_869166"><p class="author">Adam Loose <span> | </span>
<span class="date">26.10.2026</span><span> - </span><span class="time">10:23 </span> (Zuletzt geändert am 26.10.2026 - 12:41 von Admin)</p>
<div class="beitragtext-869166"><p><span>DOPPELPOST <br/>Bitte vermeiden Sie das Einstellen gleichlautender Beiträge.</span></p></div></div>
<div class="col-md-12 antwortb" id="a_869000"><p class="author">Maranrune | <span class="date">20.10.2026</span> - <span class="time">10:00</span></p>
<div class="beitragtext-869000"><p>Alter Beitrag.</p></div>
<button data-href="/petitionen/_2026/_06/_08/Petition_202289/forum/Beitrag_868214/Beitrag_869000.newbeitrag.html"></button></div>"""
BT_RSS = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>Neuer Beitrag: Verstehe ich nicht ...</title>
<link>https://epetitionen.bundestag.de/content/petitionen/_2026/_06/_08/Petition_202289/forum/Beitrag_868214.$$$.einsprung.868214.html</link>
<description><![CDATA[Warum braucht es dazu ein Gesetz?]]></description></item></channel></rss>"""


def test_bundestag_parsing():
    pets = parse_petition_list(BT_LIST)
    assert [(p.petition_id, p.forum_posts) for p in pets] == [("202289", 52), ("203000", 0)]
    assert pets[0].title == "Mietrecht: Einheitliches Widerspruchsverfahren" and pets[0].path == "_2026/_06/_08"
    threads = parse_threads(BT_THREADS)
    assert [(t.thread_id, t.opened_at, t.last_post_at) for t in threads] == [
        ("868214", datetime(2026, 10, 26, 8, 17, tzinfo=UTC), datetime(2026, 10, 26, 10, 0, tzinfo=UTC)),
        ("868184", datetime(2026, 9, 14, 6, 32, tzinfo=UTC), datetime(2026, 10, 12, 15, 58, tzinfo=UTC)),
    ]  # fmt: skip
    answers = parse_answers(BT_ANSWERS, "868214")
    assert [(c.native_id, c.native_parent_id) for c in answers] == [("870971", "869255"), ("869000", "868214")]
    assert answers[0].text == "Und wieder soll ein Gesetz einen Einzelfall lösen."
    assert answers[0].published_at == datetime(2026, 10, 26, 10, 0, tzinfo=UTC)
    _no_names(answers)
    assert parse_forum_rss(BT_RSS) == {"868214": "Warum braucht es dazu ein Gesetz?"}


def test_bundestag_fetch_new_threads_and_answers():
    base = "https://epetitionen.bundestag.de/petitionen/_2026/_06/_08/Petition_202289"
    pages = {
        "https://epetitionen.bundestag.de/epet/petuebersicht/mz.nc.content.teaser-petitionen-table.$$$.status.2.page.0.batchsize.10.html": BT_LIST,
        "https://epetitionen.bundestag.de/epet/petuebersicht/mz.nc.content.teaser-petitionen-table.$$$.status.2.page.1.batchsize.10.html": "<table></table>",
        f"{base}/forum.kommentare.table.$$$.page.0.html": BT_THREADS,
        f"{base}/forum/Beitrag_868214.alleAntwortenNachDatum.$$$.page.1.html": BT_ANSWERS,
        "https://epetitionen.bundestag.de/content/petitionen/_2026/_06/_08/Petition_202289/forum.rss.xml": BT_RSS,
    }
    http = FakeHttp(pages)
    until = datetime(2026, 10, 26, 12, tzinfo=UTC)
    items = BundestagPetitionen(http).fetch_since(until - timedelta(hours=12), until, 50)
    assert [(i.comment.native_id, i.comment.text) for i in items] == [
        ("870971", "Und wieder soll ein Gesetz einen Einzelfall lösen."),
        ("868214", "Verstehe ich nicht ...\n\nWarum braucht es dazu ein Gesetz?"),
    ]
    assert not any("868184" in u for u in http.urls)  # thread without new posts is not read
    art = items[0].article
    assert art.title == "Mietrecht: Einheitliches Widerspruchsverfahren"
    assert art.url == base + ".nc.html" and art.published_at == datetime(2026, 6, 7, 22, tzinfo=UTC)
    _no_names([i.comment for i in items])


# ------------------------------------------------------------------ Bluesky "Deutsche Politik" feed


def _feed_post(i, minute, labels=(), langs=("de",)):
    return {"post": {"uri": f"at://did:plc:abc/app.bsky.feed.post/{i}", "indexedAt": f"2026-10-26T11:{minute:02d}:00.000Z",
                     "likeCount": 2, "author": {"did": "did:plc:abc", "handle": "max.bsky.social",
                                                "labels": [{"val": v} for v in labels]},
                     "record": {"text": f"Merz und die Rente {i}", "langs": list(langs)}}}  # fmt: skip


class _FeedHttp:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params=None, **kw):
        self.calls.append((url, dict(params or {})))
        if "getFeed" in url:
            assert params["feed"] == POLITICS_FEED
            if params.get("cursor"):
                return {"feed": [_feed_post(9, 1)]}
            return {"cursor": "c1", "feed": [_feed_post(1, 50), _feed_post(2, 40, labels=["!no-unauthenticated"]),
                                             _feed_post(3, 30, langs=("en",)), _feed_post(4, 20)]}  # fmt: skip
        return {"posts": []}


def test_bluesky_politics_feed_same_ids_optout_and_reserved_share():
    http = _FeedHttp()
    until = datetime(2026, 10, 26, 12, tzinfo=UTC)
    items = Bluesky(http).fetch_since(datetime(2026, 10, 26, 11, 10, tzinfo=UTC), until, max_items=1150)
    feed = [i for i in items if "deutsche-politik" in i.article.url]
    assert [i.comment.native_id.rsplit("/", 1)[1] for i in feed] == ["1", "4"]  # opted out and English dropped
    assert feed[0].comment.id == comment_id("bluesky", "at://did:plc:abc/app.bsky.feed.post/1")
    assert all("did:plc" not in i.article.url and "did:plc" not in i.article.title for i in feed)
    assert sum("getFeed" in u for u, _ in http.calls) == 2  # second page reaches back before the window
    _no_names([i.comment for i in feed])
    # the searches still get the rest of the cap
    searches = [p for u, p in http.calls if "searchPosts" in u]
    assert searches and all(p["lang"] == "de" for p in searches)
