"""Bundestag e-petitions forum (epetitionen.bundestag.de): public discussion of open petitions.

robots.txt: the server answers ``/robots.txt`` (after its cookie check) with 404, i.e. no
restrictions (RFC 9309); every request is still checked. The site sets a session cookie on the
first request (302 to ``/epet/rediraftercookiecheck.html`` and back); the HTTP session keeps it in
its cookie jar, nothing is logged in. The forum is readable without an account.

Stream source. Each run reads

1. the list of petitions open for co-signing (``petuebersicht/mz.nc.content.teaser-petitionen-table.
   $$$.status.2.page.<N>.batchsize.10.html``: title, category, id and forum post count), and takes the
   petitions with the most forum posts;
2. per petition the table of discussion threads (``forum.kommentare.table.$$$.page.0.html``: thread
   id, title, opening time, time of the last post);
3. for threads with posts since the previous run the answers by date
   (``forum/Beitrag_<thread>.alleAntwortenNachDatum.$$$.page.1.html``: id, date, time, text, reply
   path) and, if a thread itself was opened since then, its text from the petition's forum RSS
   (``/content/petitionen/…/Petition_<id>/forum.rss.xml``, the newest 10 posts; it has no dates).

Users appear as names or pseudonyms ("Nutzer1234567"); they are never read: the author line
(``p.author``) contributes only ``span.date``/``span.time``; quotes (``blockquote``, "Zitat: von
§§§<id>§§§") and the reply header "@ <name> | <date>" are removed. Moderator notes that replaced a
post ("DOPPELPOST …") are skipped. The petition (title) is the article.
"""

from __future__ import annotations

import html as htmllib
import re
from datetime import datetime
from typing import NamedTuple

from bs4 import BeautifulSoup
from lxml import etree

from crawler.ids import BERLIN, ensure_utc
from crawler.models import ArticleRef, Comment
from crawler.sources.base import StreamItem, StreamSource
from crawler.text import clean_text, html_to_text, remove_quotes

SITE = "https://epetitionen.bundestag.de"
LIST_URL = SITE + "/epet/petuebersicht/mz.nc.content.teaser-petitionen-table.$$$.status.2.page.{page}.batchsize.10.html"
LIST_PAGES = 8
MAX_PETITIONS = 20
_PET_RE = re.compile(r"^/petitionen/(_\d{4}/_\d{2}/_\d{2})/Petition_(\d+)\.nc\.html$")
_DT_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s*-?\s*(\d{1,2}):(\d{2})")
_BEITRAG_RE = re.compile(r"Beitrag_(\d+)")
_MASK_RE = re.compile(r"§§§\d*§§§")
# "@ Nutzer6638448 | gestern - 22:04" / "@ Max Muster | 24.10.2026 - 18:00" at the start of a reply
_REPLY_HEADER_RE = re.compile(r"^\s*@[^\n|]{0,80}\|[^\n]{0,40}?(?:\d{1,2}:\d{2})\s*", re.M)
MOD_NOTE = ("DOPPELPOST", "Bitte beachten Sie die Richtlinie", "Beitrag wurde von der Moderation")


class Petition(NamedTuple):
    petition_id: str
    path: str  # "_2026/_06/_08"
    title: str
    forum_posts: int


class Thread(NamedTuple):
    thread_id: str
    title: str
    opened_at: datetime | None
    last_post_at: datetime | None


def petition_url(p: Petition) -> str:
    return f"{SITE}/petitionen/{p.path}/Petition_{p.petition_id}.nc.html"


def petition_date(p: Petition) -> datetime | None:
    """Publication day from the path (``_2026/_06/_08``), midnight Berlin time."""
    try:
        y, m, d = (int(x.lstrip("_")) for x in p.path.split("/"))
        return ensure_utc(datetime(y, m, d, tzinfo=BERLIN))
    except ValueError:
        return None


def _berlin(text: str) -> datetime | None:
    m = _DT_RE.search(text or "")
    if not m:
        return None
    d, mo, y, h, mi = m.groups()
    return ensure_utc(datetime(int(y), int(mo), int(d), int(h), int(mi), tzinfo=BERLIN))


def parse_petition_list(page_html: str) -> list[Petition]:
    soup = BeautifulSoup(page_html, "lxml")
    out: list[Petition] = []
    for row in soup.select("tr"):
        link = row.select_one("a.to-petdetails[href]")
        m = _PET_RE.match(link["href"]) if link is not None else None
        if not m:
            continue
        category = row.select_one("td[data-label=Titel] strong")
        title = (link.get("title") or link.get_text(" ", strip=True)).strip()
        if category is not None and category.get_text(strip=True):
            title = f"{category.get_text(' ', strip=True)}: {title}"
        forum = row.select_one("td[data-label=Forum]")
        count = re.search(r"(\d+)\s*Beitr", forum.get_text(" ", strip=True)) if forum is not None else None
        out.append(Petition(m.group(2), m.group(1), title, int(count.group(1)) if count else 0))
    return out


def parse_threads(table_html: str) -> list[Thread]:
    soup = BeautifulSoup(table_html, "lxml")
    out: list[Thread] = []
    for branch in soup.select("div.discussion-branch"):
        h3 = branch.select_one("h3[id^=h3_]")
        if h3 is None:
            continue
        tid = h3["id"].removeprefix("h3_")
        head = branch.select_one(".col-md-8 p")
        opened = None
        if head is not None:
            date, time_ = head.select_one("span.date"), head.select_one("span.time")
            if date is not None and time_ is not None:
                opened = _berlin(f"{date.get_text(strip=True)} - {time_.get_text(strip=True)}")
        last = branch.select_one("a.letzter-beitrag")
        if tid.isdigit():
            out.append(
                Thread(
                    tid,
                    h3.get_text(" ", strip=True),
                    opened,
                    _berlin(last.get_text(" ", strip=True)) if last is not None else opened,
                )
            )
    return out


