"""End-to-end over recorded fixtures. No network: the client is offline, so a
missing fixture raises instead of quietly reaching out.

This is the vertical slice the first milestone is defined by.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from wp_todo.cache import ResponseCache
from wp_todo.cli import write_outputs
from wp_todo.client import WikiClient
from wp_todo.config import ScopeConfig, load_scope
from wp_todo.fetch import fetch, pageview_window
from wp_todo.models import FetchResult, ScoreResult
from wp_todo.render import render_json, render_markdown
from wp_todo.score import score_corpus

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def scope() -> ScopeConfig:
    return load_scope(FIXTURES / "scope.toml")


@pytest.fixture(scope="module")
def corpus(scope: ScopeConfig) -> FetchResult:
    with WikiClient(meta=scope.meta, cache=ResponseCache(FIXTURES / "http"), offline=True) as client:
        return fetch(scope, client)


@pytest.fixture(scope="module")
def scored(corpus: FetchResult, scope: ScopeConfig) -> ScoreResult:
    return score_corpus(corpus, scope)


def test_fetch_runs_entirely_from_fixtures(corpus: FetchResult) -> None:
    assert len(corpus.articles) >= 10
    assert corpus.bot_accounts, "the bot roster is needed to classify edits"
    assert all(a.wikitext for a in corpus.articles), "markers need wikitext"
    # history_depth caps the history: rvlimit bounds a batch, not the query, so
    # following continuation here would walk the whole article history.
    assert max(len(a.revisions) for a in corpus.articles) <= scope_history_depth()


def scope_history_depth() -> int:
    return load_scope(FIXTURES / "scope.toml").scoring.history_depth


def test_acceptance_known_stale_article_surfaces_with_its_reasons(scored: ScoreResult) -> None:
    """The vertical slice: a known-stale Thalwil-area article, with the reason
    codes that explain it."""
    by_title = {a.title: a for a in scored.articles}
    assert "Küsnachter Dorfbach" in by_title

    article = by_title["Küsnachter Dorfbach"]
    codes = {reason.code for reason in article.reasons}

    # It carries a dated maintenance category, and {{Zukunft|2025|05}} is what
    # put it there - the mechanism behind "Veraltet nach <Monat> <Jahr>".
    assert "maintenance" in codes
    assert "zukunft_faellig" in codes
    assert article.subscores.maintenance > 0

    # It is the top-ranked article of the slice, and it says why.
    assert scored.articles[0].title == "Küsnachter Dorfbach"
    assert article.score > 0
    assert article.edit_url.endswith("action=edit")


def test_every_scored_article_can_explain_itself(scored: ScoreResult) -> None:
    for article in scored.articles:
        if article.score > 0:
            assert article.reasons, f"{article.title} scored without a reason"
            assert sum(r.points for r in article.reasons) > 0


def test_markdown_reports_reasons_and_edit_links(scored: ScoreResult, scope: ScopeConfig) -> None:
    markdown = render_markdown(scored, label_order=[g.label for g in scope.geo])
    assert "# Wikipedia-TODO" in markdown
    assert "Küsnachter Dorfbach" in markdown
    assert "action=edit" in markdown
    # Grouped by scope label.
    assert "## Explizit" in markdown


def test_render_is_idempotent(scored: ScoreResult, tmp_path: Path, scope: ScopeConfig) -> None:
    """Running twice over identical data yields byte-identical output."""
    first, second = tmp_path / "a", tmp_path / "b"
    write_outputs(scored, scope, first)
    write_outputs(scored, scope, second)
    for name in ("todo.md", "todo.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_pipeline_is_idempotent_end_to_end(scope: ScopeConfig, tmp_path: Path) -> None:
    """Fetch, score and render twice from the same fixtures: identical bytes."""
    outputs = []
    for run in ("first", "second"):
        with WikiClient(meta=scope.meta, cache=ResponseCache(FIXTURES / "http"), offline=True) as client:
            result = score_corpus(fetch(scope, client), scope)
        target = tmp_path / run
        write_outputs(result, scope, target)
        outputs.append((target / "todo.md").read_bytes() + (target / "todo.json").read_bytes())
    assert outputs[0] == outputs[1]


def test_json_carries_every_subscore_and_evidence(scored: ScoreResult) -> None:
    payload = json.loads(render_json(scored))
    article = next(a for a in payload["articles"] if a["title"] == "Küsnachter Dorfbach")
    assert set(article["subscores"]) == {"maintenance", "edit_age", "markers", "attention"}
    assert article["reasons"]
    assert "reference_date" in payload
    # Evidence snippets survive into the structured output.
    markers = [r for r in article["reasons"] if r["code"].startswith("marker:")]
    assert all(r["evidence"] for r in markers)


def test_markdown_has_no_timestamp_that_churns(scored: ScoreResult, scope: ScopeConfig) -> None:
    """A diff between weekly runs should show real change, not a new date."""
    markdown = render_markdown(scored, label_order=[g.label for g in scope.geo])
    today = dt.date.today().isoformat()
    assert today not in markdown
    assert scored.reference_date.isoformat() not in markdown


@pytest.mark.parametrize(
    ("reference", "months", "expected"),
    [
        (dt.date(2026, 8, 29), 12, ("20250801", "20260701")),
        (dt.date(2026, 1, 15), 12, ("20250101", "20251201")),
        (dt.date(2026, 1, 15), 1, ("20251201", "20251201")),
        (dt.date(2026, 3, 1), 3, ("20251201", "20260201")),
    ],
)
def test_pageview_window_uses_whole_completed_months(
    reference: dt.date, months: int, expected: tuple[str, str]
) -> None:
    """Never "the last N months from today": that would churn the diff daily."""
    assert pageview_window(reference, months) == expected


def test_scores_do_not_move_between_runs_in_the_same_month(scope: ScopeConfig, corpus: FetchResult) -> None:
    """A weekly job must not rewrite every row just because time passed.

    The same corpus fetched on two days of one month must score identically.
    """
    early = corpus.model_copy(update={"reference_date": dt.date(2026, 8, 3)})
    late = corpus.model_copy(update={"reference_date": dt.date(2026, 8, 27)})
    assert render_markdown(score_corpus(early, scope)) == render_markdown(score_corpus(late, scope))
