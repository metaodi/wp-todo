"""Claim extraction: wikitext in, dated assertions out.

Every case here is something dewiki actually does, not something a parser might
theoretically meet.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from wp_todo.claims import extract_claims
from wp_todo.config import ScopeConfig, load_scope
from wp_todo.models import Article

REFERENCE = dt.date(2026, 8, 1)


@pytest.fixture
def config() -> ScopeConfig:
    return load_scope(Path("config/scope.toml"))


def article(wikitext: str) -> Article:
    return Article(pageid=42, title="Testort", scope_label="Test", wikitext=wikitext)


def claims_for(wikitext: str, config: ScopeConfig) -> dict[str, object]:
    result = extract_claims(article(wikitext), config, REFERENCE)
    return {claim.field or claim.kind: claim for claim in result.claims}


# ------------------------------------------------------------------- infobox
def test_wikilink_pipe_does_not_split_a_parameter(config: ScopeConfig) -> None:
    """`[[FDP.Die Liberalen|FDP]]` is one value, not two parameters."""
    found = claims_for(
        "{{Infobox Ort in der Schweiz\n| STADTPRÄSIDENT = Markus Bürgi ([[FDP.Die Liberalen|FDP]])\n}}\n",
        config,
    )
    claim = found["STADTPRÄSIDENT"]
    assert claim.asserted_value == "Markus Bürgi ([[FDP.Die Liberalen|FDP]])"  # type: ignore[attr-defined]


def test_nested_template_in_a_value_does_not_end_the_infobox(config: ScopeConfig) -> None:
    result = extract_claims(
        article(
            "{{Infobox Ort in der Schweiz\n| FLÄCHE = {{FormatNum|7.79}}\n| WEBSITE = www.example.ch\n}}\n"
        ),
        config,
        REFERENCE,
    )
    fields = {claim.field for claim in result.claims}
    assert fields == {"FLÄCHE", "WEBSITE"}


def test_transcluded_population_is_not_a_claim(config: ScopeConfig) -> None:
    """Swiss municipality population comes from a centralised template. Sending
    an editor after a number that is already maintained elsewhere is worse than
    saying nothing."""
    result = extract_claims(
        article(
            "{{Infobox Ort in der Schweiz\n"
            "| EINWOHNER = <!-- wird durch eine zentralisierte Vorlage eingebunden-->\n"
            "| STAND_EINWOHNER = <!-- wird durch eine zentralisierte Vorlage eingebunden-->\n"
            "}}\n"
        ),
        config,
        REFERENCE,
    )
    assert [claim.field for claim in result.claims] == []


def test_stand_parameter_supplies_the_as_of_year(config: ScopeConfig) -> None:
    found = claims_for(
        "{{Infobox Ort in der Schweiz\n| EINWOHNER = 8500\n| STAND_EINWOHNER = 31. Dezember 2018\n}}\n",
        config,
    )
    assert found["EINWOHNER"].as_of_year == 2018  # type: ignore[attr-defined]


def test_year_inside_the_value_is_used_when_there_is_no_stand_field(config: ScopeConfig) -> None:
    found = claims_for(
        "{{Infobox Unternehmen\n| UMSATZ = 4,2 Mio. CHF (2019)\n}}\n",
        config,
    )
    assert found["UMSATZ"].as_of_year == 2019  # type: ignore[attr-defined]


def test_article_without_an_infobox_is_not_an_error(config: ScopeConfig) -> None:
    result = extract_claims(article("'''Nur Prosa''' und sonst nichts.\n"), config, REFERENCE)
    assert result.infobox is None
    assert result.claims == ()


def test_unclosed_infobox_is_survived(config: ScopeConfig) -> None:
    result = extract_claims(article("{{Infobox Ort in der Schweiz\n| FLÄCHE = 7.79\n"), config, REFERENCE)
    assert result.claims == ()


# ------------------------------------------------------------------- markers
def test_marker_claim_carries_its_section(config: ScopeConfig) -> None:
    result = extract_claims(
        article("== Bevölkerung ==\nDie Quote lag bei 12 % (Stand: 2015).\n"),
        config,
        REFERENCE,
    )
    marker = next(claim for claim in result.claims if claim.kind == "marker:stand_year")
    assert marker.section == "Bevölkerung"
    assert marker.as_of_year == 2015


def test_every_marker_hit_becomes_a_claim_not_just_the_oldest(config: ScopeConfig) -> None:
    """Scoring only needs the oldest hit. Research needs all of them - each one
    is a separate thing somebody has to go and check."""
    result = extract_claims(
        article(
            "== Wirtschaft ==\nUmsatz 4 Mio. (Stand: 2015).\n== Verkehr ==\nFahrgäste 900 (Stand: 2017).\n"
        ),
        config,
        REFERENCE,
    )
    years = sorted(
        claim.as_of_year
        for claim in result.claims
        if claim.kind == "marker:stand_year" and claim.as_of_year is not None
    )
    assert years == [2015, 2017]


def test_a_recent_marker_is_not_a_claim(config: ScopeConfig) -> None:
    """`max_age_years` is 3 for `stand_year`; last year's figure is current."""
    result = extract_claims(article("Die Zahl lag bei 12 (Stand: 2025).\n"), config, REFERENCE)
    assert result.claims == ()


