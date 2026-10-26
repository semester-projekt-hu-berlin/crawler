"""Command line interface.

python -m crawler run --sources all --max-articles 150 --since-hours 48
python -m crawler run --sources derstandard,taz --dry-run --out comments.jsonl
python -m crawler sources
python -m crawler check-robots
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from crawler import __version__
from crawler.config import DISABLED_SOURCES, USER_AGENT, source_limits
from crawler.logging_setup import log_event, setup_logging
from crawler.sources import SOURCES

log = logging.getLogger("crawler")


def _parse_sources(value: str) -> list[str]:
    if value.strip().lower() == "all":
        # "all" leaves out sources switched off via CRAWLER_DISABLED_SOURCES
        return [sid for sid in SOURCES if sid not in DISABLED_SOURCES]
    ids = [v.strip().lower() for v in value.split(",") if v.strip()]
    unknown = [i for i in ids if i not in SOURCES]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown source(s): {', '.join(unknown)}; known: {', '.join(SOURCES)}")
    return ids


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="crawler", description="Meinungspuls comment crawler")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    p.add_argument("--log-format", choices=["text", "json"], default=os.environ.get("LOG_FORMAT", "text"))
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="crawl comments and write them to Firestore")
    run.add_argument(
        "--sources",
        type=_parse_sources,
        default=list(SOURCES),
        help="comma separated source ids or 'all' (default: all)",
    )
    run.add_argument(
        "--max-articles",
        type=int,
        default=500,
        help="max articles per source and run, new and revisited (default: 500)",
    )
    run.add_argument(
        "--since-hours", type=float, default=48.0, help="discover articles published in the last H hours (default: 48)"
    )
    run.add_argument(
        "--max-items",
        type=int,
        default=3000,
        help="stream sources (Tichys Einblick, Mastodon, Bluesky): max items per source and run (default: 3000)",
    )
    run.add_argument(
        "--source-limit",
        action="append",
        default=[],
        metavar="SRC.KEY=N",
        help="override a per-source cap, e.g. derstandard.max_comments_per_run=3000; 0 removes it, "
        "derstandard=off removes all caps of a source (also env CRAWLER_SOURCE_LIMITS as JSON)",
    )
    run.add_argument(
        "--time-budget-minutes", type=float, default=None, help="stop starting new articles after this many minutes"
    )
    run.add_argument("--dry-run", action="store_true", help="do not touch Firestore")
    run.add_argument("--out", help="dry-run only: write comments as JSON lines to this file")
    run.add_argument("--summary-json", help="write the run summary as JSON to this file")

    sub.add_parser("sources", help="list available sources")
    sub.add_parser("check-robots", help="show robots.txt decisions for all endpoints used")
    return p


def cmd_run(args: argparse.Namespace) -> int:
    from crawler.pipeline import RunOptions, run

    store = None
    if not args.dry_run:
        from crawler.firestore import Firestore

        store = Firestore()
    out = open(args.out, "w", encoding="utf-8") if args.out and args.dry_run else None  # noqa: SIM115
    try:
        opts = RunOptions(
            max_articles=args.max_articles,
            max_items=args.max_items,
            source_limits=source_limits(args.source_limit),
            since_hours=args.since_hours,
            dry_run=args.dry_run,
            time_budget_s=args.time_budget_minutes * 60 if args.time_budget_minutes else None,
            out=out,
        )
        log_event(
            log,
            "run.start",
            source_limits=json.dumps(opts.source_limits),
            sources=",".join(args.sources),
            max_articles=args.max_articles,
            max_items=args.max_items,
            since_hours=args.since_hours,
            dry_run=args.dry_run,
            user_agent=USER_AGENT,
        )
        summary = run(args.sources, opts, store)
    finally:
        if out:
            out.close()
    log_event(
        log,
        "run.done",
        **summary.totals(),
        http_requests=summary.http_requests,
        firestore_writes=summary.firestore_writes,
    )
    print(summary.to_markdown())
    if args.summary_json:
        with open(args.summary_json, "w", encoding="utf-8") as f:
            json.dump(summary.to_dict(), f, indent=2, default=str)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(summary.to_markdown() + "\n")
    failed_sources = [s for s in summary.sources if s.error]
    return 1 if failed_sources and len(failed_sources) == len(summary.sources) else 0


def cmd_sources() -> int:
    for sid, cls in SOURCES.items():
        feed = getattr(cls, "news_sitemap", "(stream)")
        print(f"{sid:15} {cls.source_type:7} {cls.name:40} {cls.domain:28} {cls.country}  {feed}")
    return 0


def cmd_check_robots() -> int:
    from crawler.robots import RobotsCache
    from crawler.sources.mastodon import INSTANCES as MASTODON_INSTANCES

    endpoints = [
        "https://www.derstandard.at/sitemaps/news.xml",
        "https://www.derstandard.at/story/3000000000000",
        "https://capi.ds.at/forum-serve-graphql/v1/?operationName=GetForumInfo",
        "https://taz.de/sitemap-google-news.xml",
        "https://taz.de/!6000000/",
        "https://www.blick.ch/news.xml",
        "https://www.blick.ch/schweiz/abc/abc1234",
        "https://tyrion.viafoura.co/v3/sections/sites/www.blick.ch",
        "https://livecomments.viafoura.co/v4/livecomments/x/contentcontainer/id?container_id=y",
        "https://meta.tagesschau.de/?page=1",
        "https://meta.tagesschau.de/id/176269/tote-nach-angriff-auf-flughafen-in-saudi-arabien?page=1",
        "https://www.freitag.de/@@google-news-rss",
        "https://www.freitag.de/autoren/der-freitag/beispiel/@comments",
        "https://www.tichyseinblick.de/wp-json/wp/v2/comments?per_page=100",
        "https://www.tichyseinblick.de/wp-json/wp/v2/posts?include=1",
        "https://norden.social/api/v1/timelines/public?local=true",
        "https://troet.cafe/api/v1/timelines/public?local=true",
        "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q=die&lang=de",
        "https://www.cicero.de/rss.xml",
        "https://www.cicero.de/innenpolitik/ein-artikel?page=1",
        "https://www.achgut.com/rss",
        "https://www.achgut.com/artikel/ein_artikel",
        *[f"https://{h}/api/v1/timelines/public?local=true" for h in MASTODON_INSTANCES],
        "https://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post",
        "https://jetstream1.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post",
        "https://jetstream1.us-west.bsky.network/subscribe?wantedCollections=app.bsky.feed.post",
        "https://jetstream2.us-west.bsky.network/subscribe?wantedCollections=app.bsky.feed.post",
        "https://api.bsky.app/xrpc/app.bsky.actor.getProfiles?actors=did:plc:x",
        "https://www.iphone-ticker.de/feed/",
        "https://www.iphone-ticker.de/ein-artikel-289045/",
        "https://www.iphone-ticker.de/ein-artikel-289045/feed/",
        "https://www.ifun.de/feed/",
        "https://www.ifun.de/ein-artikel-293187/feed/",
        "https://apollo-news.net/wp-json/wp/v2/comments?per_page=100",
        "https://apollo-news.net/wp-json/wp/v2/posts?include=1",
        "https://overton-magazin.de/wp-json/wp/v2/comments?per_page=100",
        "https://www.appgefahren.de/wp-json/wp/v2/comments?per_page=100",
        "https://www.lawblog.de/wp-json/wp/v2/comments?per_page=100",
        "https://www.computerbase.de/rss/news.xml",
        "https://www.computerbase.de/news/gaming/ein-artikel.99971/",
        "https://www.computerbase.de/forum/threads/ein-thread.2280699/page-2",
        "https://www.wallstreet-online.de/community",
        "https://www.wallstreet-online.de/diskussion/1313540-1/biontech",
        "https://public.api.bsky.app/xrpc/app.bsky.feed.getFeed?feed=at://did:plc:xcprh522pngo62qeonh2jj5u/app.bsky.feed.generator/aaaown2rdgu6m",
        "https://www.br.de/nachrichten/newsSitemap",
        "https://www.br.de/nachrichten/bayern/beitrag,VYJBwtn",
        "https://comments.br24.de/embed/bootstrap",
        "https://comments.br24.de/assets/js/stream.e17437fa6c9c9a6068afb70790b0d44b.js",
        "https://comments.br24.de/api/graphql?query=&id=9c28aa927a6d4ee29ff476337d9563b4&variables=%7B%7D",
        *[f"https://www.rbb24.de{p}" for p in ("/", "/politik/", "/panorama/", "/wirtschaft/", "/kultur/", "/sport/")],
        "https://www.rbb24.de/politik/beitrag/2026/10/ein-artikel.html",
        "https://www.rbb24.de/politik/beitrag/2026/10/ein-artikel.commentthreads.jsn?start=0&size=100",
        "https://www.allmystery.de/themen/pr",
        "https://www.allmystery.de/themen/pr98768-5157",
        "https://www.finanztip.de/community/forum/thread-list-rss-feed/14/",
        "https://www.finanztip.de/community/forum/thread-list-rss-feed/",
        "https://www.finanztip.de/community/forum/thema/47819-ein-thema/",
        "https://www.goingelectric.de/forum/feed",
        "https://www.goingelectric.de/forum/viewtopic.php?p=2453007",
        "https://www.goingelectric.de/forum/viewtopic.php?t=102137&start=99999",
        "https://www.kuketz-forum.de/posts.json",
        "https://www.kuketz-forum.de/posts.json?before=102820",
        "https://epetitionen.bundestag.de/epet/petuebersicht/mz.nc.content.teaser-petitionen-table.$$$.status.2.page.0.batchsize.10.html",
        "https://epetitionen.bundestag.de/epet/rediraftercookiecheck.html",
        "https://epetitionen.bundestag.de/petitionen/_2026/_06/_08/Petition_202289/forum.kommentare.table.$$$.page.0.html",
        "https://epetitionen.bundestag.de/petitionen/_2026/_06/_08/Petition_202289/forum/Beitrag_868184.alleAntwortenNachDatum.$$$.page.1.html",
        "https://epetitionen.bundestag.de/content/petitionen/_2026/_06/_08/Petition_202289/forum.rss.xml",
    ]
    robots = RobotsCache()
    ok = True
    for url in endpoints:
        allowed = robots.can_fetch(url)
        ok &= allowed
        print(f"{'ALLOW' if allowed else 'DENY '}  delay={robots.crawl_delay(url)}  {url}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level, args.log_format)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "sources":
        return cmd_sources()
    if args.command == "check-robots":
        return cmd_check_robots()
    return 2


if __name__ == "__main__":
    sys.exit(main())
