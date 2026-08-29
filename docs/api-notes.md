# Phase 0 — API discovery notes

**Status: gate closed. Every claim below was verified against the live
de.wikipedia API**, from GitHub Actions (`.github/workflows/verify.yml`),
because the development sandbox has no egress to Wikimedia hosts.

Evidence: `docs/phase0-probe-summary.md` (answer sheet) and
`docs/phase0-probe-output.json` (every request and response), produced by
`scripts/phase0_probe.py`. Four runs on 2026-08-28; the numbers here are from
run 3 (probes P1–P7) and run 4 (P8) unless stated.

| Mark | Meaning |
| --- | --- |
| `LIVE` | Observed in a recorded response |
| `OPEN` | Still unverified — do not build on it |

Tally: **47 `LIVE`**, 2 `OPEN`.

---

## Headline: five things in the brief that the API contradicts

1. **`clshow=hidden` is not sufficient to harvest maintenance categories.**
   The **entire `Veraltet seit YYYY` family is non-hidden** — every year from
   2018 to 2025 reports no `hidden` flag, **2 107 articles in total** — while
   `Veraltet in …` and `Veraltet nach Jahr …` *are* hidden. Sample pages taken
   from `…seit 2024` return no Veraltet category at all under `clshow=hidden`,
   and the category only under an unfiltered `prop=categories`. Filtering on
   hidden would silently drop the single most precise staleness signal there is,
   across two thousand articles.

   | Family | Hidden | Members |
   | --- | --- | --- |
   | `Veraltet seit 2018…2025` | **no** | 357, 338, 394, 312, 250, 184, 149, 123 |
   | `Veraltet in zwei bis drei Jahren` | yes | 525 |
   | `Veraltet in über fünf Jahren` | yes | 614 |
   | `Veraltet nach Jahr 2024` / `2026` | yes | 229 / 516 |
2. **There is no bot flag on revisions.** Confirmed: over 50 revisions of
   *Thalwil* the union of keys is
   `anon, comment, minor, parentid, revid, size, tags, temp, timestamp, user, userid`.
   No `bot`. The bot bit exists only in `recentchanges`, whose oldest entry was
   `2026-07-29` — a **30-day** window, useless for "last substantive edit".
3. **`insource:/regex/` is non-deterministic and fails silently.** The
   expensive regex returned **HTTP 200 with partial results** and only a
   warning: *"The regex search timed out, so only partial results are
   available."* Two runs of the identical query reported **24 175** and
   **20 029** total hits, taking 18.9 s and 23.8 s. Anything built on it
   breaks the determinism requirement outright.
4. **`{{Zukunft}}` categorises somewhere else entirely.**
   `Kategorie:Wikipedia:Zukunft` exists but has **0 members** — yet the template
   is in active use. Found while building the fetch stage: dewiki writes
   `{{Zukunft|YYYY|MM}}` ("goes stale after that month"), and *that* is what
   populates the `Veraltet nach <Monat> <Jahr>` categories. Confirmed on
   *Küsnachter Dorfbach*, which carries `{{Zukunft|2025|05}}` and sits in
   `Kategorie:Wikipedia:Veraltet nach Mai 2025` with no `{{Veraltet}}` anywhere
   in its wikitext. Watching for the category name alone would have worked;
   watching for the template name would have found nothing.
5. **A `maxlag` violation comes back as HTTP 200, with no `Retry-After`.**
   Observed twice (`maxlag=-1`, `maxlag=0`): status 200, body
   `{"error":{"code":"maxlag","info":"Waiting for 10.64.0.50: 0.538996 seconds lagged.","lag":...}}`.
   A client that only checks the status code will treat a lag rejection as a
   successful empty result.

One thing in the brief the API **confirms** and I had wrongly flagged as
doubtful: the dated `Veraltet in … Jahren` categories are real. There are in
fact three families — see §2.

---

## 1. `list=geosearch` as a generator

| Claim | Status |
| --- | --- |
| `ggsradius` is 10–10 000 m. 10 001 → `outofrange: The value "10001" for parameter "ggsradius" must be between 10 and 10,000.` | `LIVE` |
| `ggslimit` max **500**, `highmax` 5000 (`paraminfo`). `ggslimit=501` is a **warning and a clamp**, not an error; `ggslimit=max` also yields 500 | `LIVE` |
| Generator + `prop=categories&clshow=hidden&cllimit=max` + `prop=revisions` in **one request** works: 500 pages, revisions on all 500, hidden categories on 114 of them (155 rows), `batchcomplete` present, **no continuation needed** | `LIVE` |
| Forcing `cllimit=10` produces `continue: {"clcontinue": "370065\|Wikipedia:Defekte_Weblinks/…", "continue": "\|\|"}` with `batchcomplete` **absent** | `LIVE` |

