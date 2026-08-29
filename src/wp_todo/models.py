"""Models for what we fetch and what we score.

The fetch artefact is the boundary between the two stages: `score` reads it and
never touches the network, so weights can be retuned offline against cached
data.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Revision(Strict):
    """One revision.

    There is no bot flag here, and that is not an oversight: the revision table
    does not carry one (verified). Bot authorship is decided by looking the
    editor up in the wiki's bot group, recorded alongside in the fetch artefact.
    """

    revid: int
    timestamp: dt.datetime
    user: str | None = None
    minor: bool = False
    size: int = 0
    comment: str = ""
    tags: tuple[str, ...] = ()
    anon: bool = False

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Revision:
        return cls(
            revid=int(raw.get("revid", 0)),
            timestamp=dt.datetime.fromisoformat(str(raw["timestamp"]).replace("Z", "+00:00")),
            user=raw.get("user"),
            minor=bool(raw.get("minor", False)),
            size=int(raw.get("size", 0)),
            comment=str(raw.get("comment", "")),
            tags=tuple(raw.get("tags", []) or []),
            anon=bool(raw.get("anon", False)) or bool(raw.get("temp", False)),
        )


class Article(Strict):
    """Everything fetched for one article."""

    pageid: int
    title: str
    scope_label: str
    scope_kind: str = "geo"
    #: Unfiltered: the dated Veraltet categories are not hidden categories.
    categories: tuple[str, ...] = ()
    revisions: tuple[Revision, ...] = ()
    wikitext: str | None = None
    #: month (YYYYMM) -> views. None means the endpoint had no data.
    pageviews: dict[str, int] | None = None

    @property
    def latest_revision(self) -> Revision | None:
        return self.revisions[0] if self.revisions else None


class FetchResult(Strict):
    """The cached corpus. Scoring is a pure function of this."""

    #: Server time at fetch, used as the fixed "now" so scoring is reproducible.
    reference_date: dt.date
    pageviews_start: str
    pageviews_end: str
    #: Accounts in the wiki's bot group at fetch time (one cacheable request).
    bot_accounts: tuple[str, ...] = ()
    articles: tuple[Article, ...] = ()


class Reason(Strict):
    """Why an article surfaced. Every one carries its own contribution."""

    code: str
    detail: str = ""
    points: float = 0.0
    evidence: str | None = None


class Subscores(Strict):
    maintenance: float = 0.0
    edit_age: float = 0.0
    markers: float = 0.0
    #: A multiplier, not an additive term.
    attention: float = 1.0


class ScoredArticle(Strict):
    pageid: int
    title: str
    scope_label: str
    score: float
    base_score: float
    subscores: Subscores
    reasons: tuple[Reason, ...] = ()
    last_substantive_edit: dt.date | None = None
    last_substantive_edit_days: int | None = None
    last_edit: dt.date | None = None
    monthly_pageviews: int | None = None
    edit_url: str = ""


class ScoreResult(Strict):
    reference_date: dt.date
    pageviews_window: str
    articles: tuple[ScoredArticle, ...] = Field(default_factory=tuple)
