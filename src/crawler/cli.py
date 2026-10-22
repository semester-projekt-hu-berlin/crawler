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
from crawler.config import USER_AGENT
from crawler.logging_setup import log_event, setup_logging
from crawler.sources import SOURCES

log = logging.getLogger("crawler")


def _parse_sources(value: str) -> list[str]:
    if value.strip().lower() == "all":
        return list(SOURCES)
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
            since_hours=args.since_hours,
            dry_run=args.dry_run,
            time_budget_s=args.time_budget_minutes * 60 if args.time_budget_minutes else None,
            out=out,
        )
        log_event(
            log,
            "run.start",
            sources=",".join(args.sources),
            max_articles=args.max_articles,
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
        print(f"{sid:12} {cls.name:14} {cls.domain:16} {cls.country}  {cls.news_sitemap}")
    return 0


def cmd_check_robots() -> int:
    from crawler.robots import RobotsCache

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
