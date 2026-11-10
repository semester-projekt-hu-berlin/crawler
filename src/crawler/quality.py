"""Cheap content filters applied before a comment is stored.

Every stored comment costs one Firestore write here and several reads and writes in the
analysis, so text that cannot carry an opinion is dropped first:

* ``too_short``: fewer than ``MIN_WORDS`` words once links are removed ("+1", "💚", "Moin.", a
  bare link);
* ``foreign``: clearly not German (posts on social media carry a self-declared language, and
  unmoderated blogs collect English and Asian spam);
* ``link_card``: a social media post that only repeats the headline and teaser of the page it
  links to (news bots and "share" buttons: a headline is not an opinion).

All checks are deliberately conservative: when in doubt the comment is kept. No language
detection library is used; German is recognised by its function words.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

MIN_WORDS = 2
# Texts with at least this many words must contain a German function word.
FOREIGN_MIN_WORDS = 10
# Shorter texts (from this many words on) are dropped only if they are clearly English.
ENGLISH_MIN_WORDS = 5

# Links, also without scheme and truncated by the client ("taz.de/Lehrer-gegen...").
_URL = re.compile(r"https?://\S+|www\.\S+|\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}/\S*", re.IGNORECASE)
_HASHTAG = re.compile(r"#\w+")
_WORD = re.compile(r"[^\W\d_]{2,}")


def _wordset(words: str) -> frozenset[str]:
    return frozenset(words.split())


# Function words that are frequent in German and rare elsewhere ("die", "war", "was", "so", "man",
# "in", "an", "am", "also", "hat", "da", "des", "um", "es", "du" also are words of other languages
# and are left out on purpose).
_GERMAN = _wordset(
    "aber alle alles als auch auf aus bei beim bis bitte bist dabei dafür damit danke dann das dass dazu "
    "dem den denn der deren deshalb dich diese diesem diesen dieser dieses dir doch durch eigentlich ein "
    "eine einem einen einer eines einfach etwas euch für ganz gar gegen genau gerade gibt gut habe haben "
    "hatte heute hier ich ihm ihn ihr ihre immer ins ist jetzt kann kein keine können könnte leider machen "
    "macht mehr mein meine mich mir mit muss müssen nach natürlich nein nicht nichts noch nur oder ohne "
    "schon sehr sein seine seit selbst sich sie sind soll sollen sollte über und uns unter viel viele "
    "vielleicht vom von vor wegen weil welche wenn werden wie wieder wir wird wirklich wohl wollen wurde "
    "wurden würde zum zur zwischen"
)
_ENGLISH = _wordset(
    "the and of to is are was were you your that this it for with not have has my me we they their be been "
    "from but what just about will would can so if all there who when which how its our out get more"
)
_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def words(text: str) -> list[str]:
    """Words of at least two letters, links excluded (hashtags count as words)."""
    return _WORD.findall(_URL.sub(" ", text or ""))


def too_short(text: str, min_words: int = MIN_WORDS) -> bool:
    return len(words(text)) < min_words


def _letters_latin(text: str) -> bool:
    """False if most letters are not Latin script (Cyrillic, Hangul, CJK, Arabic, …)."""
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 10:
        return True
    latin = sum(1 for ch in letters if "LATIN" in unicodedata.name(ch, ""))
    return latin >= 0.5 * len(letters)


def foreign(text: str) -> bool:
    """True if the text is clearly not German.

    Long texts (``FOREIGN_MIN_WORDS`` words or more) without a single German function word, texts
    of ``ENGLISH_MIN_WORDS`` or more words with several English and no German function words,
    and texts mostly written in a non-Latin script. Short German remarks without function words
    ("Kanzler Merz, Ilse Aigner.") are kept.
    """
    if not _letters_latin(text):
        return True
    ws = [w.lower() for w in words(text)]
    if len(ws) < ENGLISH_MIN_WORDS:
        return False
    german = sum(1 for w in ws if w in _GERMAN)
    if german:
        return False
    if len(ws) >= FOREIGN_MIN_WORDS:
        return True
    return sum(1 for w in ws if w in _ENGLISH) >= 2


def _main_title(title: str) -> str:
    """The headline without the site name some pages append ("… - WELT", "… | tagesschau.de")."""
    parts = [p.strip() for p in re.split(r"\s+[|–—-]\s+", title or "") if p.strip()]
    return max(parts, key=len) if parts else ""


def _norm(word: str) -> str:
    """Lower case, umlauts as in URL slugs ("Kürzungspläne" -> "kuerzungsplaene")."""
    return word.lower().translate(_TRANSLIT)


def repeats_link_card(text: str, title: str = "", description: str = "", links: Iterable[str] = ()) -> bool:
    """True if a post says nothing beyond the headline/teaser of the page it links to.

    ``title``/``description`` come from the link preview attached to the post (Bluesky external
    embed, Mastodon preview card), ``links`` are the linked URLs: news URLs usually spell out the
    headline ("/politik/merz-will-bei-eu-gipfel-ueber-ukraine-hilfe-sprechen"). Links inside the
    text count as well; hashtags are ignored. A post is a repeat if at most one word in six (at
    least one, at most three) is not part of the preview or a link, or if it starts with the
    headline and less than a third of its words are its own. Posts with fewer than three words are
    left to ``too_short``.
    """
    post = [_norm(w) for w in words(_HASHTAG.sub(" ", text or ""))]
    if len(post) < 3:
        return False
    urls = [*links, *_URL.findall(text or "")]
    card = {_norm(w) for w in _WORD.findall(f"{title} {description} {' '.join(urls)}")}
    if not card or not (title or description or urls):
        return False
    own = sum(1 for w in post if w not in card)
    if own <= max(1, min(3, len(post) // 6)):
        return True
    head = " ".join(_norm(w) for w in words(_main_title(title)))
    return len(head) >= 20 and " ".join(post).startswith(head) and own < len(post) / 3


def reject_reason(text: str) -> str | None:
    """Why a comment text is not worth storing (``None``: keep it)."""
    if too_short(text):
        return "too_short"
    if foreign(text):
        return "foreign"
    return None
