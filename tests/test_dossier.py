"""The dossier end to end: real fixture corpus in, briefing out.

The Wikimedia half runs offline from the recorded fixtures. The Wikidata half
runs against a mock transport, because there is no recorded fixture for the
Wikibase REST API yet - see the note in `test_enrich.py`.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from wp_todo.cache import ResponseCache
from wp_todo.client import WikiClient
from wp_todo.config import ScopeConfig, load_scope
from wp_todo.dossier import HEADER_NOTICE, render_json, render_markdown, slug
from wp_todo.fetch import fetch
from wp_todo.models import Dossier, FetchResult
from wp_todo.research import research_article
from wp_todo.sources import SourceLedger, make_verdict
from wp_todo.webclient import WebClient

FIXTURES = Path(__file__).parent / "fixtures"
ARTICLE = "Küsnachter Dorfbach"


@pytest.fixture(scope="module")
def scope() -> ScopeConfig:
    return load_scope(FIXTURES / "scope.toml")


@pytest.fixture(scope="module")
def corpus(scope: ScopeConfig) -> FetchResult:
    with WikiClient(meta=scope.meta, cache=ResponseCache(FIXTURES / "http"), offline=True) as client:
        return fetch(scope, client)


def build(
    corpus: FetchResult,
    scope: ScopeConfig,
    tmp_path: Path,
    *,
    statements: dict[str, Any] | None = None,
    compare_wikidata: bool = False,
    ledger: SourceLedger | None = None,
) -> Dossier:
    """A dossier for the acceptance article, with Wikimedia offline."""
    article = next(a for a in corpus.articles if a.title == ARTICLE)
    config = scope.model_copy(
        update={"research": scope.research.model_copy(update={"compare_wikidata": compare_wikidata})}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(statements or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    with (
        WikiClient(meta=scope.meta, cache=ResponseCache(FIXTURES / "http"), offline=True) as wiki,
        WebClient(
            meta=scope.meta,
            cache=ResponseCache(tmp_path / "web"),
            delay_s=0.0,
            respect_robots=False,
            transport=httpx.MockTransport(handler),
            reference_date=corpus.reference_date,
        ) as web,
    ):
        return research_article(article, corpus, config, wiki, web, None, ledger)


def test_the_dossier_leads_with_what_it_is_not(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """A file of "current" figures beside article text is exactly the thing
    that gets pasted in unchecked. The disclaimer is load-bearing."""
    markdown = render_markdown(build(corpus, scope, tmp_path))

    assert HEADER_NOTICE in markdown
    assert markdown.index(HEADER_NOTICE) < 200, "the notice must precede any finding"
    assert "bearbeitet die Wikipedia nicht" in markdown


def test_dated_claims_reach_the_dossier(corpus: FetchResult, scope: ScopeConfig, tmp_path: Path) -> None:
    dossier = build(corpus, scope, tmp_path)
    assert dossier.claims.claims, "the acceptance article has dated claims"

    markdown = render_markdown(dossier)
    assert "## Angaben zum Prüfen" in markdown
    for claim in dossier.claims.claims[:3]:
        assert str(claim.line_no) in markdown


def test_reference_age_is_reported(corpus: FetchResult, scope: ScopeConfig, tmp_path: Path) -> None:
    dossier = build(corpus, scope, tmp_path)
    markdown = render_markdown(dossier)

    assert "## Belege dieses Artikels" in markdown
    if dossier.claims.references.newest_year is not None:
        assert str(dossier.claims.references.newest_year) in markdown


def test_no_clock_is_read(corpus: FetchResult, scope: ScopeConfig, tmp_path: Path) -> None:
    """Ages are measured from the corpus reference date. A dossier that moved
    every week would make its own diff useless."""
    dossier = build(corpus, scope, tmp_path)
    markdown = render_markdown(dossier)

    assert corpus.reference_date.isoformat() in markdown
    assert dossier.reference_date == corpus.reference_date


def test_rendering_is_byte_identical_across_runs(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """The replay gate: same cache in, same bytes out."""
    first = build(corpus, scope, tmp_path)
    second = build(corpus, scope, tmp_path)

    assert render_markdown(first) == render_markdown(second)
    assert render_json(first) == render_json(second)


def test_json_carries_everything_the_markdown_summarises(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    payload = json.loads(render_json(build(corpus, scope, tmp_path)))

    assert payload["title"] == ARTICLE
    assert payload["claims"]["claims"]
    assert "reference_date" in payload
    assert payload["edit_url"].startswith("https://de.wikipedia.org/w/index.php")


def test_a_wikidata_delta_reaches_the_table(corpus: FetchResult, scope: ScopeConfig, tmp_path: Path) -> None:
    """`Küsnachter Dorfbach` has no mapped infobox field, so this drives the
    renderer directly rather than pretending the fixture has one."""
    from wp_todo.models import Delta

    dossier = build(corpus, scope, tmp_path).model_copy(
        update={
            "deltas": (
                Delta(
                    kind="wikidata",
                    label="Einwohnerzahl",
                    field="EINWOHNER",
                    article_value="8500",
                    external_value="9240",
                    article_as_of=2018,
                    external_as_of=2025,
                    source="https://www.wikidata.org/wiki/Q68166#P1082",
                    agrees=False,
                ),
            )
        }
    )
    markdown = render_markdown(dossier)

    assert "## Abweichungen gegenüber Wikidata" in markdown
    assert "8500 (Stand 2018)" in markdown
    assert "9240 (Stand 2025)" in markdown
    assert "https://www.wikidata.org/wiki/Q68166#P1082" in markdown
    assert "nicht automatisch im Recht" in markdown, "Wikidata is often the wrong one"


def test_an_empty_comparison_says_so_rather_than_going_quiet(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """A section that ran and found nothing must be distinguishable from a
    section that never ran.

    This test used to assert "_Keine Abweichungen gefunden._" here, which was
    the bug: the build helper never runs the comparison, so the honest answer
    is that it was not checked.
    """
    markdown = render_markdown(build(corpus, scope, tmp_path))

    assert "Nicht abgeglichen" in markdown
    assert "_Keine Abweichungen gefunden._" not in markdown


class TestWikidataEmptyStates:
    """Three different empty sections, three different sentences.

    For five live runs all three rendered as "keine Abweichungen gefunden" -
    "no differences found" - while our own robots gate was refusing the
    request. The dossier was asserting something false about the article.
    """

    def test_no_wikidata_item_says_so(self, corpus: FetchResult, scope: ScopeConfig, tmp_path: Path) -> None:
        dossier = build(corpus, scope, tmp_path).model_copy(
            update={"wikidata_item": None, "wikidata_checked": False}
        )
        assert "kein Wikidata-Objekt" in render_markdown(dossier)

    def test_a_failed_lookup_says_so_and_warns_against_reading_it_as_agreement(
        self, corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
    ) -> None:
        dossier = build(corpus, scope, tmp_path).model_copy(
            update={"wikidata_item": "Q42", "wikidata_checked": False}
        )
        markdown = render_markdown(dossier)

        assert "konnten nicht" in markdown
        assert "Q42" in markdown
        assert "kein** Hinweis darauf, dass alles stimmt" in markdown
        assert "_Keine Abweichungen gefunden._" not in markdown

    def test_a_real_comparison_with_no_differences_says_that_instead(
        self, corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
    ) -> None:
        dossier = build(corpus, scope, tmp_path).model_copy(
            update={"wikidata_item": "Q42", "wikidata_checked": True, "wikidata_comparable": 2}
        )
        markdown = render_markdown(dossier)

        assert "_Keine Abweichungen gefunden._" in markdown
        assert "Nicht abgeglichen" not in markdown
        assert "Nichts zu vergleichen" not in markdown

    def test_nothing_comparable_is_not_the_same_as_nothing_wrong(
        self, corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
    ) -> None:
        """Four of the first five live articles had no mapped infobox field, so
        nothing was compared - and the dossier said "keine Abweichungen
        gefunden", implying a comparison that never happened. Naming the
        infobox also makes the gap in PROPERTY_FOR_FIELD visible."""
        dossier = build(corpus, scope, tmp_path).model_copy(
            update={"wikidata_item": "Q42", "wikidata_checked": True, "wikidata_comparable": 0}
        )
        markdown = render_markdown(dossier)

        assert "Nichts zu vergleichen" in markdown
        assert "Infobox Fluss" in markdown, "say which infobox is unmapped"
        assert "_Keine Abweichungen gefunden._" not in markdown


class TestAgreementIsNotCurrency:
    """Adliswil's area agreed with a Wikidata figure from 2007, and the dossier
    called it "vermutlich aktuell". Two sources can agree because both are
    stale; saying otherwise turns a non-finding into false comfort."""

    @staticmethod
    def agreeing(external_as_of: int) -> Any:
        from wp_todo.models import Delta

        return Delta(
            kind="wikidata",
            label="Fläche",
            field="FLÄCHE",
            article_value="7.79",
            external_value="7.79",
            external_as_of=external_as_of,
            source="https://www.wikidata.org/wiki/Q68210#P2046",
            agrees=True,
        )

    def test_a_recent_agreement_is_reported_as_probably_current(
        self, corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
    ) -> None:
        dossier = build(corpus, scope, tmp_path).model_copy(
            update={"wikidata_checked": True, "wikidata_comparable": 1, "deltas": (self.agreeing(2025),)}
        )
        assert "vermutlich aktuell" in render_markdown(dossier)

    def test_agreement_with_an_old_figure_says_so_instead(
        self, corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
    ) -> None:
        dossier = build(corpus, scope, tmp_path).model_copy(
            update={"wikidata_checked": True, "wikidata_comparable": 1, "deltas": (self.agreeing(2007),)}
        )
        markdown = render_markdown(dossier)

        assert "vermutlich aktuell" not in markdown
        assert "beide können gemeinsam veraltet sein" in markdown


def test_not_looked_reads_differently_from_looked_and_found_nothing(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """The distinction the whole design rests on. A dossier that renders "we
    did not check" the same way as "we checked and it is fine" is lying by
    omission, and an editor would have no way to tell."""
    unchecked = build(corpus, scope, tmp_path)
    assert unchecked.interwiki_checked is False
    assert "_Nicht abgefragt._" in render_markdown(unchecked)

    checked = unchecked.model_copy(update={"interwiki_checked": True})
    assert "_Keine anderssprachige Fassung verlinkt._" in render_markdown(checked)

    with_links = checked.model_copy(update={"compared_languages": ("en", "fr")})
    assert "_Keine zusätzlichen Abschnitte in en, frwiki._" in render_markdown(with_links)


def test_a_pipe_in_a_claim_does_not_break_the_table(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    from wp_todo.models import Claim

    dossier = build(corpus, scope, tmp_path)
    claims = dossier.claims.model_copy(
        update={
            "claims": (
                Claim(
                    id="x-1",
                    kind="infobox_field",
                    text="WEBSITE = [[a|b]] | c",
                    line_no=3,
                    field="WEBSITE",
                ),
            )
        }
    )
    markdown = render_markdown(dossier.model_copy(update={"claims": claims}))

    row = next(line for line in markdown.splitlines() if "WEBSITE" in line and line.startswith("|"))
    separators = len(re.findall(r"(?<!\\)\|", row))
    assert separators == 5, f"escaped pipes must not add columns: {row}"
    assert r"[[a\|b]]" in row, "the pipe inside the value survives, escaped"


class TestSlug:
    def test_umlauts_are_transliterated_not_dropped(self) -> None:
        assert slug("Küsnachter Dorfbach") == "kuesnachter-dorfbach"

    def test_distinct_titles_do_not_collide(self) -> None:
        assert slug("Küsnacht") != slug("Ksnacht")

    def test_a_title_of_only_punctuation_still_yields_a_name(self) -> None:
        assert slug("!!!") == "artikel"


# ------------------------------------------------------------ source standing
def _ledger(domain: str, verdict: str, reason: str) -> SourceLedger:
    return SourceLedger(verdicts=(make_verdict(domain, verdict, reason, dt.date(2026, 3, 14)),))


def test_the_articles_own_sources_are_classified(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """Free to compute, and it answers what the reference count cannot: an
    article resting entirely on unrated hosts is a different problem from one
    resting on the federal statistics office."""
    dossier = build(corpus, scope, tmp_path)
    assert dossier.reference_standing, "the acceptance article cites external sources"

    markdown = render_markdown(dossier)
    assert "## Einstufung der zitierten Quellen" in markdown
    assert dossier.reference_standing[0].host in markdown


def test_unrated_is_not_presented_as_a_criticism(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """Most of the web is unrated. Saying so must not read as an accusation."""
    markdown = render_markdown(build(corpus, scope, tmp_path))
    assert "kein Urteil" in markdown
    assert "nicht, dass mit ihr etwas nicht stimmt" in markdown


def test_official_sources_sort_above_unrated_ones(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    tiers = [s.tier for s in build(corpus, scope, tmp_path).reference_standing]
    assert tiers == sorted(tiers, key=["official", "press_academic", "unrated"].index)


def test_a_blocked_host_moves_to_the_exclusions_with_its_reason(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """The rule the whole design rests on: something missing because of a
    decision must say which decision, or the dossier is lying by omission."""
    plain = build(corpus, scope, tmp_path)
    host = next(s.host for s in plain.reference_standing if s.tier == "unrated")

    blocked = build(corpus, scope, tmp_path, ledger=_ledger(host, "block", "Datendump von 2015"))
    markdown = render_markdown(blocked)

    assert "## Ausgeschlossene Quellen" in markdown
    assert "Datendump von 2015" in markdown
    assert "2026-03-14" in markdown

    table = markdown[markdown.index("## Einstufung") : markdown.index("## Ausgeschlossene")]
    assert f"`{host}`" not in table, "a blocked host belongs in the exclusions, not the table"


def test_the_exclusions_section_is_absent_when_nothing_was_dropped(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """An empty heading would imply something was considered and kept."""
    assert "## Ausgeschlossene Quellen" not in render_markdown(build(corpus, scope, tmp_path))


def test_a_note_travels_with_the_source(corpus: FetchResult, scope: ScopeConfig, tmp_path: Path) -> None:
    """This is the verdict that makes the checking compound: a source can be
    fine for one thing and useless for another."""
    plain = build(corpus, scope, tmp_path)
    host = next(s.host for s in plain.reference_standing if s.tier == "unrated")

    noted = build(corpus, scope, tmp_path, ledger=_ledger(host, "note", "gut für Öffnungszeiten"))
    markdown = render_markdown(noted)

    assert "gut für Öffnungszeiten" in markdown
    assert "## Ausgeschlossene Quellen" not in markdown, "a note is not an exclusion"


def test_standing_survives_the_json_round_trip(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    plain = build(corpus, scope, tmp_path)
    host = next(s.host for s in plain.reference_standing if s.tier == "unrated")
    payload = json.loads(render_json(build(corpus, scope, tmp_path, ledger=_ledger(host, "block", "x"))))

    entry = next(s for s in payload["reference_standing"] if s["host"] == host)
    assert entry["verdict"] == "block"
    assert entry["reason"] == "x"
    assert entry["decided"] == "2026-03-14"
