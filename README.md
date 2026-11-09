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
| `tagesschau` | [meta.tagesschau.de](https://meta.tagesschau.de) (ARD) | news | DE | public broadcaster, centre | front page listing `/?page=N` | comment list in the discussion page HTML | ~1,000 (≈4 discussions/day, open for about a day) |
| `br24` | [BR24](https://www.br.de/nachrichten/) (Bayerischer Rundfunk) | news | DE (Bavaria) | public broadcaster, centre | news sitemap `/nachrichten/newsSitemap` | Coral 9 GraphQL (persisted queries) on `comments.br24.de` | 600 (cap; ~2,000 available) |
| `rbb24` | [rbb24](https://www.rbb24.de) (Rundfunk Berlin-Brandenburg) | news | DE (Berlin/Brandenburg) | public broadcaster, centre | section pages (`/`, `/politik/`, …) | `<article>.commentthreads.jsn` (JSON) | ~150–250 (cap 250) |
| `tichyseinblick` | [Tichys Einblick](https://www.tichyseinblick.de) | news | DE | conservative / right-liberal | WordPress REST `/wp-json/wp/v2/posts` | site-wide WordPress REST comment feed | ~950 (cap 1,100) |
| `cicero` | [Cicero](https://www.cicero.de) | news | DE | conservative-liberal | RSS `/rss.xml` | comment list in the article HTML | ~250 |
| `achgut` | [Achse des Guten](https://www.achgut.com) | news | DE | right-libertarian | RSS `/rss` | reader letters in the article HTML | ~150 |
| `freitag` | [der Freitag](https://www.freitag.de) | news | DE | left | Google News sitemap `/@@google-news-rss` | Plone REST `…/@comments` | ~20 |
| `taz` | [taz](https://taz.de) | news | DE | left / green | news sitemap `/sitemap-google-news.xml` | comment list in the article HTML | ~300 |
| `derstandard` | [DER STANDARD](https://www.derstandard.at) | news | AT | left-liberal | news sitemap `/sitemaps/news.xml` | public forum GraphQL API `capi.ds.at` | ~200–650 (cap 200 strict; uncapped ~15,000–20,000) |
| `blick` | [Blick](https://www.blick.ch) (German edition) | news | CH | tabloid, centre | news sitemap `/news.xml` | public Viafoura API `livecomments.viafoura.co` | ≤100 (capped; uncapped ~3,000) |
| `mastodon` | 17 German instances (troet.cafe, mastodon.de, norden.social, nrw.social, ruhr.social, bonn.social, …) | social | DE | tends left | public local timelines (API) | the posts themselves | ~700 (cap 800, after opt-out filter) |
| `feddit` | [feddit.org](https://feddit.org) (Lemmy) | social | DE | mixed, tends left | public Lemmy API, local communities | comments under the posts | ~150–200 |
| `caschy` | [Caschys Blog](https://stadt-bremerhaven.de) | news | DE | tech | RSS `/feed/` | WordPress comments in the article HTML | ~200 |
| `netzpolitik` | [netzpolitik.org](https://netzpolitik.org) | news | DE | digital rights, left-liberal | WordPress REST | site-wide WordPress REST comment feed | ~10–15 |
| `ruhrbarone` | [Ruhrbarone](https://www.ruhrbarone.de) | news | DE | regional politics, liberal | WordPress REST | site-wide WordPress REST comment feed | ~10 |
| `bluesky` | [Bluesky](https://bsky.app), German-language searches + sample + feed "Deutsche Politik" | social | DE | tends centre-left | public AppView search and `getFeed` (API) | the posts themselves | ~2,000 search + ~200–300 feed (cap 2,300/day) |
| `bluesky_jetstream` | [Bluesky](https://bsky.app) live firehose (Jetstream) | social | DE | mixed | 5-minute live window per run | German posts (`langs` contains `de`) | ~600 (cap 700) |
| `apollo` | [Apollo News](https://apollo-news.net) | news | DE | right-conservative | WordPress REST | site-wide WordPress REST comment feed, 10 % sample | ~250 (of ~4,000; cap 300) |
| `overton` | [Overton Magazin](https://overton-magazin.de) | news | DE | left / critical of the establishment | WordPress REST | site-wide WordPress REST comment feed | ~260 |
| `lawblog` | [lawblog](https://www.lawblog.de) (Udo Vetter) | news | DE | law / civil liberties | WordPress REST | site-wide WordPress REST comment feed | ~40 |
| `iphoneticker` | [iphone-ticker.de](https://www.iphone-ticker.de) | news | DE | tech (Apple, apps) | RSS `/feed/` (with comment counts) | per-article comment feed, article HTML as fallback | ~40–270 (cap 250) |
| `ifun` | [iFun.de](https://www.ifun.de) | news | DE | tech (Apple, apps) | RSS `/feed/` (with comment counts) | per-article comment feed, article HTML as fallback | ~30–200 (cap 150) |
| `appgefahren` | [appgefahren.de](https://www.appgefahren.de) | news | DE | tech (apps) | WordPress REST | site-wide WordPress REST comment feed | ~90 |
| `computerbase` | [ComputerBase](https://www.computerbase.de) | news | DE | tech (hardware, software) | Atom `/rss/news.xml` | the news' forum thread (XenForo HTML) | ~430 (6 new threads per run, cap 400 strict) |
| `wallstreet` | [wallstreet-online.de](https://www.wallstreet-online.de) forum | news | DE | finance / retail investors | community start page (most active threads) | newest thread pages (HTML) | ~200 (cap 300) |
| `allmystery` | [Allmystery](https://www.allmystery.de/themen/pr) "Politik" forum | news (forum) | DE | mixed, lively debates on parties | category listing `/themen/pr` | one thread per party/politician, newest pages (HTML) | ~135 (cap 300) |
| `finanztip` | [Finanztip Community](https://www.finanztip.de/community/) (WoltLab) | news (forum) | DE | consumer finance, provider experiences | thread RSS (board 14 first) | page 1 of active threads (HTML) | ~40–60 (cap 200) |
| `goingelectric` | [GoingElectric](https://www.goingelectric.de/forum/) (phpBB) | news (forum) | DE | tech (electric cars, charging) | Atom feed `/forum/feed` + topic revisits | newest topic pages (HTML) | ~30–80 (cap 150) |
| `kuketz` | [Kuketz-Forum](https://www.kuketz-forum.de) (Discourse) | news (forum) | DE | tech (privacy, IT security, digital policy) | `/posts.json` | the posts themselves (`cooked`) | ~65 (cap 100) |
| `bundestag` | [ePetitionen-Forum](https://epetitionen.bundestag.de) (Deutscher Bundestag) | news (forum) | DE | citizens' petitions, mixed | petitions open for co-signing (most forum posts) | discussion threads per petition (HTML, RSS) | ~1–20 (cap 150; forums are quiet right now) |

The leaning column is a rough orientation for interpreting results (commenters are not the editorial line);
the mix was chosen to cover the spectrum. Estimates are per day at the 12-hour schedule (see
"Volume caps and Firestore cost" below).

**Political balance.** Among the comment-heavy news sources, the right of centre is over-represented (Tichys Einblick
~950/day, Apollo News, Achse des Guten, Cicero) against the left (taz, der Freitag, Overton). The public broadcasters
BR24 (Bavaria) and rbb24 (Berlin/Brandenburg) were added partly for that reason: their comment sections are moderated,
large and regionally different, and together with tagesschau they give a broad centre. Social media (Bluesky,
Mastodon) tends centre-left. When interpreting sentiment per party or person, compare sources (or weight them) rather
than reading the pooled numbers as "the" German opinion. Allmystery's party threads mix supporters and opponents in one
place; the Bundestag petitions forum is non-partisan by design.

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

**Cicero** (`cicero.de`)

- `www.cicero.de/robots.txt`, group `*`: only Drupal internals (`/core/`, `/profiles/`, `/admin/`,
  `/comment/reply/`, `/user/...`, `/search/`, `/node/add/`). Article pages (with `?page=N`) and `/rss.xml` are allowed.
- How: comments are part of the article HTML (`article.comment#comment-<n>`, `data-comment-timestamp`, subject +
  body, replies in `div.indented` after the parent). The `<h6>` with the user name is not read.

**Achse des Guten** (`achgut.com`)

- `www.achgut.com/robots.txt`, group `*`: disallows `/wp-admin/`, login, search and two single paths, and sets
  **`Crawl-delay: 10`**, which the crawler obeys (one request every 10 s).
- How: reader letters are in the article HTML (`li.comment#<n>`, `.comment-content`, replies nested in
  `ol.children`). Only the **date** of a letter is published, so a revisit checks the letters of the cutoff day
  with Firestore reads and writes only missing ones. Readers often address each other by name in the text; those
  names cannot be removed reliably (same as on the other sites).

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

**feddit.org** (Lemmy)

- robots.txt group `*`: only `/login`, `/settings`, `/create_*`, `/inbox`, `/admin`, `/search/` and similar are
  disallowed; **`Crawl-delay: 60`**, which the crawler obeys (one request per minute, ≤15 per run).
- How: `/api/v3/comment/list?type_=Local&sort=New` (public, no login), newest first until the previous run.
  German comments only (`language_id` de, or undetermined with German-looking text); deleted/removed comments and
  bot accounts are skipped; the `creator` object is never read; fediverse handles are removed from the text.
  The Lemmy post is the article. `type_=All` was dropped: only 1 of 50 comments was German.

**Caschys Blog** (`stadt-bremerhaven.de`)

- robots.txt group `*`: `Allow: /` plus search, `?replytocom=`, `/*/feed/` (this covers the site-wide comment feed
  `/comments/feed/`, so we do not use it), embeds and previews. The REST API is switched off (404).
- How: article list from `/feed/` (not covered by `/*/feed/`), comments from the article HTML (`li.comment`,
  `div#comment-<n>`, `time`, `.comment-body`, replies in `ul.children`).

**netzpolitik.org, Ruhrbarone** (WordPress REST, same code as Tichys Einblick)

- netzpolitik.org robots.txt disallows `/comments/` and `*/comments/` (WordPress comment feeds); the REST path
  `/wp-json/wp/v2/comments` is not covered. Ruhrbarone only disallows `/wp-admin/`. Ruhrbarone ignores `_fields`
  and sends author names; the parser never reads them. Both have only ~10–15 comments a day.

**Bluesky topic searches**: besides the hourly sample, every run searches ~60 rotating German terms
(`lang=de`, `sort=latest`, since the previous run): the 30 most-mentioned allowlisted entities from Firestore
(`entities`, by `mentions7d`) plus fixed terms for politics (Bundestag, Merz, AfD, SPD, …), companies (VW,
Siemens, Telekom, …), tech (KI, OpenAI, Apple, …) and TV (Tagesschau, ZDF, Markus Lanz, Maischberger, Tatort, …).
The unauthenticated search refuses cursor paging (403) and `#hashtag` queries (400), so it pages by time (`until` =
oldest post seen, ≤3 pages per term). Posts are deduplicated by URI and grouped into one pseudo-article per term
and day. Since 26 Oct 2026 the source is capped at 1,150 posts per run: 150 for the "Deutsche Politik" feed (see
below) and 1,000 for the searches (75 % targeted, 25 % sample), i.e. ~2,000 search posts a day instead of ~3,500.

**Mastodon**: 17 German instances whose local timeline is public (`timelines_access.live_feeds.local =
public`) and whose robots.txt allows the API (added on 23 Oct 2026: mastodontech.de, social.anoxinon.de, bonn.social,
nerdculture.de, machteburch.social, det.social; their robots.txt only disallows `/media_proxy/`, `/interact/` and the
domain block list, plus `GPTBot` entirely). det.social sometimes answers 429: the client backs off (Retry-After or
5/10/20 s) and then skips the instance for this run. swiss.social and wien.rocks are deliberately not used (German
focus). Above the cap of 400 posts per run (800 a day), every instance is thinned evenly.

**Bluesky Jetstream** (`jetstream{1,2}.us-{east,west}.bsky.network`)

- robots.txt answers 404 on all four instances (RFC 9309: no restrictions); checked before every connection.
  Jetstream is Bluesky's official public JSON firehose, no login or key.
- How: one WebSocket (`/subscribe?wantedCollections=app.bsky.feed.post`) for `JETSTREAM_LIVE_SECONDS` (default 300 s;
  the full firehose is ~50 events/s, of which ~1.8 German posts/s). Kept: `commit.operation == "create"` with `de` in
  `record.langs`, up to the cap. A cursor replay of the 12 h between runs would mean downloading the whole firehose
  for that time, so it is not used; `compress=true` needs Jetstream's zstd dictionary and is not used either.
- Robust: 15 s connect timeout, 30 s receive timeout (stall), failover to the next instance resuming from the last
  `time_us` cursor; errors never fail the run (what was collected is kept).
- Privacy: the DID is only held in memory. Authors are checked in batches of 25 with the public
  `app.bsky.actor.getProfiles`; authors with `!no-unauthenticated` (opted out of logged-out visibility, ~25–30 % of
  German posters) are dropped, and so are all posts of a batch whose lookup failed (fail closed). The comment id is
  `sha1("bluesky:" + post URI)`, the same as for the search source, so a post found by both is stored once.
  Timestamp = Jetstream receive time (`time_us`), not the client-set `createdAt`. No likes are known (`upvotes = null`).

**Apollo News, Overton Magazin, lawblog, appgefahren** (WordPress REST, same code as Tichys Einblick)

- robots.txt: Apollo disallows only `/trackback/`, `/wp-admin/`, `/wp-content/`, `/wp-includes/`, `xmlrpc.php`,
  `wp-config.php`; Overton and appgefahren have an empty `Disallow:`; lawblog has `Allow: /` with **`Crawl-delay: 20`**
  (obeyed). appgefahren is additionally throttled to one request per 5 s (`config.HOST_MIN_DELAY`).
- Apollo has ~2,000 comments per 12 h. We keep a **deterministic 10 % sample** (`sha1(comment id)`), so the kept
  comments are spread over the whole day and all articles, and the 12 h overlap of the next run selects exactly the
  same comments again (no duplicates, no extra writes). `ApolloNews.sample_share`, hard cap 150 per run (300 a day).

**iphone-ticker.de, iFun.de** (same publisher and WordPress theme)

- robots.txt group `*`: `/wp-admin/`, `/wp-includes/`, login, plugins, a few site paths (ifun: `/amzn/`, `/app/`,
  `/live/`) and **`Disallow: /*?`** (every URL with a query string, so no `?p=` shortlinks and no comment feed paging).
  `/feed/`, article pages and `<article>/feed/` are allowed. The REST API is not used (404 / earlier disallowed).
- How: the main feed `/feed/` gives title, categories (stored as `topics`, useful for PRODUCT entities) and the comment
  count `slash:comments`; an unchanged count costs no request at all. Otherwise the article's comment feed
  (`<wfw:commentRss>`, newest 25 comments, ~25 KB) is read; if it does not reach back to the previous crawl (or does
  not hold all comments on a first crawl), the article page (~250 KB, holds all comments) is parsed instead.
  `dc:creator`, the item title ("Von: <name>"), `.comment-author` and the reply links are never read; the "Als Antwort
  auf <name>." sentence of replies is removed and its `#comment-<id>` link becomes `parentId`. New articles are taken
  most discussed first. `topic` = `tech`.

**ComputerBase** (`computerbase.de`)

- robots.txt group `*`: disallows `/api/`, `/forum/posts/`, `/forum/members/`, `/forum/whats-new/`, `/forum/search/`,
  `/forum/goto/`, account/conversation pages, `/preisvergleich/`, `/suche/`. `/rss/news.xml`, news pages and
  `/forum/threads/…/page-N` are allowed.
- How: news from the Atom feed; the news page links its comment thread (`a.js-thread-link`), which is stored as
  `commentThreadId`. The thread is read newest page first (20 posts per page, at most 15 pages on a first crawl); a
  revisit stops at the first page with only older posts. The first post (news teaser) is skipped; quotes (which carry
  "<name> schrieb:") and `@mentions` are removed, a quote's `data-source="post: <id>"` becomes `parentId`;
  `data-author` is never read. Capped like derStandard: 6 new threads and 200 comments per run (strict).

**wallstreet-online.de** (forum)

- robots.txt group `*`: disallows ad paths, `/charts/`, `/_print/`, `/_rpc/`, `/userzentrum/`, `/realtime`,
  `/watchlist/`, `/portfolio/`, `/suche?`, `?q=`, `?orderBy=`. `/community` and `/diskussion/…` are allowed.
- How: stream source. The community start page lists the most active threads; the first 15 are read (newest page
  first, a second page only if all 20 posts are new). Posts since the previous run are taken round robin over the
  threads (cap 150 per run). `data-nick`/`data-userid`, the nick block and quotes ("Zitat von <name>") are never read;
  the quote's `#beitrag_<id>` link becomes `parentId`. Timestamps are Berlin time (`dd.mm.yy hh:mm:ss`). Source
  `topic` is `general` (the data contract has no finance topic); articles carry the topic "Börse".

**BR24** (`br.de`, Coral on `comments.br24.de`)

- `www.br.de/robots.txt`, group `*`: `/nachrichten/` and the news sitemap are allowed. `comments.br24.de/robots.txt`
  answers 404 (RFC 9309: no restrictions). The comments are shown to every anonymous visitor.
- How: articles from the Google News sitemap `/nachrichten/newsSitemap` (title, date, keywords); short agency items
  (`/nachrichten/meldung/`) have no comments and are skipped. The Coral story id is the suffix after the comma of the
  URL (`…/titel,VZ4flIU`); the article URL is stored ID-based (`…/<section>/beitrag,<id>`, redirects to the article).
  Coral refuses raw GraphQL; the embed uses persisted queries: `GET /api/graphql?query=&id=<id>&variables=<json>`.
  Each run reads `/embed/bootstrap` (~1 KB); if it names the known bundle `stream.e17437fa….js` the known ids are used,
  otherwise the new bundle is read and the ids of `AllCommentsTabQuery`, `AllCommentsTabContainerPaginationQuery` and
  `StreamQuery` are taken from it (`params:{id:"…",name:"…"}`), with the known ids as fallback. `StreamQuery` (~3 KB)
  gives `commentCounts.totalPublished` (count check), `AllCommentsTabQuery` the newest 20 threads with replies, the
  pagination query the next 50 threads by cursor (`CREATED_AT_DESC`).
- Volume: ~2,000 comments a day in total. New stories are ranked by comment count (the 30 newest are probed) and at
  most 4 are started per run; a fetch reads at most 100 comments (newest threads first). Cap 300 per run, strict.
- Privacy: `author` (username, avatar, bio, id) and `parent.author` are never read; quotes and `@name` are removed.
  Upvotes = reaction count. Country `DE` (Bavaria).

**rbb24** (`rbb24.de`)

- robots.txt group `*` allows the section pages, article pages and `*.commentthreads.jsn`.
- How: article links from the section pages `/`, `/politik/`, `/panorama/`, `/wirtschaft/`, `/kultur/`, `/sport/`
  (the newest 30 per page, current month only; at most 40 new articles per run). The article page gives the date
  (JSON-LD), the keywords and `data-comment-config` with `totalCountCommentsAndAnswers` (count check: unchanged → no
  comment request). Articles without a comment section are marked (`commentThreadId = "-"`) and never fetched again.
  Comments: `<article>.commentthreads.jsn?start=0&size=100` (JSON, newest threads first): `{id, text, author, date,
  isEditorial, answers}`; `date` is Berlin time (`Oct 24, 2026, 9:43:38 PM`). `author` is never read, editorial
  answers are skipped, answers are deduplicated by id and get the thread root as parent. Cap 125 per run, strict.

**Allmystery** (`allmystery.de`, "Politik" category)

- robots.txt group `*`: disallows `/bilder/`, `/mitglieder/`, `/pn/`, `/login/`, `/ng/`, `/fcgi/` and the imprint;
  `/themen/` is allowed. Readable without login.
- How: stream source. The listing `/themen/pr` gives the 50 most recently active threads with last-post time and last
  page. One long-running thread per party or person is followed (AfD `pr98768`, CDU/CSU `pr94635`, SPD `pr135414`,
  Linke `pr37553`, Grüne `pr89279`, BSW `pr170539`, Merz `pr172339`, Höcke `pr174320`, Trump `pr120332`) plus the 5
  most active other politics threads: only threads with posts since the previous run, last page first
  (`/themen/pr<id>-<n>`), earlier pages only while all posts are new (max 3). Posts: `div.post[data-id]` (global id),
  `span.date[title]` (Berlin time), `.post-content`; `data-username`, the user box, `@name` links (`a.atu`) and quotes
  ("<name> schrieb:", `data-parent-id` → `parentId`) are removed. The thread title is the article context. Round robin
  over threads, cap 150 per run.

**Finanztip Community** (`finanztip.de/community`, WoltLab)

- robots.txt group `*`: **`Crawl-delay: 3`** (obeyed); disallows search, member list, posting, a few TYPO3 paths.
  The thread RSS feeds and thread pages are allowed.
- How: stream source. RSS `thread-list-rss-feed/14/` (board "Erfahrungen mit Anbietern") first, then the whole forum
  (`thread-list-rss-feed/`): the 20 most recently active threads each. Threads with posts since the previous run are
  read: `article[data-post-id]`, `<woltlab-core-date-time date="ISO">`, `.messageText`; the author box, signatures,
  `a.userMention` and quotes ("Zitat von <name>", `postID` → `parentId`) are removed.
- Limitation: anonymous thread pages come from a CDN cache that ignores the query string (`?pageNo=N` returns page 1,
  `Age` up to several hours). So only page 1 (the first 20 posts) of a thread is used, and the source re-reads a 12 h
  overlap (stored posts are filtered with a read). Source `topic` is `general` (no finance topic in SCHEMA.md).

**GoingElectric** (`goingelectric.de/forum`, phpBB)

- robots.txt group `*`: disallows `index.php`, `memberlist.php`, `search.php`, `ucp.php`, `posting.php`, `report.php`,
  `download.php`, every URL with `sid=` or `hilit=`; `/forum/feed` and `viewtopic.php` are allowed.
- How: stream source. The Atom feed (10 newest posts) gives topics; `viewtopic.php?p=<post>` is that topic's last page.
  Topics stored in the last 3 days are revisited with `viewtopic.php?t=<id>&start=99999` (phpBB clamps to the last
  page). Earlier pages only while all posts are new (max 2). URLs are always built from ids, links with `sid=` are never
  followed. Dates are German (`So 25. Okt 2026, 21:08`, Berlin time). The user name, profile card, signature and
  quotes ("<name> hat geschrieben:", `p=<id>` → `parentId`) are removed. Cap 75 per run. `topic` = `tech`.

**Kuketz-Forum** (`kuketz-forum.de`, Discourse)

- robots.txt group `*`: disallows `/admin/`, `/auth/`, `/session`, `/search`, `/my`, `/g`, `/badges`, the topic and
  category RSS feeds and `api_key` URLs. `/posts.json` is allowed and public.
- How: stream source. `/posts.json` (newest 50 posts), paged with `?before=<id>` until the previous run. Only `id`,
  `topic_id`, `topic_title`, `post_number`, `reply_to_post_number`, `created_at` and `cooked` are read; `username`,
  `name`, `avatar_template`, `user_id` etc. never. Quotes (`aside.quote`) and `a.mention` are removed; only regular,
  visible posts. One topic = one article (`/t/<id>`). Cap 50 per run. `topic` = `tech`.

**Bundestag ePetitionen forum** (`epetitionen.bundestag.de`)

- robots.txt: `/robots.txt` redirects through the site's cookie check and then answers 404 (RFC 9309: no restrictions);
  every request is still checked. The site sets a session cookie on the first request (302 to
  `/epet/rediraftercookiecheck.html` and back); the HTTP session keeps it in its cookie jar. No login, no account.
- How: stream source. The list of petitions open for co-signing (`…teaser-petitionen-table.$$$.status.2.page.<N>.
  batchsize.10.html`, up to 8 pages) gives title, category and forum post count; the 20 petitions with the most posts
  are checked. Per petition the thread table (`forum.kommentare.table.$$$.page.0.html`) gives opening and last-post
  times; threads with new posts are read via `Beitrag_<thread>.alleAntwortenNachDatum.$$$.page.1.html` (date, time,
  text, reply path → `parentId`), new threads' own text comes from the petition's `forum.rss.xml` (no dates there).
  Names and pseudonyms ("Nutzer1234567") are never read: the author line contributes only date and time; quotes
  ("Zitat: von §§§<id>§§§"), the masked ids and the reply header "@ <name> | <date>" are removed; moderator notes
  ("DOPPELPOST …") are skipped. The petition title is the article. Cap 75 per run; activity is low at the moment.

**Bluesky feed "Deutsche Politik"** (part of the `bluesky` source)

- `public.api.bsky.app/xrpc/app.bsky.feed.getFeed?feed=at://did:plc:xcprh522pngo62qeonh2jj5u/app.bsky.feed.generator/aaaown2rdgu6m`
  (robots.txt like `api.bsky.app`: crawling the public API is allowed). Newest first, paged by cursor until the previous
  run, reposts skipped, only posts with `de` (or no language) in `langs`, same `!no-unauthenticated` opt-out filter and
  the same comment ids (`sha1("bluesky:" + uri)`) as the search and Jetstream, so a post found twice is stored once.
  150 of the 1,150 posts per run are reserved for the feed; the pseudo-article URL is
  `https://bsky.app/?feed=deutsche-politik&day=…` (no DID).

### Candidates (not implemented yet)

Researched on 26 Oct 2026, kept for later (or for a decision of the team):

| site | note |
|---|---|
| Hardwareluxx, PCGH / PC Games, Dr. Windows | large German tech forums; not needed for the volume now |
| DealDoktor, BVB-Forum, Tarnkappe, Abendzeitung | possible, low priority |
| MacTechNews | its terms require consent before content is reused; ask first |
| Weser-Kurier | Disqus: needs an API key and the Disqus terms |
| meinBerlin / Baden-Württemberg participation portals | public, but mostly structured proposals rather than comments |
| politikforen.net (hpf) | technically possible, but a strongly right-wing forum; would worsen the political balance |
| Badische Zeitung | an anonymous Coral instance was reported but **not verified**; the entry under "Rejected sources" (Coral only for logged-in readers) still holds until someone checks it manually in a browser |

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
| bild.de | No public comment function any more: article pages contain no comment markup or comment requests (only "Kommentar" as an editorial genre). robots.txt additionally disallows `/community/`; `rsl.xml` prohibits AI training/input use. |
| rp-online.de | Public comment API (`psc.rp-online.de`, robots 404) exists, but returns `commentAreaStatus: "hideAll"` for every article we checked: comments are not shown to anonymous readers. |
| badische-zeitung.de | Coral comments load only for logged-in (BZ-Plus) readers; nothing is loaded anonymously. |
| stuttgarter-zeitung.de, stuttgarter-nachrichten.de, mopo.de, focus.de | Comments via `*.engagently.com`, whose robots.txt is unreachable (treated as disallow). |
| weser-kurier.de | Disqus. disqus.com's robots.txt allows the embed, but reading needs the Disqus API (key, terms); not worth it for the volume. |
| augsburger-allgemeine.de | Comment section only with login (posting endpoint `/user/comment/post`, no public list found). |
| handelsblatt.com | Mostly paywalled; only a comment detail page marker found, no public comment list. |
| berliner-zeitung.de, t-online.de, nordkurier.de | No public comment section found on article pages. |
| nachdenkseiten.de | No comment function; "Leserbriefe" are an editorial selection published as articles. |
| YouTube (Data API v3) | Technically ideal (official, free key, comments under tagesschau/ZDF/WELT/BILD videos), but the **YouTube API Developer Policies** forbid our core use: "Do not aggregate API Data except ... relating to YouTube channels that are under the same content owner" (section III.E "Data Aggregation"). Our dashboards aggregate sentiment across channels. In addition, Non-Authorized Data may be stored for at most 30 days and must be refreshed or deleted. The fitting route is the **YouTube Researcher Program** (academic access, own terms) for HU Berlin; not implemented. |
| reddit.com (r/de, r/Germany) | robots.txt `User-agent: * Disallow: /`; unauthenticated JSON returns 403. Access needs a registered OAuth app approved under Reddit's Responsible Builder Policy / research access (r/reddit4researchers), the Data API terms require deleting content that users delete (periodic re-checks). Not implemented without the owner's decision. |
| mobilegeeks.de | Now redirects to basicthinking.de/blog, whose robots.txt disallows the WordPress REST API. |
| uebermedien.de | REST API restricted (401) and disallowed in robots.txt; articles partly paywalled. |
| bildblog.de | WordPress comments closed since 2009. |
| dwdl.de | No comment function. |
| fernsehserien.de | Comments only via a login overlay (JS), no public list. |
| quotenmeter.de | Comments live in the qtalk.de phpBB forum (robots allows it, Atom feed public), but only ~2 posts a day. |
| sueden.social, mstdn.social | Local timeline only for logged-in users. |
| mastodon.social, mastodon.online | Public local timeline disabled. |
| abgeordnetenwatch.de | The API (CC0 1.0) covers politicians, mandates, polls and votes but **no citizen questions**; the questions exist only as web pages. Useful for the analysis team (entity list of politicians), not as a comment source. |

## Data protection and ethics

- **No author data.** Adapters never read user names, user IDs, avatars or profile URLs; the `Comment` model has
  no field for them (a test enforces this). `@mentions` of other users are stripped from the text
  (`@Name:` at the start of a reply and inline `@username`; e-mail addresses are kept).
- Only what the analysis needs is stored: text, timestamps, thread structure (`parentId`), upvotes.
- Only public content: no login, no paywall circumvention, no bot-protection circumvention. No cookies, except the
  anonymous session cookie the Bundestag petitions site sets for every visitor (kept in the run's cookie jar only).
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
    base.py           Source (articles: discover(), fetch()) and StreamSource (fetch_since()) interfaces
    derstandard.py    GraphQL forum API
    taz.py            HTML comment list
    blick.py          Viafoura API
    tagesschau.py     meta.tagesschau.de listing + discussion pages
    freitag.py        Plone REST @comments
    tichyseinblick.py WordPress REST comment feed (stream)
    mastodon.py       public local timelines (stream, social)
    bluesky.py        public AppView search sample (stream, social)
    jetstream.py      Bluesky Jetstream live window (stream, social, WebSocket)
    appblogs.py       iphone-ticker.de / ifun.de: RSS + comment feeds / article HTML
    computerbase.py   ComputerBase news threads (XenForo HTML)
    wallstreet.py     wallstreet-online.de forum (stream, HTML)
    br24.py           BR24: news sitemap + Coral persisted GraphQL queries
    rbb24.py          rbb24: section pages + commentthreads JSON
    allmystery.py     Allmystery politics threads (stream, HTML)
    finanztip.py      Finanztip community (stream, WoltLab RSS + HTML)
    goingelectric.py  GoingElectric forum (stream, phpBB Atom + HTML)
    kuketz.py         Kuketz-Forum (stream, Discourse posts.json)
    bundestag.py      Bundestag ePetitionen forum (stream, HTML + RSS)
  discovery.py      Google News sitemap parser, JSON-LD/breadcrumb article metadata
  http.py           PoliteSession: robots check, per-host throttle, retries
  robots.py         robots.txt: urllib.robotparser + own RFC 9309 group/wildcard evaluation
  firestore.py      Firestore REST client (batchGet, runQuery, batchWrite)
  ids.py            deterministic IDs, Berlin day keys, date parsing
  text.py           text cleaning, @mention stripping
  models.py         ArticleRef, Comment, FetchResult (no author fields)
```

**Stream sources** (Tichys Einblick and the other WordPress sites, Mastodon, Bluesky, feddit, the forums) list new
items directly. Each run asks for everything
since the source's `lastCrawlAt` minus an overlap (10 min; 12 h for Tichys Einblick because of pre-moderation),
drops items from the overlap that are already stored (one Firestore read each, never a write) and groups the rest
by article (social posts: one pseudo-article per day and instance).

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
   ends there: no thread pages, no Firestore writes. tagesschau gets the count from the front page listing
   (no request at all), der Freitag from `@comments`. taz has no count endpoint, so the article page is fetched
   (all comments are in it) and compared; revisits use the stored slug URL and skip the permalink redirect.
2. *Newest first, stop early.* Both APIs return threads newest first (derStandard `sortOrder: ByTime`, Viafoura
   `sorted_by=newest`). Replies to old threads can appear on any page, so paging stops as soon as as many
   comments newer than `lastCommentCrawlAt` were seen as the count grew (without a stored count: at the first
   page that holds only older comments). Blick revisits also use smaller pages and skip the article page
   (thread id stored).
3. *Only new comments are sent.* Comments published before `lastCommentCrawlAt - 10 min` are never sent to
   Firestore. Exception: pre-moderated comments (derStandard, Blick, taz) are released later with their original
   timestamp. If the complete list was fetched and fewer new comments turned up than the count grew, the comments
   of the 3 hours before the cutoff are checked with a Firestore **read** and only the missing ones are written.
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
| `JETSTREAM_LIVE_SECONDS` | length of the Bluesky Jetstream live window per run, default `300` |
| `CRAWLER_USER_AGENT` | override the User-Agent (keep it descriptive) |
| `DERSTANDARD_HASH_FORUMINFO`, `DERSTANDARD_HASH_THREADS` | override derStandard persisted query hashes |
| `LOG_LEVEL`, `LOG_FORMAT` | logging defaults (`INFO`, `text`) |

Firestore is accessed **only over the REST API** (`google.oauth2.service_account` +
`google.auth.transport.requests.AuthorizedSession`), not gRPC.

## Volume caps and Firestore cost

**Budget.** The analysis can process ~14,700 new comments a day within its Firestore share; a backlog and new analysis
features (entities) need headroom. So all sources together must stay at or below **~11,000 new comments a day**
(`config.DAILY_COMMENT_TARGET`). Uncapped, the sources would produce well over 25,000 (derStandard alone ~15,000–20,000,
Bluesky as much as we ask for).

**Daily caps.** Every source has a daily cap in `config.DAILY_COMMENT_CAPS`; the crawl runs twice a day
(`RUNS_PER_DAY`), so the per-run cap `max_comments_per_run` is half of it. The caps add up to 10,990 (a test keeps the
sum at or below the target). Additional per-source limits are in `config.EXTRA_LIMITS`.

- Stream sources (comment feeds, social media, forums): the per-run cap is a hard limit on the items fetched.
- Article sources: `max_comments_per_run` is a budget: once this many new comments were stored in a run, no further new
  article is started (the current one is always finished). With `strict` (derStandard, Blick, ComputerBase, BR24,
  rbb24) the budget also stops revisits of tracked articles. Without `strict` (tagesschau, taz, Cicero, …) tracked
  articles are revisited without limit and crawled completely, so their threads stay coherent; the cap then limits
  only new work. A strict cap can be exceeded by one article (the article that crosses it is finished; BR24 limits a
  fetch to 100 comments for that reason).
- `max_new_articles_per_run` (derStandard 2, Blick 2, BR24 4, ComputerBase 6, rbb24 40): new articles taken per run,
  most relevant first (derStandard: Germany/EU/USA stories, most active forums; Blick: Politik, Schweiz, Ausland, …;
  BR24: most commented; app blogs: most discussed). Articles not taken stay candidates while they are discovered.
- Overrides: `--source-limit derstandard.max_comments_per_run=3000` (repeatable; `0` removes one cap,
  `derstandard=off` all caps of a source) or the env var `CRAWLER_SOURCE_LIMITS` with JSON of the same shape as
  `SOURCE_LIMITS`. Sources can be switched off with `CRAWLER_DISABLED_SOURCES=derstandard,blick`.

New comments per day, before and after the change of 26 Oct 2026. "Before" are the Firestore counts of 24 and 25 Oct
(average, `comments` by `sourceId` and `day`). "After" for the new sources and Bluesky comes from dry runs on
26 Oct 2026 (`--since-hours 12`, i.e. one run, ×2; `--since-hours 48` for the quiet forums); the other sources are
unchanged.

| source | before | after | daily cap | per run |
|---|---|---|---|---|
| bluesky (search: targeted + sample) | 3,534 | ~1,900 (dry run: 937 per run) | 2,000 | 1,000 |
| bluesky (feed "Deutsche Politik", new) | – | ~200–300 (dry run: 97 per daytime run) | 300 | 150 |
| tagesschau | 983 | 983 | 1,100 | 550 |
| tichyseinblick | 943 | 943 | 1,100 | 550 |
| mastodon (17 instances) | 693 | 693 | 800 | 400 |
| derstandard (AT) | 642 | 642 | 200 (strict, see note) | 100 |
| bluesky_jetstream | 600 | 600 | 700 | 350 |
| computerbase | 434 | 434 | 400 (strict) | 200 |
| apollo | 249 | 249 | 300 | 150 |
| taz | 213 | 213 | 300 | 150 |
| wallstreet | 202 | 202 | 300 | 150 |
| feddit | 178 | 178 | 250 | 125 |
| caschy | 173 | 173 | 220 | 110 |
| overton | 133 | 133 | 250 | 125 |
| cicero | 58 | 58 | 150 | 75 |
| iphoneticker, ifun, appgefahren | 91 | 91 | 250 / 150 / 100 | 125 / 75 / 50 |
| achgut, lawblog, freitag, netzpolitik, ruhrbarone | 109 | 109 | 100 / 60 / 60 / 30 / 20 | half |
| blick (CH) | 33 | 33 | 100 (strict) | 50 |
| **br24** (new) | – | ~600 (cap-bound; dry run: 300 per run from 3 stories) | 600 (strict) | 300 |
| **rbb24** (new) | – | ~150–250 (dry run: 183, first crawl of 20 articles) | 250 (strict) | 125 |
| **allmystery** (new) | – | ~135 (dry run: 67 per run) | 300 | 150 |
| **kuketz** (new) | – | ~65 (dry run: 33 per run) | 100 | 50 |
| **goingelectric** (new) | – | ~30–80 (dry run: 22 per run, 53 in 48 h without revisits) | 150 | 75 |
| **finanztip** (new) | – | ~40–60 (dry run: 28 per run, 85 in 48 h) | 200 | 100 |
| **bundestag** (new) | – | ~1–20 (dry run: 2 in 48 h) | 150 | 75 |
| **total** | **~9,260** | **~9,000** (≈ 7,630 existing incl. Bluesky search + ≈ 1,350 new incl. feed) | **10,990** | |

So the new sources (~1,350/day) are paid for by the smaller Bluesky search (−1,600/day); the total stays at ~9,000,
about 2,000 below the 11,000 target, and even with every cap reached it stays below 11,000 for the stream sources and
strict sources. The non-strict article sources (tagesschau, taz, Cicero, …) can exceed their cap through revisits
of busy discussions; their measured volumes are below their caps. Note: derStandard and ComputerBase were measured
above their strict caps because the article that crosses the cap (a first crawl of a busy forum) is always finished.

Each new comment costs one write by the crawler and one update by the analysis; revisits that find nothing new
cost no writes (see "Revisits"). At roughly $0.18 per 100k writes and $0.06 per 100k reads (europe-west3) the
crawler costs a few cents per day.

## Daily Firestore budget

All components share a hard daily budget in `meta/budget` (contract in SCHEMA.md) so the project stays under
~€3.70/month; the billing alert only warns. Defaults: 60,000 writes and 200,000 reads per day for all
components, of which the crawler may use 25,000 writes and 60,000 reads (`DAILY_WRITE_LIMIT`,
`DAILY_READ_LIMIT`, `CRAWLER_DAILY_WRITE_LIMIT`, `CRAWLER_DAILY_READ_LIMIT`).

- Run start: one read of `meta/budget` (counters of another day count as 0). If the budget is used up, the run
  logs `budget.exhausted` and exits 0.
- During the run: the Firestore client counts every write and document read; runners stop starting new articles
  (stream sources: fetch fewer items) when the remaining budget minus a reserve of 200 writes / 100 reads is
  reached. The current article/batch is always finished.
- Run end: one write adds the run's counts (`increment` on `writes.crawler` / `reads.crawler`, or a reset when the
  day changed), with the document's `updateTime` as precondition and retries on conflict.
- The run summary shows `crawler_writes_today`, `crawler_reads_today`, `writes_left_today`, `reads_left_today`.

At ~9,000 comments/day (at most ~11,000) the crawler needs roughly 10–12k writes and 5–10k reads per day, well inside
its share.

## Schedule

`.github/workflows/crawl.yml` runs every 12 hours (04:17 and 16:17 UTC) and on manual dispatch (inputs: sources,
max articles, since hours). It uses the organisation secret `FIREBASE_SERVICE_ACCOUNT`, never runs two crawls
concurrently and stops starting new articles after 95 minutes. `.github/workflows/ci.yml` runs ruff (lint +
format) and pytest on Python 3.11 and 3.13 for every push and pull request.

**Triggering.** GitHub's `schedule` started crawls 2.5–6 h late, so the runs are started by Cloud Scheduler
(job `meinungspuls-crawl` in the GCP project `meinungspuls-hu`, 04:17 / 16:17 UTC), which calls the API
(`GET /internal/cron/dispatch/crawl`, see `deploy/setup-gcp.sh` in the `api` repo); the API sends a
`workflow_dispatch` with the input `trigger=vercel-cron` (the value is historical, from when the cron ran on Vercel). The `schedule` stays as a fallback: the `gate` job
(`actions: read`) skips a scheduled run when a successful or still queued/running crawl was created since one hour
before its slot, and drops duplicate cron deliveries (another run within 3 h). Manual runs are never skipped.

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