# ----------------------------------------------------------------------- ids
def test_claim_ids_are_content_derived_not_positional(config: ScopeConfig) -> None:
    """A dossier diff between weekly runs should not churn because somebody
    added a paragraph higher up the page."""
    body = "== Wirtschaft ==\nUmsatz 4 Mio. (Stand: 2015).\n"
    before = extract_claims(article(body), config, REFERENCE)
    after = extract_claims(article("Ein neuer Einleitungssatz.\n\n" + body), config, REFERENCE)

    assert [claim.id for claim in before.claims] == [claim.id for claim in after.claims]
    assert before.claims[0].line_no != after.claims[0].line_no


def test_extraction_is_deterministic(config: ScopeConfig) -> None:
    body = "{{Infobox Ort in der Schweiz\n| FLÄCHE = 7.79\n}}\n== X ==\nZahl 3 (Stand: 2015).\n"
    first = extract_claims(article(body), config, REFERENCE)
    second = extract_claims(article(body), config, REFERENCE)
    assert first.model_dump_json() == second.model_dump_json()


# ---------------------------------------------------------------- references
def test_reference_years_span_the_article(config: ScopeConfig) -> None:
    result = extract_claims(
        article(
            "Satz eins.<ref>{{Literatur |Titel=Alt |Datum=1998}}</ref>\n"
            "Satz zwei.<ref>[https://example.ch/neu Bericht 2024]</ref>\n"
            "Satz drei.<ref name='x' />\n"
        ),
        config,
        REFERENCE,
    )
    assert result.references.total == 3
    assert result.references.newest_year == 2024
    assert result.references.oldest_year == 1998
    assert result.references.external_urls == ("https://example.ch/neu",)


def test_datum_parameter_beats_a_stray_year_in_the_title(config: ScopeConfig) -> None:
    """`Titel=Bericht 1848` is about 1848; `Datum=2024` is when it was published."""
    result = extract_claims(
        article("Satz.<ref>{{Literatur |Titel=Bericht 1848 |Datum=2024}}</ref>\n"),
        config,
        REFERENCE,
    )
    assert result.references.newest_year == 2024


def test_an_article_with_no_references_reports_nothing_rather_than_zero_years(
    config: ScopeConfig,
) -> None:
    result = extract_claims(article("Ganz ohne Belege.\n"), config, REFERENCE)
    assert result.references.total == 0
    assert result.references.newest_year is None


class TestMaintenanceTemplates:
    """The strongest signal the worklist has, and it was missing entirely.

    `Küsnachter Dorfbach` ranked first at 67.22, two thirds of it from
    `maintenance (Veraltet nach Mai 2025) +45` - an editor flagging that the
    page needs updating by that date. Its dossier never mentioned it, because
    only the regex marker rules were walked.
    """

    def test_an_overdue_zukunft_template_becomes_a_claim(self, config: ScopeConfig) -> None:
        result = extract_claims(article("Text {{Zukunft|2025|5}} mehr Text.\n"), config, REFERENCE)

        claim = next(c for c in result.claims if c.kind == "zukunft_template")
        assert claim.as_of_year == 2025
        assert "2025-05-01" in claim.text

    def test_a_future_zukunft_template_is_reported_without_a_year(self, config: ScopeConfig) -> None:
        """Not yet due is worth knowing, but it is not staleness."""
        result = extract_claims(article("Text {{Zukunft|2030|1}}.\n"), config, REFERENCE)

        claim = next(c for c in result.claims if c.kind == "zukunft_template")
        assert claim.as_of_year is None
        assert "ab 2030-01-01" in claim.text

    def test_a_dated_veraltet_template_becomes_a_claim(self, config: ScopeConfig) -> None:
        result = extract_claims(article("{{Veraltet|seit=2019}}\nText.\n"), config, REFERENCE)

        claim = next(c for c in result.claims if c.kind == "veraltet_template")
        assert claim.as_of_year == 2019

    def test_an_unreadable_seit_is_reported_rather_than_dropped(self, config: ScopeConfig) -> None:
        """A broken date is itself worth seeing - somebody meant to put one."""
        result = extract_claims(article("{{Veraltet|seit=einiger Zeit}}\nText.\n"), config, REFERENCE)

        claim = next(c for c in result.claims if c.kind == "veraltet_template")
        assert claim.as_of_year is None
        assert "nicht lesbar" in claim.text

    def test_the_category_is_used_only_when_the_templates_say_nothing(self, config: ScopeConfig) -> None:
        """Category and template are the same signal seen from two sides.
        Reporting both would report it twice."""
        with_template = Article(
            pageid=1,
            title="X",
            scope_label="t",
            wikitext="{{Veraltet|seit=2019}}\n",
            categories=("Kategorie:Wikipedia:Veraltet seit 2019",),
        )
        kinds = [c.kind for c in extract_claims(with_template, config, REFERENCE).claims]
        assert "veraltet_template" in kinds
        assert "veraltet_kategorie" not in kinds

    def test_the_category_stands_in_when_there_is_no_template(self, config: ScopeConfig) -> None:
        without = Article(
            pageid=1,
            title="X",
            scope_label="t",
            wikitext="Nur Prosa.\n",
            categories=("Kategorie:Wikipedia:Veraltet seit 2019",),
        )
        claim = next(
            c for c in extract_claims(without, config, REFERENCE).claims if c.kind == "veraltet_kategorie"
        )
        assert claim.as_of_year == 2019


