"""Registry of comment sources."""

from __future__ import annotations

from crawler.sources.base import Source
from crawler.sources.blick import Blick
from crawler.sources.derstandard import DerStandard
from crawler.sources.taz import Taz

SOURCES: dict[str, type[Source]] = {cls.id: cls for cls in (DerStandard, Taz, Blick)}

__all__ = ["SOURCES", "Source"]
