"""Signal-by-signal tests, including the boring edges.

Everything here is built by hand: scoring is a pure function of the fetch
artefact, so it needs no fixtures and no network.
"""

from __future__ import annotations

import datetime as dt

from wp_todo.config import MarkerRule, MetaConfig, ScopeConfig, ScoringConfig
from wp_todo.models import Article, FetchResult, Revision
from wp_todo.score import score_corpus, scoring_reference

REFERENCE = dt.date(2026, 8, 29)
BOTS = ("Xqbot", "InternetArchiveBot")


def revision(days_ago: int, *, size: int, user: str = "Mensch", minor: bool = False) -> Revision:
    return Revision(
        revid=1000 + days_ago,
        timestamp=dt.datetime.combine(REFERENCE - dt.timedelta(days=days_ago), dt.time(12, 0), dt.UTC),
        user=user,
        minor=minor,
        size=size,
    )


def corpus(*articles: Article) -> FetchResult:
    return FetchResult(
        reference_date=REFERENCE,
        pageviews_start="20250801",
        pageviews_end="20260701",
        bot_accounts=BOTS,
        articles=articles,
    )


def scope(**scoring: object) -> ScopeConfig:
    defaults: dict[str, object] = {
        "maintenance": {
            "Kategorie:Wikipedia:Veraltet seit": 60.0,
            "Kategorie:Wikipedia:Veraltet": 50.0,
            "Kategorie:Wikipedia:Belege fehlen": 15.0,
        },
        "edit_age_weight": 40.0,
        "edit_age_half_life_days": 900.0,
        "substantive_min_bytes": 100,
    }
    defaults.update(scoring)
    return ScopeConfig(
        meta=MetaConfig(contact="https://github.com/metaodi/wp-todo"),
        pages=("X",),
        scoring=ScoringConfig(**defaults),  # type: ignore[arg-type]
    )


