# wp-todo — working rules

Three rules come before anything else in this repository.

## 1. This tool never edits Wikipedia

Read-only API access only. `action=query`, `action=paraminfo`, and GET requests
to the pageviews REST endpoint. No `action=edit`, no login, no OAuth, no write
tokens, no bot framework, no `assert=user`. If a change starts to need one of
those, it is out of scope — stop and say so.

The HTTP client enforces this: it refuses any request whose `action` is not on
an allowlist. Do not widen that allowlist.

## 2. API etiquette is not optional

- Every request carries a descriptive `User-Agent` with real contact
  information, per the Wikimedia User-Agent policy. The client **refuses to
  run** if the contact is unset or still the placeholder.
- `maxlag=5` on every action-API read.
- **A maxlag rejection arrives as HTTP 200** with `error.code == "maxlag"` in
  the body, and no `Retry-After` header (verified — see `docs/api-notes.md`).
  Always inspect the body for `error.code`; never branch on status alone.
- Requests are serialised per host with a small delay. No parallel hammering.
- Responses are cached on disk. A development rerun must not re-hit the API
  unless `--refresh` is passed.

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