def _clean(fragment: BeautifulSoup) -> str:
    remove_quotes(fragment)
    for el in fragment.select("script, style"):
        el.decompose()
    text = _MASK_RE.sub("", html_to_text(str(fragment)))
    text = _REPLY_HEADER_RE.sub("", text)
    text = clean_text(text)
    return "" if any(note in text for note in MOD_NOTE) else text


def parse_answers(page_html: str, thread_id: str, source_id: str = "bundestag") -> list[Comment]:
    """Answers of one discussion thread (newest first). ``p.author`` is read for date/time only."""
    soup = BeautifulSoup(page_html, "lxml")
    out: list[Comment] = []
    for box in soup.select("div.antwortb[id^=a_]"):
        native_id = box["id"].removeprefix("a_")
        author = box.select_one("p.author")
        body = box.select_one(f"div.beitragtext-{native_id}")
        if not native_id.isdigit() or author is None or body is None:
            continue
        date, time_ = author.select_one("span.date"), author.select_one("span.time")
        published = _berlin(f"{date.get_text(strip=True)} - {time_.get_text(strip=True)}") if date and time_ else None
        path = next((el.get("data-href") or "" for el in box.select("[data-href*=Beitrag_]")), "")
        chain = [b for b in _BEITRAG_RE.findall(path) if b != native_id]
        parent = chain[-1] if chain else thread_id
        text = _clean(body)
        if published and text:
            out.append(Comment(source_id, native_id, text, published, native_parent_id=parent))
    return out


def parse_forum_rss(xml: bytes) -> dict[str, str]:
    """Post id -> text of the newest posts of a petition forum (the feed has no dates)."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)
    root = etree.fromstring(xml, parser=parser)
    out: dict[str, str] = {}
    if root is None:
        return out
    for item in root.iter("item"):
        m = re.search(r"einsprung\.(\d+)\.html", item.findtext("link") or "")
        if m:
            out[m.group(1)] = _clean(BeautifulSoup(htmllib.unescape(item.findtext("description") or ""), "lxml"))
    return out


class BundestagPetitionen(StreamSource):
    id = "bundestag"
    name = "Bundestag ePetitionen-Forum"
    domain = "epetitionen.bundestag.de"
    country = "DE"
    source_type = "news"  # petitions as articles (the data contract has no forum type)
    topic = "politics"

    def petitions(self) -> list[Petition]:
        found: dict[str, Petition] = {}
        for page in range(LIST_PAGES):
            try:
                rows = parse_petition_list(self.http.get(LIST_URL.format(page=page)).text)
            except Exception as exc:  # noqa: BLE001
                self.log.warning("petition list page %d failed: %s", page, type(exc).__name__)
                break
            if not rows:
                break
            for p in rows:
                found.setdefault(p.petition_id, p)
        ranked = sorted(found.values(), key=lambda p: p.forum_posts, reverse=True)
        return [p for p in ranked if p.forum_posts > 0][:MAX_PETITIONS]

    def fetch_since(self, since: datetime, until: datetime, max_items: int) -> list[StreamItem]:
        if max_items <= 0:
            return []
        per_petition: list[list[StreamItem]] = []
        for pet in self.petitions():
            base = f"{SITE}/petitionen/{pet.path}/Petition_{pet.petition_id}"
            ref = ArticleRef(
                url=petition_url(pet),
                source_id=self.id,
                title=pet.title,
                published_at=petition_date(pet),
                topics=["Petition", pet.title.split(":", 1)[0]],
            )
            comments: dict[str, Comment] = {}
            try:
                threads = parse_threads(self.http.get(f"{base}/forum.kommentare.table.$$$.page.0.html").text)
                active = [t for t in threads if t.last_post_at is None or t.last_post_at >= since]
                for t in active:
                    page = self.http.get(f"{base}/forum/Beitrag_{t.thread_id}.alleAntwortenNachDatum.$$$.page.1.html")
                    for c in parse_answers(page.text, t.thread_id, self.id):
                        if since <= c.published_at <= until:
                            comments.setdefault(c.native_id, c)
                opened = [t for t in active if t.opened_at and since <= t.opened_at <= until]
                if opened:
                    texts = parse_forum_rss(
                        self.http.get(
                            f"{SITE}/content/petitionen/{pet.path}/Petition_{pet.petition_id}/forum.rss.xml"
                        ).content
                    )
                    for t in opened:
                        if texts.get(t.thread_id):
                            body = f"{t.title}\n\n{texts[t.thread_id]}" if t.title else texts[t.thread_id]
                            comments.setdefault(t.thread_id, Comment(self.id, t.thread_id, body, t.opened_at))
            except Exception as exc:  # noqa: BLE001 - one petition must not stop the others
                self.log.warning("petition %s failed: %s", pet.petition_id, type(exc).__name__)
            if comments:
                ref.published_at = ref.published_at or min(c.published_at for c in comments.values())
                items = [
                    StreamItem(ref, c) for c in sorted(comments.values(), key=lambda c: c.published_at, reverse=True)
                ]
                per_petition.append(items)
        out: list[StreamItem] = []
        while len(out) < max_items and any(per_petition):
            for items in per_petition:
                if items and len(out) < max_items:
                    out.append(items.pop(0))
        return out
