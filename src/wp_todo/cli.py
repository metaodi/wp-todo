"""Command line: fetch, score, render, and a run that chains them."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer

from .cache import ResponseCache
from .client import WikiClient
from .config import ScopeConfig, load_scope
from .fetch import fetch as fetch_corpus
from .models import FetchResult, ScoreResult
from .render import render_json, render_markdown
from .score import score_corpus

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
    with WikiClient(meta=scope.meta, cache=cache, dry_run=dry_run) as client:
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
    with WikiClient(meta=scope.meta, cache=cache, dry_run=dry_run) as client:
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
