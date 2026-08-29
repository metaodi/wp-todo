"""Operating the research stage: single-article fetch, links, and the workflow.

The workflow test is a static one. It cannot run Actions, but the property it
checks - that no `${{ }}` interpolation reaches a shell - is exactly the kind
that is verified by reading, and exactly the kind that gets broken later by
somebody adding one innocuous-looking line.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import httpx
import pytest

from wp_todo.cache import ResponseCache
from wp_todo.client import WikiClient
from wp_todo.config import ScopeConfig, load_scope
from wp_todo.fetch import fetch_one
from wp_todo.models import ScoredArticle, ScoreResult, Subscores
from wp_todo.render import render_markdown

FIXTURES = Path(__file__).parent / "fixtures"
WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "research.yml"


@pytest.fixture(scope="module")
def scope() -> ScopeConfig:
    return load_scope(FIXTURES / "scope.toml")


def offline(scope: ScopeConfig) -> WikiClient:
    return WikiClient(meta=scope.meta, cache=ResponseCache(FIXTURES / "http"), offline=True)


# ------------------------------------------------------------------- fetch_one
def test_one_article_comes_back_fully_detailed(scope: ScopeConfig) -> None:
    """Researching one page must not require fetching a whole worklist."""
    with offline(scope) as client:
        result = fetch_one(scope, client, "Adliswil")

    assert len(result.articles) == 1
    article = result.articles[0]
    assert article.title == "Adliswil"
    assert article.wikitext, "without wikitext there is nothing to research"
    assert article.revisions
    assert result.bot_accounts, "history needs the bot roster to classify edits"


@pytest.mark.parametrize(
    ("title", "page"),
    [
        ("Gibt Es Nicht Wirklich Zzz", {"ns": 0, "title": "Gibt Es Nicht Wirklich Zzz", "missing": True}),
        ("Kategorie:Gemeinde im Kanton Zürich", {"ns": 14, "pageid": 7, "title": "Kategorie:…"}),
    ],
    ids=["missing", "wrong-namespace"],
)
def test_an_unresearchable_title_yields_nothing(
    scope: ScopeConfig, tmp_path: Path, title: str, page: dict[str, object]
) -> None:
    """A typo, or a Kategorie: prefix, should be reported as such - not turned
    into a blank dossier that looks like a finished piece of work."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "siprop" in str(request.url):
            return httpx.Response(200, json={"query": {"general": {"time": "2026-08-01T00:00:00Z"}}})
        return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": [page]}})

    with WikiClient(
        meta=scope.meta,
        cache=ResponseCache(tmp_path / "http"),
        delay_s=0.0,
        transport=httpx.MockTransport(handler),
    ) as client:
        result = fetch_one(scope, client, title)

    assert result.articles == ()


def test_the_reference_date_comes_from_the_server_not_the_clock(scope: ScopeConfig) -> None:
    with offline(scope) as client:
        assert fetch_one(scope, client, "Adliswil").reference_date is not None


def test_exclusions_do_not_apply_to_a_title_somebody_typed(scope: ScopeConfig) -> None:
    """The exclusions keep lists and disambiguation pages out of a *discovered*
    worklist. A typed title was not discovered, and silently dropping it would
    answer a different question than the one asked."""
    excluding = scope.model_copy(
        update={"exclude": scope.exclude.model_copy(update={"titles": ("Adliswil",)})}
    )
    assert excluding.is_excluded("Adliswil") is not None

    with offline(excluding) as client:
        result = fetch_one(excluding, client, "Adliswil")

    assert [a.title for a in result.articles] == ["Adliswil"]


# ----------------------------------------------------------------- todo.md link
def article(pageid: int, title: str) -> ScoredArticle:
    return ScoredArticle(
        pageid=pageid, title=title, scope_label="Test", score=1.0, base_score=1.0, subscores=Subscores()
    )


def result_of(*articles: ScoredArticle) -> ScoreResult:
    return ScoreResult(reference_date=dt.date(2026, 8, 1), pageviews_window="x", articles=articles)


def test_no_research_dir_reproduces_the_worklist_unchanged() -> None:
    """The link column must be opt-in, or every existing row would churn."""
    scored = result_of(article(1, "Adliswil"))
    assert "Recherche" not in render_markdown(scored, research_dir=None)


