from datetime import UTC, datetime, timedelta

from bs4 import BeautifulSoup

from crawler.models import ArticleRef
from crawler.sources.bluesky import Bluesky, parse_posts, post_text
from crawler.sources.freitag import Freitag, parse_items
from crawler.sources.mastodon import parse_statuses, status_text
from crawler.sources.tagesschau import Tagesschau, parse_comments, parse_listing, parse_total
from crawler.sources.tichyseinblick import parse_wp_comments

NAMES = ("beispielnutzer", "andere_person", "Max Muster", "maxm", "did:plc:abc", "max.bsky.social")


def _no_names(comments):
    for c in comments:
        for n in NAMES:
            assert n not in c.text


# ------------------------------------------------------------------ tagesschau


def test_tagesschau_listing(fixture_path):
    refs = parse_listing(BeautifulSoup(fixture_path("tagesschau_listing.html").read_text(), "lxml"))
    assert [r.url for r in refs] == [
        "https://meta.tagesschau.de/id/176269/tote-nach-angriff-auf-flughafen",
        "https://meta.tagesschau.de/id/176268/eu-parlament-kompromiss",
    ]
    assert refs[0].listed_comment_count == 81 and refs[1].listed_comment_count == 1093
    assert refs[0].published_at == datetime(2026, 10, 22, 7, 50, 35, tzinfo=UTC)
    assert refs[0].title == "Tote nach Angriff auf Flughafen"


def test_tagesschau_comments(fixture_path):
    soup = BeautifulSoup(fixture_path("tagesschau_article.html").read_text(), "lxml")
    comments = parse_comments(soup)
    assert [c.native_id for c in comments] == ["6108714", "6108717"]
    assert comments[0].text == "Angriffe auf zivile Ziele sollten geahndet werden.\n\nZweiter Absatz."
    assert comments[1].native_parent_id == "6108714"
    assert comments[1].text == "Und wie stellen Sie sich das vor?"  # "@name" parent link removed
    assert comments[0].published_at == datetime(2026, 10, 22, 10, 6, 59, tzinfo=UTC)
    assert parse_total(soup) == 81
    _no_names(comments)


def test_tagesschau_unchanged_listing_count_needs_no_request():
    ref = ArticleRef("https://meta.tagesschau.de/id/1/x", "tagesschau", known=True,
                     last_comment_crawl_at=datetime.now(UTC), known_comment_count=5, listed_comment_count=5)  # fmt: skip
    result = Tagesschau(http=None).fetch(ref)  # http=None: any request would fail
    assert result.unchanged


# ------------------------------------------------------------------ Freitag


def test_freitag_items():
    items = [
        {"comment_id": "1", "in_reply_to": None, "creation_date": "2026-10-19T11:48:43+00:00",
         "author_name": "Max Muster", "author_username": "maxm", "text": {"data": "<p>Was ist aus der SPD geworden?</p>"}},
        {"comment_id": "2", "in_reply_to": "1", "creation_date": "2026-10-19T12:00:00+00:00",
         "author_name": "Max Muster", "text": {"data": "<p>@maxm Gute Frage.</p>"}},
    ]  # fmt: skip
    comments = parse_items(items)
    assert [c.text for c in comments] == ["Was ist aus der SPD geworden?", "Gute Frage."]
    assert comments[1].native_parent_id == "1"
    _no_names(comments)


def test_freitag_canonical():
    f = Freitag(http=None)
    assert f.canonical_url("https://www.freitag.de/autoren/der-freitag/ein-artikel") is not None
    assert f.canonical_url("https://www.freitag.de/politik") is None


# ------------------------------------------------------------------ Tichys Einblick


