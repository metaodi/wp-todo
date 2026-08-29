# wp-todo

A prioritised TODO list of German Wikipedia articles that probably need
updating, for the Zürichsee region and any other scope you configure.

**It never edits Wikipedia.** Read-only API access, no login, no bot framework.
The product is two files: `out/todo.md` to work through by hand, and
`out/todo.json` with every subscore and evidence snippet behind it.

```sh
uv sync
uv run wp-todo run            # fetch, score, render
uv run wp-todo run --limit 20 # a quick slice while iterating
```

`fetch` is the only command that touches the network. `score` and `render` work
off the cached corpus, so weights can be retuned offline:

```sh
uv run wp-todo fetch            # writes cache/corpus.json
uv run wp-todo render           # re-reads it, rewrites out/
uv run wp-todo fetch --refresh  # bypass the on-disk response cache
```

## The scoring model

```
score = (maintenance + edit_age + markers) × attention
```

Three additive signals, one multiplier. Every one is visible per article in
`todo.json`, and the top three appear in `todo.md`, so an article can always
explain why it surfaced.

### 1. Maintenance categories

Each category on the article is matched against `[scoring.maintenance]` by
**longest prefix**, so `Kategorie:Wikipedia:Veraltet seit 2024` scores as the
dated family rather than as plain `…:Veraltet`.

Categories are fetched **unfiltered**, not with `clshow=hidden`. That is not an
oversight: `Kategorie:Wikipedia:Veraltet seit 2024` is *not* a hidden category,
so filtering on hidden would silently drop the most precise signal available.

On top of the flat weight, a dated marker adds a bonus that grows with how
overdue it is, from whichever of these is present:

