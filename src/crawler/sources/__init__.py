"""Registry of comment sources."""

from __future__ import annotations

from crawler.sources.achgut import Achgut
from crawler.sources.allmystery import Allmystery
from crawler.sources.appblogs import Ifun, IphoneTicker
from crawler.sources.base import Source, StreamSource
from crawler.sources.blick import Blick
from crawler.sources.bluesky import Bluesky
from crawler.sources.br24 import BR24
from crawler.sources.bundestag import BundestagPetitionen
from crawler.sources.caschy import Caschy
from crawler.sources.cicero import Cicero
from crawler.sources.computerbase import ComputerBase
from crawler.sources.derstandard import DerStandard
from crawler.sources.finanztip import Finanztip
from crawler.sources.freitag import Freitag
from crawler.sources.goingelectric import GoingElectric
from crawler.sources.jetstream import BlueskyJetstream
from crawler.sources.kuketz import Kuketz
from crawler.sources.lemmy import Feddit
from crawler.sources.mastodon import Mastodon
from crawler.sources.rbb24 import Rbb24
from crawler.sources.tagesschau import Tagesschau
from crawler.sources.taz import Taz
from crawler.sources.tichyseinblick import (
    ApolloNews,
    Appgefahren,
    Lawblog,
    Netzpolitik,
    Overton,
    Ruhrbarone,
    TichysEinblick,
)
from crawler.sources.wallstreet import WallstreetOnline

SOURCES: dict[str, type[Source] | type[StreamSource]] = {
    cls.id: cls
    for cls in (
        # Germany
        Tagesschau,
        BR24,
        Rbb24,
        Taz,
        Freitag,
        TichysEinblick,
        Cicero,
        Achgut,
        Netzpolitik,
        Ruhrbarone,
        ApolloNews,
        Overton,
        Lawblog,
        Caschy,
        IphoneTicker,
        Ifun,
        Appgefahren,
        ComputerBase,
        WallstreetOnline,
        Allmystery,
        Finanztip,
        GoingElectric,
        Kuketz,
        BundestagPetitionen,
        Bluesky,
        BlueskyJetstream,
        Mastodon,
        Feddit,
        # Austria / Switzerland (capped)
        DerStandard,
        Blick,
    )
}

__all__ = ["SOURCES", "Source", "StreamSource"]