def test_tichy_wp_comments():
    rows = [
        {"id": 3615789, "post": 1324598, "parent": 0, "date_gmt": "2026-10-22T10:55:38",
         "content": {"rendered": "<p><span>&#8222;Zitat&#8220;???</span></p>\n<p>Das glaube, wer will.</p>"}},
        {"id": 3615790, "post": 1324598, "parent": 3615789, "date_gmt": "2026-10-22T11:00:00",
         "content": {"rendered": "<p>Antwort</p>"}},
    ]  # fmt: skip
    pairs = parse_wp_comments(rows)
    assert [p for p, _ in pairs] == [1324598, 1324598]
    first, second = (c for _, c in pairs)
    assert first.text == "„Zitat“???\n\nDas glaube, wer will."
    assert first.published_at == datetime(2026, 10, 22, 10, 55, 38, tzinfo=UTC)
    assert second.native_parent_id == "3615789"


# ------------------------------------------------------------------ Mastodon


def _status(i, **kw):
    s = {
        "id": str(i), "created_at": "2026-10-22T11:37:11.792Z", "visibility": "public", "language": "de",
        "reblog": None, "in_reply_to_id": None, "favourites_count": 2,
        "content": '<p><span class="h-card"><a class="u-url mention" href="https://x/@maxm">@<span>maxm</span></a></span> '
                   'Saubere Luft für alle! <a class="mention hashtag" href="https://x/tags/klima">#<span>Klima</span></a></p>',
        "account": {"id": "9", "acct": "maxm", "display_name": "Max Muster", "bot": False, "noindex": False,
                    "indexable": True},
    }  # fmt: skip
    s.update(kw)
    return s


def test_mastodon_filters_and_strips_mentions():
    statuses = [
        _status(1),
        _status(2, language="en"),
        _status(3, visibility="unlisted"),
        _status(4, account={"bot": True}),
        _status(5, account={"noindex": True, "indexable": True}),
        _status(6, account={"noindex": False, "indexable": False}),
        _status(7, in_reply_to_id="1"),
    ]
    comments = parse_statuses(statuses, "norden.social")
    assert [c.native_id for c in comments] == ["norden.social:1", "norden.social:7"]
    assert comments[0].text == "Saubere Luft für alle! #Klima"
    assert comments[1].native_parent_id == "norden.social:1"
    _no_names(comments)


def test_mastodon_status_text_plain():
    assert status_text("<p>Zeile<br>zwei</p>") == "Zeile\nzwei"


# ------------------------------------------------------------------ Bluesky


def test_bluesky_mentions_and_opt_out():
    text = "@max.bsky.social Merz hat recht"
    start = 0
    end = len(b"@max.bsky.social")
    post = {
        "uri": "at://did:plc:abc/app.bsky.feed.post/1",
        "indexedAt": "2026-10-22T11:27:49.575Z",
        "likeCount": 3,
        "author": {"did": "did:plc:abc", "handle": "max.bsky.social", "displayName": "Max Muster", "labels": []},
        "record": {"text": text, "createdAt": "2020-01-01T00:00:00Z", "facets": [
            {"index": {"byteStart": start, "byteEnd": end},
             "features": [{"$type": "app.bsky.richtext.facet#mention", "did": "did:plc:abc"}]}]},
    }  # fmt: skip
    hidden = dict(post, uri="at://x/2", author={"labels": [{"val": "!no-unauthenticated"}]})
    comments = parse_posts([post, hidden])
    assert len(comments) == 1
    assert comments[0].text == "Merz hat recht"
    assert comments[0].published_at == datetime(2026, 10, 22, 11, 27, 49, 575000, tzinfo=UTC)  # indexedAt
    assert post_text({"text": "ohne Facetten"}) == "ohne Facetten"
    _no_names(comments)


class _Http:
    def __init__(self):
        self.calls = []

    def get_json(self, url, params=None, **kw):
        self.calls.append(params)
        return {"posts": []}


def test_bluesky_samples_hourly_windows():
    http = _Http()
    until = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    Bluesky(http).fetch_since(until - timedelta(hours=12), until, max_items=3000)
    assert len(http.calls) == 12
    assert http.calls[0]["until"] == "2026-10-22T12:00:00Z" and http.calls[0]["since"] == "2026-10-22T11:00:00Z"
    assert all(c["lang"] == "de" for c in http.calls)
    http = _Http()
    Bluesky(http).fetch_since(until - timedelta(hours=12), until, max_items=300)
    assert len(http.calls) == 3


