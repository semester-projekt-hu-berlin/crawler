"""Content filters (quality.py) and how the pipeline and the social sources apply them."""

from collections import Counter
from datetime import UTC, datetime, timedelta

from crawler import quality
from crawler.models import ArticleRef, Comment, FetchResult
from crawler.pipeline import RunOptions, SourceRunner, StreamRunner, fingerprint
from crawler.sources.base import StreamItem
from crawler.sources.bluesky import Bluesky, link_preview, parse_posts
from crawler.sources.mastodon import Mastodon, parse_statuses

from .test_incremental import FakeStream
from .test_pipeline import FakeStore

NOW = datetime.now(UTC).replace(microsecond=0)

# ------------------------------------------------------------------ quality.py


def test_too_short_needs_two_words_outside_links():
    for text in ("+1", "💚", "Moin.", "Word!", "#Merz 😜", "RE: https://bonn.social/117548294881406120",
                 "www.welt.de/politik/ausl...", "taz.de/Lehrer-gegen...", ""):  # fmt: skip
        assert quality.too_short(text), text
    for text in ("Merz raus", "AfD verbieten!", "Dobrindt ist ein Demokratiezersetzer.", "Nicht OK. Gar nicht."):
        assert not quality.too_short(text), text
    assert quality.reject_reason("Danke!") == "too_short" and quality.reject_reason("Danke, Merz!") is None


def test_foreign_drops_clear_non_german_and_keeps_short_german_remarks():
    foreign = (
        "Your article helped me a lot, is there any more related content? Thanks!",
        "I like it when you pretend to resist.",
        "tava de coberta em casa e to pingando suor na rua mas ai bate o vento e eu fico tremendo",
        "Bu Sayfamda ki 15.03.2022 tarihli Yazımın bütününü okumanızı rica ediyorum: Tüm zamanları kapsıyor",
        "수원오피 정보를 통해 지역 내 다양한 제휴점과 유용한 이용 후기를 편리하게 확인해 보세요.",
        "Explore a Modern Crypto Gaming Platform Built Around the Classic Snake Game https://btcsnake.pages.dev",
    )
    for text in foreign:
        assert quality.foreign(text) and quality.reject_reason(text) == "foreign", text
    german = (
        "Kanzler Merz, Ilse Aigner. Ilse Aigner, Kanzler Merz. 😌",  # no function word, but short
        "Stürzt ab iPad Pro M4.",
        "Gerade gehört, definitiv schrecklich.",
        "Boah dieser beschissene KI Slop überall",
        "Die AfD ist eine Gefahr für unsere offene Gesellschaft & Sicherheit.",
        "Trump thought he spoke to the caterer, aber das ist ja nichts Neues bei ihm.",  # mixed: kept
        "Schickt Corona-Masken (wahre Gschicht).",
    )
    for text in german:
        assert not quality.foreign(text), text


