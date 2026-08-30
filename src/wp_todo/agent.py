"""The opt-in research agent: check the references first, search only after.

Off unless `--agent` is passed. The deterministic dossier costs nothing and is
what a normal run produces; this stage costs money and reads hosts that never
asked to be read, so it runs when somebody asks for it.

The order of work is the whole design, and it comes from a simple observation:
**the article usually already cites the answer.** A page whose population figure
says "Stand 2018" very often cites the statistical office that has since
published 2025. Reading what is already cited is cheaper than searching, it
produces a finding an editor can act on immediately, and it cannot drag in a
source nobody has vetted. So:

1. an agenda is assembled with no model at all - the claims that carry a date
   old enough to be worth asking about, and the sections other language
   editions have and this one does not;
2. the article's own references are fetched and the model is asked, per claim,
   whether any of them now says something newer;
3. only for the claims that came back empty does an open web search happen -
   one discovery call for the whole article, whose URLs are read out of the
   structured search-result blocks and then fetched by *our* client;
4. missing sections get a few bullet points summarising what the other
   edition's section actually says.

Then the gates. Everything the model says passes through five mechanical checks
applied **in code, after the model has spoken**, and the model's own confidence
orders the list without ever admitting anything to it:

* **quote containment** - the quote, whitespace-normalised, must appear
  verbatim in the stored text of the document it was attributed to. This is the
  one that makes fabricated sourcing structurally impossible rather than merely
  discouraged;
* **provenance** - the model picks a document by index out of a list we built.
  It never emits a URL, so it cannot invent one;
* **recency** - a source not newer than the claim is demoted to context, not
  reported as an update;
* **circularity** - a document that is a copy of the article is dropped, and an
  explicit `trust` cannot override that. Trusting a mirror is always an error;
* **source standing** - a `block` removes the document, and the removal is
  always reported.

Every drop is counted and rendered. A run where the quote gate rejected six of
twenty answers is telling the reader something important, and hiding it would
make the survivors look better than they are.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .config import ScopeConfig
from .llm import LlmCall, LlmClient
from .models import AgentRun, ArticleClaims, Claim, Delta, DroppedFinding, Finding, SectionNote
from .sources import SourceLedger, circularity, host_of, standing
from .webclient import Document, WebClient

log = logging.getLogger(__name__)

#: What the model is allowed to say a document does to a claim. `nothing_found`
#: is first because it is the answer we most want it to be comfortable giving:
#: a stage that cannot say "no" produces findings for everything.
STATUSES = (
    "nothing_found",
    "confirms_current",
    "supersedes_with_newer_value",
    "contradicts_current",
)

#: Statuses worth putting in a dossier. `confirms_current` is in: "the cited
#: source still says this" is a real result an editor can stop worrying about.
REPORTABLE = frozenset(STATUSES[1:])

VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": list(STATUSES)},
        "document": {
            "type": "integer",
            "description": "1-based index of the document, or 0 when none of them answers.",
        },
        "current_value": {"type": ["string", "null"]},
        "as_of": {"type": ["integer", "null"], "description": "Year the document's value is as of."},
        "quote": {
            "type": "string",
            "description": "Verbatim from the chosen document. Copied, never paraphrased.",
        },
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["status", "document", "current_value", "as_of", "quote", "confidence", "reasoning"],
    "additionalProperties": False,
}

DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "queries": {"type": "array", "items": {"type": "string"}},
        "note": {"type": "string"},
    },
    "required": ["queries", "note"],
    "additionalProperties": False,
}

SECTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "integer",
                        "description": "1-based number of the section, exactly as listed.",
                    },
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["section", "bullets"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sections"],
    "additionalProperties": False,
}

SYSTEM = (
    "Du hilfst einer Person, die einen Wikipedia-Artikel aktualisieren will. "
    "Du schreibst keinen Artikeltext und keine Belege - du sagst nur, ob ein "
    "vorgelegtes Dokument eine Angabe des Artikels überholt.\n\n"
    "Regeln, die nicht verhandelbar sind:\n"
    "- Zitiere wörtlich aus dem gewählten Dokument. Ein umformuliertes Zitat "
    "wird maschinell verworfen, die Antwort ist dann wertlos.\n"
    "- Wähle das Dokument über seine Nummer. Nenne nie eine URL.\n"
    "- Wenn keines der Dokumente die Angabe beantwortet, ist "
    "'nothing_found' die richtige Antwort. Rate nicht.\n"
    "- Benutze ausschliesslich die vorgelegten Dokumente, nie dein eigenes Wissen."
)

SECTIONS_SYSTEM = (
    "Du fasst zusammen, worum es in einem Abschnitt einer anderen "
    "Sprachversion geht - in wenigen Stichpunkten, damit eine Person "
    "entscheiden kann, ob sich das Übersetzen lohnt. Keine Wertung, kein "
    "Artikeltext, nur worüber der Abschnitt handelt. Ausschliesslich aus dem "
    "vorgelegten Text, nie aus eigenem Wissen.\n\n"
    "Wähle jeden Abschnitt über seine Nummer aus der vorgelegten Liste. Die "
    "Überschrift schreibst du nicht ab - die Stichpunkte selbst gehören auf Deutsch, "
    "und eine übersetzte Überschrift wäre nicht mehr wiederzuerkennen."
)

#: Characters of one document put in front of the model. The quote gate checks
#: against the *whole* stored text, so a quote from outside this window is
#: still verified rather than being waved through.
EXCERPT_CHARS = 6_000

_WHITESPACE = re.compile(r"\s+")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_PARAGRAPH = re.compile(r"\n\s*\n")


@dataclass
class SourceDoc:
    """A document the model may be shown, with what is known about it."""

    index: int
    url: str
    document: Document
    standing_label: str
    from_reference: bool


@dataclass
class AgentOutcome:
    """Everything the stage produced, for the dossier to render."""

    findings: tuple[Finding, ...] = ()
    section_notes: tuple[SectionNote, ...] = ()
    run: AgentRun | None = None
    calls: tuple[LlmCall, ...] = ()
    dropped: list[DroppedFinding] = field(default_factory=list)


def failed_outcome(llm: LlmClient, exc: BaseException) -> AgentOutcome:
    """What to report when the stage died partway through.

    The deterministic dossier is finished by the time this stage starts, and it
    cost real requests to Wikimedia. Throwing it away because an optional,
    paid-for extra failed is the wrong trade - so the failure becomes a fact
    the dossier states, not a reason to have nothing.

    The calls made before the failure are kept: a transcript that stops mid-run
    is exactly what somebody debugging the run needs to see.
    """
    log.warning("the research agent failed after %d call(s): %s", len(llm.calls), exc)
    return AgentOutcome(
        run=AgentRun(
            model=llm.model,
            effort=llm.effort,
            calls=len(llm.calls),
            cached_calls=sum(1 for call in llm.calls if call.cached),
            budget=llm.budget.limit,
            failed=_condense_error(exc),
        ),
        calls=tuple(llm.calls),
    )


def _condense_error(exc: BaseException) -> str:
    """One readable line. The traceback is for the log, not for the dossier."""
    return _WHITESPACE.sub(" ", f"{type(exc).__name__}: {exc}").strip()[:300]


def run_agent(
    *,
    claims: ArticleClaims,
    wikitext: str,
    deltas: tuple[Delta, ...],
    foreign_texts: dict[str, tuple[str, str]],
    config: ScopeConfig,
    reference: dt.date,
    web: WebClient,
    llm: LlmClient,
    ledger: SourceLedger,
) -> AgentOutcome:
    """Work the agenda within the budget, and report what was left undone."""
    research = config.research
    dropped: list[DroppedFinding] = []

    agenda = _agenda(claims, reference, research.stale_after_years)[: research.max_claims]
    log.info("%s: %d claim(s) on the agenda", claims.title, len(agenda))

    docs = _reference_documents(claims, web, ledger, config, dropped)
    findings: list[Finding] = []
    answered: set[str] = set()

    # 1 - the article's own references, which are free of both search cost and
    #     the risk of dragging in a host nobody has vetted.
    if docs:
        context = _context(docs)
        for claim in agenda:
            call = llm.ask(
                purpose="reference_check",
                subject=claim.id,
                system=SYSTEM,
                context=context,
                prompt=_claim_prompt(claims, claim, searched=False),
                schema=VERIFY_SCHEMA,
            )
            found = _gate(call, claim, docs, wikitext, config, dropped)
            if found is not None:
                findings.append(found)
                answered.add(claim.id)

    # 2 - the open web, and only for what the references could not answer. Two
    #     calls minimum (discover, then verify at least one claim), so the stage
    #     does not spend the budget on a search whose results it cannot read.
    unanswered = [c for c in agenda if c.id not in answered]
    search_docs: list[SourceDoc] = []
    searched = False
    if unanswered and llm.budget.left >= 2:
        searched = True
        urls = _discover(claims, unanswered, llm)
        search_docs = _fetch_urls(
            list(urls)[: research.max_search_docs],
            web,
            ledger,
            claims,
            config,
            dropped,
        )
        if search_docs:
            context = _context(search_docs)
            for claim in unanswered:
                if llm.budget.left <= 0:
                    break
                call = llm.ask(
                    purpose="web_check",
                    subject=claim.id,
                    system=SYSTEM,
                    context=context,
                    prompt=_claim_prompt(claims, claim, searched=True),
                    schema=VERIFY_SCHEMA,
                )
                found = _gate(call, claim, search_docs, wikitext, config, dropped)
                if found is not None:
                    findings.append(found)
                    answered.add(claim.id)

    # 3 - what the article does not have. Grounded in the other edition's own
    #     text, so the bullets point at something a person can go and read.
    notes = _section_notes(deltas, foreign_texts, llm, dropped)

    run = AgentRun(
        model=llm.model,
        effort=llm.effort,
        calls=len(llm.calls),
        cached_calls=sum(1 for call in llm.calls if call.cached),
        budget=llm.budget.limit,
        budget_exhausted=llm.budget.exhausted,
        unexamined=tuple(sorted(c.id for c in agenda if c.id not in answered)),
        documents=len(docs) + len(search_docs),
        reference_documents=len(docs),
        searched=searched,
        dropped=tuple(dropped),
    )
    return AgentOutcome(
        findings=tuple(sorted(findings, key=_finding_order)),
        section_notes=notes,
        run=run,
        calls=tuple(llm.calls),
        dropped=dropped,
    )


# ------------------------------------------------------------------- agenda
def _agenda(claims: ArticleClaims, reference: dt.date, stale_after: int) -> list[Claim]:
    """Claims worth spending a call on, most overdue first.

    A claim with no year is not on the agenda. That is deliberate: "the article
    says the mayor is X, with no date" is a question the open web cannot settle
    cheaply, and asking it anyway is how a budget disappears into answers of
    the form "I could not tell".
    """
    worth: list[Claim] = []
    for claim in claims.claims:
        if claim.kind in ("veraltet_template", "zukunft_template", "belege_fehlen"):
            # An editor has already said in the article that this is stale.
            # That is the strongest signal on the page; it goes first.
            worth.append(claim)
        elif claim.as_of_year is not None and reference.year - claim.as_of_year >= stale_after:
            worth.append(claim)
    return sorted(worth, key=lambda c: (_agenda_rank(c), c.as_of_year or 0, c.line_no, c.id))


def _agenda_rank(claim: Claim) -> int:
    # `belege_fehlen` sorts last on purpose. It is a whole-article signal with
    # no date, so it is the least specific question here; a dated infobox
    # figure should get the budget first, and this one gets whatever is left.
    order = {"veraltet_template": 0, "zukunft_template": 0, "infobox_field": 1, "belege_fehlen": 3}
    return order.get(claim.kind, 2)


# ---------------------------------------------------------------- documents
def _reference_documents(
    claims: ArticleClaims,
    web: WebClient,
    ledger: SourceLedger,
    config: ScopeConfig,
    dropped: list[DroppedFinding],
) -> list[SourceDoc]:
    """Fetch the article's own references, best standing first.

    Capped, and the cap is why the order matters: an official statistics office
    is far likelier to carry a current figure than the eighth blog post, so the
    budget goes there. What was left unread is in the transcript.
    """
    urls = _ordered_references(claims, ledger)
    return _fetch_urls(
        urls[: config.research.max_reference_docs],
        web,
        ledger,
        claims,
        config,
        dropped,
        from_reference=True,
    )


def _ordered_references(claims: ArticleClaims, ledger: SourceLedger) -> list[str]:
    """Every document the article points at, cited or merely linked.

    `Weblinks` and `Literatur` are included because an article can cite forty
    books, carry no fetchable reference at all, and still link the one document
    worth reading. Standing still orders them, so a `Weblinks` entry on an
    official host outranks a cited blog rather than being appended at the end.
    """
    official = _official_website(claims)
    seen: set[str] = set()
    unique: list[str] = []
    for url in (*claims.references.external_urls, *claims.references.linked_urls):
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return sorted(
        unique,
        key=lambda url: (standing(url, ledger=ledger, article_official=official).sort_key, url),
    )


def _official_website(claims: ArticleClaims) -> str:
    for claim in claims.claims:
        if claim.field == "WEBSITE" and claim.asserted_value:
            return claim.asserted_value
    return ""


def _fetch_urls(
    urls: list[str],
    web: WebClient,
    ledger: SourceLedger,
    claims: ArticleClaims,
    config: ScopeConfig,
    dropped: list[DroppedFinding],
    *,
    from_reference: bool = False,
) -> list[SourceDoc]:
    """Fetch, applying the standing gate *before* spending a request on it.

    Documents are numbered from 1 in every phase. The model is shown exactly
    one list per question, so a number that continued across phases would be a
    number it has no way to guess - and every answer would fail the provenance
    gate for a reason that was ours, not its.
    """
    official = _official_website(claims)
    cited = frozenset(host_of(u) for u in claims.references.external_urls if host_of(u))
    docs: list[SourceDoc] = []
    index = 1

    for url in urls:
        found = standing(url, ledger=ledger, article_official=official, cited_hosts=cited)
        if found.blocked:
            # Applied before the fetch, so a block saves budget, and reported
            # anyway, so a shortened source list is never silent.
            reason = found.verdict.reason if found.verdict else ""
            dropped.append(DroppedFinding(gate="source_standing", detail=reason, url=url))
            continue
        document = web.fetch(url)
        if document is None or not document.text.strip():
            continue
        docs.append(
            SourceDoc(
                index=index,
                url=url,
                document=document,
                standing_label=found.describe(),
                from_reference=from_reference,
            )
        )
        index += 1
    return docs


def _context(docs: list[SourceDoc]) -> str:
    """The document excerpts, numbered. The stable half of every prompt."""
    parts = ["Dokumente:", ""]
    for doc in docs:
        parts += [
            f"### Dokument {doc.index}",
            f"Einstufung: {doc.standing_label}",
            f"Abgerufen: {doc.document.fetched_on.isoformat()}",
            "",
            _excerpt(doc.document.text),
            "",
        ]
    return "\n".join(parts)


def _excerpt(text: str) -> str:
    """A bounded window of a document, biased towards paragraphs with years.

    The whole text is what a quote is checked against; this is only what the
    model gets to read. Biasing towards years is a heuristic for "the part that
    could make a dated claim stale", and the head is always kept because that
    is where a page states what it is.
    """
    if len(text) <= EXCERPT_CHARS:
        return text

    head_chars = EXCERPT_CHARS // 4
    head = text[:head_chars]
    rest = text[head_chars:]
    chosen: list[str] = []
    budget = EXCERPT_CHARS - head_chars
    for paragraph in _PARAGRAPH.split(rest):
        # Two characters below the cap there is no room left for a separator,
        # and `paragraph[:budget - 2]` would start slicing from the *end*.
        if budget <= 2:
            break
        if not _YEAR.search(paragraph):
            continue
        piece = paragraph[: budget - 2]
        if not piece:
            break
        chosen.append(piece)
        budget -= len(piece) + 2
    return head + ("\n\n" + "\n\n".join(chosen) if chosen else "")


# ----------------------------------------------------------------- prompting
def _claim_prompt(claims: ArticleClaims, claim: Claim, *, searched: bool) -> str:
    if claim.kind == "belege_fehlen":
        return _sourcing_prompt(claims, claim, searched=searched)

    where = claim.section or claim.field or "—"
    stand = f"Stand laut Artikel: {claim.as_of_year}" if claim.as_of_year else "Kein Stand angegeben"
    origin = (
        "Die Dokumente stammen aus einer Websuche."
        if searched
        else "Die Dokumente sind die Belege, die der Artikel selbst zitiert."
    )
    return (
        f"Artikel: {claims.title}\n"
        f"Abschnitt: {where}\n"
        f"{stand}\n\n"
        f"Angabe im Artikel:\n{claim.text}\n\n"
        f"{origin}\n"
        "Sagt eines der Dokumente etwas Neueres oder Abweichendes zu genau "
        "dieser Angabe? Wenn nicht: status = nothing_found."
    )


def _sourcing_prompt(claims: ArticleClaims, claim: Claim, *, searched: bool) -> str:
    """A different question, because `{{Belege fehlen}}` is a different claim.

    Every other claim asks "is this still true?". This one is the article
    saying *nothing here is sourced*, and the useful answer is not a newer
    value but a citable document - something an editor can put behind a
    sentence that currently has nothing behind it. Asking the dated question
    here would earn `nothing_found` every time, which is how a call gets spent
    on a question nobody asked.

    The gates do not soften for it: the quote must still be verbatim in the
    document, the document must still be one we fetched, and a mirror of the
    article is still dropped. All that changes is what is being asked.
    """
    origin = (
        "Die Dokumente stammen aus einer Websuche."
        if searched
        else "Die Dokumente sind die Belege und Weblinks des Artikels."
    )
    return (
        f"Artikel: {claims.title}\n"
        f"Abschnitt: {claim.section or '—'}\n\n"
        f"Wartungshinweis im Artikel:\n{claim.text}\n\n"
        f"{origin}\n"
        "Belegt eines der Dokumente eine konkrete, überprüfbare Aussage über "
        "das Thema des Artikels - etwas, das sich als Einzelnachweis "
        "verwenden liesse? Wenn ja: status = confirms_current, current_value = "
        "die belegte Aussage, quote = wörtlich aus dem Dokument. Wenn keines "
        "der Dokumente etwas Belegbares hergibt: status = nothing_found."
    )


def _discover(claims: ArticleClaims, unanswered: list[Claim], llm: LlmClient) -> tuple[str, ...]:
    """One search call for the whole article. URLs come from the tool result.

    The model's prose is not read for URLs at all - only the structured
    `web_search_tool_result` blocks are, which is why a hallucinated URL cannot
    reach the fetcher.
    """
    lines = [f"Artikel: {claims.title}", "", "Diese Angaben konnten die Belege des Artikels nicht klären:"]
    lines += [
        f"- {claim.text}" + (f" (Stand {claim.as_of_year})" if claim.as_of_year else "")
        for claim in unanswered
    ]
    lines += [
        "",
        "Suche nach aktuellen, möglichst amtlichen deutschsprachigen Quellen "
        "zu diesen Angaben. Gib danach die verwendeten Suchanfragen zurück.",
    ]
    call = llm.ask(
        purpose="search",
        subject=claims.title,
        system=SYSTEM,
        prompt="\n".join(lines),
        schema=DISCOVERY_SCHEMA,
        web_search=True,
    )
    return call.found_urls if call is not None else ()


def _section_notes(
    deltas: tuple[Delta, ...],
    foreign_texts: dict[str, tuple[str, str]],
    llm: LlmClient,
    dropped: list[DroppedFinding],
) -> tuple[SectionNote, ...]:
    """Bullet points for the sections this article does not have.

    Provenance works by number here, exactly as it does for documents, and for
    a reason paid for in a live run. It used to work by heading, compared with
    string equality against the heading we asked about - and since every prompt
    in this stage is in German, the model translated them. `Historical floods`
    came back as `Historische Hochwasser`, missed the match, and *both*
    summaries in the Küsnachter Dorfbach run were discarded without a word: the
    dossier showed the bare English headings, the good bullets sat in the
    transcript, and nothing anywhere said they had been thrown away.

    A number cannot be translated. What the model can still get wrong - a
    number that is not on the list - is reported rather than dropped quietly.
    """
    gaps = [d for d in deltas if d.kind == "interwiki_section" and d.external_value]
    if not gaps or not foreign_texts:
        return ()

    wanted: dict[str, list[str]] = {}
    for delta in gaps:
        lang = delta.label.removesuffix("wiki")
        wanted[lang] = [h.strip() for h in (delta.external_value or "").split(";") if h.strip()]

    context_parts: list[str] = []
    offered: list[tuple[str, str]] = []
    for lang, headings in sorted(wanted.items()):
        entry = foreign_texts.get(lang)
        if entry is None:
            continue
        _, wikitext = entry
        for heading in headings:
            body = _section_body(wikitext, heading)
            if not body:
                continue
            offered.append((lang, heading))
            context_parts += [f"### Abschnitt {len(offered)} - {lang}wiki: {heading}", body[:2_000], ""]

    if not offered:
        return ()

    call = llm.ask(
        purpose="sections",
        subject="fehlende Abschnitte",
        system=SECTIONS_SYSTEM,
        context="\n".join(context_parts),
        prompt=(
            "Fasse jeden Abschnitt in zwei bis vier Stichpunkten zusammen: "
            "worüber er handelt, welche Zahlen oder Ereignisse darin "
            "vorkommen. Kein Fliesstext, keine Wertung. Gib zu jedem "
            "Abschnitt seine Nummer aus der Liste an."
        ),
        schema=SECTIONS_SCHEMA,
    )
    if call is None:
        return ()

    notes: list[SectionNote] = []
    for raw in call.reply.get("sections", []) or []:
        if not isinstance(raw, dict):
            continue
        # Provenance again, by number: a section the model invented has no
        # number, and one it renamed still carries the right one.
        index = raw.get("section")
        if not isinstance(index, int) or isinstance(index, bool) or not 1 <= index <= len(offered):
            dropped.append(
                DroppedFinding(gate="section_provenance", detail=f"Abschnitt {index!r} gibt es nicht")
            )
            continue
        lang, heading = offered[index - 1]
        bullets = tuple(str(b).strip() for b in raw.get("bullets", []) or [] if str(b).strip())
        if not bullets:
            dropped.append(DroppedFinding(gate="section_empty", detail=f"{lang}wiki: {heading}"))
            continue
        entry = foreign_texts.get(lang)
        notes.append(
            SectionNote(
                heading=heading,
                lang=lang,
                source=_section_url(lang, entry[0], heading) if entry else "",
                bullets=bullets[:4],
            )
        )
    return tuple(sorted(notes, key=lambda n: (n.lang, n.heading)))


def _section_url(lang: str, title: str, heading: str) -> str:
    anchor = heading.replace(" ", "_")
    return f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}#{anchor}"


def _section_body(wikitext: str, heading: str) -> str:
    """The text under one heading, up to the next heading of any level."""
    pattern = re.compile(
        r"^\s*(={2,6})\s*" + re.escape(heading) + r"\s*\1\s*$(?P<body>.*?)(?=^\s*={2,6}|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(wikitext)
    return match.group("body").strip() if match else ""


# -------------------------------------------------------------------- gates
def _gate(
    call: LlmCall | None,
    claim: Claim,
    docs: list[SourceDoc],
    wikitext: str,
    config: ScopeConfig,
    dropped: list[DroppedFinding],
) -> Finding | None:
    """Every check that stands between what the model said and the dossier."""
    if call is None:
        return None

    reply = call.reply
    status = str(reply.get("status", ""))
    if status not in REPORTABLE:
        if status not in STATUSES:
            dropped.append(DroppedFinding(claim_id=claim.id, gate="schema", detail=status or "leer"))
        return None

    # Gate 2 - provenance. The model chose a number; if it does not resolve to
    # a document this run fetched, there is nothing to check the quote against.
    index = reply.get("document")
    doc = next((d for d in docs if d.index == index), None) if isinstance(index, int) else None
    if doc is None:
        dropped.append(
            DroppedFinding(claim_id=claim.id, gate="provenance", detail=f"Dokument {index!r} gibt es nicht")
        )
        return None

    # Gate 1 - quote containment. The one that makes a fabricated source
    # structurally impossible rather than merely discouraged.
    quote = str(reply.get("quote", "")).strip()
    if not quote or not _contains(doc.document.text, quote):
        dropped.append(
            DroppedFinding(
                claim_id=claim.id,
                gate="quote",
                detail="das Zitat steht so nicht im Dokument",
                url=doc.url,
            )
        )
        return None

    # Gate 4 - circularity. Deliberately after the quote gate and deliberately
    # not overridable by `trust`: a perfect quote from a copy of the article is
    # exactly the failure a human check does not catch.
    copied = circularity(
        doc.document.text,
        wikitext,
        host=host_of(doc.url),
        mirror_domains=config.research.mirror_domains,
        span=config.research.circularity_span,
    )
    if copied:
        dropped.append(DroppedFinding(claim_id=claim.id, gate="circularity", detail=copied, url=doc.url))
        return None

    # Gate 3 - recency. Not a drop: a source that is not newer is still worth
    # knowing about, it just is not an update.
    as_of = reply.get("as_of")
    as_of_year = int(as_of) if isinstance(as_of, int) else None
    demoted = ""
    if claim.as_of_year is not None and as_of_year is not None and as_of_year < claim.as_of_year:
        demoted = f"Quelle von {as_of_year} ist älter als die Angabe im Artikel ({claim.as_of_year})"

    value = reply.get("current_value")
    confidence = reply.get("confidence")
    return Finding(
        claim_id=claim.id,
        claim_text=claim.text,
        status=status,
        current_value=str(value) if isinstance(value, str) and value.strip() else None,
        as_of=as_of_year,
        quote=quote,
        url=doc.url,
        host=host_of(doc.url),
        standing=doc.standing_label,
        from_reference=doc.from_reference,
        demoted=demoted,
        confidence=float(confidence) if isinstance(confidence, int | float) else 0.0,
    )


def _contains(document_text: str, quote: str) -> bool:
    """Whitespace-insensitive containment, and nothing more forgiving.

    Case is *not* folded: a quote is meant to be copied, and "close enough"
    matching is how a paraphrase gets through the one gate that exists to stop
    paraphrases.
    """
    return _WHITESPACE.sub(" ", quote).strip() in _WHITESPACE.sub(" ", document_text)


def _finding_order(finding: Finding) -> tuple[int, int, float, str]:
    """Newest, most confident, most actionable first - with a stable tail.

    `claim_id` breaks every remaining tie, so two findings that score the same
    never swap places between runs.
    """
    kind = {"supersedes_with_newer_value": 0, "contradicts_current": 1, "confirms_current": 2}
    return (
        1 if finding.demoted else 0,
        kind.get(finding.status, 3),
        -round(finding.confidence, 3),
        finding.claim_id,
    )
