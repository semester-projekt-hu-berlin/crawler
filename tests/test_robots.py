from crawler.robots import RobotsPolicy

ROBOTS = """
User-agent: GPTBot
Disallow: /

User-agent: *
Disallow: /suche/
Disallow: */comment-thread?
Disallow: /*comment/
Allow: /*comment/public$
Allow: /api/ticker
Disallow: /api/
Crawl-delay: 2
"""


def test_wildcards_and_longest_match():
    p = RobotsPolicy(ROBOTS)
    assert p.can_fetch("https://x.de/politik/artikel-1")
    assert not p.can_fetch("https://x.de/suche/?q=1")
    assert not p.can_fetch("https://x.de/politik/2026-10/foo/comment-thread?page=2")
    assert p.can_fetch("https://x.de/politik/2026-10/foo/comment-thread")
    assert not p.can_fetch("https://x.de/story/comment/")
    assert p.can_fetch("https://x.de/story/comment/public")
    assert not p.can_fetch("https://x.de/api/other")
    assert p.can_fetch("https://x.de/api/ticker/1")
    assert p.crawl_delay == 2


def test_specific_group_wins_over_star():
    robots = "User-agent: *\nDisallow: /\n\nUser-agent: MeinungspulsBot\nDisallow: /privat/\n"
    p = RobotsPolicy(robots)
    assert p.can_fetch("https://x.de/news")
    assert not p.can_fetch("https://x.de/privat/x")


def test_other_bots_groups_do_not_apply():
    p = RobotsPolicy("User-agent: ClaudeBot\nDisallow: /\n\nUser-agent: *\nDisallow: /profil/\n")
    assert p.can_fetch("https://www.derstandard.at/story/1")
    assert not p.can_fetch("https://www.derstandard.at/profil/123")


def test_full_disallow_for_star():
    p = RobotsPolicy("User-agent: *\nDisallow: /\n")
    assert not p.can_fetch("https://hua-production.la.welt.de/la-hua-api/v1/articles/x/comments")


def test_unavailable_and_unreachable():
    assert RobotsPolicy(None, allow_all=True).can_fetch("https://capi.ds.at/forum-serve-graphql/v1/")
    assert not RobotsPolicy(None, disallow_all=True).can_fetch("https://x.de/")


def test_stdlib_check_can_only_restrict():
    # urllib.robotparser applies the first matching rule; if it denies, we deny as well.
    p = RobotsPolicy("User-agent: *\nDisallow: /api/\nAllow: /api/ticker\n")
    assert not p.can_fetch("https://x.de/api/other")


GOINGELECTRIC = """
User-agent: SeekportBot
Disallow: /

User-agent: *
Disallow: /forum/index.php
Disallow: /forum/*?sid=*
Disallow: /forum/*&sid=*
Disallow: /?
Disallow: /%
Disallow: /search/
"""


def test_rule_with_empty_query_does_not_block_the_whole_site():
    # Python <= 3.12's urllib.robotparser turns "Disallow: /?" into "Disallow: /": GoingElectric's
    # feed was refused on the production Python (3.12) and allowed on 3.13. Same answer everywhere:
    p = RobotsPolicy(GOINGELECTRIC)
    assert p.can_fetch("https://www.goingelectric.de/forum/feed")
    assert p.can_fetch("https://www.goingelectric.de/forum/viewtopic.php?t=102137&start=99999")
    assert p.can_fetch("https://www.goingelectric.de/")
    # the rule itself still holds, and so do the others
    assert not p.can_fetch("https://www.goingelectric.de/?s=tesla")
    assert not p.can_fetch("https://www.goingelectric.de/forum/viewtopic.php?t=1&sid=abc")
    assert not p.can_fetch("https://www.goingelectric.de/forum/index.php")
    assert not p.can_fetch("https://www.goingelectric.de/search/x")
    # "/suche?" stays a prefix of "/suche?q=…" only
    q = RobotsPolicy("User-agent: *\nDisallow: /suche?\n")
    assert q.can_fetch("https://x.de/suchergebnis") and q.can_fetch("https://x.de/suche")
    assert not q.can_fetch("https://x.de/suche?q=merz")


class _FakeSession:
    """Serves canned responses per URL and records the request keyword arguments."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kw):
        import io

        import requests

        self.calls.append((url, kw))
        status, headers, body = self.responses[url]
        resp = requests.Response()
        resp.status_code = status
        resp.url = url
        resp.headers.update(headers)
        resp.raw = io.BytesIO(body)
        return resp


def test_robots_fetch_is_capped_at_512_kib():
    from crawler.robots import ROBOTS_MAX_BYTES, RobotsCache

    head = b"User-agent: *\nDisallow: /privat/\n"
    filler = b"# " + b"x" * 97 + b"\n"
    body = head + filler * (ROBOTS_MAX_BYTES // len(filler) + 1) + b"Disallow: /tail/\n" + filler * 10_000
    assert len(body) > 2 * ROBOTS_MAX_BYTES
    session = _FakeSession({"https://x.de/robots.txt": (200, {"Content-Type": "text/plain"}, body)})
    cache = RobotsCache(session)
    assert not cache.can_fetch("https://x.de/privat/1")
    assert cache.can_fetch("https://x.de/tail/1")  # the rule after 512 KiB is not read
    (_url, kw), *more = session.calls
    assert not more and kw["stream"] is True and kw["allow_redirects"] is False


def test_robots_fetch_follows_up_to_five_redirects():
    from crawler.robots import RobotsCache

    hops = {f"https://x.de/r{i}": (301, {"Location": f"/r{i + 1}"}, b"") for i in range(1, 5)}
    responses = {
        "https://x.de/robots.txt": (301, {"Location": "https://x.de/r1"}, b""),
        **hops,
        "https://x.de/r5": (200, {}, b"User-agent: *\nDisallow: /privat/\n"),
    }
    cache = RobotsCache(_FakeSession(responses))
    assert cache.can_fetch("https://x.de/news") and not cache.can_fetch("https://x.de/privat/1")

    # a sixth redirect: unreachable, nothing on the host is fetched
    responses["https://x.de/r5"] = (302, {"Location": "/r6"}, b"")
    responses["https://x.de/r6"] = (200, {}, b"User-agent: *\nDisallow:\n")
    cache = RobotsCache(_FakeSession(responses))
    assert not cache.can_fetch("https://x.de/news")


def test_robots_fetch_status_outcomes():
    from crawler.robots import RobotsCache

    session = _FakeSession(
        {
            "https://a.de/robots.txt": (404, {}, b"not found"),
            "https://b.de/robots.txt": (503, {}, b""),
            "https://c.de/robots.txt": (301, {"Location": "https://d.de/robots.txt"}, b""),
            "https://d.de/robots.txt": (403, {}, b""),
        }
    )
    cache = RobotsCache(session)
    assert cache.can_fetch("https://a.de/x")  # 4xx: unavailable, allow
    assert not cache.can_fetch("https://b.de/x")  # 5xx: unreachable, disallow
    assert cache.can_fetch("https://c.de/x")  # redirect to another host's 4xx: allow