- `{{Veraltet|seit=…}}` — the value is free text in the wild ("2012-12", "einiger
  Zeit", empty), so it is parsed leniently. An unparsable value scores nothing
  but is still reported, because a broken `seit=` is itself worth seeing.
- `{{Zukunft|YYYY|MM}}` — "goes stale after that month". This is what actually
  populates the `Veraltet nach <Monat> <Jahr>` categories; `Kategorie:Wikipedia:Zukunft`
  itself is empty.
- The dated categories, when the wikitext is unavailable.

A note on weights: in the Bezirk Horgen sample, about two thirds of all
maintenance-category rows were dead-link bookkeeping from InternetArchiveBot.
Those families are pinned near zero deliberately — otherwise the worklist ranks
by bot noise. Retune in `scope.toml`, not in code.

### 2. Time since the last substantive edit

The newest revision that is neither a bot edit nor a trivial one, on a
saturating curve (`edit_age_half_life_days`) rather than a linear one: the
difference between three months and three years matters more than between
five years and ten.

"Not a bot edit" cannot be read off the revision history — **revisions carry no
bot flag**, and the `recentchanges` table that does only retains about 30 days.
Authorship is therefore decided against the wiki's bot group, fetched once per
run. That is current group membership, so a bot deflagged since its edit is
misjudged; acceptable for a ranking heuristic.

"Not trivial" is a size delta of at least `substantive_min_bytes`. This does the
real work: dewiki has prolific humans who make hundreds of thousands of typo
fixes, and no flag distinguishes them from a bot.

### 3. In-text staleness markers

Regexes over the wikitext for `Stand: YYYY`, `seit YYYY`, and bare adverbs
(`derzeit`, `aktuell`, `zurzeit`) — the last only count when a year appears
within `year_window` characters, since "derzeit" in a sentence about 1961 is
history, not staleness. A rule fires when the year is more than
`max_age_years` old, and the matching line is captured as evidence.

These run **locally over fetched wikitext**, not as CirrusSearch
`insource:/regex/`. Server-side regex search times out into partial results with
only a warning, and returned different totals on two runs of the same query —
which would break determinism outright.

### 4. Attention multiplier

Mean monthly pageviews over `months` complete calendar months, as
`1 + strength × log₁₀(1 + views/pivot)`, capped. A stale high-traffic article
outranks a stale stub; it can never make a fresh article look stale. Missing
pageview data is neutral (multiplier 1.0), never a penalty.

`agent = "user"` excludes spiders — about 19 % of raw views on a sample article,
and skewed towards exactly the pages bots crawl hardest.

## Rate limiting and request volume

Wikimedia sets no hard limit on read requests, but asks for a descriptive
User-Agent, serial rather than parallel requests, and `maxlag` on unattended
tasks. All of that is enforced in one place, `client.py`:

- **Serialised.** One request at a time, with a floor of `http.delay_s` seconds
  between the start of one and the next. That floor is the hard ceiling on the
  request rate: `1.0` means at most one request per second.
- **`maxlag=5`** on every action-API read. A rejection arrives as **HTTP 200**
  with `error.code == "maxlag"` and no `Retry-After`, so the response body is
  inspected on every request, not just the status code.
- **Exponential backoff** on 429, 5xx and maxlag, honouring `Retry-After`.
- **A per-run request budget** (`http.max_requests`). Hitting it stops the run
  loudly, so a scope change cannot quietly become an unbounded crawl.
- **A descriptive User-Agent** with contact information, refusing to run without
  one.

The pageviews endpoint is a different service on a different host, and it goes
through the same path — it carries most of the volume, so it is the last place
that should be allowed to hammer.

### Measured cost

A full run over the configured Zürichsee scope: **790 articles, roughly 1 600
requests, about 27 minutes** at one request per second. Discovery is nearly
free — one request per tile returns categories and the latest revision for up
to 500 articles. Everything after that costs about two requests per article:
one for revision history (`rvlimit` is single-page only, so it cannot be
batched) and one for pageviews.

Two things keep that from growing without bound:

- `fetch.detail_top_n` ranks articles by a lower-bound score computed from
  discovery data alone and fetches full detail only for the top N. Articles past
  the cut still appear, marked `provisional`, with a score that can only be an
  underestimate.
- Responses are cached on disk, keyed per request — and batched title queries
  are cached **per title**, so adding one article to `scope.toml` costs one
  request rather than re-fetching every batch it lands in.

## Editing `config/scope.toml`

- `[[geo]]` — one geosearch tile: `label`, `lat`, `lon`, `radius_m`. **10 km is
  the API's hard maximum**, which is why the region is tiled. Tiles may overlap;
  articles are de-duplicated and keep the label of the first tile that found
  them, in file order.
- `[[category]]` — `name` and `depth`, walked with `list=categorymembers`. We
  walk it ourselves rather than using `deepcat:`, which depends on an external
  SPARQL service and caps at 256 categories — neither is deterministic enough.
- `pages` — titles always checked. Must appear **before** any `[table]` header,
  as TOML requires.
- `[exclude]` — `titles`, `title_patterns`, `category_patterns` (regexes) for
  lists, disambiguation pages and Jahresartikel.
- `[scoring]` — every weight and curve parameter.
- `[http]` — politeness: `delay_s`, `maxlag`, `max_retries`, `timeout_s`,
  `max_requests`.
- `[fetch]` — `detail_top_n`, the per-article request budget.

`meta.contact` must be a real email address or project URL: the Wikimedia
User-Agent policy requires contact information, and the loader refuses a
placeholder rather than letting the tool run as an anonymous scraper.

## Determinism

Two runs over the same data produce byte-identical output. Ordering is stable
with an explicit title tie-break, floats are rounded at fixed precision, and
`todo.md` contains no timestamp at all.

Ages are measured from a reference **snapped to the first of the month**, not
from the fetch instant. Otherwise a weekly job would move every article's
edit-age subscore every week and the diff would be entirely noise. The exact
fetch date is still recorded in `cache/corpus.json` and in `todo.json`.

## Tests

```sh
uv run pytest
```

The suite never touches the network. `tests/fixtures/http/` **is** a recorded
response cache; the tests run the real pipeline against it with an offline
client, so a missing fixture raises instead of quietly making a request.

To re-record, run the `Verify assumptions` workflow with `mode=fixtures`, or
locally:

```sh
export WP_TODO_CONTACT="https://github.com/metaodi/wp-todo"
uv run python scripts/record_fixtures.py
```

## Workflows

- `ci.yml` — ruff, mypy and pytest on every push and PR.
- `refresh.yml` — weekly, plus manual. Runs the pipeline against the live API and
  commits `out/` if it changed. It fails loudly on an API error and refuses to
  commit a suspiciously short list over a good one.
- `verify.yml` — manual. Runs `scripts/phase0_probe.py` against the live API and
  records the answers in `docs/api-notes.md`; also re-records test fixtures.

## Where the API behaviour is documented

`docs/api-notes.md` — what was verified live, with the recorded evidence in
`docs/phase0-probe-output.json`. Anything surprising in this codebase is
explained there.