Implementation consequences:

- 10 km max confirms the tiling design. Four tiles covered 744 distinct
  articles across the Bezirk Horgen / left-shore area.
- **Continuation returns the same 500 pages again**, carrying *more categories*
  for pages that had more than the limit allowed. The client must **merge
  category lists by `pageid` across batches**, not append pages. Treating each
  batch as a fresh page set would duplicate articles and truncate their
  categories.
- `batchcomplete` is the completion signal, not the absence of results.
- One request per tile suffices at `cllimit=max` in this area, but a denser
  tile will continue — so continuation must be handled centrally regardless.

## 2. Hidden maintenance categories in the Bezirk Horgen area

Observed over **744 articles**, four tiles (full table in the answer sheet):

| Category | Articles |
| --- | --- |
| `Wikipedia:Weblink offline IABot` | 42 |
| `Wikipedia:Defekte Weblinks/Ungeprüfte Archivlinks 2019-05` | 41 |
| `Wikipedia:Weblink offline` | 40 |
| **`Wikipedia:Belege fehlen`** | **22** |
| `Wikipedia:Bilderwunsch an bestimmtem Ort` | 20 |
| `Wikipedia:Vorlagenfehler/Vorlage:Infobox Burg` | 19 |
| **`Wikipedia:Veraltet nach Mai 2025`** | **3** |
| **`Wikipedia:Veraltet`**, `Veraltet nach Juni 2020` | 2 each |
| **`Wikipedia:Lückenhaft`**, `Wikipedia:Neutralität` | 2 each |
| `Veraltet in zwei bis drei Jahren`, `Veraltet in über fünf Jahren`, `Veraltet nach Jahr 2026` | 1 each |

The distribution is the finding: roughly **two thirds of all hidden-category
rows are dead-link bookkeeping** from InternetArchiveBot, not editorial
signals. `Belege fehlen` (22) is the only high-volume editorial one; the
`Veraltet` family totals about 9 articles in the whole area. Weight
accordingly — an unweighted count would rank the worklist by bot noise.

Verified names and sizes (`prop=categoryinfo`, whole wiki):

| Category | Exists | Hidden | Members |
| --- | --- | --- | --- |
| `Kategorie:Wikipedia:Belege fehlen` | yes | yes | 52 052 |
| `Kategorie:Wikipedia:Weblink offline` | yes | yes | 45 719 |
| `Kategorie:Wikipedia:Lückenhaft` | yes | yes | 16 014 |
| `Kategorie:Wikipedia:Überarbeiten` | yes | yes | 9 146 |
| `Kategorie:Wikipedia:Veraltet` | yes | yes | 1 750 |
| `Kategorie:Wikipedia:Veraltet in zwei bis drei Jahren` | yes | yes | 525 |
| `Kategorie:Wikipedia:Veraltet seit 2024` | yes | **NO** | 149 |
| `Kategorie:Wikipedia:Veraltet seit 2025` | yes | `OPEN` | 123 |
| `Kategorie:Wikipedia:Veraltet nach Mai 2025` | yes | yes | 17 |
| `Kategorie:Wikipedia:Zukunft` | yes | — | **0** (see headline 4) |
| `Kategorie:Wikipedia:Defekter Weblink` | **no** | — | — |

**Three dated `Veraltet` families exist**, all seen in the wild:
`Veraltet seit YYYY`, `Veraltet nach <Monat> YYYY` / `Veraltet nach Jahr YYYY`,
and `Veraltet in <zwei bis drei\|über fünf> Jahren`.

### `{{Veraltet}}` and `seit=`

Sampled invocations, verbatim:

| Page | Invocation | Categories it lands in |
| --- | --- | --- |
| 8hours | `{{veraltet\|seit=2012-12}}` | `Wikipedia:Veraltet` |
| 1001 Movies You Must See Before You Die | `{{Veraltet\|seit=}}` | `Wikipedia:Veraltet` |
| 600-Meter-Lauf | `{{veraltet\|seit=einiger Zeit\|des folgenden Abschnitts}}` | `Wikipedia:Veraltet` |
| 3×3 German Championship | `{{Veraltet\|seit=2024}}` | `Wikipedia:Veraltet seit 2024` (**not hidden**) |
| SPOT (Satellit) | — | `Veraltet nach Jahr 2024`, `Veraltet nach Mai 2025` |

