# wp-todo — working rules

Three rules come before anything else in this repository.

## 1. This tool never edits Wikipedia

Read-only API access only. `action=query`, `action=paraminfo`, and GET requests
to the pageviews REST endpoint. No `action=edit`, no login, no OAuth, no write
tokens, no bot framework, no `assert=user`. If a change starts to need one of
those, it is out of scope — stop and say so.

The HTTP client enforces this: it refuses any request whose `action` is not on
an allowlist. Do not widen that allowlist.

This covers the research stage too. `wp-todo research` produces a *briefing* —
notes for a human to read before editing — and never an edit, never draft
article prose, and never a post to a talk page or anywhere else. It reads hosts
outside Wikimedia through `WebClient`, which is GET-only by construction: no
other verb is implemented, so none can be called. When a research feature needs
data from a Wikimedia service whose `action` is not on the allowlist, reach for
that service's REST endpoint (as the Wikidata comparison does), or drop the
feature. Do not widen the allowlist to make a feature fit.

The research agent (`--agent`) does not weaken any of this. **The model is never
handed a tool that can write anywhere.** Its only tool is Anthropic's
server-side web search, and that is used for *discovery only*: URLs are read out
of the structured result blocks and then fetched by `WebClient`, so every
document a finding rests on is in our own cache, GET-fetched, robots-checked and
checkable. The model never emits a URL and never sees a way to reach Wikipedia.

## 2. API etiquette is not optional

- Every request carries a descriptive `User-Agent` with real contact
  information, per the Wikimedia User-Agent policy. The client **refuses to
  run** if the contact is unset or still the placeholder.
- `maxlag=5` on every action-API read.
- **A maxlag rejection arrives as HTTP 200** with `error.code == "maxlag"` in
  the body, and no `Retry-After` header (verified — see `docs/api-notes.md`).
  Always inspect the body for `error.code`; never branch on status alone.
- Requests are serialised per host with a small delay. No parallel hammering.
  `http.delay_s` is the hard ceiling on the request rate; the pageviews REST
  endpoint goes through the same path as the action API, not around it.
- A per-run request budget (`http.max_requests`) stops the run rather than
  letting a scope change become an unbounded crawl.
- Responses are cached on disk. A development rerun must not re-hit the API
  unless `--refresh` is passed. Batched title queries are cached per title, so
  changing the scope does not invalidate whole batches.

Hosts outside Wikimedia never asked to be read by anybody's tool, so the
research stage is stricter, not looser: `robots.txt` is consulted and honoured
per host, hosts are paced independently, responses are size-capped and
content-type filtered, the User-Agent identifies the research client separately
rather than claiming the Wikimedia one, and a skip — a 404, a robots
prohibition — is cached so a rerun does not ask again. The stage is opt-in per
article and is deliberately not wired into `refresh.yml`.

## 3. Output must be deterministic

`out/todo.md` and `out/todo.json` must be byte-identical across runs given the
same API responses. That means:

- Stable sort keys everywhere, with an explicit tie-breaker (title) so equal
  scores never reorder.
- No wall-clock timestamps in the body of either artefact. "Age in days" is
  computed against a **fixed reference date** taken from the fetched data, not
  from `datetime.now()`.
- Pageview windows are whole calendar months, chosen relative to that same
  reference — never "the last 12 months from today".
- Floats are rounded at a fixed precision before serialisation.

The point is a meaningful `git diff` between weekly runs. A diff full of churn
is a bug.

This rule governs `out/`. Research dossiers under `research/` get the weaker but
honest guarantee of **reproducibility by replay**: they are a pure function of
the cache, so a rerun without `--refresh` produces byte-identical files, and
`--refresh` is where new information — and a real diff — comes from. Everything
above still applies to them: no clock is read, ages come from the corpus
reference date, and ordering is stable.

Dossiers **are** committed, and `out/todo.md` links to the ones that exist. So
the worklist is a function of the API responses *and* of what is committed under
`research/` — byte-identical for a given checkout, which is what the weekly diff
depends on. The link column changes only when a dossier is added or removed,
which is a real change worth seeing.

Committing them is a decision with a cost, and it is worth naming rather than
forgetting: a dossier in a public repository can be found and read as though it
were authoritative. Nothing in the deterministic sections is *asserted* — every
figure is a pointer with both sides linked — but that stops being true for the
open-web stage. So its findings section says inside the section, not only in the
file header, that every figure there is unverified until a human has opened the
source. Do not move that notice to the header and do not shorten it: the header
is read once by whoever opens the file, and the findings section is the part
that gets scrolled to, screenshotted and pasted.

The agent commits a second file, `research/<id>-<slug>.transcript.md`, holding
every exchange — including the answers the gates threw away. It is subject to
the same replay guarantee and to a **louder** header than the dossier, because a
discarded model answer sitting in a public file must not read as a finding to
somebody who arrives at it directly. Model calls are cached like every other
response, so a rerun without `--refresh` reproduces both files byte for byte.

Running out of the call budget is reported, never absorbed: a short findings
list because the ceiling was hit is a different fact from a short findings list
because there was little to find, and the dossier names every claim it never got
to. That is the same rule as `_Nicht abgefragt._` versus `_Keine gefunden._`,
applied one layer further out.

## Verifying API behaviour

The development sandbox has no network egress to Wikimedia hosts. Run
`.github/workflows/verify.yml` (workflow_dispatch) to execute
`scripts/phase0_probe.py` against the live API from a runner; it commits the
answer sheet back to the branch. `docs/api-notes.md` records what was verified
and when. Do not add a finding there without evidence in
`docs/phase0-probe-output.json`.

## Conventions

- Python 3.12+, `uv`, `src/` layout. `ruff` for lint and format, `mypy --strict`.
- Conventional commits, small and focused.
- Tests never touch the network. Fixtures live in `tests/fixtures/`, recorded by
  `scripts/record_fixtures.py` via the same workflow.