def test_day_article_groups_posts_by_berlin_day():
    b = Bluesky(http=None)
    late = b.day_article(
        "https://bsky.app/search?q=die&lang=de", "Bluesky", datetime(2026, 10, 21, 22, 30, tzinfo=UTC), []
    )
    assert late.url.endswith("&day=2026-10-22")
    assert late.published_at == datetime(2026, 10, 21, 22, 0, tzinfo=UTC)


# ------------------------------------------------------------------ Cicero, Achgut, RSS


def test_cicero_comments(fixture_path):
    from crawler.sources.cicero import parse_comments as cicero_comments

    comments = cicero_comments(BeautifulSoup(fixture_path("cicero_article.html").read_text(), "lxml"))
    assert [c.native_id for c in comments] == ["496299", "496309"]
    assert (
        comments[0].text
        == "Ganz nebenbei: Haben Doppelstaatler das Wahlrecht zum Regierenden Bürgermeister?\n\nDas sollte man wissen."
    )
    assert comments[0].published_at == datetime(2026, 10, 20, 12, 3, 59, tzinfo=UTC)
    assert comments[1].native_parent_id == "496299"
    assert all("Muster" not in c.text and "Erika" not in c.text for c in comments)


def test_achgut_comments_and_day_resolution(fixture_path):
    from crawler.sources.achgut import Achgut
    from crawler.sources.achgut import parse_comments as achgut_comments

    comments = achgut_comments(BeautifulSoup(fixture_path("achgut_article.html").read_text(), "lxml"))
    assert [c.native_id for c in comments] == ["1284690", "1284700"]
    assert comments[0].text == "Geschichte ist nicht jedermanns Sache."  # @mention stripped
    assert comments[1].native_parent_id == "1284690"
    assert comments[0].published_at == datetime(2026, 10, 21, 22, 0, tzinfo=UTC)  # midnight Berlin

    class Resp:
        text = fixture_path("achgut_article.html").read_text()

    class Http:
        def get(self, url, **kw):
            return Resp()

    result = Achgut(Http()).fetch(ArticleRef("https://www.achgut.com/artikel/x", "achgut"))
    assert result.day_resolution and len(result.comments) == 2


def test_day_resolution_revisit_checks_by_reading():
    from crawler.models import Comment, FetchResult
    from crawler.pipeline import comments_to_store

    crawl = datetime(2026, 10, 22, 16, 0, tzinfo=UTC)
    ref = ArticleRef("u", "achgut", known=True, last_comment_crawl_at=crawl, known_comment_count=1)
    today = Comment("achgut", "1", "a", datetime(2026, 10, 21, 22, 0, tzinfo=UTC))  # 22 Oct, Berlin
    older = Comment("achgut", "2", "b", datetime(2026, 10, 20, 22, 0, tzinfo=UTC))
    write, check = comments_to_store(FetchResult(ref, [today, older], 2, day_resolution=True))
    assert write == [] and check == [today]


def test_parse_rss():
    from crawler.discovery import parse_rss

    xml = b"""<?xml version="1.0"?><rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
    <item><title>A &amp; B</title><link>https://www.cicero.de/innenpolitik/ein-artikel-ueber-x?utm=1</link>
    <pubDate>Wed, 21 Oct 2026 12:17:17 +0000</pubDate><category>Innenpolitik</category></item>
    <item><title>C</title><link>https://www.achgut.com/artikel/c_d</link><dc:date>2026-10-22T12:00:00+00:00</dc:date>
    <dc:subject>Inland, Migration,</dc:subject></item></channel></rss>"""
    a, b = parse_rss(xml)
    assert a.url == "https://www.cicero.de/innenpolitik/ein-artikel-ueber-x" and a.title == "A & B"
    assert a.published_at == datetime(2026, 10, 21, 12, 17, 17, tzinfo=UTC) and a.keywords == ["Innenpolitik"]
    assert b.published_at == datetime(2026, 10, 22, 12, 0, tzinfo=UTC) and b.keywords == ["Inland", "Migration"]
