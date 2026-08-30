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

Once the worklist has told you *which* article to look at, `research` tells you
*what* on it is worth checking:

```sh
uv run wp-todo research "Küsnachter Dorfbach"   # writes research/<id>-<slug>.md
uv run wp-todo research "Küsnachter Dorfbach" --agent   # ... and asks a model
```

Any de.wikipedia title works, in or out of the configured scope, with or without
a fetched corpus — an article that is not in `cache/corpus.json` is fetched on
its own, about five requests. Or run it from the browser: **Actions → Research an
article → Run workflow**, type the title, and the dossier appears in the job
summary and is committed to `research/`.

**It still never edits.** The dossier is a briefing to read, not a draft and not
a source — see `docs/research-policy.md`.

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

`edit_age_weight` is deliberately lower than the maintenance weights. The signal
applies to nearly every article — 766 of 790 in the first full run — so a high
weight buries the handful an editor has actually flagged. Halving it from 40 to
20 moved those 22 articles from 11 to 15 of the visible top 50.

"Not a bot edit" cannot be read off the revision history — **revisions carry no
bot flag**, and the `recentchanges` table that does only retains about 30 days.
Authorship is therefore decided against the wiki's bot group, fetched once per
run. That is current group membership, so a bot deflagged since its edit is
misjudged; acceptable for a ranking heuristic.

"Not trivial" is a size delta of at least `substantive_min_bytes`. This does the
real work: dewiki has prolific humans who make hundreds of thousands of typo
fixes, and no flag distinguishes them from a bot.

### 3. In-text staleness markers

Regexes over the wikitext for `Stand: YYYY` and bare adverbs (`derzeit`,
`aktuell`, `zurzeit`) — the adverbs only count when a year appears within
`year_window` characters, since "derzeit" in a sentence about 1961 is history,
not staleness. A rule fires when the year is more than `max_age_years` old, and
the matching line is captured as evidence.

A `seit YYYY` rule was tried and removed. It fired on 244 of 790 articles and
every sample was historical prose — *"seit 1928 alljährlich"*, *"Seit der
Streckeneröffnung im Jahre 1954"* — and for 189 of them it was the only marker,
so the worklist offered a historical fact as its reason for calling the article
stale. The rule is left commented out in `scope.toml` with that note.

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

## The research stage

`wp-todo research "<Titel>"` builds a per-article dossier under `research/`.
It answers a different question from the worklist — not "which article", but
"what on this one has probably gone stale, and where would I check".

It takes the article from the fetched corpus when it is there and fetches it on
its own when it is not, so any de.wikipedia title works. Four sections, plus the
source classification below:

- **Abweichungen gegenüber Wikidata** — infobox values compared against the
  item's statements, with the point-in-time qualifier on each side. A dewiki
  `EINWOHNER` of 8 500 (Stand 2018) against a Wikidata `P1082` of 9 240 (Stand
  2025) is a finding with a source and no inference anywhere in it. Agreement is
  reported too, in its own section: knowing a figure has already been checked is
  worth an editor's time.
- **Möglicherweise fehlend** — section headings en/fr/itwiki have and this
  article does not, boilerplate (`Einzelnachweise`, `Weblinks`, …) filtered out.
  A weak signal, and labelled as one: the same content often lives here under a
  different name.
- **Angaben zum Prüfen** — every infobox value and in-text marker that can go
  stale, with its line number, section and as-of year. This is the list to work
  through, not a list of errors.
- **Belege dieses Artikels** — how many references, and how old. "The newest
  source on this page is from 2011" is often the most informative line in the
  file.

Three things it deliberately does not do: draft article prose, post anywhere, or
decide who is right. Wikidata is frequently the side that is out of date, and
the dossier gives you both values and both links rather than a verdict.

### `--agent`: the part that costs money

Off by default. Without the flag no model is consulted and the run is free; with
it, two more sections appear.

The order of work is the whole idea: **the article's own references are read
first, and the open web only for what they could not answer.** A page whose
population figure says "Stand 2018" very often already cites the office that has
since published 2025 — so reading what is already cited is both cheaper and
better than searching, and it cannot drag in a source nobody has vetted. Only
the claims the references leave open trigger a single web-search call for the
whole article; sections other editions have and this one does not get a few
bullet points summarising what the *linked* text says.

