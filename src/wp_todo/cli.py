"""Command line: fetch, score, render, and a run that chains them."""

from __future__ import annotations

import datetime as dt
import difflib
import logging
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated

import typer

from .agent import AgentOutcome
from .cache import ResponseCache
from .client import WikiClient
from .config import ScopeConfig, load_scope
from .dossier import render_json as render_dossier_json
from .dossier import render_markdown as render_dossier_markdown
from .dossier import slug
from .fetch import fetch as fetch_corpus
from .fetch import fetch_one
from .llm import LlmBudget, LlmClient, LlmUnavailableError
from .models import Article, Dossier, FetchResult, ScoreResult
from .render import render_json, render_markdown
from .research import foreign_clients, research_article
from .score import score_corpus
from .sources import SourceVerdictError, format_entry, load_ledger, make_verdict
from .transcript import render_transcript
from .webclient import WebClient

log = logging.getLogger(__name__)

app = typer.Typer(add_completion=False, help="Build a prioritised de.wikipedia worklist. Read-only.")

ConfigOpt = Annotated[Path, typer.Option("--config", "-c", help="Path to scope.toml")]
CorpusOpt = Annotated[Path, typer.Option("--corpus", help="Where the fetched corpus is stored")]
OutOpt = Annotated[Path, typer.Option("--out", help="Output directory")]
CacheOpt = Annotated[Path, typer.Option("--cache-dir", help="On-disk response cache")]
LimitOpt = Annotated[int | None, typer.Option("--limit", help="Only process the first N articles")]
RefreshOpt = Annotated[bool, typer.Option("--refresh", help="Bypass the response cache")]
DryRunOpt = Annotated[bool, typer.Option("--dry-run", help="Make no requests; report what would happen")]
VerboseOpt = Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")]

DEFAULT_CONFIG = Path("config/scope.toml")
DEFAULT_CORPUS = Path("cache/corpus.json")
DEFAULT_OUT = Path("out")
DEFAULT_CACHE = Path("cache/http")
DEFAULT_RESEARCH = Path("research")


def _client(scope: ScopeConfig, cache: ResponseCache, dry_run: bool) -> WikiClient:
    """One place where the politeness settings reach the client."""
    return WikiClient(
        meta=scope.meta,
        cache=cache,
        dry_run=dry_run,
        delay_s=scope.http.delay_s,
        max_retries=scope.http.max_retries,
        maxlag=scope.http.maxlag,
        timeout_s=scope.http.timeout_s,
        max_requests=scope.http.max_requests,
        progress_every=scope.http.progress_every,
    )


def _web_client(scope: ScopeConfig, cache: ResponseCache, dry_run: bool, reference: dt.date) -> WebClient:
    """The same idea for hosts outside Wikimedia, with their own budget."""
    return WebClient(
        meta=scope.meta,
        cache=cache,
        dry_run=dry_run,
        delay_s=scope.research.delay_s,
        max_retries=scope.research.max_retries,
        timeout_s=scope.research.timeout_s,
        max_requests=scope.research.max_fetches,
        max_bytes=scope.research.max_doc_bytes,
        respect_robots=scope.research.respect_robots,
        reference_date=reference,
    )


def _llm_client(scope: ScopeConfig, cache: ResponseCache, dry_run: bool) -> LlmClient:
    """The model, on the same cache and the same budget discipline as the rest.

    Built only when `--agent` was passed. Everything else in this file runs
    without it and costs nothing.
    """
    return LlmClient(
        model=scope.research.model,
        effort=scope.research.effort,
        cache=cache,
        budget=LlmBudget(limit=scope.research.max_llm_calls),
        dry_run=dry_run,
    )


def _today() -> dt.date:
    """The one legitimate clock read in this codebase.

    Rule 3 forbids a clock because a computed artefact must not move on its own.
    This is not that: it records *when a person decided something*, which is a
    fact about the person, not about the data. It never reaches an age, an
    ordering, or `out/`.
    """
    return dt.date.today()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-8s %(name)s %(message)s",
    )


