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
    #: URLs found inside `<ref>` bodies: what the article actually cites.
    external_urls: tuple[str, ...] = ()
    #: URLs found under `Weblinks` and `Literatur` and cited nowhere. Readable
    #: documents, but not sourcing - see `claims._linked_urls` for why the two
    #: are not one field.
    linked_urls: tuple[str, ...] = ()


class ArticleClaims(Strict):
    """Everything the deterministic pass could work out from the wikitext."""

    pageid: int
    title: str
    infobox: str | None = None
    sections: tuple[str, ...] = ()
    claims: tuple[Claim, ...] = ()
    references: ReferenceSummary = Field(default_factory=ReferenceSummary)


class LinkStatus(Strict):
    """Whether one of the article's links still resolves, and how sure we are.

    Deliberately not a boolean. A checker that reports "dead" for a host that
    merely refused *us* causes real damage: an editor acting on it replaces a
    working link with an archive copy. That is the failure mode dewiki has
    already lived through with bot-driven dead-link tagging, which is why
    `scope.toml` weights those maintenance categories near zero.

    So `verdict` distinguishes "the document is gone" from "the host refused
    us", from "not reachable right now", from "we did not look". Only `tot`
    says the document is gone.
    """

    url: str
    #: tot | gesperrt | nicht erreichbar | umgeleitet | erreichbar | nicht geprüft
    verdict: str
    #: The HTTP status, when there was one. None for a refusal or a timeout.
    status: int | None = None
    #: Set only when it differs materially from `url` - a redirect to a
    #: different host, or one that dropped the path. The soft-404 shape.
    final_url: str = ""
    #: True for a URL inside a `<ref>`; False for one under Weblinks/Literatur.
    #: The split matters here for the same reason it does in ReferenceSummary.
    cited: bool = True
    #: The article already carries an archive link or a dead-link marker for
    #: this URL. Still checked - a marker can be stale and an archive link can
    #: be wrong - but it is not new work for the editor.
    archived_in_article: bool = False
    #: A Wayback snapshot, when one was found. A *candidate*, never a verified
    #: replacement: the snapshot may itself have captured a soft 404.
    snapshot_url: str = ""
    snapshot_date: str = ""
    #: The reason, when the verdict alone does not carry it.
    detail: str = ""


class LinkSummary(Strict):
    """Counts for the link check, and whether it got to finish.

    `checked` versus `total` is the load-bearing pair: a short dead-link list
    because the ceiling stopped the run is a different fact from a short list
    because the links are fine.
    """

    total: int = 0
    checked: int = 0
    dead: int = 0
    blocked: int = 0
    unreachable: int = 0
    redirected: int = 0
    reachable: int = 0
    #: True when the request ceiling stopped the check before it was done.
    budget_exhausted: bool = False


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


class SourceStanding(Strict):
    """What is known about one host before anybody reads what it says.

    Reported, not enforced: only an explicit `block` removes anything, and a
    block is always listed with its reason. Everything else is ordering and
    annotation, which costs no recall.
    """

    host: str
    tier: str = "unrated"
    signals: tuple[str, ...] = ()
    verdict: str | None = None
    reason: str = ""
    decided: dt.date | None = None
    #: How many of the article's references point at this host.
    references: int = 0
    #: The rendered German summary, so the JSON says the same as the markdown.
    label: str = ""


class Finding(Strict):
    """One thing a model claims to have found, after the gates had their say.

    A finding is the most dangerous object in this codebase: it is the only
    place where something a language model said reaches a file that gets
    committed and read. So it carries its own evidence rather than its own
    conclusion - a verbatim quote that was mechanically checked to exist in a
    document we fetched and stored, and the URL of that document.

    `status` is what the model said. It is not a verdict, and nothing here is
    true until a person has opened `url` and read it.
    """

    claim_id: str
    #: What the claim was, repeated here so a finding reads on its own.
    claim_text: str = ""
    #: confirms_current | supersedes_with_newer_value | contradicts_current
    status: str
    current_value: str | None = None
    as_of: int | None = None
    #: Verbatim, and verified to appear in the stored text of `url`.
    quote: str = ""
    url: str = ""
    host: str = ""
    #: The host's tier and signals, rendered - so the reader can weigh it.
    standing: str = ""
    #: True when this came from a source the article already cites, which is a
    #: different and cheaper kind of finding than one from an open web search.
    from_reference: bool = False
    #: Set when the recency gate demoted it: the source is not newer than the
    #: article, so it is context rather than an update.
    demoted: str = ""
    #: False when a number in `current_value` is not in the quote. The quote
    #: gate proves the sentence is on the page; only this says whether the
    #: sentence carries the figure printed beside it, which is what decides
    #: whether the dossier may write "Laut Quelle".
    quote_supports_value: bool = True
    #: The model's own confidence. Orders the list; never admits anything.
    confidence: float = 0.0


class DroppedFinding(Strict):
    """Something the model said that a gate refused, and which gate.

    Reported rather than swallowed. A run where the quote gate rejected six of
    twenty answers is telling the reader something important about that run,
    and hiding it would make the surviving findings look better than they are.
    """

    claim_id: str = ""
    gate: str
    detail: str = ""
    url: str = ""