So `seit=` is **free text**: a bare year, a `YYYY-MM`, an empty value, or German
prose. The brief's "`Veraltet` with a malformed `seit=`" test case is not an
edge case — it is the common case, and the template name is also case-varying
(`{{veraltet}}` / `{{Veraltet}}`).

**Design consequences:** fetch categories **unfiltered**, then classify by name
prefix rather than relying on the hidden flag; keep the prefix→weight mapping in
`scope.toml`; parse `seit=` leniently, extracting a year where one exists and
recording the raw string as evidence when it does not.

## 3. CirrusSearch keywords on dewiki

| Probe | Result |
| --- | --- |
| `deepcat:"Bezirk Horgen"` / `deepcategory:` | `LIVE` — both work, **282 hits** |
| `hastemplate:Veraltet` | `LIVE` — **7 804 hits**; bare, quoted and `"Vorlage:Veraltet"` all identical |
| `incategory:"Kanton Zürich"` | `LIVE` — **4 hits**: the category is nearly empty |
| `insource:/Stand: 201[0-9]/` (bare) | `LIVE` — completed in 4.3–5.0 s, 6 779 hits, no timeout |
| `insource:/[Ss]tand:? *20[0-2][0-9]/` | `LIVE` — **timed out, HTTP 200, warning, partial results**, 18.9 s / 23.8 s, 24 175 → 20 029 hits across two runs |
| `deepcat:` depth 5 / 256-category cap | `OPEN` — documented, not exercised |
| Why `hastemplate:Veraltet` (7 803) ≫ `Kategorie:Wikipedia:Veraltet` (1 750) | `LIVE` (measured), explanation inferred — see below |
| `deepcat:` silently truncates a wide tree, with only a warning | `LIVE` |

Note on the first run: `hastemplate:"Veraltet" incategory:"Kanton Zürich"`
returned 0, which looked like `hastemplate` failing. The control queries show
`hastemplate` is fine — `incategory:"Kanton Zürich"` was the empty half. Worth
remembering when writing scope config: **`incategory` is exact and shallow**,
which is precisely what `deepcat` is for.

### Why `hastemplate` counts four times the category

Measured: `hastemplate:Veraltet` 7 803 (identical in ns0 and across all
namespaces), `incategory:"Wikipedia:Veraltet"` 1 750, `insource:"{{Veraltet"`
16 266, and **zero redirects** to `Vorlage:Veraltet`.

The plain category does not hold every article carrying the template — it holds
the ones with *no dated variant*. The rest are distributed across the dated
families, and those add up: 1 750 + 2 107 (`seit` 2018-2025) + 745
(`nach Jahr` 2024/2026) + 1 139 (`in …`) ≈ 5 741, before the year categories
this probe did not enumerate. That accounts for the gap. Stated as an
inference from the arithmetic, not as a verified mechanism.

### `deepcat:` truncates quietly

| Tree | Hits | Warning |
| --- | --- | --- |
| `deepcat:"Bezirk Horgen"` | 282 | none |
| `deepcat:"Kanton Zürich"` | 13 176 | none |
| `deepcat:"Schweiz"` | 67 250 | *"Deep category query returned too many categories. Only a subset of categories has been applied."* |

The cap bites somewhere between a canton and a country, and when it does the
result is a **warning attached to a successful response**, not an error. A
client that ignores warnings gets a silently truncated result set. Another
reason the category trees are walked with `list=categorymembers` here.

**Recommendation:** use `deepcat:` / `hastemplate:` for *discovery* (widening
scope), and run the signal-3 regexes **locally over fetched wikitext**. Local
regex is deterministic, has no timeout, cannot be silently truncated, and hands
us the matching line as evidence for free.

## 4. `prop=revisions` — bot and minor edits

