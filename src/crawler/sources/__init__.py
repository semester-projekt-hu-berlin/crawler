"""Registry of comment sources."""

from __future__ import annotations

from crawler.sources.achgut import Achgut
from crawler.sources.base import Source, StreamSource
from crawler.sources.blick import Blick
from crawler.sources.bluesky import Bluesky
from crawler.sources.cicero import Cicero
from crawler.sources.derstandard import DerStandard
from crawler.sources.freitag import Freitag
from crawler.sources.mastodon import Mastodon
from crawler.sources.tagesschau import Tagesschau
from crawler.sources.taz import Taz
from crawler.sources.tichyseinblick import TichysEinblick

SOURCES: dict[str, type[Source] | type[StreamSource]] = {
    cls.id: cls
    for cls in (DerStandard, Taz, Blick, Tagesschau, Freitag, TichysEinblick, Cicero, Achgut, Mastodon, Bluesky)
}

__all__ = ["SOURCES", "Source", "StreamSource"]
