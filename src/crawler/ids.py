"""Deterministic IDs and date helpers as defined in infrastructure/SCHEMA.md."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")


def sha1_id(value: str) -> str:
    """First 20 hex chars of sha1(value)."""
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:20]


def canonicalize_url(url: str) -> str:
    """Normalise an article URL: https, lowercase host, no query string or fragment.

    News sites put tracking parameters (``?ref=rss``, ``#ref=rss``) on links; the
    path alone identifies an article on all supported sources.
    """
    parts = urlsplit(url.strip())
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, "", ""))


def article_id(canonical_url: str) -> str:
    """``articleId = sha1(canonicalUrl)[:20]``."""
    return sha1_id(canonical_url)


def comment_id(source_id: str, native_comment_id: str | int) -> str:
    """``commentId = sha1(sourceId + ":" + nativeCommentId)[:20]``."""
    return sha1_id(f"{source_id}:{native_comment_id}")


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # Naive timestamps from German-language sites are local (Berlin/Vienna/Zurich share CET).
        dt = dt.replace(tzinfo=BERLIN)
    return dt.astimezone(UTC)


def berlin_day(dt: datetime) -> str:
    """``YYYY-MM-DD`` of a timestamp in Europe/Berlin."""
    return ensure_utc(dt).astimezone(BERLIN).strftime("%Y-%m-%d")


def parse_datetime(value: str | int | float | None) -> datetime | None:
    """Parse ISO-8601 strings (with ``Z`` or offset) or epoch milliseconds into aware UTC datetimes."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, UTC)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return ensure_utc(datetime.fromisoformat(text))
    except ValueError:
        return None
