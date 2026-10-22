# Meinungspuls · Crawler

Component 1 of the HU Berlin semester project **"Stimmungsanalyse im Web"** (product name *Meinungspuls*).
The crawler continuously collects **reader comments** under German-language online news articles and stores
them in Firestore. The [analysis](https://github.com/semester-projekt-hu-berlin/analysis) component then runs
entity recognition and sentiment analysis on them; the API and frontend only ever show aggregates.

The data contract is [`infrastructure/SCHEMA.md`](https://github.com/semester-projekt-hu-berlin/infrastructure/blob/main/SCHEMA.md).
The crawler writes `sources`, `articles`, `comments` and updates `meta/stats` (`lastCrawlAt`, `comments`,
`articles`, `sources`).

## Sources

| id | site | country | articles from | comments from | volume |
|---|---|---|---|---|---|
| `derstandard` | [DER STANDARD](https://www.derstandard.at) | AT | news sitemap `/sitemaps/news.xml` | public forum GraphQL API `capi.ds.at/forum-serve-graphql` | very high (often hundreds per article) |
| `taz` | [taz](https://taz.de) | DE | news sitemap `/sitemap-google-news.xml` | server-rendered comment list in the article HTML | low to medium |
| `blick` | [Blick](https://www.blick.ch) (German edition) | CH | news sitemap `/news.xml` | public Viafoura comments API `livecomments.viafoura.co` | medium |

### Why each source is allowed and how its comments are fetched

All requests carry the User-Agent
`MeinungspulsBot/0.1.0 (+https://github.com/semester-projekt-hu-berlin/crawler; HU Berlin student project)`.
Our product token `MeinungspulsBot` is not named in any of the robots.txt files, so the `User-agent: *` group
applies. `python -m crawler check-robots` prints the live decision for every endpoint we use.

**derStandard** (`derstandard.at`)

- `www.derstandard.at/robots.txt`, group `*`: only `Disallow: /profil/` (user profiles, which we never fetch),
  plus `Crawl-delay: 1`. Article pages and the news sitemap (listed as `Sitemap:`) are allowed.
- `capi.ds.at/robots.txt` answers `403 {"message":"Forbidden"}` (an API gateway that only serves API routes).
  Per [RFC 9309 §2.3.1.3](https://www.rfc-editor.org/rfc/rfc9309#section-2.3.1.3) a 4xx robots.txt means
  "unavailable": crawlers may access the resources. The forum is shown to every anonymous visitor of an
  article; no login, no paywall.
- How: for each article we send the two GET requests the website itself sends: `GetForumInfo(contextUri)`
  returns the forum id and the posting count, then `ThreadsByForumQuery(id, first: Max)` returns the root
  postings with nested replies, paged by cursor. Both are Apollo *persisted queries* (only known query hashes
  are accepted). The hashes are constants in `sources/derstandard.py` and can be overridden with
  `DERSTANDARD_HASH_FORUMINFO` / `DERSTANDARD_HASH_THREADS` if derStandard redeploys its frontend (the error
  message says so). Upvotes = the "positive" reaction count. Live tickers (`/jetzt/livebericht/`) are skipped.

**taz** (`taz.de`)

- `taz.de/robots.txt`, group `*`: `Disallow: /openads` and one hashed path. Everything else is allowed. taz
  has no paywall (voluntary payment model).
- How: comments are part of the article HTML (`<li class="comment" data-cid=...>`, replies nested inside their
  parent). One GET of the article page per crawl. taz shows no like counts, so `upvotes` is `null`.

**Blick** (`blick.ch`)

- `www.blick.ch/robots.txt`, group `*`: disallows search, ads, `*.json`, archive etc.; article pages and the
  news sitemap are allowed. `livecomments.viafoura.co/robots.txt` and `tyrion.viafoura.co/robots.txt` return
  404, i.e. no restrictions (RFC 9309). Comments are visible without login.
- How: the article page contains the Viafoura container id (`<vf-conversations vf-container-id=...>`). We
  resolve it to a content container (`/contentcontainer/id`), then read the comments with replies
  (`limit=100`, paged via `starting_from`). French-language pages (`/fr/`) are skipped. Upvotes = likes.

### Rejected sources

| site | reason |
|---|---|
| spiegel.de | Comments are switched off on all articles we checked (`"isCommentsEnabled": false` in the page data). |
| welt.de | Comments come from `hua-production.la.welt.de/la-hua-api/...`; that host's robots.txt is `User-agent: * Disallow: /`. Also `Disallow: /api/` on www.welt.de. |
| zeit.de | Bot protection answers every request from a non-browser client with 403 (including plain article pages). Additionally `Disallow: */comment-thread?` covers the comment endpoint. Not circumvented. |
| faz.net | Comment endpoint `/external/comment/asset-comment-info/` is covered by `Disallow: */asset-comment-info/*`; `/json/` is disallowed too. Many articles are paywalled (F+). |
| tagesspiegel.de | Comments load from `/community-comments/...`, which is `Disallow: /community-comments`. |
| heise.de | Forum is `Disallow: /forum/*` (only the forum index `/forum/` is allowed). |
| golem.de | Forum lives on `forum.golem.de`, robots.txt `Disallow: /`. |
| 20min.ch | `Disallow: /*comment/` for all user agents. |
| krone.at | `Disallow: /forum/*`. |
| telepolis.de | `Disallow: /forum/`. |
| focus.de, n-tv.de, t-online.de | No public comment section on article pages (no comment markup and no comment requests). |

## Data protection and ethics

- **No author data.** Adapters never read user names, user IDs, avatars or profile URLs; the `Comment` model has
  no field for them (a test enforces this). `@mentions` of other users are stripped from the text
  (`@Name:` at the start of a reply and inline `@username`; e-mail addresses are kept).
- Only what the analysis needs is stored: text, timestamps, thread structure (`parentId`), upvotes.
- Only public content: no login, no cookies, no paywall circumvention, no bot-protection circumvention.
- Comment text is internal to the pipeline (see SCHEMA.md); the public API only serves aggregates.
- **Politeness:** robots.txt is checked (with wildcard support) before *every* request, including redirect
  targets. At most one request every 1.5 s per host (more if `Crawl-delay` says so), one worker per source,
  retries with exponential backoff on 429/5xx honouring `Retry-After`. Revisits back off as articles age.

## Architecture

```
news sitemap ──► discover ─┐
                           ├─► candidates ──► Source.fetch(article) ──► new comments ──► Firestore (REST)
Firestore: articles of the ┘   (newest first,     (robots + rate limit)    (exists:false      comments/{id}
last 3 days (revisits)          max N/source)                               precondition)     articles/{id}
                                                                                              sources/{id}
                                                                                              meta/stats
```

```
src/crawler/
  cli.py            argparse CLI (run, sources, check-robots)
  pipeline.py       orchestration, revisit logic, idempotent writes, run summary
  sources/
    base.py         Source interface: canonical_url(), discover(), fetch()
    derstandard.py  GraphQL forum API
    taz.py          HTML comment list
    blick.py        Viafoura API
  discovery.py      Google News sitemap parser, JSON-LD/breadcrumb article metadata
  http.py           PoliteSession: robots check, per-host throttle, retries
  robots.py         robots.txt via urllib.robotparser + RFC 9309 wildcard matching
  firestore.py      Firestore REST client (batchGet, runQuery, batchWrite)
  ids.py            deterministic IDs, Berlin day keys, date parsing
  text.py           text cleaning, @mention stripping
  models.py         ArticleRef, Comment, FetchResult (no author fields)
```

**Adding a source:** subclass `Source` in `src/crawler/sources/<id>.py` (set `id`, `name`, `domain`, `country`,
`news_sitemap`; implement `canonical_url()` and `fetch()`), register it in `sources/__init__.py`, add a fixture
based test and document the robots.txt situation above.

### IDs and idempotency

- `articleId = sha1(canonicalUrl)[:20]`. The canonical URL is the source's **ID-based permalink**
  (`https://www.derstandard.at/story/<id>`, `https://taz.de/!<id>/`, `https://www.blick.ch/<id>`). These redirect
  to the current article URL, and unlike slug URLs they do not change when an editor changes the headline,
  so an article never gets two IDs.
- `commentId = sha1(sourceId + ":" + nativeCommentId)[:20]` with the source's own comment ID.
- Comments are written with `documents:batchWrite` and the precondition `currentDocument.exists = false`.
  Existing comments (possibly already analysed) are never overwritten, `analyzed: false` is only set on
  insert, and reruns never create duplicates. Counters (`commentCount`, `meta/stats.comments/articles`) are
  incremented by the number of actually inserted documents.
- `articles.lastCommentCrawlAt` is only advanced after all comment writes for the article succeeded.

### Revisits

Every run takes the articles of the last `--since-hours` from the news sitemap plus all stored articles of the
last 3 days (`articles` where `sourceId == X and publishedAt >= now-3d`, uses the existing composite index).
An article is crawled again if its last crawl is at least 1 h (article < 12 h old), 3 h (< 36 h) or 6 h (older)
ago. On a revisit only comments published after `lastCommentCrawlAt - 60 min` are written (the overlap catches
comments that appear late because of moderation). Consequence: `upvotes` is a snapshot taken when the comment
was first stored.

## Running locally

Python 3.11+.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# no Firestore access needed: crawl a few articles and dump comments to a file
python -m crawler run --sources taz,blick --max-articles 5 --dry-run --out comments.jsonl

# real run
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json   # never commit this file
python -m crawler run --sources all --max-articles 150 --since-hours 48

python -m crawler sources        # list sources
python -m crawler check-robots   # live robots.txt decisions for all endpoints
pytest && ruff check . && ruff format --check .
```

CLI options of `run`: `--sources all|id,id`, `--max-articles N` (per source, new + revisited, default 500),
`--since-hours H` (sitemap window, default 48), `--time-budget-minutes M`, `--dry-run`, `--out FILE`
(dry run only), `--summary-json FILE`, plus global `--log-level` and `--log-format text|json`.

At the end of each run a summary table (per source: discovered, revisit candidates, crawled, new articles,
comments seen, new comments, failures, duration) is printed; in GitHub Actions it is also added to the job
summary.

### Environment variables

| variable | purpose |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT` | full service account JSON key as a string (used in CI) |
| `GOOGLE_APPLICATION_CREDENTIALS` | path to the service account key file (local runs) |
| `FIRESTORE_PROJECT` / `FIRESTORE_DATABASE` | default `meinungspuls-hu` / `(default)` |
| `CRAWLER_MIN_DELAY` | minimum seconds between requests per host, default `1.5` |
| `CRAWLER_USER_AGENT` | override the User-Agent (keep it descriptive) |
| `DERSTANDARD_HASH_FORUMINFO`, `DERSTANDARD_HASH_THREADS` | override derStandard persisted query hashes |
| `LOG_LEVEL`, `LOG_FORMAT` | logging defaults (`INFO`, `text`) |

Firestore is accessed **only over the REST API** (`google.oauth2.service_account` +
`google.auth.transport.requests.AuthorizedSession`), not gRPC.

## Schedule

`.github/workflows/crawl.yml` runs every 2 hours (minute 17, UTC) and on manual dispatch (inputs: sources,
max articles, since hours). It uses the organisation secret `FIREBASE_SERVICE_ACCOUNT`, never runs two crawls
concurrently and stops starting new articles after 95 minutes. `.github/workflows/ci.yml` runs ruff (lint +
format) and pytest on Python 3.11 and 3.13 for every push and pull request.

## Known limitations

- derStandard's persisted query hashes may change when their frontend is redeployed; the run then logs
  `PersistedQueryError` for every derStandard article. Update the hashes (browser dev tools, network tab,
  filter `forum-serve-graphql`) via the env vars or in `sources/derstandard.py`.
- Comments deleted by the site after we stored them are not removed from Firestore.
- Upvotes are not updated after the first insert (saves writes).
- derStandard produces by far the most comments (easily tens of thousands of writes per day); keep an eye on
  Firestore quotas.