def test_repeats_link_card_spots_headline_reposts_but_keeps_comments_on_links():
    title = "Abwehrkampf gegen Russland: Merz will bei EU-Gipfel über Ukraine-Hilfe sprechen - Bytes Deutschland"
    teaser = "Bundeskanzler Friedrich Merz (CDU) will beim EU-Gipfel in Irland am Freitag über weitere Milliardenhilfen"
    bot = (
        "Abwehrkampf gegen Russland: Merz will bei EU-Gipfel über Ukraine-Hilfe sprechen\n\n"
        "https://www.bytesde.com/2074767/\n\nBundeskanzler Friedrich Merz (CDU) will beim EU-Gipfel in Irland"
    )
    assert quality.repeats_link_card(bot, title, teaser)
    # the headline with a site prefix and hashtags is still only the headline
    assert quality.repeats_link_card(
        "TAGESSCHAU: Überschwemmungen in Gaza schüren Sorge vor neuer Katastrophe #Gaza #News",
        "Überschwemmungen in Gaza schüren Sorge vor neuer Katastrophe",
        links=["https://www.tagesschau.de/ausland/asien/gaza-regenfaelle-humanitaere-lage-100.html"],
    )
    # no preview at all: the link itself spells out the headline (umlauts as ae/oe/ue)
    assert quality.repeats_link_card(
        "Nach Depression: Junge Chemnitzer kommen mit kraftvoller Musik zurück "
        "https://www.freiepresse.de/chemnitz/nach-depression-junge-chemnitzer-kommen-mit-kraftvoller-musik-zurueck-artikel14461889"
    )
    # people who say something about the link are kept
    assert not quality.repeats_link_card(
        "Diese Arroganz ist unglaublich.... www.fr.de/politik/merz...",
        "„Schön zum Italiener“: Merz lässt Klimagipfel für seine Geburtstagsfeier sausen",
        "Kanzler Merz sagt den Weltklimagipfel ab.",
    )
    assert not quality.repeats_link_card(
        "Von Panik keine Spur: Deutschland und seine Gasvorräte\n\nWir hätten Habeck haben können!\n\nflip.it/70x5-r",
        "Von Panik keine Spur: Deutschland und seine Gasvorräte",
    )
    assert not quality.repeats_link_card("Kanzler Merz kann auch regieren!", "", "")
    assert not quality.repeats_link_card("Wait for it", "Unsere Vorschläge für die Wahl der Bundespräsidentin")


def test_fingerprint_ignores_links_digits_and_punctuation():
    a = fingerprint("Die AfD ist eine Gefahr für unsere offene Gesellschaft! https://a.example/1")
    b = fingerprint("die afd ist eine gefahr für unsere offene gesellschaft https://b.example/2 3")
    assert a is not None and a == b
    assert fingerprint("Guten Morgen zusammen") is None  # short texts are legitimately repeated


# ------------------------------------------------------------------ pipeline


class _Src:
    id = "tagesschau"
    source_type = "news"


def _result(texts):
    ref = ArticleRef(url="https://example.org/a", source_id="tagesschau")
    comments = [Comment("tagesschau", str(i), t, NOW - timedelta(minutes=i)) for i, t in enumerate(texts)]
    return FetchResult(article=ref, comments=comments)


def test_article_sources_drop_noise_before_storing_and_count_it():
    texts = [
        "Merz macht das gut.",
        "+1",
        "Danke!",
        "I truly appreciate your technique of writing a blog, thanks.",
        "👍",
    ]
    runner = SourceRunner(_Src(), None, RunOptions(dry_run=True), None)  # type: ignore[arg-type]
    runner.store_result(_result(texts), NOW)
    assert runner.stats.comments_new == 1 and runner.stats.comments_filtered == 4
    assert runner.filtered == {"too_short": 3, "foreign": 1}
    keep_all = SourceRunner(_Src(), None, RunOptions(dry_run=True, quality_filter=False), None)  # type: ignore[arg-type]
    keep_all.store_result(_result(texts), NOW)
    assert keep_all.stats.comments_new == 5 and keep_all.stats.comments_filtered == 0


def test_filtered_comments_cost_no_firestore_operation():
    store = FakeStore()
    items = [Comment("s", "1", "Sehr gut gesagt.", NOW), Comment("s", "2", "💚", NOW), Comment("s", "3", "+1", NOW)]
    stats = StreamRunner(FakeStream([items]), store, RunOptions(), last_crawl_at=None).run()
    assert stats.comments_seen == 3 and stats.comments_new == 1 and stats.comments_filtered == 2
    assert store.write_count == 2  # one comment + its article
    assert len([p for p in store.docs if p.startswith("comments/")]) == 1


