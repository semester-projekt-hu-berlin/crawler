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
| `tagesschau` | [meta.tagesschau.de](https://meta.tagesschau.de) (ARD) | news | DE | public broadcaster, centre | front page listing `/?page=N` | comment list in the discussion page HTML | ~400 (≈4 discussions/day, open for about a day) |
| `tichyseinblick` | [Tichys Einblick](https://www.tichyseinblick.de) | news | DE | conservative / right-liberal | WordPress REST `/wp-json/wp/v2/posts` | site-wide WordPress REST comment feed | ~950 |
| `cicero` | [Cicero](https://www.cicero.de) | news | DE | conservative-liberal | RSS `/rss.xml` | comment list in the article HTML | ~250 |
| `achgut` | [Achse des Guten](https://www.achgut.com) | news | DE | right-libertarian | RSS `/rss` | reader letters in the article HTML | ~150 |
| `freitag` | [der Freitag](https://www.freitag.de) | news | DE | left | Google News sitemap `/@@google-news-rss` | Plone REST `…/@comments` | ~20 |
| `taz` | [taz](https://taz.de) | news | DE | left / green | news sitemap `/sitemap-google-news.xml` | comment list in the article HTML | ~300 |
| `derstandard` | [DER STANDARD](https://www.derstandard.at) | news | AT | left-liberal | news sitemap `/sitemaps/news.xml` | public forum GraphQL API `capi.ds.at` | ≤200 (capped; uncapped ~15,000–20,000) |
| `blick` | [Blick](https://www.blick.ch) (German edition) | news | CH | tabloid, centre | news sitemap `/news.xml` | public Viafoura API `livecomments.viafoura.co` | ≤100 (capped; uncapped ~3,000) |
| `mastodon` | 17 German instances (troet.cafe, mastodon.de, norden.social, nrw.social, ruhr.social, bonn.social, …) | social | DE | tends left | public local timelines (API) | the posts themselves | ~1,100 (cap 600/run, after opt-out filter) |
| `feddit` | [feddit.org](https://feddit.org) (Lemmy) | social | DE | mixed, tends left | public Lemmy API, local communities | comments under the posts | ~150–200 |
| `caschy` | [Caschys Blog](https://stadt-bremerhaven.de) | news | DE | tech | RSS `/feed/` | WordPress comments in the article HTML | ~200 |
| `netzpolitik` | [netzpolitik.org](https://netzpolitik.org) | news | DE | digital rights, left-liberal | WordPress REST | site-wide WordPress REST comment feed | ~10–15 |
| `ruhrbarone` | [Ruhrbarone](https://www.ruhrbarone.de) | news | DE | regional politics, liberal | WordPress REST | site-wide WordPress REST comment feed | ~10 |
| `bluesky` | [Bluesky](https://bsky.app), German-language searches + sample | social | DE | tends centre-left | public AppView search (API) | the posts themselves | ~3,500 (cap 2,000/run: targeted searches + sample) |
| `bluesky_jetstream` | [Bluesky](https://bsky.app) live firehose (Jetstream) | social | DE | mixed | 5-minute live window per run | German posts (`langs` contains `de`) | ~650 (cap 500/run) |
| `apollo` | [Apollo News](https://apollo-news.net) | news | DE | right-conservative | WordPress REST | site-wide WordPress REST comment feed, 10 % sample | ~400 (of ~4,000; cap 250/run) |
| `overton` | [Overton Magazin](https://overton-magazin.de) | news | DE | left / critical of the establishment | WordPress REST | site-wide WordPress REST comment feed | ~260 |
| `lawblog` | [lawblog](https://www.lawblog.de) (Udo Vetter) | news | DE | law / civil liberties | WordPress REST | site-wide WordPress REST comment feed | ~40 |
| `iphoneticker` | [iphone-ticker.de](https://www.iphone-ticker.de) | news | DE | tech (Apple, apps) | RSS `/feed/` (with comment counts) | per-article comment feed, article HTML as fallback | ~450 (cap 300/run) |
| `ifun` | [iFun.de](https://www.ifun.de) | news | DE | tech (Apple, apps) | RSS `/feed/` (with comment counts) | per-article comment feed, article HTML as fallback | ~300 (cap 250/run) |
| `appgefahren` | [appgefahren.de](https://www.appgefahren.de) | news | DE | tech (apps) | WordPress REST | site-wide WordPress REST comment feed | ~90 |
| `computerbase` | [ComputerBase](https://www.computerbase.de) | news | DE | tech (hardware, software) | Atom `/rss/news.xml` | the news' forum thread (XenForo HTML) | ~400 (6 new threads, 200 comments per run) |
| `wallstreet` | [wallstreet-online.de](https://www.wallstreet-online.de) forum | news | DE | finance / retail investors | community start page (most active threads) | newest thread pages (HTML) | ~300 (cap 150/run) |

The leaning column is a rough orientation for interpreting results (commenters are not the editorial line);
the mix was chosen to cover the spectrum. Estimates are per day at the 12-hour schedule (see
"Volume caps and Firestore cost" below).

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
and day. Cap 2,000 posts per run (75 % searches, 25 % sample).

**Mastodon**: 17 German instances whose local timeline is public (`timelines_access.live_feeds.local =
public`) and whose robots.txt allows the API (added on 23 Oct 2026: mastodontech.de, social.anoxinon.de, bonn.social,
nerdculture.de, machteburch.social, det.social; their robots.txt only disallows `/media_proxy/`, `/interact/` and the
domain block list, plus `GPTBot` entirely). det.social sometimes answers 429: the client backs off (Retry-After or
5/10/20 s) and then skips the instance for this run. swiss.social and wien.rocks are deliberately not used (German
focus). Above the cap of 600 posts per run, every instance is thinned evenly.

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
  same comments again (no duplicates, no extra writes). `ApolloNews.sample_share`, hard cap 250 per run.

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
  discovery.py      Google News sitemap parser, JSON-LD/breadcrumb article metadata
  http.py           PoliteSession: robots check, per-host throttle, retries
  robots.py         robots.txt: urllib.robotparser + own RFC 9309 group/wildcard evaluation
  firestore.py      Firestore REST client (batchGet, runQuery, batchWrite)
  ids.py            deterministic IDs, Berlin day keys, date parsing
  text.py           text cleaning, @mention stripping
  models.py         ArticleRef, Comment, FetchResult (no author fields)
```

**Stream sources** (Tichys Einblick, Mastodon, Bluesky) list new items directly. Each run asks for everything
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

Uncapped, the sources produce ~23,000–28,000 new comments per day, ~70 % from derStandard (Austria). That is more
than the analysis can process (2 runs × 150 min per day on 2-vCPU runners), costs writes for data the project does
not focus on and drowns the German sources. derStandard and Blick are therefore capped per run in
`config.SOURCE_LIMITS`:

| source | `max_new_articles_per_run` | `max_comments_per_run` | selection of new articles |
|---|---|---|---|
| `derstandard` | 2 | 100 (strict) | only stories about Germany, the EU or the USA (title filter); International, Wirtschaft and Web first (section RSS feeds), among those the most active forums, then by recency |
| `blick` | 2 | 50 (strict) | Politik, Schweiz, Ausland, Wirtschaft, Sport first (URL path), then by recency |

- `strict` (derStandard, Blick): the comment budget also stops revisits of tracked articles, so the daily
  maximum holds. Both sources can be switched off completely with `CRAWLER_DISABLED_SOURCES=derstandard,blick`.
- Otherwise the caps only limit **new** articles. Articles already being tracked are revisited without limit, and every
  kept article is crawled completely (all threads, newest first), so the data stays coherent instead of
  containing truncated threads.
- `max_comments_per_run` is a budget: once this many new comments were stored in a run, no further new article is
  started (the current one is always finished).
- Articles not taken stay candidates while they are in the sitemap window and may be taken by a later run if they
  rank high then (their first crawl is then complete as well).
- Overrides: `--source-limit derstandard.max_comments_per_run=3000` (repeatable; `0` removes one cap,
  `derstandard=off` all caps of a source) or the env var `CRAWLER_SOURCE_LIMITS` with JSON of the same shape as
  `SOURCE_LIMITS`.

Estimated new comments per day with the caps (2 runs a day; new sources measured with dry runs on 23 Oct 2026,
existing ones from the Firestore counts of 21–23 Oct):

| source | per day | cap per run |
|---|---|---|
| bluesky (search) | ~1,850 (cap allows 4,000) | 2,000 |
| mastodon (17 instances) | ~1,100 | 600 |
| tichyseinblick | ~950 | – |
| bluesky_jetstream | ~650 (5-min window: ~535 German posts, ~330 after the opt-out filter) | 500 |
| iphoneticker | ~450 | 300 |
| apollo | ~400 (10 % sample of ~4,000) | 250 |
| computerbase | ~400 | 6 new threads / 200 (strict) |
| tagesschau | ~400 | – |
| ifun | ~300 | 250 |
| wallstreet | ~300 | 150 |
| taz | ~300 | – |
| overton | ~260 | 250 |
| cicero | ~250 | 600 |
| caschy | ~200 | – |
| feddit | ~150–200 | – |
| achgut | ~150 | 400 |
| appgefahren | ~90 | 100 |
| lawblog | ~40 | 100 |
| freitag, netzpolitik, ruhrbarone | ~45 | – |
| **Germany** | **~8,300** | |
| derstandard (AT) | ≤200 | 2 new articles / 100 (strict) |
| blick (CH) | ≤100 | 2 new articles / 50 (strict) |
| **total** | **~8,600**, about 96 % from German sources | |

With every cap reached the total stays below ~13,000 comments a day, i.e. ≤ ~14,500 crawler writes (comments plus
article docs), well inside the crawler's 25,000; the downstream analysis reads each comment once more.

A dry run (`python -m crawler run --sources derstandard,blick --since-hours 24 --dry-run`) on 22 Oct 2026 took
10 of 168 derStandard candidates (1,953 comments, the first crawl of busy forums) and 15 of 131 Blick
candidates (310 comments). Right after enabling the caps, articles that were tracked before keep being revisited
for up to 3 days, so the volume falls to the target over that period.

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

At ~8,600 comments/day the crawler needs roughly 9–10k writes and 5–10k reads per day, well inside its share.

## Schedule

`.github/workflows/crawl.yml` runs every 12 hours (04:17 and 16:17 UTC) and on manual dispatch (inputs: sources,
max articles, since hours). It uses the organisation secret `FIREBASE_SERVICE_ACCOUNT`, never runs two crawls
concurrently and stops starting new articles after 95 minutes. `.github/workflows/ci.yml` runs ruff (lint +
format) and pytest on Python 3.11 and 3.13 for every push and pull request.

**Triggering.** GitHub's `schedule` started crawls 2.5–6 h late, so the runs are started by Vercel Cron in the
API project (`vercel.json` in the `api` repo, `GET /internal/cron/dispatch/crawl`), which sends a
`workflow_dispatch` with the input `trigger=vercel-cron`. On the Hobby plan Vercel fires anywhere within the
scheduled hour (04:00–04:59 / 16:00–16:59 UTC). The `schedule` stays as a fallback: the `gate` job
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