| Claim | Status |
| --- | --- |
| Revision keys: `anon, comment, minor, parentid, revid, size, tags, temp, timestamp, user, userid` — **no `bot`** | `LIVE` |
| 20 of the last 50 *Thalwil* revisions carry `minor` | `LIVE` |
| `rvlimit` + multiple titles → `invalidparammix: … may only be used on a single page` | `LIVE` |
| Oldest `recentchanges` entry: `2026-07-29T22:02Z` — a ~30-day window | `LIVE` |
| dewiki accounts in the `bot` group: **65** (`list=allusers&augroup=bot`, one cacheable request) | `LIVE` |

The group check illustrates why this needs care: `Xqbot` and
`InternetArchiveBot` are in the `bot` group; **`Aka` is not** — a human sysop
with 4.6 M edits, most of them tiny typo fixes. So neither the bot group nor
edit volume identifies a *substantive* edit on its own; the configurable
size-delta threshold is doing the real work, with the bot roster and the `minor`
flag as supporting filters. A `temp` key also appears — temporary accounts,
which the classifier should treat as anonymous rather than crashing on.

Shape of the fetch stage this forces: **one batched request for the latest
revision of many articles** (works — 500 at a time, see §1), then a
**per-article** history request for survivors only, since `rvlimit` is
single-page.

## 5. Pageviews REST endpoint

| Claim | Status |
| --- | --- |
| `https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/de.wikipedia.org/{access}/{agent}/{title}/monthly/{start}/{end}` | `LIVE` |
| Encoding `quote(title.replace(" ", "_"), safe="")` works for umlauts (`Wädenswil`), spaces (`Bezirk Horgen`) and accents (`Rüschlikon`) — 12/12 months each | `LIVE` |
| Missing title → **HTTP 404** with a JSON body (`detail`, `status`, `title`, `type`) | `LIVE` |
| Both `YYYYMMDD` and `YYYYMMDDHH` accepted; timestamps come back as `YYYYMMDD00` | `LIVE` |
| `agent=user` vs `all-agents` on *Thalwil* 2025: **13 232 vs 16 382** views — ~19 % non-human | `LIVE` |
| AQS 2.0 on `api.wikimedia.org` | `OPEN` — both paths tried returned an HTML wiki page, not an API. `rest_v1` works; no reason to move |
| The monthly endpoint returns the **current, incomplete month** (a `2026080100` point on 2026-08-29); daily data runs about one day behind | `LIVE` |

Use `agent=user`, and compute the multiplier over a **fixed** window (the N
complete calendar months before the run month) so the output does not churn.

## 6. User-Agent policy and `maxlag`

| Claim | Status |
| --- | --- |
| `wp-todo-phase0-probe/0.1 (https://github.com/metaodi/wp-todo) python-urllib/3.12` accepted; ~150 requests at ~1 req/s across four runs, no throttling, no 429 | `LIVE` |
| A project URL satisfies the contact requirement — no personal email need leave the runner | `LIVE` |
| `maxlag=5` on a read: accepted, HTTP 200 | `LIVE` |
| **A maxlag rejection returns HTTP 200, `error.code == "maxlag"`, no `Retry-After`** | `LIVE` |
| Whether genuine replication lag returns 503 + `Retry-After` as documented | `OPEN` — handle both |
| `paraminfo` limits: geosearch `radius` 10–10 000; `limit` 1–500 (highmax 5000) for geosearch, search, revisions and categories alike | `LIVE` |

The client must therefore **parse the response body for `error.code` on every
request**, not branch on HTTP status. Guard the User-Agent: refuse to run with
an unfilled contact placeholder.

---

## 7. The research stage's endpoints (P10)

`src/wp_todo/enrich.py` was written against documentation without egress. P10
settled all three of its endpoints from a runner on 2026-08-29.

| Question | Answer | Mark |
| --- | --- | --- |
| `prop=pageprops&ppprop=wikibase_item` returns the item id | yes, at `pages[].pageprops.wikibase_item` (Thalwil → `Q68959`, Adliswil → `Q68210`) | `LIVE` |
| Wikibase REST path | `/w/rest.php/wikibase/v1` works; **`v0` is 404** | `LIVE` |
| REST statements payload shape | as assumed: `rank`, `value.type == "value"`, `value.content` (`{amount, unit}` for a quantity), `qualifiers[].property.id` — `P585` present on the population statement | `LIVE` |
| `?property=P1082` filters the statements response | yes | `LIVE` |
| `lllang` accepts a pipe-separated list | **no — and it fails silently** | `LIVE` |

### The one that was wrong: `lllang` is not multi-valued

`paraminfo` reports `multi: false` for `query+langlinks`'s `lang` parameter, and
the live behaviour matches:

| request | langlinks returned |
| --- | --- |
| `lllang=en\|fr\|it` | **0** |
| `lllang=en` | 1 |
| no `lllang` | 42 |

The pipe form is accepted with **no error and no warning** — it simply matches
nothing. In `wp-todo` that would have surfaced as *"Keine anderssprachige
Fassung verlinkt"* on every single article: an answer rather than a failure, and
so invisible. This is the case for probing an endpoint even when the code
"obviously" works.

Fixed by dropping `lllang` entirely and filtering client-side. It costs nothing:
`lllimit=max` is one request either way, and 42 links for a municipality is a
trivial payload. `tests/test_enrich.py::TestLanglinks::test_lllang_is_never_sent`
asserts on the outgoing URL, not on the parsed result, because the parsed result
of the broken form is indistinguishable from a correct empty one.

### Still assumed rather than measured

The `en`/`fr`/`it` article *content* fetch (`prop=revisions&rvprop=content`
against `{lang}.wikipedia.org/w/api.php`) is the same module already verified
against dewiki in §4; only the host differs. Not separately probed.

## 8. `robots.txt` blocks the Wikibase REST API (P10 follow-up)

Found by running the research workflow on five real articles on 2026-08-29, not
by a probe. Every dossier reported *"Keine Abweichungen gegenüber Wikidata"* and
the comparison had never run once.

```
INFO wp_todo.webclient robots.txt disallows
     https://www.wikidata.org/w/rest.php/wikibase/v1/entities/items/Q50039750/statements
```

| Finding | Mark |
| --- | --- |
| Wikimedia's `robots.txt` carries `Disallow: /w/` | `LIVE` |
| The Wikibase REST API lives under `/w/rest.php`, so a robots-respecting client blocks its own API call | `LIVE` |

`robots.txt` is a crawl-exclusion convention: it tells automated agents not to
walk a *site*. Calling a published API, at the documented rate, with a
User-Agent naming a contact, is a different activity, and the Wikimedia API
etiquette policy is what governs it — that is why `WikiClient` never consulted
robots.txt for `/w/api.php` either.

`webclient.API_PREFIXES` now exempts the named API endpoints from the crawl
check. **Only that check.** The pacing, the per-run budget, the size cap and the
User-Agent all still apply, and an ordinary page under `/w/` on any other host
is still refused.

### The part worth remembering

The failure was invisible because the dossier rendered "no differences found"
for both "compared, nothing differs" and "never retrieved". A section that
cannot tell those apart is not reporting, it is asserting. `Dossier.wikidata_checked`
now carries the distinction and the renderer prints three different sentences.

---

## Remaining `OPEN` items

1. AQS 2.0 base path. Not needed while `rest_v1` works; both hosts tried
   returned a wiki portal page rather than an API.
2. Whether *genuine* replication lag returns 503 + `Retry-After` as documented,
   rather than the HTTP 200 seen with a forced threshold. Cannot be triggered on
   demand; the client handles both.

Neither blocks anything. The three research-stage items previously listed here
were closed by probe P10 on 2026-08-29 — see §7.

### Answered since the first pass

- **What dewiki uses instead of `Kategorie:Wikipedia:Zukunft`**: `{{Zukunft|YYYY|MM}}`,
  which populates the `Veraltet nach <Monat> <Jahr>` families. See headline 4.
  `wp-todo` parses the template directly and dates its overdue bonus from it.
- **Is the whole `seit` family non-hidden?** Yes — all of 2018-2025. See
  headline 1.
- **Why `hastemplate` outcounts the category** — see §3.
- **`deepcat:` caps in practice** — it truncates with a warning, see §3.
- **Pageview recency** — the monthly endpoint serves the running month; see §5.

## Re-verifying

`.github/workflows/verify.yml` → **Run workflow**. Inputs: `probes` (`all` or
e.g. `P1,P4`), `contact`, `commit_results`. It publishes the answer sheet to the
job summary, uploads raw evidence as the `phase0-probe-raw` artifact, and
commits the results back to the branch. Locally:

```sh
export WP_TODO_CONTACT="https://github.com/metaodi/wp-todo"
python3 scripts/phase0_probe.py           # stdlib only, no install
```

The probe is read-only by construction: `action=query`, `action=paraminfo` and
GETs to the pageviews endpoint. There is no code path in it that can write, and
there must never be one.
