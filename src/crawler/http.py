"""Polite HTTP client: robots.txt check, per-host rate limit, retries with backoff."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from crawler.config import MAX_RETRIES, MIN_DELAY_PER_HOST, REQUEST_TIMEOUT, USER_AGENT
from crawler.logging_setup import log_event
from crawler.robots import RobotsCache

log = logging.getLogger(__name__)


class RobotsDisallowed(Exception):
    """Raised when robots.txt forbids fetching a URL. The URL is then skipped."""


class _HostThrottle:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self, delay: float) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)
            self._next_allowed = time.monotonic() + delay


class PoliteSession:
    """Wraps :class:`requests.Session`. Every request

    1. is checked against robots.txt of its host (cached per run),
    2. waits until at least ``max(min_delay, Crawl-delay)`` seconds passed since the
       previous request to the same host,
    3. is retried on network errors, 429 and 5xx with exponential backoff
       (``Retry-After`` is honoured).
    """

    def __init__(self, min_delay: float = MIN_DELAY_PER_HOST, user_agent: str = USER_AGENT) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "de-DE,de;q=0.9",
                # Compressed transfer (requests decodes gzip/deflate transparently).
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self.min_delay = min_delay
        self.robots = RobotsCache(self.session)
        self._throttles: dict[str, _HostThrottle] = {}
        self._lock = threading.Lock()
        self.request_count = 0
        # Bytes received on the wire (compressed), for the run summary.
        self.bytes_received = 0

    def _throttle(self, host: str) -> _HostThrottle:
        with self._lock:
            return self._throttles.setdefault(host, _HostThrottle())

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        allow_404: bool = False,
    ) -> requests.Response:
        """GET with robots check, throttling and retries. Redirects are followed manually
        (max 5) so that every redirect target is checked against robots.txt as well."""
        current = requests.Request("GET", url, params=params).prepare().url or url
        for _ in range(6):
            resp = self._get_once(current, headers=headers)
            if resp.is_redirect and resp.headers.get("Location"):
                current = urljoin(current, resp.headers["Location"])
                continue
            if resp.status_code == 404 and allow_404:
                return resp
            resp.raise_for_status()
            return resp
        raise requests.TooManyRedirects(f"too many redirects for {url}")

    def _get_once(self, full_url: str, headers: dict[str, str] | None) -> requests.Response:
        if not self.robots.can_fetch(full_url):
            log_event(log, "http.robots_disallowed", logging.WARNING, url=full_url)
            raise RobotsDisallowed(full_url)
        host = urlsplit(full_url).netloc
        delay = max(self.min_delay, self.robots.crawl_delay(full_url) or 0.0)
        throttle = self._throttle(host)

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            throttle.wait(delay)
            with self._lock:
                self.request_count += 1
            try:
                resp = self.session.get(full_url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=False)
            except requests.RequestException as exc:
                last_exc = exc
                log_event(log, "http.error", logging.WARNING, url=full_url, attempt=attempt, error=str(exc))
                time.sleep(2**attempt * 2)
                continue
            _ = resp.content  # read the body so the raw (compressed) byte count is final
            try:
                wire = int(resp.raw.tell())
            except (AttributeError, TypeError, ValueError):
                wire = len(resp.content)
            with self._lock:
                self.bytes_received += wire
            log_event(log, "http.get", logging.DEBUG, url=full_url, status=resp.status_code, bytes=wire)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After", "")
                backoff = float(retry_after) if retry_after.isdigit() else 2**attempt * 5
                log_event(
                    log,
                    "http.retry",
                    logging.WARNING,
                    url=full_url,
                    status=resp.status_code,
                    attempt=attempt,
                    backoff=backoff,
                )
                last_exc = requests.HTTPError(f"{resp.status_code} for {full_url}", response=resp)
                time.sleep(min(backoff, 120))
                continue
            return resp
        assert last_exc is not None
        raise last_exc

    def get_json(self, url: str, **kwargs: Any) -> Any:
        """GET and decode JSON. With ``allow_404=True`` a 404 yields ``None``."""
        resp = self.get(url, **kwargs)
        if resp.status_code == 404:
            return None
        return resp.json()
