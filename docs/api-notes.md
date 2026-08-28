# Phase 0 — API discovery notes

**Status: the gate is NOT closed.** Every Wikimedia host is blocked by this
session's network egress policy, so not one claim below was confirmed against
the live API. See [Verification blocker](#verification-blocker).

Everything here is documentation-derived. Treat it as a hypothesis list to be
checked by `scripts/phase0_probe.py`, not as settled fact — no feature code
should be written against an unconfirmed line.

## Status legend

| Mark | Meaning |
| --- | --- |
| `LIVE` | Confirmed by a live API call, with the response recorded |
| `DOC` | From official documentation only. Plausible, unconfirmed, may be wrong for dewiki specifically |
| `BLOCKED` | Cannot be answered from documentation at all — needs live data |
| `OPEN` | Design question that the live probe should settle |

Current tally: **0 `LIVE`**, 17 `DOC`, 4 `BLOCKED`, 9 `OPEN`.

## Verification blocker

`curl`, Python `urllib` and the `WebFetch` tool all fail identically:

```
$ curl -sS https://de.wikipedia.org/w/api.php?action=query&meta=siteinfo&format=json
curl: (56) CONNECT tunnel failed, response 403

$ curl -sS "$HTTPS_PROXY/__agentproxy/status" | jq .recentRelayFailures
[ { "kind": "connect_rejected",
    "detail": "gateway answered 403 to CONNECT (policy denial or upstream failure)",
    "host": "de.wikipedia.org:443" } ]
```

Blocked, all with the same 403 at the CONNECT stage: `de.wikipedia.org`,
`en.wikipedia.org`, `wikimedia.org`, `api.wikimedia.org`, `meta.wikimedia.org`,
`www.wikidata.org`. Per the proxy's own README a 403 is an organisation policy
denial and must not be routed around, so this is reported rather than worked
around. Only `github.com` and the package registries are reachable.

Two ways to close the gate:

1. **Run the probe locally** (fastest — a few minutes, stdlib only, no install):
   ```sh
   export WP_TODO_CONTACT="oderbolz@gmail.com"
   python3 scripts/phase0_probe.py
   ```
   It writes `docs/phase0-probe-output.json`. Commit that (or paste it back) and
   this file gets rewritten with `LIVE` marks and real evidence.
2. **Allowlist the Wikimedia hosts** for this environment's egress policy
   (`*.wikipedia.org`, `wikimedia.org`, `api.wikimedia.org`), then the probe
   runs here.

The probe is read-only by construction: `action=query`, `action=paraminfo` and
GET requests to the pageviews REST endpoint. There is no code path in it that
can write.

---

## 1. `list=geosearch` as a generator

| # | Claim | Status |
| --- | --- | --- |
| 1.1 | `ggsradius` accepts 10–10000 metres; 10000 m is the hard maximum | `DOC` |
| 1.2 | `ggslimit` max is 500 for normal accounts, 5000 with the `apihighlimits` right | `DOC` |
| 1.3 | GeoData is installed on all Wikipedias, dewiki included | `DOC` |
| 1.4 | `prop=categories&clshow=hidden&cllimit=max` can be combined with the generator in one request | `OPEN` |
| 1.5 | Combining a generator with prop modules yields **two** continuation tokens, and the prop modules must be exhausted before advancing the generator | `DOC` |

Notes and consequences:

- 10 km max radius confirms the tiling design in `scope.toml`. Bezirk Horgen
  plus the right shore needs roughly 4–6 tiles; the probe uses 4 as a start.
- 1.4 is the one that shapes the fetch layer. The API generally allows
  generator + `prop=` in one request, but when a prop module has a per-page
  limit the effective batch size is often clamped (frequently to 50 pages).
  **If it clamps, a 500-page geosearch batch silently becomes ten round trips**
  — which is fine, but the continuation code must handle it rather than assume
  one page per tile. The probe records the first two pages of a combined
  request verbatim so the real shape is on record.
- On 1.5: with `formatversion=2` and the modern `continue` protocol, the
  correct client behaviour is simply "resubmit the original query with every
  key from the `continue` object merged in, until `continue` is absent". The
  ordering rule (props before generator) is handled by the API itself in that
  protocol; the old `rawcontinue` warning does not apply. This is the argument
  for handling continuation centrally in one client method — do not scatter it.
- `paraminfo` (`action=paraminfo&modules=query+geosearch`) is the authoritative
  source for both limits and is probed in P6; prefer it over the wiki docs.

## 2. Hidden maintenance categories actually present in the Bezirk Horgen area

**`BLOCKED` — this cannot be answered without live data, and I will not guess a
list.** An observed list with counts is exactly what probe P2 produces: it walks
the geo tiles with `prop=categories&clshow=hidden`, dedupes per page and emits a
frequency table plus per-tile breakdown.

What I can offer, explicitly as *candidates to look for in the output* and not
as findings:

| Candidate category | Backing template | Status |
| --- | --- | --- |
| `Kategorie:Wikipedia:Veraltet` | `{{Veraltet}}` | `DOC` (name), `BLOCKED` (presence/count) |
| `Kategorie:Wikipedia:Lückenhaft` | `{{Lückenhaft}}` | `OPEN` — exact name unconfirmed |
| `Kategorie:Wikipedia:Überarbeiten` | `{{Überarbeiten}}` | `OPEN` — exact name unconfirmed |
| `Kategorie:Wikipedia:Belege fehlen` | `{{Belege fehlen}}` | `OPEN` — exact name unconfirmed |
| dated `Veraltet`/`Zukunft` categories | `{{Veraltet|seit=YYYY}}`, `{{Zukunft}}` | `OPEN` — **I could not confirm that dated "Veraltet in … Jahren" categories exist at all on dewiki.** The brief assumes they do; the probe output will show what really exists |
| offline-weblink tracking | dewiki uses `{{Toter Link}}`, not a "Weblink offline" template as far as I can tell | `OPEN` — name and mechanism both unconfirmed |

`{{Veraltet}}` does take a `seit=` parameter (e.g.
`{{Veraltet|dieses Abschnitts|Kennzahlen bitte erneuern.|seit=2026}}`) — `DOC`.
Whether `seit=` surfaces as a category or only in the rendered banner is `OPEN`;
if it is banner-only, the year must be parsed from the wikitext, which changes
the fetch plan (we would need `prop=revisions&rvslots=main&rvprop=content` for
every candidate rather than categories alone). This is the single biggest
unknown for signal 1 and it should be settled before any scoring code is written.

Design consequence either way: **the category names belong in `scope.toml`, not
in Python.** Weights keyed by category string, with an "unknown maintenance
category" bucket, means a dewiki rename does not require a code change.

## 3. CirrusSearch keywords on dewiki

| # | Claim | Status |
| --- | --- | --- |
| 3.1 | `deepcat:` / `deepcategory:` exist as CirrusSearch keywords | `DOC` |
| 3.2 | `deepcat:` traverses at most **5 levels** and **256 categories** (both configurable per wiki) | `DOC` |
| 3.3 | `deepcat:` is backed by the WDQS SPARQL category service — an external dependency that can be down independently of the search index | `DOC` |
| 3.4 | `deepcat:` is actually enabled on dewiki, and its limits there | `OPEN` |
| 3.5 | `hastemplate:` works, but is unreliable where a template is wrapped by another template | `DOC` |
| 3.6 | `insource:/regex/` is aborted after **20 seconds** | `DOC` |
| 3.7 | A bare `insource:/regex/` will usually time out and blocks other users; every regex query must be narrowed by non-regex terms first, ideally `insource:foo insource:/foo…/` | `DOC` |
| 3.8 | Whether a timed-out regex returns an error or a silent partial result set | `OPEN` |

Consequences for the design:

- 3.2 is a real constraint against the `[[category]]` scope entries: a
  `depth` above 5 in `scope.toml` cannot be honoured by `deepcat:`, and a broad
  category tree will silently hit the 256-category cap. Options are to validate
  `depth <= 5` at config load, or to walk the tree ourselves with
  `list=categorymembers` (more requests, but no cap, no SPARQL dependency, and
  cacheable). **My recommendation is the explicit walk** — it is deterministic,
  which `deepcat:` is not if the SPARQL service is flaky, and determinism is a
  stated requirement of this project.
- 3.6/3.7/3.8 together argue against using `insource:/regex/` for the in-text
  staleness markers (signal 3) at all. Those regexes are better run **locally
  over wikitext we already fetched** for the candidate set: no timeout risk, no
  API etiquette problem, fully deterministic, and it gives us the matching line
  as evidence for free — which the brief requires and which a search snippet
  would only approximate. Search-side `insource:` then stays as an optional
  *discovery* mechanism for widening scope, not as the scoring path.

## 4. `prop=revisions` — distinguishing bot and minor edits

| # | Claim | Status |
| --- | --- | --- |
| 4.1 | `rvprop` accepts `ids,timestamp,user,userid,flags,comment,size,tags,sha1,…` | `DOC` |
| 4.2 | `rvprop=flags` exposes **`minor` only** | `DOC` — and this is the important one |
| 4.3 | There is **no bot flag on revisions**: the bot bit lives in `recentchanges` (`rcprop=flags`, `rcshow=bot`), which has limited retention (typically ~30–90 days) | `DOC`, needs live confirmation |
| 4.4 | `rvlimit > 1` works only when querying a single page | `DOC` |
| 4.5 | Whether `rvprop=tags` carries anything usable for bot classification on dewiki | `OPEN` |

This is the finding most likely to change the plan. If 4.2/4.3 hold, "last
revision that is not a bot edit" **cannot be answered from the revision history
directly** for anything older than the recentchanges window. The workable
approach:

1. Fetch the last N revisions per article with
   `rvprop=ids|timestamp|user|flags|comment|size|tags`.
2. Classify an editor as a bot via `list=allusers&augroup=bot` — one cached
   request gives the full dewiki bot roster (probe P4 counts it). This is
   *current* group membership, so a bot deflagged since the edit is misjudged;
   acceptable for a ranking heuristic, and it should be stated in the README.
3. Apply the configurable size-delta threshold and the `minor` flag on top.

`rvlimit` per page (4.4) also means the fetch stage cannot get revision history
for 500 geosearch results in one request. Realistic shape: one cheap batched
pass for the latest revision of every candidate, then a per-article history
request only for articles that survive a first filter. That keeps request counts
sane and is another argument for the cached `fetch`/`score` split.

## 5. Pageviews REST endpoint

| # | Claim | Status |
| --- | --- | --- |
| 5.1 | `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/{access}/{agent}/{title}/{granularity}/{start}/{end}` | `DOC` |
| 5.2 | For us: project `de.wikipedia.org`, granularity `monthly` | `DOC` |
| 5.3 | Title encoding: spaces → underscores, **then** percent-encode (so `/` and `?` in titles survive) | `DOC` |
| 5.4 | Dates as `YYYYMMDD`; whether `YYYYMMDDHH` is also accepted on monthly | `OPEN` |
| 5.5 | A title with no data returns **404**, not an empty series | `OPEN` — this is the "missing pageview data" test case and the exact shape matters |
| 5.6 | Whether the legacy `rest_v1` path is still the right one, or whether the AQS 2.0 host (`api.wikimedia.org`) is now preferred and/or requires auth | `OPEN` |
| 5.7 | Data starts 2015-07 | `DOC` |

Design note: `access=all-access&agent=user` is probably the right attention
signal — `agent=all-agents` includes spiders and would inflate the multiplier
for exactly the articles bots crawl hardest. The probe fetches both so the
difference is visible before we commit. Also, the multiplier must be computed
over a **fixed** window (e.g. the 12 complete calendar months before the run
month), never "last 12 months from today", or the output churns on every run and
breaks the determinism requirement.

## 6. User-Agent policy and `maxlag`

| # | Claim | Status |
| --- | --- | --- |
| 6.1 | A descriptive `User-Agent` with contact info is mandatory; default library UAs like `python-requests/x` may be blocked outright | `DOC` |
| 6.2 | Preferred format: `<client>/<version> (<contact>) <library>/<version>` | `DOC` |
| 6.3 | Contact may be an email, a URL, or `(wikipedia:de; User:Name)` | `DOC` |
| 6.4 | `maxlag` applies to reads too, and is recommended for any non-interactive task; `maxlag=5` is the conventional value | `DOC` |
| 6.5 | Exceeding maxlag returns HTTP 503 with a `Retry-After` header, which the client must honour | `DOC`, shape unconfirmed |
| 6.6 | No hard rate limit on reads, but serial requests are the safe default; prefer GET over POST | `DOC` |

Consequences: the httpx client gets a UA built from a configured contact string
and **refuses to run if the contact placeholder is unfilled** — that is a
one-line guard that prevents the whole project from ever being an anonymous
scraper. `maxlag=5` on every action-API read; `Retry-After` honoured with
backoff. Note the pageviews REST endpoint is a different service and does not
take `maxlag`; it has its own rate limiting, so the client needs two politeness
profiles, not one.

## Open questions the probe will settle

1. Does generator+`prop=categories` clamp the batch size? (1.4)
2. What hidden maintenance categories really exist on Zürichsee articles, and at
   what frequency? (§2 — nothing can be built on signal 1 before this)
3. Does `{{Veraltet|seit=}}` surface as a category, or must wikitext be parsed? (§2)
4. Is `deepcat:` enabled on dewiki, and does its 5/256 cap bite for the
   categories in scope? (3.4)
5. Does a timed-out regex search error, or silently truncate? (3.8)
6. Is there genuinely no bot flag on revisions? (4.3)
7. How large is the dewiki bot roster, and is caching it viable? (4.3)
8. Exact 404 body for a title with no pageview data. (5.5)
9. `rest_v1` vs AQS 2.0. (5.6)

## Sources

Documentation consulted (via search snippets — the pages themselves could not
be fetched from this environment, so quotations are second-hand):

- [API:Geosearch](https://www.mediawiki.org/wiki/API:Geosearch)
- [API:Continue](https://www.mediawiki.org/wiki/API:Continue), [API:Raw query continue](https://www.mediawiki.org/wiki/API:Raw_query_continue)
- [Help:CirrusSearch](https://www.mediawiki.org/wiki/Help:CirrusSearch), [DeepcatFeature class reference](https://doc.wikimedia.org/CirrusSearch/master/php/classCirrusSearch_1_1Query_1_1DeepcatFeature.html)
- [API:Revisions](https://www.mediawiki.org/wiki/API:Revisions)
- [Vorlage:Veraltet](https://de.wikipedia.org/wiki/Vorlage:Veraltet), [Kategorie:Wikipedia:Veraltet](https://de.wikipedia.org/wiki/Kategorie:Wikipedia:Veraltet)
- [Wikimedia Analytics API — page views](https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/reference/page-views.html), [access policy](https://doc.wikimedia.org/generated-data-platform/aqs/analytics-api/documentation/access-policy.html)
- [Policy:Wikimedia Foundation User-Agent Policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy), [API:Etiquette](https://www.mediawiki.org/wiki/API:Etiquette), [Manual:Maxlag parameter](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter)
