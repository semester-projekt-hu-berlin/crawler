# Meinungspuls · Crawler

Component 1 of the HU Berlin semester project **"Stimmungsanalyse im Web"** (product name *Meinungspuls*).
The crawler continuously collects **reader comments** under German-language online news articles and stores
them in Firestore. The [analysis](https://github.com/semester-projekt-hu-berlin/analysis) component then runs
entity recognition and sentiment analysis on them; the API and frontend only ever show aggregates.

The data contract is [`infrastructure/SCHEMA.md`](https://github.com/semester-projekt-hu-berlin/infrastructure/blob/main/SCHEMA.md).
The crawler writes `sources`, `articles`, `comments` and updates `meta/stats` (`lastCrawlAt`, `comments`,
`articles`, `sources`).

## Sources

| id | site | type | country | leaning (rough) | articles / posts from | comments from | est. comments per day |
|---|---|---|---|---|---|---|---|
| `tagesschau` | [meta.tagesschau.de](https://meta.tagesschau.de) (ARD) | news | DE | public broadcaster, centre | front page listing `/?page=N` | comment list in the discussion page HTML | VOL_tagesschau |
| `tichyseinblick` | [Tichys Einblick](https://www.tichyseinblick.de) | news | DE | conservative / right-liberal | WordPress REST `/wp-json/wp/v2/posts` | site-wide WordPress REST comment feed | VOL_tichyseinblick |
| `freitag` | [der Freitag](https://www.freitag.de) | news | DE | left | Google News sitemap `/@@google-news-rss` | Plone REST `…/@comments` | VOL_freitag |
| `taz` | [taz](https://taz.de) | news | DE | left / green | news sitemap `/sitemap-google-news.xml` | comment list in the article HTML | VOL_taz |
| `derstandard` | [DER STANDARD](https://www.derstandard.at) | news | AT | left-liberal | news sitemap `/sitemaps/news.xml` | public forum GraphQL API `capi.ds.at` | VOL_derstandard |
| `blick` | [Blick](https://www.blick.ch) (German edition) | news | CH | tabloid, centre | news sitemap `/news.xml` | public Viafoura API `livecomments.viafoura.co` | VOL_blick |
| `mastodon` | [norden.social](https://norden.social), [troet.cafe](https://troet.cafe) | social | DE | tends left | public local timelines (API) | the posts themselves | VOL_mastodon |
| `bluesky` | [Bluesky](https://bsky.app), German-language sample | social | DE | tends centre-left | public AppView search (API) | the posts themselves | VOL_bluesky |

The leaning column is a rough orientation for interpreting results (commenters are not the editorial line);
the mix was chosen to cover the spectrum. Estimates are per day at the 12-hour schedule (see
"Firestore cost" below).

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

**tagesschau** (`meta.tagesschau.de`, ARD's official comment platform for tagesschau.de)

- `meta.tagesschau.de/robots.txt`, group `*`: `Disallow: */comment/` (single-comment permalinks), `/comment/reply/`,
  `/user/...`, `/search/`, `/admin/`, `/node/add/`. The front page (`/?page=N`) and the discussion pages
  (`/id/<n>/<slug>?page=N`) are allowed. Reading needs no login.
- How: the front page lists discussions (newest first) with opening time **and comment count**, so unchanged
  discussions are skipped without any request. A discussion page holds ~15 threads (oldest first) with nested
  replies; we page with `?page=N`. The parent of a reply comes from its "@name" parent link (the link text, a
  user name, is removed). No like counts (`upvotes = null`).

**Tichys Einblick** (`tichyseinblick.de`)

- `www.tichyseinblick.de/robots.txt`, group `*`: no rule covers `/wp-json/`. The WordPress REST API is public.
- How: `GET /wp-json/wp/v2/comments?after=…&per_page=100&_fields=id,post,parent,date_gmt,content` returns the
  newest approved comments of the whole site, so one run needs a handful of requests instead of one per article.
  `_fields` makes the server omit author name, avatar and author id. Article title/link/date come from
  `/wp-json/wp/v2/posts?include=…`. Comments are pre-moderated and released with their original timestamp, so
  the source re-reads a 12-hour overlap and drops already stored comments with a Firestore read (never a write).
  Article URL = WordPress shortlink `https://www.tichyseinblick.de/?p=<id>`.

**der Freitag** (`freitag.de`)

- `www.freitag.de/robots.txt`, group `*`: only `Disallow: /acl_users/session/` and
  `/acl_users/credentials_cookie_auth/` (login). Sitemaps `/sitemap.xml` and `/@@google-news-rss` are listed.
- How: the article page itself loads `<article>/@comments` (Plone REST API, JSON, anonymous `view_comments`).
  We read `comment_id`, `in_reply_to`, `creation_date`, `text`; `author_*` fields are ignored. `items_total`
  is the count for unchanged-skipping. The news sitemap has day-only dates, so `publishedAt` is midnight.

**Mastodon** (`norden.social`, `troet.cafe`)

- robots.txt of both instances, group `*`: only `/media_proxy/`, `/interact/` and
  `/api/v1/instance/domain_blocks` are disallowed. `GET /api/v2/instance` reports
  `timelines_access.live_feeds.local: "public"`, i.e. the operators publish the local timeline to logged-out
  visitors. Instance rules cover user behaviour, nothing forbids reading. API limit 300 requests / 5 min.
- How: `/api/v1/timelines/public?local=true&limit=40`, paged back with `max_id` until the previous run. Kept:
  public, `language = de`, no boosts, no bots, and only accounts that have **not** opted out of indexing
  (`noindex = false` and `indexable != false`); on norden.social that is roughly a quarter of the posts. Mentions
  are removed from the text. Posts are grouped into one pseudo-article per instance and day (see SCHEMA.md).

**Bluesky** (`api.bsky.app`)

- `api.bsky.app/robots.txt`: "Crawling the public parts of the API is allowed. HTTP 429 ("backoff") status codes
  are used for rate-limiting. Up to a handful concurrent requests should be ok." (`User-agent: * Allow: /`).
  We send one request at a time, every 1.5 s at most.
- How: German Bluesky has ~100 posts per minute, far more than needed, so each run takes a **time-spread
  sample**: the time since the last run is split into 1-hour windows and the newest 100 posts of each window are
  read (`searchPosts?q=die&lang=de&sort=latest&since=…&until=…`; the search needs a term, "die" is the most
  frequent German word). Authors with the `!no-unauthenticated` label (opted out of logged-out visibility) are
  skipped. Timestamp = server `indexedAt`. Mention facets are cut out. No handles, DIDs or display names.

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
| focus.de | Article pages have a comment button, but the comments load only after the consent/JS flow; no public, documented endpoint could be identified, so not used. |
| n-tv.de, t-online.de | No public comment section on article pages (no comment markup and no comment requests). |
| fr.de, merkur.de, tz.de (Ippen) | robots.txt group `*`: `Disallow: /` (only `/ueber-uns/` and `/advertorials/` allowed). |
| stern.de | Bot protection (403 for non-browser clients). |
| sueddeutsche.de, waz.de (Funke) | Comments are served by `*.engagently.com`; its robots.txt is unreachable (connection closed), which RFC 9309 treats as complete disallow. |
| ksta.de | Public comment API (`community-api.kdm.koeln`, robots.txt 404) works, but very few comments per article; not worth an adapter for now (easy to add). |
| cicero.de | Drupal site whose comments we could not find in the pages without login; no endpoint. |
| jungewelt.de | `Disallow: /comment.php` and `/leserbrief/`. |
| kicker.de | `Disallow: /news/fussball*` (and the other sports) plus `/kickerapi*`. |
| transfermarkt.de | robots.txt allows everything, but the site answers non-browser clients with a bot challenge (HTTP 202 interstitial). Not circumvented. |
| jungefreiheit.de | WordPress REST comments require login (401). |
| social.tchncs.de | robots.txt `User-agent: * Disallow: /`. |
| chaos.social | Connection reset from our network; not reachable. |
| abgeordnetenwatch.de | The API (CC0 1.0) covers politicians, mandates, polls and votes but **no citizen questions**; the questions exist only as web pages. Useful for the analysis team (entity list of politicians), not as a comment source. |

## Data protection and ethics

- **No author data.** Adapters never read user names, user IDs, avatars or profile URLs; the `Comment` model has
  no field for them (a test enforces this). `@mentions` of other users are stripped from the text
  (`@Name:` at the start of a reply and inline `@username`; e-mail addresses are kept).
- Only what the analysis needs is stored: text, timestamps, thread structure (`parentId`), upvotes.
- Only public content: no login, no cookies, no paywall circumvention, no bot-protection circumvention.
- Social posts are treated exactly like comments: text, timestamp, reply structure. Opt-outs are honoured
  (Mastodon `noindex`/`indexable`, Bluesky `!no-unauthenticated`), bots and non-public posts are skipped.
- Comment text is internal to the pipeline (see SCHEMA.md); the public API only serves aggregates.
- **Politeness:** robots.txt is checked before *every* request (with `urllib.robotparser` plus our own RFC 9309 wildcard evaluation; both must allow), including redirect
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
  robots.py         robots.txt: urllib.robotparser + own RFC 9309 group/wildcard evaluation
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

### Revisits (incremental)

Every run takes the articles of the last `--since-hours` from the news sitemap plus all stored articles of the
last 3 days (`articles` where `sourceId == X and publishedAt >= now-3d`, existing composite index; only the
fields the revisit logic needs are selected). Candidates are ordered by recent activity (the newer of
`publishedAt` and `lastCommentAt`) and capped at `--max-articles`.

**When** an article is revisited:

| article age | revisit |
|---|---|
| never crawled | always |
| up to 24 h | every run (at most once per hour for manual runs) |
| 24 h to 3 days | once a day, in the morning run (runs starting before 12:00 UTC) |
| older than 3 days | never |

**What** a revisit costs. The crawler keeps three optional fields per article (documented in SCHEMA.md):
`knownCommentCount` (comment total the source reported at the last change), `lastCommentAt` and
`commentThreadId`.

1. *Count check.* derStandard (`GetForumInfo.totalPostingCount`) and Blick (Viafoura
   `total_visible_content`) expose the total in a small request. If it equals `knownCommentCount`, the revisit
   ends there: no thread pages, no Firestore writes. taz has no count endpoint, so the article page is fetched
   (all comments are in it) and compared.
2. *Newest first, stop early.* Both APIs return threads newest first (derStandard `sortOrder: ByTime`, Viafoura
   `sorted_by=newest`). Replies to old threads can appear on any page, so paging stops as soon as as many
   comments newer than `lastCommentCrawlAt` were seen as the count grew (without a stored count: at the first
   page that holds only older comments). Blick revisits also use smaller pages and skip the article page
   (thread id stored).
3. *Only new comments are sent.* Comments published before `lastCommentCrawlAt - 10 min` are never sent to
   Firestore. Exception: if the complete list was fetched and fewer new comments turned up than the count grew
   (comments released late from moderation keep their original timestamp), all fetched comments are sent once;
   the `exists: false` precondition skips stored ones.
4. *Article doc only on change.* The article is updated only if comments were inserted, the source count
   changed or the thread id was learned. Unchanged articles cost zero writes (their `lastCommentCrawlAt` stays
   as is; the daily slot bounds them to one cheap count check per day).

`upvotes` is a snapshot taken when the comment was first stored.

**HTTP.** Responses are requested gzip-compressed. Conditional requests were evaluated: the comment APIs send
no validators, taz ignores `If-Modified-Since` (its `Last-Modified` reflects the article, not the comments), the
sitemaps change within minutes (always `200` between 12-hourly runs) and Blick article pages, which do honour
ETags, are no longer fetched on revisits. So conditional requests would save nothing and are not used.

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

`.github/workflows/crawl.yml` runs every 12 hours (04:17 and 16:17 UTC) and on manual dispatch (inputs: sources,
max articles, since hours). It uses the organisation secret `FIREBASE_SERVICE_ACCOUNT`, never runs two crawls
concurrently and stops starting new articles after 95 minutes. `.github/workflows/ci.yml` runs ruff (lint +
format) and pytest on Python 3.11 and 3.13 for every push and pull request.

## Known limitations

- derStandard's persisted query hashes may change when their frontend is redeployed; the run then logs
  `PersistedQueryError` for every derStandard article. Update the hashes (browser dev tools, network tab,
  filter `forum-serve-graphql`) via the env vars or in `sources/derstandard.py`.
- Comments deleted by the site after we stored them are not removed from Firestore.
- Upvotes are not updated after the first insert (saves writes).
- A deleted and a new comment between two crawls leave the count unchanged; that new comment is picked up only
  once the count changes again.
- derStandard produces by far the most comments (easily tens of thousands of writes per day); keep an eye on
  Firestore quotas.
