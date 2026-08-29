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
