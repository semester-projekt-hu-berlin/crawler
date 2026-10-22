"""robots.txt handling.

Parsing and user-agent group selection use :mod:`urllib.robotparser` from the
standard library. Its rule *matching* only supports plain prefixes, but almost
every news site uses wildcards (``Disallow: /*comment/``, ``Disallow: */comment-thread?``).
We therefore evaluate the rules of the selected group ourselves following
RFC 9309 / Google semantics: ``*`` matches any sequence, ``$`` anchors the end,
the longest matching rule wins and ``Allow`` wins a tie.

Fetch outcomes (RFC 9309, section 2.3.1):

* 2xx: parse and obey.
* 4xx ("unavailable"): the crawler may access any resource on that host.
* 5xx / network error ("unreachable"): assume complete disallow.
"""

from __future__ import annotations

import logging
import re
import threading
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import requests

from crawler.config import REQUEST_TIMEOUT, ROBOTS_AGENT, USER_AGENT
from crawler.logging_setup import log_event

log = logging.getLogger(__name__)


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in pattern)
    return re.compile(regex + ("$" if anchored else ""))


@dataclass
class _Rule:
    pattern: str
    allow: bool

    def __post_init__(self) -> None:
        self.regex = _pattern_to_regex(self.pattern)

    def matches(self, path: str) -> bool:
        return self.regex.match(path) is not None


class RobotsPolicy:
    """Rules of one host for our user agent."""

    def __init__(self, text: str | None, *, allow_all: bool = False, disallow_all: bool = False) -> None:
        self.allow_all = allow_all
        self.disallow_all = disallow_all
        self.rules: list[_Rule] = []
        self.crawl_delay: float | None = None
        if text is not None:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(text.splitlines())
            entry = self._select_entry(parser)
            if entry is not None:
                for line in entry.rulelines:
                    # robotparser stores percent-quoted paths; "*" and "$" become %2A / %24.
                    pattern = unquote(line.path)
                    if not pattern:
                        # "Disallow:" (empty) means allow everything; it adds no rule.
                        continue
                    if not pattern.startswith(("/", "*")):
                        pattern = "/" + pattern
                    self.rules.append(_Rule(pattern, bool(line.allowance)))
                delay = entry.delay
                self.crawl_delay = float(delay) if delay is not None else None

    @staticmethod
    def _select_entry(parser: urllib.robotparser.RobotFileParser):  # noqa: ANN205 - stdlib private type
        """The group naming our product token, else the ``*`` group (RFC 9309 2.2.1)."""
        star = None
        for entry in parser.entries:
            if "*" in entry.useragents:
                star = star or entry
            elif entry.applies_to(ROBOTS_AGENT):
                return entry
        return parser.default_entry or star

    def can_fetch(self, url: str) -> bool:
        if self.disallow_all:
            return False
        if self.allow_all:
            return True
        parts = urlsplit(url)
        path = unquote(parts.path or "/") + (f"?{unquote(parts.query)}" if parts.query else "")
        best: _Rule | None = None
        for rule in self.rules:
            if not rule.matches(path):
                continue
            if (
                best is None
                or len(rule.pattern) > len(best.pattern)
                or (len(rule.pattern) == len(best.pattern) and rule.allow and not best.allow)
            ):
                best = rule
        return True if best is None else best.allow


class RobotsCache:
    """Fetches and caches robots.txt per scheme+host for the lifetime of a run."""

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()
        self._policies: dict[str, RobotsPolicy] = {}
        self._lock = threading.Lock()

    def policy(self, url: str) -> RobotsPolicy:
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        with self._lock:
            if key not in self._policies:
                self._policies[key] = self._fetch(key)
            return self._policies[key]

    def _fetch(self, origin: str) -> RobotsPolicy:
        robots_url = f"{origin}/robots.txt"
        try:
            resp = self._session.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            log_event(log, "robots.unreachable", logging.WARNING, url=robots_url, error=str(exc))
            return RobotsPolicy(None, disallow_all=True)
        if resp.status_code >= 500:
            log_event(log, "robots.unreachable", logging.WARNING, url=robots_url, status=resp.status_code)
            return RobotsPolicy(None, disallow_all=True)
        if resp.status_code >= 400:
            log_event(log, "robots.unavailable", url=robots_url, status=resp.status_code, rule="RFC 9309: allow")
            return RobotsPolicy(None, allow_all=True)
        policy = RobotsPolicy(resp.text)
        log_event(log, "robots.loaded", url=robots_url, rules=len(policy.rules), crawl_delay=policy.crawl_delay)
        return policy

    def can_fetch(self, url: str) -> bool:
        return self.policy(url).can_fetch(url)

    def crawl_delay(self, url: str) -> float | None:
        return self.policy(url).crawl_delay
