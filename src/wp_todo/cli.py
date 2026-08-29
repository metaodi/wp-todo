"""Command line: fetch, score, render, and a run that chains them."""

from __future__ import annotations

import datetime as dt
import difflib
import logging
from contextlib import ExitStack
from pathlib import Path
from typing import Annotated

import typer

from .cache import ResponseCache
from .client import WikiClient
from .config import ScopeConfig, load_scope
from .dossier import render_json as render_dossier_json
from .dossier import render_markdown as render_dossier_markdown
from .dossier import slug
from .fetch import fetch as fetch_corpus
from .models import FetchResult, ScoreResult
from .render import render_json, render_markdown
from .research import foreign_clients, research_article
from .score import score_corpus
from .webclient import WebClient

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
        timeout_s=scope.research.timeout_s,
        max_requests=scope.research.max_fetches,
        max_bytes=scope.research.max_doc_bytes,
        respect_robots=scope.research.respect_robots,
        reference_date=reference,
    )


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
    title: Annotated[str, typer.Argument(help="Article title, exactly as in the corpus")],
    config: ConfigOpt = DEFAULT_CONFIG,
    corpus: CorpusOpt = DEFAULT_CORPUS,
    out: Annotated[Path, typer.Option("--out", help="Where dossiers are written")] = DEFAULT_RESEARCH,
    cache_dir: CacheOpt = DEFAULT_CACHE,
    refresh: RefreshOpt = False,
    dry_run: DryRunOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Build a research dossier for one article. Never edits anything.

    Reads the article out of the fetched corpus, works out which of its claims
    carry a date, and compares them against Wikidata and the other language
    editions. The result is a briefing to read - not a draft, and not a source.
    """
    _setup_logging(verbose)
    scope = _load(config)
    fetched = _read_corpus(corpus)

    article = next((a for a in fetched.articles if a.title == title), None)
    if article is None:
        typer.secho(f"{title!r} is not in {corpus}", fg="red", err=True)
        _suggest_titles(fetched, title)
        raise typer.Exit(2)
    if not article.wikitext:
        typer.secho(
            f"{title!r} was fetched at discovery detail only, so there is no wikitext to read; "
            "raise fetch.detail_top_n or add it to `pages` in scope.toml",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)

    cache = ResponseCache(cache_dir, refresh=refresh)
    with (
        _client(scope, cache, dry_run) as wiki,
        _web_client(scope, cache, dry_run, fetched.reference_date) as web,
    ):
        foreign = foreign_clients(scope, wiki)
        with ExitStack() as stack:
            for client in foreign.values():
                stack.enter_context(client)
            built = research_article(article, fetched, scope, wiki, web, foreign)
        requests = wiki.stats.requests + web.stats.requests + sum(c.stats.requests for c in foreign.values())

    if dry_run:
        return

    out.mkdir(parents=True, exist_ok=True)
    stem = f"{built.pageid}-{slug(built.title)}"
    (out / f"{stem}.md").write_text(render_dossier_markdown(built), encoding="utf-8")
    (out / f"{stem}.json").write_text(render_dossier_json(built), encoding="utf-8")
    typer.echo(
        f"wrote {out / f'{stem}.md'} — {len(built.claims.claims)} dated claim(s), "
        f"{len(built.deltas)} comparison(s), {requests} request(s)"
    )


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


def write_outputs(result: ScoreResult, scope: ScopeConfig, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    labels = [tile.label for tile in scope.geo]
    (out / "todo.md").write_text(render_markdown(result, label_order=labels), encoding="utf-8")
    (out / "todo.json").write_text(render_json(result), encoding="utf-8")


def _read_corpus(path: Path) -> FetchResult:
    if not path.exists():
        typer.secho(f"no corpus at {path}; run `wp-todo fetch` first", fg="red", err=True)
        raise typer.Exit(2)
    return FetchResult.model_validate_json(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    app()