# --------------------------------------------------- Weblinks and Literatur
def test_weblinks_and_literatur_urls_are_collected(config: ScopeConfig) -> None:
    """`Sanatorium Kilchberg`: 46 print refs, and the only two readable
    documents sitting under `Literatur` and `Weblinks` where nothing looked."""
    result = extract_claims(
        article(
            "Satz.<ref>Buch ohne URL, 1901.</ref>\n"
            "== Literatur ==\n"
            "* Autor: [https://example.ch/monografie.pdf ''Titel''] 2017.\n"
            "== Weblinks ==\n"
            "* [https://example.ch/ Offizielle Website]\n"
            "== Einzelnachweise ==\n"
            "<references />\n"
        ),
        config,
        REFERENCE,
    )
    assert result.references.total == 1
    assert result.references.external_urls == ()
    assert result.references.linked_urls == (
        "https://example.ch/",
        "https://example.ch/monografie.pdf",
    )


def test_linked_urls_stay_out_of_the_cited_ones(config: ScopeConfig) -> None:
    """The split is the point: `already_cited` and the standing table both mean
    "cited as a reference", and folding these in would overstate the sourcing."""
    result = extract_claims(
        article(
            "Satz.<ref>[https://example.ch/beleg Beleg]</ref>\n"
            "== Weblinks ==\n* [https://example.ch/ Seite]\n"
        ),
        config,
        REFERENCE,
    )
    assert result.references.external_urls == ("https://example.ch/beleg",)
    assert result.references.linked_urls == ("https://example.ch/",)


def test_a_url_that_is_both_cited_and_linked_is_not_counted_twice(config: ScopeConfig) -> None:
    result = extract_claims(
        article("Satz.<ref>[https://example.ch/x X]</ref>\n== Weblinks ==\n* [https://example.ch/x X]\n"),
        config,
        REFERENCE,
    )
    assert result.references.external_urls == ("https://example.ch/x",)
    assert result.references.linked_urls == ()


def test_links_outside_those_sections_are_left_alone(config: ScopeConfig) -> None:
    """Only the two sections that conventionally hold readable documents."""
    result = extract_claims(
        article("== Geschichte ==\nText mit https://example.ch/irgendwo mittendrin.\n"),
        config,
        REFERENCE,
    )
    assert result.references.linked_urls == ()


# ------------------------------------------------------------ Belege fehlen
def test_belege_fehlen_becomes_a_claim_with_the_editors_words(config: ScopeConfig) -> None:
    found = claims_for("{{Belege fehlen|Teilweise: Nur spärliche Einzelnachweise}}\nText.\n", config)
    claim = found["belege_fehlen"]
    assert claim.as_of_year is None
    assert "Teilweise: Nur spärliche Einzelnachweise" in claim.text


def test_belege_fehlen_without_a_reason_still_becomes_a_claim(config: ScopeConfig) -> None:
    assert "belege_fehlen" in claims_for("{{Belege fehlen}}\nText.\n", config)


def test_belege_fehlen_does_not_swallow_the_veraltet_category(config: ScopeConfig) -> None:
    """The category fallback is guarded by `if not claims`. Appending the
    sourcing claim any earlier would silence a real `Veraltet seit` category."""
    result = extract_claims(
        Article(
            pageid=42,
            title="Testort",
            scope_label="Test",
            wikitext="{{Belege fehlen}}\nText.\n",
            categories=("Kategorie:Wikipedia:Veraltet seit 2019",),
        ),
        config,
        REFERENCE,
    )
    kinds = {claim.kind for claim in result.claims}
    assert kinds == {"belege_fehlen", "veraltet_kategorie"}


def test_the_references_tag_is_not_itself_a_reference(config: ScopeConfig) -> None:
    """`<references />` closes the list, it is not an entry in it. Without the
    lookahead in `_REF` it matched as a self-closing `<ref/>` and every article
    that ends with the tag - which is nearly all of them - counted one too
    many."""
    result = extract_claims(
        article("Satz.<ref>Beleg</ref>\n== Einzelnachweise ==\n<references />\n"),
        config,
        REFERENCE,
    )
    assert result.references.total == 1
