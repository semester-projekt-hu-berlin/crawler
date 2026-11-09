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