class ClaimOutcome(Strict):
    """What became of one claim on the agenda, and where.

    The dossier used to say one thing about every claim it could not report a
    finding for, chosen from a single run-level flag. Three different facts
    were printed as the same sentence - and for a claim whose answer a gate
    refused, that sentence said the opposite of what happened: *"keine Quelle
    sagte etwas dazu"* about a source that had said something the machine then
    threw away.

    So the outcome is recorded per claim, at the point the decision is made.
    It is the `_Nicht abgefragt._` versus `_Keine gefunden._` rule from
    CLAUDE.md, applied to the agent's own agenda.
    """

    claim_id: str
    #: found | dropped | nothing_found | budget | not_asked
    outcome: str
    #: Which phase the outcome came from: `reference`, `web`, or empty when the
    #: claim was never put to the model at all.
    phase: str = ""


class SectionNote(Strict):
    """A couple of bullet points on a section this article does not have.

    Grounded in what the other language edition's section actually says, not
    in what a model knows about the subject: the bullets are a pointer at text
    somebody can go and read, in the same way every other line of a dossier is.
    """

    heading: str
    lang: str
    source: str = ""
    bullets: tuple[str, ...] = ()


class AgentRun(Strict):
    """What the model layer did, and what it did not get to.

    Present only when `--agent` was passed. Its absence in a dossier means the
    stage never ran, which is the normal case: the deterministic dossier costs
    nothing and this one costs money.
    """

    model: str
    effort: str
    calls: int = 0
    cached_calls: int = 0
    budget: int = 0
    #: True when a call was refused because the ceiling was reached. The
    #: dossier says so loudly: a short findings list because the budget ran out
    #: is a different fact from a short findings list because there was little
    #: to find.
    budget_exhausted: bool = False
    #: Claims that were on the agenda and never examined, whatever the reason.
    #: Derived from `outcomes`, and kept because `todo.json` consumers read it.
    unexamined: tuple[str, ...] = ()
    #: One entry per agenda claim, saying what became of it. Empty on a dossier
    #: written before this existed, which is why the renderer still handles
    #: `unexamined` on its own.
    outcomes: tuple[ClaimOutcome, ...] = ()
    #: True when there were sections to summarise and the budget refused the
    #: call. Without it an empty summary block is indistinguishable from one
    #: the model had nothing to say about.
    sections_skipped: bool = False
    #: Documents fetched for verification, and how many were the article's own
    #: references rather than search results.
    documents: int = 0
    reference_documents: int = 0
    searched: bool = False
    dropped: tuple[DroppedFinding, ...] = ()
    #: Relative path of the committed transcript, so the dossier can link it.
    transcript: str = ""
    #: Why the stage stopped early, when it stopped by failing rather than by
    #: finishing. Empty on a healthy run. A dossier whose agent died has to say
    #: so where the findings would have been: "nothing found" and "the stage
    #: crashed before it could look" are not the same sentence.
    failed: str = ""


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
    #: Whether the Wikidata statements were actually retrieved. "We compared and
    #: found nothing" and "we never got the data" are different answers, and a
    #: dossier that renders them the same way is asserting something false.
    #:
    #: This is not hypothetical: for five live runs it said "keine Abweichungen
    #: gefunden" while our own robots gate was refusing the request.
    wikidata_checked: bool = False
    #: How many infobox fields had a mapped Wikidata property to compare
    #: against. Zero means nothing was compared, which is a different answer
    #: from "compared and agreed" and must not render as one.
    wikidata_comparable: int = 0
    claims: ArticleClaims
    deltas: tuple[Delta, ...] = ()
    #: Whether the interwiki comparison ran at all. "We looked and found
    #: nothing" and "we did not look" are different answers, and a dossier that
    #: renders them the same way is lying by omission.
    interwiki_checked: bool = False
    #: Languages that were actually compared, so an empty result can be told
    #: apart from a language that has no article.
    compared_languages: tuple[str, ...] = ()
    #: Standing for the hosts this article already cites, best first. Free to
    #: compute - it needs no request - and it answers "how well sourced is this
    #: article" before any research happens.
    reference_standing: tuple[SourceStanding, ...] = ()
    #: Set only when the research agent ran. `None` means it was not asked to,
    #: which must never render as "it looked and found nothing".
    #: Reachability of the article's own links. Empty when the check was
    #: turned off (`research.max_link_checks = 0`), which the renderer has to
    #: tell apart from "checked and everything resolves".
    links: tuple[LinkStatus, ...] = ()
    link_summary: LinkSummary | None = None
    agent: AgentRun | None = None
    findings: tuple[Finding, ...] = ()
    section_notes: tuple[SectionNote, ...] = ()
    edit_url: str = ""


class ScoreResult(Strict):
    reference_date: dt.date
    pageviews_window: str
    articles: tuple[ScoredArticle, ...] = Field(default_factory=tuple)