def test_social_streams_store_a_repeated_text_once_news_streams_keep_both():
    text = "Die AfD ist eine Gefahr für unsere offene Gesellschaft und Sicherheit, sagt die Kampagne."
    items = [Comment("s", "1", text, NOW), Comment("s", "2", text + " https://x.example/2", NOW)]
    social = StreamRunner(FakeStream([items]), None, RunOptions(dry_run=True), last_crawl_at=None).run()
    assert social.comments_new == 1 and social.comments_filtered == 1
    news = FakeStream([items])
    news.source_type = "news"  # two readers may well write the same sentence under an article
    assert StreamRunner(news, None, RunOptions(dry_run=True), last_crawl_at=None).run().comments_new == 2


def test_summary_table_shows_filtered_column():
    from crawler.pipeline import RunSummary, SourceStats

    summary = RunSummary(started_at="2026-11-09T16:17:00+00:00", sources=[SourceStats("taz", comments_filtered=7)])
    head, _, row = summary.to_markdown().split("\n")[2:5]
    assert head.count("|") == row.count("|") and "filtered" in head and "| 7 |" in row
    assert summary.totals()["comments_filtered"] == 7


# ------------------------------------------------------------------ candidates: only read what is crawled


class _CountingStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.read_paths: list[str] = []

    def batch_get(self, paths):
        self.read_paths.extend(paths)
        return super().batch_get(paths)


def test_capped_source_looks_up_only_the_articles_it_takes():
    from .test_limits import CappedSource

    store = _CountingStore()
    src = CappedSource(n_new=250, comments_per_article=1)  # derStandard/Blick discover ~250 articles per run
    opts = RunOptions(source_limits={"capped": {"max_new_articles_per_run": 2}})
    stats = SourceRunner(src, store, opts, None).run()
    assert stats.articles_crawled == 2 and stats.articles_capped == 248
    assert len([p for p in store.read_paths if p.startswith("articles/")]) == 2  # was 250


def test_uncapped_source_still_recognises_tracked_articles_the_query_missed():
    from .test_limits import CappedSource

    store = _CountingStore()
    src = CappedSource(n_new=3, comments_per_article=2)
    SourceRunner(src, store, RunOptions(), None).run()
    for path, doc in store.docs.items():
        if path.startswith("articles/"):
            # older in Firestore than the revisit query looks back, crawled a moment ago
            doc["publishedAt"] = NOW - timedelta(days=5)
    src.fetched.clear()
    stats = SourceRunner(src, store, RunOptions(), None).run()
    assert src.fetched == [] and stats.articles_skipped_recent == 3 and stats.comments_new == 0


# ------------------------------------------------------------------ Bluesky


def _post(i, text="Merz macht das wirklich gut", author="a", minute=1, **record):
    return {"uri": f"at://did:plc:{author}/app.bsky.feed.post/{i}", "indexedAt": f"2026-11-09T11:{minute:02d}:00.000Z",
            "likeCount": 0, "author": {"labels": []}, "record": {"text": f"{text} {i}", **record}}  # fmt: skip


def test_bluesky_drops_headline_reposts_short_and_foreign_posts():
    card = {"$type": "app.bsky.embed.external", "external": {
        "uri": "https://www.tagesschau.de/inland/fuehrerschein-100.html",
        "title": "Wie der Führerschein günstiger werden soll", "description": "Die Regierung plant eine Reform."}}  # fmt: skip
    posts = [
        _post(1),
        _post(2, "Wie der Führerschein günstiger werden soll\n\nwww.tagesschau.de/inland/innen...", embed=card),
        _post(3, "Endlich! Das war längst überfällig, finde ich. www.tagesschau.de/inland/innen...", embed=card),
        _post(4, "💚"),
        _post(5, "These were never the Epstein files. They were the Trump files"),
    ]
    filtered = Counter()
    kept = parse_posts(posts, filtered=filtered)
    assert [c.native_id.rsplit("/", 1)[1] for c in kept] == ["1", "3"]
    assert filtered == {"link_card": 1, "too_short": 1, "foreign": 1}
    assert link_preview(posts[1]["record"])[0] == "Wie der Führerschein günstiger werden soll"


