"""Comment text cleaning. Removes markup and @mentions of other users."""

from __future__ import annotations

import re
import unicodedata

from bs4 import BeautifulSoup

# "@Birne Helene: ..." at the very start of a reply (taz style, names may contain spaces).
_LEADING_MENTION = re.compile(r"^\s*@[^\n:@]{1,40}:\s*")
# Fediverse handles "@user@instance.tld"
_FEDI_HANDLE = re.compile(r"(?<![\w.])@[\w.\-]+@[\w.\-]+\w")
# "@username" anywhere (not e-mail addresses: the @ must not follow a word character).
_INLINE_MENTION = re.compile(r"(?<![\w.])@[\w.\-]{2,40}\w")
_SPACES = re.compile(r"[ \t ]+")
_SPACE_BEFORE_PUNCT = re.compile(r"(?<=\w) +([,;!?])")
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
    text = _FEDI_HANDLE.sub("", text)
    text = _LEADING_MENTION.sub("", text)
    return _INLINE_MENTION.sub("", text)


def clean_text(text: str) -> str:
    """Normalise whitespace and unicode, strip @mentions."""
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("​", "")
    text = strip_mentions(text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    return _NEWLINES.sub("\n\n", text).strip()


def remove_quotes(fragment) -> list[dict]:  # noqa: ANN001 - bs4 Tag
    """Remove quoted posts (also nested ones) from a parsed fragment; return their attributes in document order.

    Attributes are read before anything is removed, so the caller can take the quoted post's id
    from them (a nested quote is gone once its outer quote is removed).
    """
    quotes = fragment.select("blockquote")
    attrs = [dict(q.attrs) for q in quotes]
    for q in quotes:
        if q.find_parent("blockquote") is None:
            q.decompose()
    return attrs
