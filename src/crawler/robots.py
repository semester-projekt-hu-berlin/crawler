"""robots.txt handling.

Every URL must be allowed by two checks:

1. :mod:`urllib.robotparser` from the standard library, and
2. our own RFC 9309 evaluation. The standard library only supports plain prefix
   rules, but almost every news site uses wildcards (``Disallow: /*comment/``,
   ``Disallow: */comment-thread?``), and its path normalisation differs between
   Python versions. We therefore also parse the groups ourselves: ``*`` matches
   any sequence, ``$`` anchors the end, the longest matching rule wins and
   ``Allow`` wins a tie.

Fetch outcomes (RFC 9309, section 2.3.1):

* 2xx: parse and obey.
* 4xx ("unavailable"): the crawler may access any resource on that host.
* 5xx / network error ("unreachable"): assume complete disallow.
"""

from __future__ import annotations

import contextlib
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


def _parse_groups(text: str) -> list[tuple[list[str], list[tuple[str, str]]]]:
    """Split robots.txt into groups ``(user_agents, [(field, value), ...])`` (RFC 9309 section 2.1)."""
    groups: list[tuple[list[str], list[tuple[str, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key in ("user-agent", "useragent"):
            if rules:  # a user-agent line after rules starts a new group
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
        elif key in ("allow", "disallow", "crawl-delay") and agents:
            rules.append((key, value))
    if agents:
        groups.append((agents, rules))
    return groups


class RobotsPolicy:
    """Rules of one host for our user agent.

    Groups naming our product token win; otherwise the ``*`` groups apply (several
    matching groups are merged, as RFC 9309 requires). In addition, the URL must
    be allowed by :mod:`urllib.robotparser` (it cannot evaluate wildcards, so it
    only ever adds restrictions, never removes them).
    """

    def __init__(self, text: str | None, *, allow_all: bool = False, disallow_all: bool = False) -> None:
        self.allow_all = allow_all
        self.disallow_all = disallow_all
        self.rules: list[_Rule] = []
        self.crawl_delay: float | None = None
        self._stdlib: urllib.robotparser.RobotFileParser | None = None
        if text is None:
            return
        groups = _parse_groups(text)
        token = ROBOTS_AGENT.lower()
        selected = [rules for agents, rules in groups if token in agents]
        if not selected:
            selected = [rules for agents, rules in groups if "*" in agents]
        for rules in selected:
            for key, value in rules:
                if key == "crawl-delay":
                    with contextlib.suppress(ValueError):
                        self.crawl_delay = max(self.crawl_delay or 0.0, float(value))
                elif value:  # an empty "Disallow:" allows everything and adds no rule
                    pattern = value if value.startswith(("/", "*")) else "/" + value
                    self.rules.append(_Rule(unquote(pattern), key == "allow"))
        self._stdlib = urllib.robotparser.RobotFileParser()
        self._stdlib.parse(text.splitlines())

    def can_fetch(self, url: str) -> bool:
        if self.disallow_all:
            return False
        if self.allow_all:
            return True
        if self._stdlib is not None and not self._stdlib.can_fetch(ROBOTS_AGENT, url):
            return False
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
