"""Registry of comment sources."""

from __future__ import annotations

from crawler.sources.achgut import Achgut
from crawler.sources.base import Source, StreamSource
from crawler.sources.blick import Blick
from crawler.sources.bluesky import Bluesky
from crawler.sources.caschy import Caschy
from crawler.sources.cicero import Cicero
from crawler.sources.derstandard import DerStandard
from crawler.sources.freitag import Freitag
from crawler.sources.lemmy import Feddit
from crawler.sources.mastodon import Mastodon
from crawler.sources.tagesschau import Tagesschau
from crawler.sources.taz import Taz
from crawler.sources.tichyseinblick import Netzpolitik, Ruhrbarone, TichysEinblick

SOURCES: dict[str, type[Source] | type[StreamSource]] = {
    cls.id: cls
    for cls in (
        # Germany
        Tagesschau,
        Taz,
        Freitag,
        TichysEinblick,
        Cicero,
        Achgut,
        Netzpolitik,
        Ruhrbarone,
        Caschy,
        Bluesky,
        Mastodon,
        Feddit,
        # Austria / Switzerland (capped)
        DerStandard,
        Blick,
    )
}

__all__ = ["SOURCES", "Source", "StreamSource"]