class _QuotaHttp:
    """Search answers: 'Merz' and 'AfD' are busy (100 posts each), 'SAP' is quiet (2 posts)."""

    def __init__(self):
        self.queries = []

    def get_json(self, url, params=None, **kw):
        if "getFeed" in url:
            return {"feed": []}
        q = params["q"]
        self.queries.append(q)
        if q == "die":
            return {"posts": []}
        n = {"Merz": 100, "AfD": 100, "SAP": 2}[q]
        posts = [_post(f"{q}{i}", f"{q} ist mal wieder Thema", author=f"{q}{i}", minute=59 - i % 60) for i in range(n)]
        return {"posts": [p for p in posts if p["indexedAt"] <= params["until"].replace("Z", ".999Z")]}


def test_bluesky_gives_every_query_its_share_and_hands_leftovers_to_busy_ones(monkeypatch):
    import crawler.sources.bluesky as bs

    monkeypatch.setattr(bs, "STATIC_QUERIES", ("Merz", "AfD", "SAP"))
    monkeypatch.setattr(bs, "MIN_PER_QUERY", 5)
    http = _QuotaHttp()
    until = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    items = Bluesky(http).fetch_since(until - timedelta(hours=12), until, max_items=160)  # 100 feed + 60 search
    per_query = {}
    for i in items:
        q = i.article.title.split("„")[1].split("“")[0]
        per_query[q] = per_query.get(q, 0) + 1
    # 54 for the entity searches (90 % of 60): 18 each in round one, SAP has only 2,
    # the 16 it left over go to the busy queries in the next round (it used to be: Merz takes all)
    assert per_query["SAP"] == 2 and per_query["Merz"] + per_query["AfD"] == 52
    assert abs(per_query["Merz"] - per_query["AfD"]) <= 16 and min(per_query["Merz"], per_query["AfD"]) >= 18
    assert http.queries[:3] == ["Merz", "AfD", "SAP"] and "SAP" not in http.queries[3:]  # exhausted: not asked again


def test_bluesky_stores_at_most_three_posts_per_account_and_run(monkeypatch):
    import crawler.sources.bluesky as bs

    class Http:
        def get_json(self, url, params=None, **kw):
            if "getFeed" in url or params["q"] != "Merz":
                return {"posts": [], "feed": []}
            return {"posts": [_post(i, author="bot", minute=30 - i) for i in range(8)] + [_post(99, author="mensch")]}

    monkeypatch.setattr(bs, "STATIC_QUERIES", ("Merz",))
    src = Bluesky(Http())
    until = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    items = src.fetch_since(until - timedelta(hours=12), until, max_items=1000)
    authors = [i.comment.native_id.split("/")[2] for i in items]
    assert authors.count("did:plc:bot") == bs.PER_AUTHOR == 3 and authors.count("did:plc:mensch") == 1
    assert src.filtered["per_author"] == 5
    assert all("did:plc" not in i.comment.text and "did:plc" not in i.article.url for i in items)


# ------------------------------------------------------------------ Mastodon


def _status(i, text, account="1", card=None):
    return {"id": str(i), "created_at": f"2026-11-09T10:{i % 60:02d}:00Z", "visibility": "public", "language": "de",
            "reblog": None, "content": f"<p>{text}</p>", "card": card,
            "account": {"id": account, "bot": False, "noindex": False, "indexable": True}}  # fmt: skip


def test_mastodon_drops_link_card_repeats_noise_and_the_fourth_post_of_an_account():
    card = {"url": "https://taz.de/Kampagne-gegen-Linke/!6226930/", "title": "Kampagne gegen Linke: Die Methode",
            "description": "Wie eine Kampagne gegen die Linke orchestriert wird."}  # fmt: skip
    statuses = [
        _status(1, "Kampagne gegen Linke: Die Methode https://taz.de/Kampagne-gegen-Linke/!6226930/", card=card),
        _status(2, "Was für eine eloquente, richtige und wichtige Einordnung. Dankeschön.", card=card),
        _status(3, "Moin."),
        *[_status(10 + i, f"Noch ein Gedanke zur Bahn, Teil {i}", account="2") for i in range(5)],
    ]
    filtered = Counter()
    kept = parse_statuses(statuses, "x.social", filtered=filtered)
    assert [c.native_id for c in kept] == ["x.social:2", "x.social:10", "x.social:11", "x.social:12"]
    assert filtered == {"link_card": 1, "too_short": 1, "per_author": 2}


