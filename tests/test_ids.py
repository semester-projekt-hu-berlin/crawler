import hashlib
from datetime import UTC, datetime

from crawler.ids import article_id, berlin_day, canonicalize_url, comment_id, parse_datetime


def test_article_id_is_sha1_prefix_of_canonical_url():
    url = "https://taz.de/!6223153/"
    assert article_id(url) == hashlib.sha1(url.encode()).hexdigest()[:20]
    assert len(article_id(url)) == 20


def test_comment_id_follows_schema():
    expected = hashlib.sha1(b"derstandard:3L2oRG0asoLQQGd7sppOoYzyRUI").hexdigest()[:20]
    assert comment_id("derstandard", "3L2oRG0asoLQQGd7sppOoYzyRUI") == expected
    assert comment_id("taz", 5294612) == comment_id("taz", "5294612")


def test_ids_are_deterministic_and_lowercase():
    assert comment_id("blick", "x") == comment_id("blick", "x")
    assert comment_id("blick", "x").islower()
    assert comment_id("blick", "x") != comment_id("taz", "x")


def test_canonicalize_url_strips_tracking():
    assert canonicalize_url("http://WWW.Spiegel.de/a-1?ref=rss#x") == "https://www.spiegel.de/a-1"


def test_berlin_day_crosses_midnight():
    # 22:30 UTC on 21 Oct is 00:30 on 22 Oct in Berlin (CEST, UTC+2).
    assert berlin_day(datetime(2026, 10, 21, 22, 30, tzinfo=UTC)) == "2026-10-22"
    # Winter time (CET, UTC+1)
    assert berlin_day(datetime(2026, 12, 31, 22, 59, tzinfo=UTC)) == "2026-12-31"
    assert berlin_day(datetime(2026, 12, 31, 23, 0, tzinfo=UTC)) == "2027-01-01"


def test_parse_datetime_variants():
    assert parse_datetime("2026-10-22T09:31:53.554Z") == datetime(2026, 10, 22, 9, 31, 53, 554000, tzinfo=UTC)
    assert parse_datetime("2026-10-22T09:10:44+02:00") == datetime(2026, 10, 22, 7, 10, 44, tzinfo=UTC)
    assert parse_datetime(1792662350214) == datetime(2026, 10, 22, 9, 45, 50, 214000, tzinfo=UTC)
    # naive -> interpreted as Berlin local time
    assert parse_datetime("2026-10-21T19:55:00") == datetime(2026, 10, 21, 17, 55, tzinfo=UTC)
    assert parse_datetime("") is None
    assert parse_datetime("kaputt") is None
