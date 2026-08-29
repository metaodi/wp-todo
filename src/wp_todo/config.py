"""Scope and scoring configuration, loaded from ``config/scope.toml``.

Everything the maintainer is expected to retune lives here rather than in code:
the geographic tiles, the category trees, the maintenance-category weights and
the curve parameters. See ``docs/api-notes.md`` for why the limits are what they
are.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Verified live: ggsradius accepts 10..10000 metres, ggslimit tops out at 500.
MAX_GEO_RADIUS_M = 10_000
MAX_GEO_LIMIT = 500
# Verified doc-side only: deepcat traverses at most 5 levels.
MAX_CATEGORY_DEPTH = 5


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GeoScope(Frozen):
    """One geosearch tile. The API caps a single request at a 10 km radius, so
    covering the Zürichsee region means tiling it."""

    label: str
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    radius_m: int = Field(default=MAX_GEO_RADIUS_M, ge=10, le=MAX_GEO_RADIUS_M)


class CategoryScope(Frozen):
    """A category tree walked with ``list=categorymembers``.

    We walk it ourselves rather than using CirrusSearch ``deepcat:``: the walk is
    cacheable and deterministic, whereas deepcat depends on an external SPARQL
    service and silently caps at 256 categories.
    """

    name: str
    depth: int = Field(default=1, ge=0, le=MAX_CATEGORY_DEPTH)
    label: str | None = None


class ExcludeConfig(Frozen):
    titles: tuple[str, ...] = ()
    title_patterns: tuple[str, ...] = ()
    category_patterns: tuple[str, ...] = ()

    def compiled_title_patterns(self) -> tuple[re.Pattern[str], ...]:
        return tuple(re.compile(p) for p in self.title_patterns)

    def compiled_category_patterns(self) -> tuple[re.Pattern[str], ...]:
        return tuple(re.compile(p) for p in self.category_patterns)


class MarkerRule(Frozen):
    """An in-text staleness marker.

    ``pattern`` may capture a year in a group named ``year``. If it does not, a
    year is looked for elsewhere on the same line - that is how the bare adverbs
    ("derzeit", "aktuell") are made meaningful.
    """

    code: str
    pattern: str
    max_age_years: int = Field(ge=0)
    weight: float = 0.0
    requires_year: bool = True

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern, re.IGNORECASE)


class PageviewsConfig(Frozen):
    """Attention multiplier, not an additive term."""

    months: int = Field(default=12, ge=1, le=60)
    pivot: float = Field(default=500.0, gt=0.0)
    strength: float = Field(default=0.5, ge=0.0)
    max_multiplier: float = Field(default=3.0, ge=1.0)
    # access/agent as verified live; agent=user excludes spiders.
    access: str = "all-access"
    agent: str = "user"


class ScoringConfig(Frozen):
    """Weights and curves. Retune here, never in code."""

    # Maintenance categories are matched by prefix, longest prefix wins, so
    # "Kategorie:Wikipedia:Veraltet seit" can outrank plain "…:Veraltet".
    maintenance: dict[str, float] = Field(default_factory=dict)
    unknown_maintenance_weight: float = 0.0
    # {{Veraltet|seit=YYYY}} - older seit= means more overdue.
    veraltet_seit_bonus_per_year: float = 3.0
    veraltet_seit_bonus_cap: float = 30.0

    # Time since the last substantive edit, on a saturating curve.
    edit_age_weight: float = 40.0
    edit_age_half_life_days: float = 900.0
    substantive_min_bytes: int = Field(default=100, ge=0)
    history_depth: int = Field(default=50, ge=1, le=MAX_GEO_LIMIT)

    markers: tuple[MarkerRule, ...] = ()
    marker_cap: float = 30.0

    pageviews: PageviewsConfig = PageviewsConfig()

    @model_validator(mode="after")
    def _check_marker_codes(self) -> Self:
        codes = [m.code for m in self.markers]
        duplicates = sorted({c for c in codes if codes.count(c) > 1})
        if duplicates:
            raise ValueError(f"duplicate marker codes: {duplicates}")
        return self

    def maintenance_weight(self, category: str) -> tuple[str, float] | None:
        """Longest matching prefix wins; returns the matched key and its weight."""
        best: tuple[str, float] | None = None
        for prefix, weight in self.maintenance.items():
            if category.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
                best = (prefix, weight)
        if best is None and self.unknown_maintenance_weight:
            return ("<unknown>", self.unknown_maintenance_weight)
        return best


class MetaConfig(Frozen):
    """Identity sent to Wikimedia on every request.

    The policy requires real contact information - an email or a project URL.
    An unfilled placeholder is a hard error, not a warning.
    """

    contact: str
    user_agent_product: str = "wp-todo"

    @model_validator(mode="after")
    def _reject_placeholder(self) -> Self:
        placeholder = not self.contact.strip() or "example.com" in self.contact or "FIXME" in self.contact
        if placeholder:
            raise ValueError(
                "meta.contact must be a real email address or project URL: the Wikimedia "
                "User-Agent policy requires contact information, and default or placeholder "
                "agents are blocked outright."
            )
        return self


class ScopeConfig(Frozen):
    meta: MetaConfig
    geo: tuple[GeoScope, ...] = ()
    category: tuple[CategoryScope, ...] = ()
    pages: tuple[str, ...] = ()
    exclude: ExcludeConfig = ExcludeConfig()
    scoring: ScoringConfig = ScoringConfig()

    @model_validator(mode="after")
    def _needs_some_scope(self) -> Self:
        if not (self.geo or self.category or self.pages):
            raise ValueError("scope.toml defines no scope: add a [[geo]], [[category]] or pages entry")
        labels = [g.label for g in self.geo]
        duplicates = sorted({label for label in labels if labels.count(label) > 1})
        if duplicates:
            raise ValueError(f"duplicate geo labels: {duplicates}")
        return self

    def is_excluded(self, title: str, categories: tuple[str, ...] = ()) -> str | None:
        """Returns the reason for exclusion, or None."""
        if title in self.exclude.titles:
            return "title"
        for pattern in self.exclude.compiled_title_patterns():
            if pattern.search(title):
                return f"title_pattern:{pattern.pattern}"
        for pattern in self.exclude.compiled_category_patterns():
            for category in categories:
                if pattern.search(category):
                    return f"category_pattern:{pattern.pattern}"
        return None


ConfigPath = Annotated[Path, "path to scope.toml"]


def load_scope(path: ConfigPath) -> ScopeConfig:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    return ScopeConfig.model_validate(raw)