def test_only_articles_with_a_dossier_get_a_link(tmp_path: Path) -> None:
    (tmp_path / "1-adliswil.md").write_text("# x", encoding="utf-8")
    scored = result_of(article(1, "Adliswil"), article(2, "Thalwil"))

    markdown = render_markdown(scored, research_dir=tmp_path)

    rows = {line.split("|")[1].strip(): line for line in markdown.splitlines() if line.startswith("| [")}
    assert "Recherche" in rows["[Adliswil](https://de.wikipedia.org/wiki/Adliswil)"]
    assert "Recherche" not in rows["[Thalwil](https://de.wikipedia.org/wiki/Thalwil)"]


def test_the_link_is_relative_to_the_output_directory(tmp_path: Path) -> None:
    """`out/todo.md` and `research/` are siblings, so the link has to climb."""
    (tmp_path / "1-kuesnachter-dorfbach.md").write_text("# x", encoding="utf-8")
    markdown = render_markdown(result_of(article(1, "Küsnachter Dorfbach")), research_dir=tmp_path)

    assert f"../{tmp_path.name}/1-kuesnachter-dorfbach.md" in markdown


def test_the_link_uses_the_same_slug_as_the_dossier_writer(tmp_path: Path) -> None:
    """Umlauts are where a re-implemented slug would silently drift."""
    from wp_todo.dossier import slug

    stem = f"42-{slug('Küsnachter Dorfbach')}"
    (tmp_path / f"{stem}.md").write_text("# x", encoding="utf-8")

    assert "Recherche" in render_markdown(
        result_of(article(42, "Küsnachter Dorfbach")), research_dir=tmp_path
    )


# -------------------------------------------------------------------- workflow
class TestWorkflowSafety:
    """The title is arbitrary text from whoever clicks Run workflow."""

    @staticmethod
    def run_blocks() -> list[tuple[int, str]]:
        """Every line inside a `run:` block, with its line number."""
        lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
        inside: list[tuple[int, str]] = []
        in_run = False
        indent = 0
        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if re.match(r"^run:\s*\|?", stripped):
                in_run = True
                indent = len(line) - len(line.lstrip())
                continue
            if in_run and stripped and (len(line) - len(line.lstrip())) <= indent:
                in_run = False
            if in_run:
                inside.append((number, line))
        return inside

    def test_no_interpolation_reaches_a_shell(self) -> None:
        """`${{ inputs.title }}` in a run block is a command-injection hole: a
        title of `"; rm -rf . #` would execute. Values arrive through `env:`.

        The rule is stated with no exceptions on purpose - "no interpolation in
        a run block" is checkable by anyone, where "none except the safe ones"
        needs a judgement call every time somebody edits this file.
        """
        offenders = [(n, line.strip()) for n, line in self.run_blocks() if "${{" in line]
        assert offenders == [], f"interpolation inside a run: block: {offenders}"

    def test_the_title_is_quoted_where_it_is_used(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        assert 'uv run wp-todo research "$TITLE"' in text
        assert "$TITLE" not in text.replace('"$TITLE"', ""), "every use of TITLE must be quoted"

    def test_it_is_never_scheduled(self) -> None:
        """This stage reads hosts that did not ask to be read. It runs when
        somebody asks for it."""
        assert "schedule:" not in WORKFLOW.read_text(encoding="utf-8")

    def test_it_can_only_write_contents(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        assert "permissions:\n  contents: write" in text
        assert "pull-requests:" not in text

    def test_the_agent_is_off_unless_somebody_asks_for_it(self) -> None:
        """A dispatch that costs money must be a decision, not a default."""
        text = WORKFLOW.read_text(encoding="utf-8")
        agent_input = text.split("      agent:", 1)[1].split("\nconcurrency", 1)[0]
        assert "default: false" in agent_input

    def test_a_missing_api_key_stops_the_run_rather_than_quietly_doing_less(self) -> None:
        """Without the secret the CLI would fall over somewhere further in, or
        worse, produce a dossier that looks complete. An unset secret is an
        empty string, so the check has to be for emptiness."""
        text = WORKFLOW.read_text(encoding="utf-8")
        assert 'if [ -z "${ANTHROPIC_API_KEY:-}" ]' in text
        assert "::error::the agent was requested but ANTHROPIC_API_KEY is not set" in text

    def test_the_transcript_is_not_mistaken_for_the_dossier(self) -> None:
        """`research/*.md` matches the transcript too, and the transcript is
        written first - so mtime order alone would publish the wrong file to
        the job summary."""
        text = WORKFLOW.read_text(encoding="utf-8")
        assert "grep -v '\\.transcript\\.md$'" in text