Up to sixteen model calls per article at the shipped defaults, a few cents.
Every one is cached, so a rerun without `--refresh` replays it for nothing. The
ceiling has to be able to pay for what the claim ceilings authorise, and a
config where it cannot is refused at load time rather than silently doing less:
at 10 against ceilings authorising 12 the section summaries — one call, and in
practice the most substantial part of a dossier — were structurally the first
thing starved. A call is now reserved for them before the per-claim loops
start.

Two more things go on the agenda besides dated claims. A **Wikidata
disagreement** is the sharpest question the free stage produces — two values
that cannot both be right, with a link on each side — and it used to be
computed, rendered and never asked; it now gets a question of its own, ranked
just behind an editor's own `{{Veraltet}}`. **Undated infobox values** ("the
mayor is X", no date anywhere) are asked of the article's own references only:
a web search cannot settle one cheaply, but the official website is fetched and
paid for by then.

Running out of the budget is reported in the dossier, and so is everything else
that left a claim unreported. Those are not one fact: a claim the model
answered `nothing_found`, a claim whose answer a gate refused, and a claim that
was never asked each get their own line, because the middle one used to print
as *"keine Quelle sagte etwas dazu"* — the opposite of what happened.

Then the paranoid half. Everything the model says goes through five checks
applied **in code, afterwards**:

| check | what it refuses |
| --- | --- |
| quote containment | a quote that is not verbatim in the stored document — a paraphrase is refused even when it is *true* |
| provenance | a document index that resolves to nothing; the model picks by number and never emits a URL |
| recency | a source older than the article — demoted to context, not sold as an update |
| circularity | a copy of the article. `trust` cannot override this; trusting a mirror is always an error |
| source standing | a host you blocked. Applied before the fetch, and always reported |
| numeric containment | a figure the quote does not carry — demoted to "Schluss des Modells", never printed as "Laut Quelle" |

Every rejection is counted and shown. A run where the quote check rejected six
of twenty answers is telling you something about that run.

Alongside the dossier it commits `research/<id>-<slug>.transcript.md`: what was
asked, what came back, and which check refused what — including the answers that
were thrown away. It carries a louder header than the dossier for that reason.

Needs `uv sync --extra agent` and `ANTHROPIC_API_KEY`. Model, effort and every
budget live in `[research]` in `config/scope.toml`.

### What it costs, and what it will not do to a website

Two action-API requests per article (`pageprops`, `langlinks`), one Wikidata
REST GET, and one content fetch per compared language. Everything is cached, so
a rerun without `--refresh` makes no requests at all and produces a
byte-identical file.

Hosts outside Wikimedia go through a separate client (`webclient.py`), which is
stricter than the Wikimedia one rather than looser, because those hosts never
asked to be read by anybody's tool:

- **GET only.** No other verb is implemented, so none can be called.
- **`robots.txt` honoured**, fetched once per host through the same cache and
  the same pacing rather than by `urllib` behind our back.
- **Hosts paced independently.** One site's politeness delay is not an
  allowance to hammer the next.
- **Its own User-Agent.** Claiming the Wikimedia one at a cantonal statistics
  office would be a lie about who is calling.
- **Size-capped and content-type filtered**; a skip is cached *with its reason*,
  so a rerun does not re-ask a host for the same 404 and the dossier can say
  which documents it could not read. HTML, plain text and PDF are read; a
  cantonal statistics office publishing PDF was the largest recall hole this
  stage had. Needs `uv sync --extra pdf`.
- **Fetched text is never part of the system prompt.** It goes to the model as
  user content. Worth being exact about what that does and does not buy: a
  hostile page can carry both an instruction and a sentence that satisfies the
  quote gate, because the gate proves the sentence is *on the page*, not that
  the page is honest. What it guarantees is that the quote really is at the URL
  shown — which is what makes "check every finding at its source" something a
  reader can carry out.

The stage is opt-in per article and is **not** wired into `refresh.yml`. The
weekly job stays Wikimedia-only.

### Quellen einstufen und aussortieren

