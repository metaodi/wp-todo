"""Configuration is where the maintainer retunes things, so its edges matter."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from wp_todo.config import ExcludeConfig, GeoScope, MetaConfig, ScopeConfig, ScoringConfig, load_scope

REPO_SCOPE = Path(__file__).parent.parent / "config" / "scope.toml"


def test_repo_scope_loads() -> None:
    scope = load_scope(REPO_SCOPE)
    assert scope.geo
    assert scope.scoring.maintenance


def test_longest_maintenance_prefix_wins() -> None:
    scoring = ScoringConfig(
        maintenance={
            "Kategorie:Wikipedia:Veraltet": 50.0,
            "Kategorie:Wikipedia:Veraltet seit": 60.0,
            "Kategorie:Wikipedia:Weblink offline": 4.0,
            "Kategorie:Wikipedia:Weblink offline IABot": 1.0,
        }
    )
    assert scoring.maintenance_weight("Kategorie:Wikipedia:Veraltet seit 2024") == (
        "Kategorie:Wikipedia:Veraltet seit",
        60.0,
    )
    assert scoring.maintenance_weight("Kategorie:Wikipedia:Veraltet") == (
        "Kategorie:Wikipedia:Veraltet",
        50.0,
    )
    # The bot-generated family must not inherit the higher human-signal weight.
    assert scoring.maintenance_weight("Kategorie:Wikipedia:Weblink offline IABot") == (
        "Kategorie:Wikipedia:Weblink offline IABot",
        1.0,
    )
    assert scoring.maintenance_weight("Kategorie:Ort in der Schweiz") is None


@pytest.mark.parametrize("contact", ["", "   ", "you@example.com", "FIXME"])
def test_placeholder_contact_is_rejected(contact: str) -> None:
    """An unfilled contact would make us an anonymous scraper. Hard error."""
    with pytest.raises(ValidationError):
        MetaConfig(contact=contact)


def test_scope_needs_some_scope(meta: MetaConfig) -> None:
    with pytest.raises(ValidationError):
        ScopeConfig(meta=meta)  # no geo, no category, no pages


def test_duplicate_geo_labels_rejected(meta: MetaConfig) -> None:
    with pytest.raises(ValidationError):
        ScopeConfig(
            meta=meta,
            geo=(
                GeoScope(label="A", lat=47.0, lon=8.0),
                GeoScope(label="A", lat=47.1, lon=8.1),
            ),
        )


def test_radius_above_the_api_maximum_is_rejected(meta: MetaConfig) -> None:
    """10 km is the live cap; a bigger radius is a config error, not a request."""
    with pytest.raises(ValidationError):
        ScopeConfig(meta=meta, geo=(GeoScope(label="A", lat=47.0, lon=8.0, radius_m=10001),))


def test_exclusions(meta: MetaConfig) -> None:
    scope = ScopeConfig(
        meta=meta,
        pages=("Thalwil",),
        exclude=ExcludeConfig(
            titles=("Handverbot",),
            title_patterns=(r"^Liste ", r"^\d{1,4}$"),
            category_patterns=("Begriffsklärung",),
        ),
    )
    assert scope.is_excluded("Handverbot") == "title"
    assert scope.is_excluded("Liste der Seen") == "title_pattern:^Liste "
    assert scope.is_excluded("1984") == r"title_pattern:^\d{1,4}$"
    assert scope.is_excluded("Zug", ("Kategorie:Wikipedia:Begriffsklärung",)) == (
        "category_pattern:Begriffsklärung"
    )
    assert scope.is_excluded("Thalwil") is None


def test_research_defaults_are_conservative() -> None:
    """Every one of these is a ceiling somebody would have to raise on purpose."""
    scope = load_scope(REPO_SCOPE)

    assert scope.research.respect_robots is True
    assert scope.research.max_fetches > 0, "an unbounded crawl must not be the default"
    assert scope.research.delay_s >= 1.0
    assert scope.research.max_doc_bytes <= 10_000_000


def test_unknown_research_key_is_rejected(tmp_path: Path) -> None:
    """A typo in a budget key must not silently leave the budget at its default."""
    path = tmp_path / "scope.toml"
    path.write_text(
        '[meta]\ncontact = "https://example.org/wp-todo"\npages = ["X"]\n[research]\nmax_fetch = 5\n',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_scope(path)


def test_a_call_budget_below_its_own_claim_ceilings_is_refused(tmp_path: Path) -> None:
    """Two ceilings that cannot both be honoured is a trap, not a policy.

    At the shipped defaults it was one: max_llm_calls was 10 against ceilings
    authorising 12, so the section summaries - one call, and the most
    substantial part of the committed dossiers - were structurally the first
    thing starved, and nothing said so.
    """
    path = tmp_path / "scope.toml"
    path.write_text(
        "pages = ['Musterwil']\n"
        "[meta]\ncontact = 'mail@beispiel.ch'\n"
        "[research]\nmax_llm_calls = 4\nmax_claims = 5\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as raised:
        load_scope(path)
    message = str(raised.value)
    assert "max_llm_calls" in message
    assert "15" in message, "the error names the number to raise it to"


def test_the_shipped_defaults_can_pay_for_their_own_agenda(tmp_path: Path) -> None:
    """The regression guard for the arithmetic above."""
    path = tmp_path / "scope.toml"
    path.write_text("pages = ['Musterwil']\n[meta]\ncontact = 'mail@beispiel.ch'\n", encoding="utf-8")
    research = load_scope(path).research
    assert research.max_llm_calls >= research.calls_needed


def test_a_fetch_ceiling_that_cannot_pay_for_the_link_check_is_refused(tmp_path: Path) -> None:
    """Same trap as the model budget, one layer out: a ceiling below what the
    settings authorise does not mean "fetch less", it means the agent runs out
    of documents partway through every article."""
    path = tmp_path / "scope.toml"
    path.write_text(
        "pages = ['Musterwil']\n"
        "[meta]\ncontact = 'mail@beispiel.ch'\n"
        "[research]\nmax_fetches = 20\nmax_link_checks = 40\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as raised:
        load_scope(path)
    assert "max_fetches" in str(raised.value)


def test_turning_the_link_check_off_is_always_a_valid_config(tmp_path: Path) -> None:
    """`max_link_checks = 0` is the way back to a Wikimedia-only run, so it
    must never be blocked by the ceiling arithmetic."""
    path = tmp_path / "scope.toml"
    path.write_text(
        "pages = ['Musterwil']\n"
        "[meta]\ncontact = 'mail@beispiel.ch'\n"
        "[research]\nmax_link_checks = 0\nmax_fetches = 20\n",
        encoding="utf-8",
    )
    assert load_scope(path).research.max_link_checks == 0
