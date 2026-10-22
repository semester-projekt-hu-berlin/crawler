import json
from dataclasses import fields
from datetime import UTC, datetime

from bs4 import BeautifulSoup

from crawler.discovery import parse_news_sitemap, parse_page_meta
from crawler.models import Comment
from crawler.sources.blick import Blick, find_container_id, parse_contents
from crawler.sources.derstandard import DerStandard, parse_postings
from crawler.sources.taz import Taz, parse_comments, parse_tags

FORBIDDEN = ("author", "user", "name", "avatar", "profile")


def test_comment_model_has_no_author_fields():
    names = {f.name for f in fields(Comment)}
    assert not any(word in n for n in names for word in FORBIDDEN)


# --------------------------------------------------------------- derStandard


def test_derstandard_parse_postings(fixture_path):
    data = json.loads(fixture_path("derstandard_threads.json").read_text())
    nodes = [e["node"] for e in data["data"]["getForumRootPostingsV2"]["edges"]]
    comments = parse_postings(nodes)
    # 11 nodes in the fixture, one of them deleted
    assert len(comments) == 10
    by_id = {c.native_id: c for c in comments}
    roots = [c for c in comments if c.native_parent_id is None]
    assert len(roots) == 3
    deleted = nodes[0]["replies"][0]
    assert deleted["id"] not in by_id
    # replies of the deleted posting keep it as parent
    for reply in deleted["replies"]:
        assert by_id[reply["id"]].native_parent_id == deleted["id"]
    first = by_id[nodes[0]["id"]]
    assert first.text.startswith("Titel\nBeispieltext 1")
    assert first.upvotes == nodes[0]["reactions"]["aggregated"][0]["value"]
    assert first.published_at.tzinfo is not None
    assert all("Redacted" not in c.text for c in comments)


def test_derstandard_canonical_url():
    ds = DerStandard(http=None)
    assert ds.canonical_url("https://www.derstandard.at/story/3000000345479/slug") == (
        "https://www.derstandard.at/story/3000000345479"
    )
    assert ds.canonical_url("https://www.derstandard.at/jetzt/livebericht/3000000345458/x") is None


# --------------------------------------------------------------- taz


def test_taz_parse_comments(fixture_path):
    soup = BeautifulSoup(fixture_path("taz_article.html").read_text(), "lxml")
    comments = parse_comments(soup)
    assert [c.native_id for c in comments] == ["5294502", "5294534", "5294573"]
    first, reply, third = comments
    assert first.text == "Was folgt jetzt daraus? Nichts.\n\nDie Union muss sich entscheiden."
    assert first.native_parent_id is None
    assert reply.native_parent_id == "5294502"
    # leading "@Name:" mention stripped, e-mail kept
    assert reply.text == "Ist das ein Whataboutism? Schreiben Sie an info@example.org."
    assert third.published_at == datetime(2026, 10, 22, 6, 0, tzinfo=UTC)
    assert first.upvotes is None
    for c in comments:
        for name in ("Beispielnutzerin", "Anderer Name", "Dritte Person", "ku11111"):
            assert name not in c.text


def test_taz_meta_and_tags(fixture_path):
    soup = BeautifulSoup(fixture_path("taz_article.html").read_text(), "lxml")
    meta = parse_page_meta(soup)
    assert meta.title == "Beispiel: Ein Artikel über Politik"
    assert meta.published_at == datetime(2026, 10, 21, 17, 55, tzinfo=UTC)
    assert meta.topics == ["Gesellschaft", "Debatte"]
    assert parse_tags(soup) == ["Schwerpunkt AfD", "Datenleak"]


def test_taz_canonical_url():
    taz = Taz(http=None)
    assert taz.canonical_url("https://taz.de/Geleakte-AfD-Mails/!6223153/") == "https://taz.de/!6223153/"
    assert taz.canonical_url("https://taz.de/!6223153/") == "https://taz.de/!6223153/"
    assert taz.canonical_url("https://taz.de/Gesellschaft/!p4611/") is None


# --------------------------------------------------------------- Blick


def test_blick_parse_contents(fixture_path):
    data = json.loads(fixture_path("blick_comments.json").read_text())
    cc = data["content_container_uuid"]
    comments = parse_contents(data["contents"], cc)
    assert len(comments) == len(data["contents"]) - 1  # one disabled
    tops = {c.native_id for c in comments if c.native_parent_id is None}
    replies = [c for c in comments if c.native_parent_id is not None]
    assert replies and all(r.native_parent_id in tops for r in replies)
    assert all("@someuser" not in c.text for c in comments)
    assert all(c.upvotes is not None for c in comments)


def test_blick_container_and_url():
    html = '<div><vf-conversations vf-container-id="94e3b91d-2865-4115-96f8-0121ac065881"></vf-conversations></div>'
    assert find_container_id(html) == "94e3b91d-2865-4115-96f8-0121ac065881"
    assert find_container_id("<html></html>") is None
    blick = Blick(http=None)
    assert blick.canonical_url("https://www.blick.ch/schweiz/europaeischer-gerichtshof/c9yebr3") == (
        "https://www.blick.ch/c9yebr3"
    )
    assert blick.canonical_url("https://www.blick.ch/fr/suisse/quelque-chose/c9yebr3") is None


# --------------------------------------------------------------- discovery


def test_parse_news_sitemap(fixture_path):
    entries = parse_news_sitemap(fixture_path("news_sitemap.xml").read_bytes())
    assert len(entries) == 4
    assert entries[0].url == "https://www.derstandard.at/story/3000000345479/verbotene-veroeffentlichung-olg-gibt-recht"
    assert entries[0].title == "OLG spricht frei"
    assert entries[0].published_at == datetime(2026, 10, 22, 9, 22, 29, 984000, tzinfo=UTC)
    assert entries[2].keywords == ["Technik", "Sport"]


class _FakeResp:
    def __init__(self, content: bytes):
        self.content = content


class _FakeHttp:
    def __init__(self, content: bytes):
        self._content = content

    def get(self, url, **kwargs):
        return _FakeResp(self._content)


def test_discover_filters_window_and_non_articles(fixture_path):
    ds = DerStandard(http=_FakeHttp(fixture_path("news_sitemap.xml").read_bytes()))
    refs = ds.discover(datetime(2026, 10, 21, tzinfo=UTC), datetime(2026, 10, 23, tzinfo=UTC))
    assert [r.url for r in refs] == ["https://www.derstandard.at/story/3000000345479"]
    assert refs[0].source_id == "derstandard"
