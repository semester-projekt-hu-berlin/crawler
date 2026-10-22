"""Comment text cleaning. Removes markup and @mentions of other users."""

from __future__ import annotations

import re
import unicodedata

from bs4 import BeautifulSoup

# "@Birne Helene: ..." at the very start of a reply (taz style, names may contain spaces).
_LEADING_MENTION = re.compile(r"^\s*@[^\n:@]{1,40}:\s*")
# "@username" anywhere (not e-mail addresses: the @ must not follow a word character).
_INLINE_MENTION = re.compile(r"(?<![\w.])@[\w.\-]{2,40}\w")
_SPACES = re.compile(r"[ \t ]+")
_NEWLINES = re.compile(r"\n{3,}")


def html_to_text(html: str) -> str:
    """Convert a comment HTML fragment to plain text, keeping line breaks."""
    soup = BeautifulSoup(html, "lxml")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for p in soup.find_all("p"):
        p.insert_after("\n\n")
    return soup.get_text()


def strip_mentions(text: str) -> str:
    text = _LEADING_MENTION.sub("", text)
    return _INLINE_MENTION.sub("", text)


def clean_text(text: str) -> str:
    """Normalise whitespace and unicode, strip @mentions."""
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("​", "")
    text = strip_mentions(text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return _NEWLINES.sub("\n\n", text).strip()
