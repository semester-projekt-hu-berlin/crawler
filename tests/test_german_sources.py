from datetime import UTC, datetime, timedelta

from bs4 import BeautifulSoup

from crawler.sources import SOURCES
from crawler.sources.caschy import parse_comments as caschy_comments
from crawler.sources.lemmy import looks_german, markdown_text, parse_views
from crawler.sources.mastodon import Mastodon
from crawler.sources.tichyseinblick import Netzpolitik, WordPressComments


def test_every_source_has_country_type_and_topic():
    for sid, cls in SOURCES.items():
        assert cls.country in ("DE", "AT", "CH"), sid
        assert cls.source_type in ("news", "social"), sid
        assert cls.topic in ("politics", "tech", "tv", "general"), sid
    german = [c for c in SOURCES.values() if c.country == "DE"]
    assert len(german) >= len(SOURCES) - 2


def _view(i, content, lang=32, path=None, bot=False, deleted=False):
    return {
        "comment": {"id": i, "content": content, "language_id": lang, "published": "2026-10-23T10:00:00Z",
                    "path": path or f"0.{i}", "deleted": deleted, "removed": False},
        "post": {"id": 7, "name": "Tempolimit jetzt?", "published": "2026-10-23T08:00:00Z"},
        "community": {"name": "dach", "title": "DACH"},
        "creator": {"name": "maxm", "bot_account": bot, "actor_id": "https://feddit.org/u/maxm"},
        "counts": {"upvotes": 4},
    }  # fmt: skip


def test_feddit_filters_and_threads():
    views = [
        _view(1, "Ich finde, das ist eine gute Idee und längst überfällig."),
        _view(2, "> Zitat\n\n@maxm@feddit.org Das sehe ich nicht so, aber [Quelle](https://x)", path="0.1.2"),
        _view(3, "This is English and should be skipped by the filter.", lang=37),
        _view(4, "Und das ist auch deutsch, nicht wahr?", lang=0),
        _view(5, "and this is english with undetermined language", lang=0),
        _view(6, "Bot sagt hallo und tschüss", bot=True),
        _view(7, "gelöscht und weg", deleted=True),
    ]
    pairs = parse_views(views)
    assert [c.native_id for _, c in pairs] == ["1", "2", "4"]
    ref, reply = pairs[1]
    assert reply.native_parent_id == "1" and reply.text == "Zitat\n\nDas sehe ich nicht so, aber Quelle"
    assert ref.url == "https://feddit.org/post/7" and ref.title == "Tempolimit jetzt?" and ref.topics == ["DACH"]
    assert pairs[0][1].upvotes == 4
    assert looks_german("Das ist nicht so, und ich weiß es.") and not looks_german("This is not German at all.")
    assert markdown_text("**fett** [Link](http://x)") == "fett Link"


def test_caschy_comments():
    html = """<ol class="comment-list"><li class="comment depth-1" id="li-comment-1">
      <div id="comment-1"><header><cite class="fn">Max</cite><time datetime="2026-10-23T12:36:19+02:00">x</time></header>
      <div class="comment-body"><p>Canva hat sich bedient.</p></div></div>
      <ul class="children"><li class="comment depth-2" id="li-comment-2"><div id="comment-2">
      <header><cite class="fn">Erika</cite><time datetime="2026-10-23T13:00:00+02:00">y</time></header>
      <div class="comment-body"><p>Stimmt nicht.</p></div></div></li></ul></li></ol>"""
    comments = caschy_comments(BeautifulSoup(html, "lxml"))
    assert [(c.native_id, c.native_parent_id) for c in comments] == [("1", None), ("2", "1")]
    assert comments[0].published_at == datetime(2026, 10, 23, 10, 36, 19, tzinfo=UTC)
    assert "Max" not in comments[0].text


def test_wordpress_subclasses_use_their_own_site():
    class Http:
        def __init__(self):
            self.urls = []

        def get_json(self, url, params=None, **kw):
            self.urls.append(url)
            if url.endswith("/comments"):
                return [{"id": 1, "post": 9, "parent": 0, "date_gmt": "2026-10-23T09:00:00", "author_name": "X",
                         "content": {"rendered": "<p>Gut recherchiert.</p>"}}]  # fmt: skip
            return [{"id": 9, "link": "https://netzpolitik.org/2026/foo/", "title": {"rendered": "Foo"},
                     "date_gmt": "2026-10-23T08:00:00"}]  # fmt: skip

    http = Http()
    until = datetime(2026, 10, 23, 12, tzinfo=UTC)
    items = Netzpolitik(http).fetch_since(until - timedelta(hours=12), until, 100)
    assert issubclass(Netzpolitik, WordPressComments)
    assert all(u.startswith("https://netzpolitik.org/wp-json/wp/v2/") for u in http.urls)
    assert items[0].article.url == "https://netzpolitik.org/?p=9" and items[0].article.topics == []
    assert items[0].comment.text == "Gut recherchiert."


def test_mastodon_thins_evenly_over_instances(monkeypatch):

    def statuses(instance, n):
        return [{"id": str(i), "created_at": f"2026-10-23T10:{i % 60:02d}:00Z", "visibility": "public",
                 "language": "de", "reblog": None, "content": f"<p>Ein Beitrag {i}</p>",
                 "account": {"bot": False, "noindex": False, "indexable": True}} for i in range(n)]  # fmt: skip

    class Http:
        def get_json(self, url, params=None, **kw):
            if params.get("max_id"):
                return []
            return statuses(url, 40 if "a.social" in url else 10)

    src = Mastodon(Http())
    src.instances = ("a.social", "b.social")
    until = datetime(2026, 10, 23, 12, tzinfo=UTC)
    items = src.fetch_since(until - timedelta(hours=12), until, max_items=25)
    per = {i.article.url.split("/")[2]: 0 for i in items}
    for i in items:
        per[i.article.url.split("/")[2]] += 1
    assert sum(per.values()) <= 25 and per["a.social"] > per["b.social"] > 0