def _load(config_path: Path) -> ScopeConfig:
    try:
        return load_scope(config_path)
    except Exception as exc:
        typer.secho(f"cannot load {config_path}: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc


@app.command()
def fetch(
    config: ConfigOpt = DEFAULT_CONFIG,
    corpus: CorpusOpt = DEFAULT_CORPUS,
    cache_dir: CacheOpt = DEFAULT_CACHE,
    limit: LimitOpt = None,
    refresh: RefreshOpt = False,
    dry_run: DryRunOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Query the API and store the corpus. The only command that uses the network."""
    _setup_logging(verbose)
    scope = _load(config)
    cache = ResponseCache(cache_dir, refresh=refresh)
    with _client(scope, cache, dry_run) as client:
        result = fetch_corpus(scope, client, limit=limit)
        typer.echo(
            f"fetched {len(result.articles)} articles "
            f"({client.stats.requests} requests, {client.stats.cache_hits} cache hits)"
        )
    if dry_run:
        return
    corpus.parent.mkdir(parents=True, exist_ok=True)
    corpus.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"wrote {corpus}")


@app.command()
def score(
    config: ConfigOpt = DEFAULT_CONFIG,
    corpus: CorpusOpt = DEFAULT_CORPUS,
    verbose: VerboseOpt = False,
) -> None:
    """Score the stored corpus. No network access."""
    _setup_logging(verbose)
    scope = _load(config)
    result = score_corpus(_read_corpus(corpus), scope)
    ranked = [a for a in result.articles if a.score > 0]
    typer.echo(f"scored {len(result.articles)} articles, {len(ranked)} with a non-zero score")


@app.command()
def render(
    config: ConfigOpt = DEFAULT_CONFIG,
    corpus: CorpusOpt = DEFAULT_CORPUS,
    out: OutOpt = DEFAULT_OUT,
    verbose: VerboseOpt = False,
) -> None:
    """Write out/todo.md and out/todo.json from the stored corpus."""
    _setup_logging(verbose)
    scope = _load(config)
    result = score_corpus(_read_corpus(corpus), scope)
    write_outputs(result, scope, out)
    typer.echo(f"wrote {out / 'todo.md'} and {out / 'todo.json'}")


@app.command()
def run(
    config: ConfigOpt = DEFAULT_CONFIG,
    corpus: CorpusOpt = DEFAULT_CORPUS,
    out: OutOpt = DEFAULT_OUT,
    cache_dir: CacheOpt = DEFAULT_CACHE,
    limit: LimitOpt = None,
    refresh: RefreshOpt = False,
    dry_run: DryRunOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """fetch, then score, then render."""
    _setup_logging(verbose)
    scope = _load(config)
    cache = ResponseCache(cache_dir, refresh=refresh)
    with _client(scope, cache, dry_run) as client:
        fetched = fetch_corpus(scope, client, limit=limit)
        typer.echo(
            f"fetched {len(fetched.articles)} articles "
            f"({client.stats.requests} requests, {client.stats.cache_hits} cache hits)"
        )
    if dry_run:
        return
    corpus.parent.mkdir(parents=True, exist_ok=True)
    corpus.write_text(fetched.model_dump_json(indent=2), encoding="utf-8")
    write_outputs(score_corpus(fetched, scope), scope, out)
    typer.echo(f"wrote {out / 'todo.md'} and {out / 'todo.json'}")


@app.command()
def research(
    title: Annotated[str, typer.Argument(help="Article title, exactly as on de.wikipedia")],
    config: ConfigOpt = DEFAULT_CONFIG,
    corpus: CorpusOpt = DEFAULT_CORPUS,
    out: Annotated[Path, typer.Option("--out", help="Where dossiers are written")] = DEFAULT_RESEARCH,
    cache_dir: CacheOpt = DEFAULT_CACHE,
    corpus_only: Annotated[
        bool, typer.Option("--corpus-only", help="Never fetch; require the article in the corpus")
    ] = False,
    agent: Annotated[
        bool,
        typer.Option(
            "--agent",
            help="Also ask a language model. Costs money; writes a transcript beside the dossier.",
        ),
    ] = False,
    refresh: RefreshOpt = False,
    dry_run: DryRunOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Build a research dossier for one article. Never edits anything.

    Works out which of the article's claims carry a date and compares them
    against Wikidata and the other language editions. The result is a briefing
    to read - not a draft, and not a source.

    The article is taken from the fetched corpus when it is there, and fetched
    on its own when it is not. So this works on any dewiki title, with or
    without a corpus: researching one page should not cost a whole worklist.

    Without `--agent` no model is consulted and the run is free. With it, the
    article's own references are read first and an open web search happens only
    for what they could not answer; every exchange lands in a transcript
    committed next to the dossier.
    """
    _setup_logging(verbose)
    scope = _load(config)

    try:
        ledger = load_ledger(scope.research.sources)
    except SourceVerdictError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc

    stored = _read_corpus_if_present(corpus)
    article = _from_corpus(stored, title)
    if article is None and corpus_only:
        typer.secho(f"{title!r} is not in {corpus} (--corpus-only)", fg="red", err=True)
        if stored is not None:
            _suggest_titles(stored, title)
        raise typer.Exit(2)

    cache = ResponseCache(cache_dir, refresh=refresh)
    with _client(scope, cache, dry_run) as wiki:
        fetched = stored
        if article is None:
            log.info("%r is not in the corpus; fetching it on its own", title)
            fetched = fetch_one(scope, wiki, title)
            article = fetched.articles[0] if fetched.articles else None
        if article is None or fetched is None:
            typer.secho(
                f"de.wikipedia has no article {title!r} (check spelling, and note that only "
                "the main namespace can be researched)",
                fg="red",
                err=True,
            )
            if stored is not None:
                _suggest_titles(stored, title)
            raise typer.Exit(2)

        llm = _llm_client(scope, cache, dry_run) if agent else None
        if llm is not None:
            typer.echo(
                f"--agent: up to {llm.budget.limit} call(s) to {llm.model}. "
                "The article's own references are read first; the open web only after."
            )

        with (
            _web_client(scope, cache, dry_run, fetched.reference_date) as web,
            ExitStack() as stack,
        ):
            foreign = foreign_clients(scope, wiki)
            for client in foreign.values():
                stack.enter_context(client)
            try:
                built, outcome = research_article(article, fetched, scope, wiki, web, foreign, ledger, llm)
            except LlmUnavailableError as exc:
                typer.secho(str(exc), fg="red", err=True)
                raise typer.Exit(2) from exc
            requests = (
                wiki.stats.requests + web.stats.requests + sum(c.stats.requests for c in foreign.values())
            )

    if dry_run:
        return

    built = write_dossier(out, built, outcome)
    stem = f"{built.pageid}-{slug(built.title)}"
    typer.echo(
        f"wrote {out / f'{stem}.md'} — {len(built.claims.claims)} dated claim(s), "
        f"{len(built.deltas)} comparison(s), {requests} request(s)"
    )
    if built.link_summary is not None and built.link_summary.total:
        summary = built.link_summary
        typer.echo(
            f"links: {summary.checked} of {summary.total} checked — {summary.dead} dead, "
            f"{summary.unreachable} unreachable, {summary.blocked} blocked (blocked is not dead)"
        )
        if summary.budget_exhausted:
            typer.secho(
                "the request budget ran out before every link was checked; the dossier says "
                "which ones, and --config can raise research.max_fetches",
                fg="yellow",
                err=True,
            )
    if built.agent is not None:
        run = built.agent
        typer.echo(
            f"agent: {len(built.findings)} finding(s) from {run.calls} call(s) "
            f"({run.cached_calls} replayed), {len(run.dropped)} answer(s) rejected by the checks"
        )
        if run.budget_exhausted:
            typer.secho(
                f"the budget of {run.budget} call(s) ran out before every claim was examined; "
                "the dossier says so, and --config can raise research.max_llm_calls",
                fg="yellow",
                err=True,
            )
        if run.failed:
            # The dossier is written and says what happened - but the agent was
            # explicitly asked for and did not run, so the command still fails.
            # A green run that quietly did less than was asked is how a broken
            # key goes unnoticed for weeks.
            typer.secho(
                f"the research agent failed: {run.failed}\n"
                f"the deterministic dossier was still written to {out / f'{stem}.md'}",
                fg="red",
                err=True,
            )
            raise typer.Exit(1)


sources_app = typer.Typer(
    add_completion=False,
    help="Record what you concluded about a source, so the next dossier knows it.",
)
app.add_typer(sources_app, name="sources")

DomainArg = Annotated[str, typer.Argument(help="Host, e.g. beispiel.ch (not a URL)")]
ReasonOpt = Annotated[str, typer.Option("--reason", help="Why. Required, and it ends up in the dossier.")]


def _record(config_path: Path, domain: str, verdict: str, reason: str) -> None:
    """Append one verdict, then confirm what will happen because of it."""
    scope = _load(config_path)
    try:
        entry = make_verdict(domain, verdict, reason, _today())
    except SourceVerdictError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc

    path = scope.research.sources
    path.parent.mkdir(parents=True, exist_ok=True)
    # Appended, never rewritten: hand-written comments and ordering survive.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(format_entry(entry))

    consequence = {
        "block": "wird nicht mehr abgerufen und im Dossier als ausgeschlossen gemeldet",
        "note": "wird weiter abgerufen; die Notiz steht künftig dabei",
        "trust": "wird beim Sortieren bevorzugt (Zitatprüfung und Spiegel-Erkennung bleiben)",
    }[verdict]
    typer.echo(f"{entry.domain}: {verdict} — {consequence}")
    typer.echo(f"recorded in {path}")


@sources_app.command("block")
def sources_block(domain: DomainArg, reason: ReasonOpt, config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Never fetch this domain again. The dossier still reports the exclusion."""
    _record(config, domain, "block", reason)


@sources_app.command("note")
def sources_note(domain: DomainArg, reason: ReasonOpt, config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Keep fetching it, but attach what you concluded last time."""
    _record(config, domain, "note", reason)


@sources_app.command("trust")
def sources_trust(domain: DomainArg, reason: ReasonOpt, config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Promote it when sorting. Does not bypass any verification gate."""
    _record(config, domain, "trust", reason)


@sources_app.command("list")
def sources_list(
    config: ConfigOpt = DEFAULT_CONFIG,
    verdict: Annotated[str | None, typer.Option("--verdict", help="block, note or trust")] = None,
) -> None:
    """Show what has been recorded so far."""
    scope = _load(config)
    try:
        ledger = load_ledger(scope.research.sources)
    except SourceVerdictError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc

    entries = ledger.of_kind(verdict) if verdict else ledger.verdicts
    if not entries:
        typer.echo(f"nothing recorded in {scope.research.sources} yet")
        return
    for item in sorted(entries, key=lambda v: (v.verdict, v.domain)):
        typer.echo(f"{item.verdict:6} {item.domain:40} {item.decided}  {item.reason}")


def _read_corpus_if_present(path: Path) -> FetchResult | None:
    """The corpus, or None. Missing is normal for `research`, not an error."""
    if not path.exists():
        return None
    return FetchResult.model_validate_json(path.read_text(encoding="utf-8"))


def _from_corpus(stored: FetchResult | None, title: str) -> Article | None:
    """The article, if the corpus has it *with wikitext*.

    An article held at discovery detail has no wikitext, and there is nothing to
    research without it. Rather than telling the reader to raise
    `fetch.detail_top_n`, treat it as absent and fetch it properly.
    """
    if stored is None:
        return None
    found = next((a for a in stored.articles if a.title == title), None)
    return found if found is not None and found.wikitext else None


def _suggest_titles(fetched: FetchResult, title: str) -> None:
    """A near-miss on a title is the likeliest way to land here.

    Fuzzy rather than substring, because the near-miss is usually an umlaut
    typed as `ue` - which shares no substring with the real title at all.
    """
    titles = [article.title for article in fetched.articles]
    close = difflib.get_close_matches(title, titles, n=5, cutoff=0.6)
    close += [t for t in titles if title.casefold() in t.casefold() and t not in close]
    if close:
        typer.secho("did you mean: " + ", ".join(close[:5]), fg="yellow", err=True)


def write_dossier(out: Path, built: Dossier, outcome: AgentOutcome | None) -> Dossier:
    """Write the dossier, and the transcript beside it when the agent ran.

    The transcript goes first, because the dossier links to it. A link to a
    file that is not there yet is the kind of small lie this project has spent
    a lot of effort not telling - and the returned dossier is the one carrying
    that link, so the markdown and the JSON say the same thing.
    """
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{built.pageid}-{slug(built.title)}"

    if outcome is not None and outcome.run is not None:
        transcript = out / f"{stem}.transcript.md"
        transcript.write_text(render_transcript(built, outcome), encoding="utf-8")
        built = built.model_copy(
            update={"agent": outcome.run.model_copy(update={"transcript": transcript.name})}
        )

    (out / f"{stem}.md").write_text(render_dossier_markdown(built), encoding="utf-8")
    (out / f"{stem}.json").write_text(render_dossier_json(built), encoding="utf-8")
    return built


def write_outputs(result: ScoreResult, scope: ScopeConfig, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    labels = [tile.label for tile in scope.geo]
    markdown = render_markdown(result, label_order=labels, research_dir=scope.research.dir)
    (out / "todo.md").write_text(markdown, encoding="utf-8")
    (out / "todo.json").write_text(render_json(result), encoding="utf-8")


def _read_corpus(path: Path) -> FetchResult:
    if not path.exists():
        typer.secho(f"no corpus at {path}; run `wp-todo fetch` first", fg="red", err=True)
        raise typer.Exit(2)
    return FetchResult.model_validate_json(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    app()
