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
    #: How far either side of the match to look for a year, in characters, when
    #: the pattern does not capture one itself. "derzeit" in a sentence about
    #: 1961 is history, not staleness, so the year has to actually be nearby.
    year_window: int = Field(default=60, ge=0, le=500)

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


class HttpConfig(Frozen):
    """Politeness settings. Requests are serialised, so `delay_s` is the hard
    ceiling on our request rate: 1.0 means at most one request per second."""

    delay_s: float = Field(default=1.0, ge=0.0, le=60.0)
    max_retries: int = Field(default=4, ge=1, le=10)
    maxlag: int = Field(default=5, ge=1, le=30)
    timeout_s: float = Field(default=60.0, gt=0.0)
    #: Hard ceiling on requests per run; 0 disables. A scope change should not
    #: be able to turn into an unbounded crawl by accident.
    max_requests: int = Field(default=8000, ge=0)
    progress_every: int = Field(default=250, ge=1)


class FetchConfig(Frozen):
    """How much of the corpus to fetch in full.

    Discovery is cheap - one request per tile returns categories and the latest
    revision for up to 500 articles. Everything after that costs about two
    requests per article, which is where a region-sized scope turns into
    thousands of requests.

    So detail is spent where it can change the answer: articles are ranked by a
    provisional score first, and only the top `detail_top_n` get their revision
    history, wikitext and pageviews fetched. Set to 0 to fetch everything.
    """

    detail_top_n: int = Field(default=1000, ge=0)


class ResearchConfig(Frozen):
    """The per-article research stage: budgets and what it may read.

    Every ceiling here is a hard stop that raises, in the spirit of
    `http.max_requests`. The stage is opt-in per article and is deliberately
    not wired into the weekly refresh: it costs requests to hosts that never
    asked to be crawled, so it runs when somebody asks for it.
    """

    #: Requests to hosts outside Wikimedia, per run.
    max_fetches: int = Field(default=60, ge=0)
    #: Seconds between requests *to the same host*.
    delay_s: float = Field(default=2.0, ge=0.0)
    timeout_s: float = Field(default=20.0, gt=0.0)
    max_doc_bytes: int = Field(default=2_000_000, ge=1_000)
    #: Honouring robots.txt is the default and turning it off is a decision
    #: somebody has to write down in the config file.
    respect_robots: bool = True
    compare_wikidata: bool = True
    compare_languages: tuple[str, ...] = ("en", "fr", "it")

    #: Where dossiers are written, and where the worklist looks for them when
    #: deciding which rows get a "Recherche" link. One key so the renderer and
    #: the research command cannot disagree about the path.
    dir: Path = Path("research")
    #: Where recorded source verdicts live. Kept out of scope.toml because the
    #: CLI appends to it, and a file a program writes should not also be the
    #: file holding every hand-tuned scoring weight.
    sources: Path = Path("config/sources.toml")
    #: Fast path for circularity only. The verbatim-span check below is what
    #: actually decides, because new Wikipedia mirrors appear faster than any
    #: hand-kept list is updated.
    mirror_domains: tuple[str, ...] = (
        "wikiwand.com",
        "dbpedia.org",
        "alchetron.com",
        "everipedia.org",
        "wikizero.com",
        "wiki2.org",
        "cleverpedia.net",
        "deacademic.com",
        "wikibrief.org",
    )
    #: Characters of verbatim overlap with the article above which a document is
    #: treated as a copy of it rather than as a source for it.
    circularity_span: int = Field(default=200, ge=40)

    # ------------------------------------------------------------- the agent
    # Only read when `--agent` is passed. Without it nothing below costs
    # anything, because no model is ever consulted.
    model: str = "claude-opus-5"
    effort: str = "medium"
    #: Model calls per article. Deliberately tight: the stage earns its keep by
    #: reading the article's own references, which is a handful of questions,
    #: not by exploring. Running out is reported, never silently truncated.
    max_llm_calls: int = Field(default=10, ge=1, le=100)
    #: Claims put to the model, most overdue first. The rest are named in the
    #: dossier as unexamined rather than left to look answered.
    max_claims: int = Field(default=5, ge=1, le=40)
    #: References fetched and shown, best standing first. What was left unread
    #: is in the transcript.
    max_reference_docs: int = Field(default=8, ge=1, le=40)
    #: Search results fetched, when the references answered nothing.
    max_search_docs: int = Field(default=6, ge=1, le=40)
    #: How stale a dated claim has to be before it is worth a call. A figure
    #: from last year is not news.
    stale_after_years: int = Field(default=2, ge=0, le=50)

    @model_validator(mode="after")
    def _check_effort(self) -> Self:
        allowed = ("low", "medium", "high", "xhigh", "max")
        if self.effort not in allowed:
            raise ValueError(f"research.effort must be one of {', '.join(allowed)}")
        return self


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
    http: HttpConfig = HttpConfig()
    fetch: FetchConfig = FetchConfig()
    geo: tuple[GeoScope, ...] = ()
    category: tuple[CategoryScope, ...] = ()
    pages: tuple[str, ...] = ()
    exclude: ExcludeConfig = ExcludeConfig()
    scoring: ScoringConfig = ScoringConfig()
    research: ResearchConfig = ResearchConfig()

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