def article(**kwargs: object) -> Article:
    base: dict[str, object] = {"pageid": 1, "title": "Testartikel", "scope_label": "Thalwil"}
    base.update(kwargs)
    return Article(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------- edges
def test_article_with_no_maintenance_templates_scores_no_maintenance() -> None:
    result = score_corpus(
        corpus(article(categories=("Kategorie:Ort im Kanton Zürich",), revisions=(revision(10, size=5000),))),
        scope(),
    )
    scored = result.articles[0]
    assert scored.subscores.maintenance == 0.0
    assert not [r for r in scored.reasons if r.code == "maintenance"]
    # A recent, unflagged article still scores something for age, but little.
    assert scored.score < 1.0


def test_only_bot_edits_falls_through_to_the_last_human_edit() -> None:
    """Revisions carry no bot flag, so authorship comes from the bot group."""
    revisions = (
        revision(5, size=5300, user="InternetArchiveBot"),
        revision(20, size=5200, user="Xqbot"),
        revision(800, size=5100, user="Mensch"),
        revision(900, size=4000, user="Mensch"),
    )
    result = score_corpus(corpus(article(revisions=revisions)), scope())
    scored = result.articles[0]
    expected = REFERENCE - dt.timedelta(days=800)
    assert scored.last_substantive_edit == expected
    # Ages are measured from the month-snapped reference, not the fetch day.
    assert scored.last_substantive_edit_days == (scoring_reference(REFERENCE) - expected).days


def test_trivial_edits_below_the_size_threshold_are_not_substantive() -> None:
    revisions = (
        revision(3, size=5010),  # +10 bytes vs its parent: a typo fix
        revision(9, size=5000),  # unchanged size: also trivial
        revision(1200, size=5000),  # +200 bytes vs its parent: substantive
        revision(1300, size=4800),
    )
    result = score_corpus(corpus(article(revisions=revisions)), scope())
    assert result.articles[0].last_substantive_edit == REFERENCE - dt.timedelta(days=1200)


def test_no_revision_data_scores_zero_edit_age() -> None:
    result = score_corpus(corpus(article(revisions=())), scope())
    scored = result.articles[0]
    assert scored.subscores.edit_age == 0.0
    assert scored.last_substantive_edit is None
    assert any(r.code == "no_substantive_edit" for r in scored.reasons)


def test_missing_pageview_data_is_neutral() -> None:
    """Missing data must neither punish nor reward."""
    with_views = article(
        pageid=1, title="A", revisions=(revision(1000, size=5000),), pageviews={"202601": 5000}
    )
    without = article(pageid=2, title="B", revisions=(revision(1000, size=5000),), pageviews=None)
    result = score_corpus(corpus(with_views, without), scope())
    by_title = {a.title: a for a in result.articles}
    assert by_title["B"].subscores.attention == 1.0
    assert by_title["B"].monthly_pageviews is None
    assert any(r.code == "no_pageview_data" for r in by_title["B"].reasons)
    # The high-traffic article outranks the identical low-traffic one.
    assert by_title["A"].score > by_title["B"].score
    assert by_title["A"].base_score == by_title["B"].base_score


def test_malformed_seit_is_reported_but_scores_nothing() -> None:
    """`seit=` is free text in practice: prose, a YYYY-MM, or empty."""
    for raw in ("einiger Zeit", "", "bald"):
        result = score_corpus(
            corpus(
                article(
                    categories=("Kategorie:Wikipedia:Veraltet",),
                    wikitext="{{Veraltet|seit=" + raw + "|des Abschnitts}}\nText",
                    revisions=(revision(100, size=5000),),
                )
            ),
            scope(),
        )
        reasons = {r.code: r for r in result.articles[0].reasons}
        assert "veraltet_seit_unparsable" in reasons, raw
        assert reasons["veraltet_seit_unparsable"].points == 0.0
        # The maintenance category itself still counts.
        assert result.articles[0].subscores.maintenance == 50.0


def test_well_formed_seit_adds_a_bonus_per_year() -> None:
    result = score_corpus(
        corpus(
            article(
                categories=("Kategorie:Wikipedia:Veraltet seit 2020",),
                wikitext="{{Veraltet|seit=2020}}",
                revisions=(revision(100, size=5000),),
            )
        ),
        scope(veraltet_seit_bonus_per_year=3.0, veraltet_seit_bonus_cap=30.0),
    )
    reasons = {r.code: r for r in result.articles[0].reasons}
    assert reasons["veraltet_seit"].points == 18.0  # 6 years x 3
    assert reasons["maintenance"].points == 60.0  # longest prefix wins


def test_zukunft_template_drives_the_dated_categories() -> None:
    """{{Zukunft|YYYY|MM}} is what populates "Veraltet nach <Monat> <Jahr>";
    Kategorie:Wikipedia:Zukunft itself is empty on dewiki."""
    due_result = score_corpus(
        corpus(article(wikitext="{{Zukunft|2025|05}}", revisions=(revision(100, size=5000),))),
        scope(),
    )
    reasons = {r.code: r for r in due_result.articles[0].reasons}
    assert "zukunft_faellig" in reasons
    assert reasons["zukunft_faellig"].points > 0

    not_yet = score_corpus(
        corpus(article(wikitext="{{Zukunft|2030|01}}", revisions=(revision(100, size=5000),))),
        scope(),
    )
    codes = {r.code for r in not_yet.articles[0].reasons}
    assert "zukunft_offen" in codes
    assert "zukunft_faellig" not in codes


# ----------------------------------------------------------------- markers
def marker_scope(year_window: int = 60, **kwargs: object) -> ScopeConfig:
    rule = MarkerRule(
        code="derzeit",
        pattern=r"\b(?:derzeit|aktuell)\b",
        max_age_years=5,
        weight=8.0,
        year_window=year_window,
    )
    stand = MarkerRule(
        code="stand_year",
        pattern=r"Stand:?\s*(?P<year>(?:19|20)\d{2})",
        max_age_years=3,
        weight=12.0,
    )
    kwargs.setdefault("marker_cap", 30.0)
    return scope(markers=(rule, stand), **kwargs)


def test_marker_captures_the_matching_line_as_evidence() -> None:
    text = "Einleitung\nDie Anlage ist derzeit im Bau, geplant seit 2009.\nSchluss"
    result = score_corpus(
        corpus(article(wikitext=text, revisions=(revision(10, size=5000),))), marker_scope()
    )
    hits = [r for r in result.articles[0].reasons if r.code == "marker:derzeit"]
    assert hits and hits[0].points == 8.0
    assert hits[0].evidence == "Die Anlage ist derzeit im Bau, geplant seit 2009."


def test_a_year_far_from_the_adverb_does_not_count() -> None:
    """ "derzeit" in a sentence that happens to mention 1961 elsewhere is
    history, not staleness."""
    filler = "x" * 200
    text = f"Im Jahr 1961 geschah etwas. {filler} Die Anlage ist derzeit in Betrieb."
    result = score_corpus(
        corpus(article(wikitext=text, revisions=(revision(10, size=5000),))), marker_scope(year_window=40)
    )
    assert not [r for r in result.articles[0].reasons if r.code == "marker:derzeit"]


def test_recent_year_does_not_trip_the_marker() -> None:
    text = "Stand: 2025 sind es 100 Einwohner."
    result = score_corpus(
        corpus(article(wikitext=text, revisions=(revision(10, size=5000),))), marker_scope()
    )
    assert not [r for r in result.articles[0].reasons if r.code.startswith("marker:")]


def test_marker_total_is_capped() -> None:
    text = "Stand: 2001 derzeit\n" * 5
    result = score_corpus(
        corpus(article(wikitext=text, revisions=(revision(10, size=5000),))),
        marker_scope(marker_cap=15.0),
    )
    assert result.articles[0].subscores.markers == 15.0


def test_no_wikitext_means_no_markers() -> None:
    result = score_corpus(
        corpus(article(wikitext=None, revisions=(revision(10, size=5000),))), marker_scope()
    )
    assert result.articles[0].subscores.markers == 0.0


# ------------------------------------------------------------------ curves
def test_edit_age_curve_saturates() -> None:
    def age_score(days: int) -> float:
        result = score_corpus(corpus(article(revisions=(revision(days, size=5000),))), scope())
        return result.articles[0].subscores.edit_age

    assert age_score(30) < age_score(900) < age_score(3000) < age_score(9000)
    # Saturating, not linear: ten times the age is nowhere near ten times the score.
    assert age_score(9000) < 40.0
    assert age_score(9000) < 3 * age_score(900)


def test_ranking_is_stable_for_tied_scores() -> None:
    """Equal scores must never reorder between runs."""
    articles = [
        article(pageid=i, title=title, revisions=(revision(500, size=5000),))
        for i, title in enumerate(["Zürich", "Adliswil", "Meilen"], start=1)
    ]
    first = score_corpus(corpus(*articles), scope())
    second = score_corpus(corpus(*reversed(articles)), scope())
    assert [a.title for a in first.articles] == [a.title for a in second.articles]
    assert [a.title for a in first.articles] == ["Adliswil", "Meilen", "Zürich"]