The dossier classifies every host the article cites — and, once the open-web
stage lands, every host it finds. Two separate axes: a **tier** from the host
(`amtlich`, `Presse/Wissenschaft`, `nicht eingestuft`) and any number of
**signals**, including two that cost nothing because the article supplies them
itself: the official website named in its own infobox, and the domains it
already cites.

```
| Domain                    | Einstufung                                    | Belege |
| `www.web.statistik.zh.ch` | amtlich                                       |      2 |
| `www.zsz.ch`              | Presse/Wissenschaft                           |      1 |
| `www.adliswil.ch`         | nicht eingestuft · offizielle Website         |      3 |
```

`nicht eingestuft` is the default and is not a criticism — most of the web is
on no list at all.

**There is no allowlist, deliberately.** If you check every source yourself,
an allowlist only costs you findings you never see: its failures are invisible,
and it cannot bootstrap, because the only way to discover what belongs on it is
to run without one. A blocklist fails the other way round — junk in a dossier is
obvious, and one line removes it for good. So: sort by default, and remove only
what is worth nothing.

Record what you concluded, right after you concluded it:

```sh
uv run wp-todo sources block beispiel.example --reason "Datendump von 2015"
uv run wp-todo sources note  gemeinde.example --reason "gut für Öffnungszeiten, nicht für Statistik"
uv run wp-todo sources trust bfs.admin.ch     --reason "amtlich, mehrfach geprüft"
uv run wp-todo sources list
```

- **`block`** — never fetched again, and listed under `Ausgeschlossene Quellen`
  with your reason. Something missing because of a decision says which decision.
- **`note`** — still fetched and shown, with your note attached. The one that
  earns its keep: a source can be excellent for opening hours and useless for
  population figures.
- **`trust`** — sorted higher. It does **not** bypass the citation check or the
  Wikipedia-mirror detection; trusting a mirror is always an error.

`--reason` is required. A blocklist encodes its author's judgement, and
"unreliable" and "I disagree with it" are easy to conflate — writing down which
one it was keeps the excluded set auditable later, by you and by anyone you show
the work to. Entries live in `config/sources.toml`, which the CLI **appends** to
and never rewrites, so your own comments and ordering survive. Domains match on
label boundaries: `beispiel.ch` covers `www.beispiel.ch` and not
`nichtbeispiel.ch`, and the most specific entry wins rather than the first.

After enough articles the `trust` set becomes an allowlist you actually earned,
rather than one you guessed at up front.

### Running it from GitHub

`.github/workflows/research.yml`, `workflow_dispatch` only — never on a
schedule, because this stage reads hosts that did not ask to be read.

| input | default | |
| --- | --- | --- |
| `title` | — | the article, exactly as on de.wikipedia |
| `refresh` | false | bypass the response cache |
| `commit` | true | commit the dossier to `research/` |
| `agent` | false | also ask a language model — needs the `ANTHROPIC_API_KEY` secret |

The dossier goes to the job summary (readable immediately in the browser), to a
downloadable artifact, and — unless you turn `commit` off — into `research/` on
the branch you dispatched from.

The title is arbitrary text typed by whoever clicks the button, so it reaches
the runner through `env:` and never as a `${{ }}` interpolation inside a shell
script: a title of `"; rm -rf . #` would otherwise execute. That rule is stated
with no exceptions and is enforced by a test that parses the workflow, because
"no interpolation in a run block" is checkable by anyone while "none except the
safe ones" needs a judgement call every time the file is edited.

### Dossiers in `out/todo.md`

Dossiers are committed, and a worklist row links to its own dossier when one
exists — `[bearbeiten] · [Recherche]`. The link appears on the next weekly
refresh rather than immediately: rendering `todo.md` needs `cache/corpus.json`,
which is not committed, so the research workflow cannot regenerate it. The
dossier itself lands right away; only the cross-link waits.

### Not deterministic — reproducible

`out/` keeps its byte-identical guarantee unchanged. Dossiers get the weaker but
honest one: they are a pure function of the cache, so a replay reproduces them
exactly, and `--refresh` is where new information comes from. They are
gitignored by default; a directory of unchecked "current" figures is not
something to publish.

An empty section always says which kind of empty it is — `_Nicht abgefragt._`
is not the same answer as `_Keine Abweichungen gefunden._`, and a file that
rendered them the same way would be lying by omission.

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
