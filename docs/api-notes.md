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

Tally: **34 `LIVE`**, 6 `OPEN`.

---

## Headline: five things in the brief that the API contradicts

1. **`clshow=hidden` is not sufficient to harvest maintenance categories.**
   `Kategorie:Wikipedia:Veraltet seit 2024` is **not a hidden category**
   (`categoryinfo` reports no `hidden` flag; 149 members). Two sample pages
   taken from it return *no* Veraltet category at all under `clshow=hidden`,
   and the category only under an unfiltered `prop=categories`. Every other
   maintenance category checked *is* hidden. Fetching hidden categories only
   would silently drop the single most precise staleness signal we have.
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
4. **`{{Zukunft}}` is a dead signal.** `Kategorie:Wikipedia:Zukunft` exists but
   has **0 members**. Drop it, or find what dewiki actually uses.
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
| `Kategorie:Wikipedia:Zukunft` | yes | — | **0** |
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
| Why `hastemplate:Veraltet` (7 804) ≫ `Kategorie:Wikipedia:Veraltet` (1 750) | `OPEN` |

Note on the first run: `hastemplate:"Veraltet" incategory:"Kanton Zürich"`
returned 0, which looked like `hastemplate` failing. The control queries show
`hastemplate` is fine — `incategory:"Kanton Zürich"` was the empty half. Worth
remembering when writing scope config: **`incategory` is exact and shallow**,
which is precisely what `deepcat` is for.

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
| How stale the most recent complete month is | `OPEN` |

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

## Remaining `OPEN` items

1. Is `Kategorie:Wikipedia:Veraltet seit 2025` also non-hidden? (Assume the
   whole `seit` family may be visible; fetching unfiltered categories makes it
   moot.)
2. Why `hastemplate:Veraltet` reports 4× the members of the category.
3. What dewiki uses in place of the empty `Kategorie:Wikipedia:Zukunft`.
4. `deepcat:` depth/category caps in practice.
5. AQS 2.0 base path (not needed while `rest_v1` works).
6. Genuine-lag `maxlag` behaviour, and pageview data recency.

None blocks the first milestone. Items 1–3 are worth one more probe run before
the scoring weights are finalised.

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
