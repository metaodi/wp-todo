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
    #: False when the article was left at discovery detail - see FetchConfig.
    detailed: bool = True

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


class MarkerHit(Strict):
    """One place a staleness marker fired, with enough to go back to it."""

    code: str
    year: int
    line_no: int
    line: str


class Claim(Strict):
    """One dated assertion the article makes, and where it makes it.

    A claim is not a finding. It is the *question* the research stage goes and
    asks: "the article says this, as of that year - is it still true?"
    """

    #: Content-derived, so it survives an edit elsewhere in the article and a
    #: dossier diff between two weekly runs stays readable.
    id: str
    kind: str
    text: str
    line_no: int
    section: str | None = None
    #: Infobox parameter name, when the claim came from one.
    field: str | None = None
    asserted_value: str | None = None
    as_of_year: int | None = None


class ReferenceSummary(Strict):
    """How well-sourced this article is, and how old its sourcing is.

    "The newest source on this page is from 2011" is often the single most
    informative line in a dossier.
    """

    total: int = 0
    with_year: int = 0
    newest_year: int | None = None
    oldest_year: int | None = None
    external_urls: tuple[str, ...] = ()


class ArticleClaims(Strict):
    """Everything the deterministic pass could work out from the wikitext."""

    pageid: int
    title: str
    infobox: str | None = None
    sections: tuple[str, ...] = ()
    claims: tuple[Claim, ...] = ()
    references: ReferenceSummary = Field(default_factory=ReferenceSummary)


class Delta(Strict):
    """A comparison against an already-structured source.

    Deliberately not a judgement. It records what the article says, what the
    other source says, when each is as of, and where to check - and stops
    there. Wikidata is often the one that is wrong, and only a human reading
    both is in a position to know.
    """

    kind: str
    label: str = ""
    claim_id: str | None = None
    field: str | None = None
    article_value: str | None = None
    external_value: str | None = None
    article_as_of: int | None = None
    external_as_of: int | None = None
    source: str = ""
    detail: str = ""
    #: True when the two sources say the same thing. Agreement is worth
    #: reporting too: it tells an editor a figure has already been checked.
    agrees: bool = False


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
    #: True when only discovery data was available, so the score is a lower bound.
    provisional: bool = False
    edit_url: str = ""


class Dossier(Strict):
    """A briefing on one article, for a human to read before editing it.

    Not an edit, not a draft, and not a source. Every entry is a pointer at
    something to go and check - the checking, and all the writing, stays with
    the editor.

    Nothing here is derived from the wall clock: `reference_date` comes from the
    corpus, so re-running the stage against the same cache reproduces the file
    byte for byte and a weekly diff shows only what actually changed.
    """

    pageid: int
    title: str
    scope_label: str = ""
    reference_date: dt.date
    wikidata_item: str | None = None
    claims: ArticleClaims
    deltas: tuple[Delta, ...] = ()
    #: Whether the interwiki comparison ran at all. "We looked and found
    #: nothing" and "we did not look" are different answers, and a dossier that
    #: renders them the same way is lying by omission.
    interwiki_checked: bool = False
    #: Languages that were actually compared, so an empty result can be told
    #: apart from a language that has no article.
    compared_languages: tuple[str, ...] = ()
    edit_url: str = ""


class ScoreResult(Strict):
    reference_date: dt.date
    pageviews_window: str
    articles: tuple[ScoredArticle, ...] = Field(default_factory=tuple)