def test_mastodon_gives_up_on_a_rate_limited_instance_after_one_retry():
    seen = []

    class Http:
        def get_json(self, url, params=None, **kw):
            seen.append(kw)
            raise RuntimeError("429 for " + url)

    src = Mastodon(Http())
    src.instances = ("a.social", "b.social")
    until = datetime(2026, 11, 9, 12, tzinfo=UTC)
    assert src.fetch_since(until - timedelta(hours=12), until, 100) == []
    assert seen == [{"max_retries": 1}, {"max_retries": 1}]  # instead of four attempts and 75 s of backoff


def test_stream_item_reports_adapter_filters_in_run_stats():
    class Src(FakeStream):
        def fetch_since(self, since, until, max_items):
            self.filtered["link_card"] += 2
            return [StreamItem(self.article, Comment("s", "1", "Ein guter Punkt.", NOW))]

    src = Src([[]])
    src.article = ArticleRef("https://s.example/a", "s", published_at=NOW)
    stats = StreamRunner(src, None, RunOptions(dry_run=True), last_crawl_at=None).run()
    assert stats.comments_new == 1 and stats.comments_filtered == 2


# ------------------------------------------------------------------ HTTP: giving up without waiting


def test_max_retries_zero_makes_one_request_and_defers_the_host_instead_of_sleeping(monkeypatch):
    import requests

    import crawler.http as http_mod
    from crawler.http import PoliteSession

    class Resp:
        status_code = 429
        headers = {"Retry-After": "30"}
        content = b""
        is_redirect = False

        class raw:  # noqa: N801
            @staticmethod
            def tell():
                return 0

    session = PoliteSession(min_delay=0)
    calls, slept = [], []
    monkeypatch.setattr(session.robots, "can_fetch", lambda url: True)
    monkeypatch.setattr(session.robots, "crawl_delay", lambda url: None)
    monkeypatch.setattr(session.session, "get", lambda url, **kw: calls.append(url) or Resp())
    monkeypatch.setattr(http_mod.time, "sleep", slept.append)
    try:
        session.get_json("https://det.social/api/v1/timelines/public", max_retries=0)
        raise AssertionError("429 must raise")
    except requests.HTTPError:
        pass
    assert len(calls) == 1 and slept == []  # the caller moves on at once …
    throttle = session._throttle("det.social")
    assert throttle._next_allowed - http_mod.time.monotonic() > 25  # … and the host gets its Retry-After pause
    # default: retries with backoff as before, and the pause after the last attempt is not slept either
    calls.clear()
    monkeypatch.setattr(throttle, "wait", lambda delay: None)
    try:
        session.get_json("https://det.social/api/v1/timelines/public")
        raise AssertionError("429 must raise")
    except requests.HTTPError:
        pass
    assert len(calls) == http_mod.MAX_RETRIES + 1 and slept == [30.0] * http_mod.MAX_RETRIES


def test_all_leaves_out_default_disabled_sources_but_naming_one_runs_it():
    import crawler.cli as cli

    ids = cli._parse_sources("all")
    assert "bluesky_jetstream" not in ids and "bundestag" not in ids and "lawblog" not in ids and "finanztip" not in ids
    assert {"bluesky", "mastodon", "tagesschau", "tichyseinblick", "taz", "goingelectric"} <= set(ids)
    assert cli._parse_sources("bundestag,bluesky_jetstream") == ["bundestag", "bluesky_jetstream"]
    args = cli.build_parser().parse_args(["run", "--dry-run"])
    assert args.sources is None and args.no_quality_filter is False  # default: the enabled sources
