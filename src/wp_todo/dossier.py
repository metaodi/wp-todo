"""Rendering a research dossier, for a person to read.

Same rule as `render.py`: nothing here reads a clock, so two runs over the same
cache produce byte-identical files and a weekly diff shows only real change.

The header is not decoration. A file full of "current" figures next to
Wikipedia article text is exactly the sort of thing that gets pasted in without
checking, and the one thing this project must not become is a way to put
unverified claims into an encyclopedia. So every dossier says what it is, in
the first thing anybody reads.
"""

from __future__ import annotations

import json
import re

from .links import SEVERITY as SEVERITY_ORDER
from .models import Delta, Dossier, Finding, LinkStatus

HEADER_NOTICE = (
    "> **Dieses Werkzeug bearbeitet die Wikipedia nicht.** Alles hier ist ein *Hinweis*,\n"
    "> keine Quelle und kein fertiger Text. Jede Angabe vor der Verwendung selbst am\n"
    "> Beleg prüfen und selbst formulieren."
)

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slug(title: str) -> str:
    """A filename-safe stem. Umlauts are transliterated, not dropped, so
    `Küsnacht` and `Ksnacht` cannot collide."""
    folded = (
        title.lower()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .replace("à", "a")
        .replace("é", "e")
        .replace("è", "e")
        .replace("ç", "c")
    )
    return _SLUG_STRIP.sub("-", folded).strip("-") or "artikel"


def render_markdown(dossier: Dossier) -> str:
    lines: list[str] = [
        f"# Recherche: {dossier.title}",
        "",
        HEADER_NOTICE,
        "",
        f"[Artikel](https://de.wikipedia.org/wiki/{dossier.title.replace(' ', '_')})"
        f" · [bearbeiten]({dossier.edit_url})"
        + (
            f" · [Wikidata](https://www.wikidata.org/wiki/{dossier.wikidata_item})"
            if dossier.wikidata_item
            else ""
        ),
        "",
        f"Stand der Daten: {dossier.reference_date.isoformat()}",
        "",
    ]

    # Above everything deterministic, because it is what an editor came for -
    # and, for the same reason, the section that has to be loudest about how
    # little it is worth before somebody checks it.
    lines += _findings_section(dossier)

    # "Not retrieved" must not read as "nothing to report". For five live runs
    # it did, while our own robots gate was refusing the request.
    unchecked = _wikidata_unchecked(dossier) or _nothing_comparable(dossier)

    lines += _delta_section(
        "Abweichungen gegenüber Wikidata",
        [d for d in dossier.deltas if d.kind == "wikidata" and not d.agrees],
        empty=unchecked or "_Keine Abweichungen gefunden._",
        note=(
            "Wikidata ist nicht automatisch im Recht - oft ist es die Seite, die falsch\n"
            "liegt. Beide Angaben an der jeweiligen Quelle prüfen."
        ),
    )

    agreeing = [d for d in dossier.deltas if d.kind == "wikidata" and d.agrees]
    lines += _delta_section(
        "Übereinstimmend mit Wikidata",
        agreeing,
        empty=unchecked or "_Nichts abgeglichen._",
        note=_agreement_note(agreeing, dossier.reference_date.year),
    )

    lines += _interwiki_section(dossier)
    lines += _claims_section(dossier)
    lines += _references_section(dossier)
    lines += _links_section(dossier)
    lines += _standing_section(dossier)
    lines += _excluded_section(dossier)
    lines += _agent_section(dossier)

    return "\n".join(lines).rstrip() + "\n"


def render_json(dossier: Dossier) -> str:
    payload = dossier.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


# ------------------------------------------------------------------ sections
def _wikidata_unchecked(dossier: Dossier) -> str:
    """The honest empty-state, or "" when the comparison really did run."""
    if dossier.wikidata_checked:
        return ""
    if dossier.wikidata_item is None:
        return "_Nicht abgeglichen: der Artikel hat kein Wikidata-Objekt._"
    return (
        f"_Nicht abgeglichen: die Angaben zu [{dossier.wikidata_item}]"
        f"(https://www.wikidata.org/wiki/{dossier.wikidata_item}) konnten nicht "
        "geladen werden. Das ist **kein** Hinweis darauf, dass alles stimmt._"
    )


def _nothing_comparable(dossier: Dossier) -> str:
    """When the lookup worked but no field could be compared.

    Distinct from "compared and agreed": for four of the first five live
    articles the infobox had no mapped property at all, and "keine Abweichungen
    gefunden" implied a comparison that never happened. Naming the infobox also
    makes the gap in `PROPERTY_FOR_FIELD` visible, which is how it gets fixed.
    """
    if not dossier.wikidata_checked or dossier.wikidata_comparable:
        return ""
    infobox = dossier.claims.infobox
    which = f"`{infobox}`" if infobox else "diese Infobox"
    return (
        f"_Nichts zu vergleichen: kein Feld aus {which} ist einer "
        "Wikidata-Eigenschaft zugeordnet. Es wurde also **nicht** geprüft, ob "
        "die Angaben übereinstimmen._"
    )


#: Years after which a Wikidata figure is too old to be evidence of currency.
STALE_AGREEMENT_YEARS = 5


def _agreement_note(agreeing: list[Delta], reference_year: int) -> str:
    """Agreement is only reassuring when the other side is itself current.

    Adliswil's area agreed with a Wikidata figure carrying `point in time 2007`.
    Calling that "vermutlich aktuell" was unfounded: two sources can agree
    because both are stale, and saying otherwise turns a non-finding into false
    comfort.
    """
    oldest = [d.external_as_of for d in agreeing if d.external_as_of is not None]
    if oldest and reference_year - min(oldest) > STALE_AGREEMENT_YEARS:
        return (
            "Beide Seiten sagen dasselbe. Das heisst **nicht**, dass es aktuell ist:\n"
            "die Wikidata-Angabe ist selbst alt, beide können gemeinsam veraltet sein."
        )
    return "Diese Angaben stimmen mit Wikidata überein - vermutlich aktuell."


def _delta_section(heading: str, deltas: list[Delta], *, empty: str, note: str) -> list[str]:
    lines = [f"## {heading}", ""]
    if not deltas:
        lines += [empty, ""]
        return lines
    lines += [note, ""]
    lines.append("| Angabe | Im Artikel | Laut Wikidata | Beleg |")
    lines.append("| --- | --- | --- | --- |")
    for delta in deltas:
        lines.append(
            "| {label} | {ours} | {theirs} | [{prop}]({source}) |".format(
                label=_escape(delta.label or delta.field or "—"),
                ours=_value(delta.article_value, delta.article_as_of),
                theirs=_value(delta.external_value, delta.external_as_of),
                prop=_escape(delta.source.partition("#")[2] or "Wikidata"),
                source=delta.source,
            )
        )
    lines.append("")
    return lines


def _interwiki_section(dossier: Dossier) -> list[str]:
    lines = ["## Möglicherweise fehlend", ""]
    deltas = [d for d in dossier.deltas if d.kind == "interwiki_section"]
    if not dossier.interwiki_checked:
        lines += ["_Nicht abgefragt._", ""]
        return lines
    if not dossier.compared_languages:
        lines += ["_Keine anderssprachige Fassung verlinkt._", ""]
        return lines
    if not deltas:
        lines += [f"_Keine zusätzlichen Abschnitte in {', '.join(dossier.compared_languages)}wiki._", ""]
        return lines
    lines += [
        "Abschnitte, die andere Sprachversionen haben und diese nicht. Ein Titel allein\n"
        "sagt wenig - derselbe Inhalt steht hier oft unter anderem Namen.",
        "",
    ]
    for delta in deltas:
        lines.append(f"- **[{_escape(delta.label)}]({delta.source})** — {_escape(delta.detail)}")
        lines.append(f"  - {_escape(delta.external_value or '')}")
    lines.append("")
    lines += _section_notes_block(dossier)
    return lines


def _claims_section(dossier: Dossier) -> list[str]:
    lines = ["## Angaben zum Prüfen", ""]
    claims = dossier.claims.claims
    if not claims:
        lines += ["_Keine prüfenswerten Angaben gefunden._", ""]
        return lines
    dated = sum(1 for claim in claims if claim.as_of_year is not None)
    lines += [
        f"{len(claims)} Stelle(n), die veralten können - {dated} davon mit einer Jahresangabe.",
        "Eine Liste zum Abarbeiten, keine Liste von Fehlern: ein Wert ohne Stand ist",
        "nicht falsch, nur ungeprüft.",
        "",
        "| Zeile | Abschnitt | Stand | Angabe |",
        "| ---: | --- | ---: | --- |",
    ]
    for claim in claims:
        lines.append(
            "| {line} | {section} | {as_of} | {text} |".format(
                line=claim.line_no,
                section=_escape(claim.section or claim.field or "—"),
                as_of=claim.as_of_year if claim.as_of_year is not None else "—",
                text=_escape(claim.text),
            )
        )
    lines.append("")
    return lines


def _references_section(dossier: Dossier) -> list[str]:
    refs = dossier.claims.references
    lines = ["## Belege dieses Artikels", ""]
    if not refs.total:
        lines += ["_Der Artikel hat keine Einzelnachweise._", ""]
        return lines
    parts = [f"{refs.total} Einzelnachweis(e)"]
    if refs.newest_year is not None:
        age = dossier.reference_date.year - refs.newest_year
        aged = f", {age} Jahre alt" if age > 1 else ""
        parts.append(f"neuester datierter Beleg: {refs.newest_year}{aged}")
    if refs.oldest_year is not None:
        parts.append(f"ältester: {refs.oldest_year}")
    parts.append(f"{len(refs.external_urls)} externe(r) Link(s)")
    if refs.linked_urls:
        # Counted apart from the references on purpose: an article with 46
        # print citations and one `Weblinks` entry is not an article with 47
        # references, and the difference is the whole point of the split.
        parts.append(f"{len(refs.linked_urls)} unter Weblinks/Literatur")
    lines += [" · ".join(parts), ""]
    return lines


#: Said inside the section, not only in the file header, for the same reason
#: the findings notice is: this is the part that gets scrolled to, and a reader
#: who believes the list is exhaustive will trust "erreichbar" further than it
#: has earned.
LINKS_NOTICE = (
    "> **Was hier nicht steht.** Eine Seite, die mit HTTP 200 auf eine\n"
    "> Fehlermeldung antwortet - «Seite nicht gefunden» im Text, aber alles in\n"
    "> Ordnung im Statuscode - wird **nicht** erkannt; nur die Umleitung auf die\n"
    "> Startseite. Und `erreichbar` sagt etwas über die URL, nicht über den\n"
    "> Inhalt: die Seite kann längst umgeschrieben sein."
)

#: What each verdict means, in the order the reader needs it. `gesperrt` is the
#: one that has to be spelled out: a host refusing us is the most common way a
#: link checker earns a false "tot", and an editor who replaces a live link
#: with an archive copy has made the article worse.
VERDICT_NOTES = {
    "tot": "Dokument ist weg (404/410)",
    "nicht erreichbar": "gerade nicht erreichbar - nicht dasselbe wie weg",
    "gesperrt": "der Host hat *uns* abgelehnt; im Browser oft trotzdem da",
    "umgeleitet": "landet woanders - häufig die stille Form von «gibt es nicht mehr»",
    "nicht geprüft": "nicht angesehen",
    "erreichbar": "löst auf",
}


def _links_section(dossier: Dossier) -> list[str]:
    """Which of the article's links still resolve.

    Absent entirely when the check was turned off, which must never render as
    "checked and everything is fine" - the same distinction `wikidata_checked`
    and `interwiki_checked` already draw.
    """
    summary = dossier.link_summary
    if summary is None:
        return []

    lines = ["## Erreichbarkeit der Belege", ""]
    if not summary.total:
        lines += ["_Der Artikel verlinkt nichts, was sich prüfen liesse._", ""]
        return lines

    lines += [
        " · ".join(
            [
                f"{summary.checked} von {summary.total} Link(s) geprüft",
                f"{summary.dead} tot",
                f"{summary.unreachable} nicht erreichbar",
                f"{summary.blocked} gesperrt",
                f"{summary.redirected} umgeleitet",
                f"{summary.reachable} erreichbar",
            ]
        ),
        "",
    ]
    if summary.budget_exhausted:
        lines += [
            "_Das Anfrage-Budget war aufgebraucht, bevor alle Links geprüft waren. Die "
            "übrigen stehen unten als `nicht geprüft` - das ist **kein** Hinweis darauf, "
            "dass sie in Ordnung sind._",
            "",
        ]
    lines += [LINKS_NOTICE, ""]

    interesting = [link for link in dossier.links if link.verdict != "erreichbar"]
    if not interesting:
        lines += ["Alle geprüften Links lösen auf.", ""]
        return lines

    # The glossary goes here, once, rather than repeated down the Befund
    # column. `gesperrt` is why it exists at all: a reader who takes it for
    # "dead" replaces a working reference with an archive copy.
    lines += ["Was die Befunde heissen:", ""]
    lines += [
        f"- **{verdict}** — {VERDICT_NOTES[verdict]}"
        for verdict in SEVERITY_ORDER
        if any(link.verdict == verdict for link in interesting)
    ]
    lines += [""]

    lines += ["| Link | Befund | Archiv |", "| --- | --- | --- |"]
    lines += [
        f"| {_link_cell(link)} | {_verdict_cell(link)} | {_snapshot_cell(link)} |" for link in interesting
    ]
    lines.append("")
    return lines


def _link_cell(link: LinkStatus) -> str:
    """The URL, and whether it is sourcing or a Weblinks entry.

    A dead `<ref>` is an unsourced statement; a dead `Weblinks` entry is
    untidy. The split `ReferenceSummary` already draws matters more here than
    anywhere else.
    """
    where = "Beleg" if link.cited else "Weblink"
    marker = " · _bereits archiviert_" if link.archived_in_article else ""
    return f"<{link.url}> ({where}){marker}"


def _verdict_cell(link: LinkStatus) -> str:
    """The verdict and what is specific to *this* link.

    Deliberately not carrying the glossary: that is printed once above the
    table, and repeating it on every row buried the per-link detail, which is
    the part a reader cannot get anywhere else.
    """
    parts = [f"**{link.verdict}**"]
    if link.status is not None:
        parts.append(f"HTTP {link.status}")
    if link.detail:
        parts.append(_escape(link.detail))
    if link.final_url:
        parts.append(f"→ <{link.final_url}>")
    return " · ".join(parts)


def _snapshot_cell(link: LinkStatus) -> str:
    """A snapshot is a candidate, never a replacement.

    No `{{Webarchiv}}` call is written here on purpose:
    `docs/research-policy.md` says the tool does not draft article text, and a
    ready-to-paste template is exactly what invites pasting it without opening
    the snapshot first. URL and date; the editor writes the template.
    """
    if not link.snapshot_url:
        return "—"
    dated = f" ({link.snapshot_date})" if link.snapshot_date else ""
    return f"[Schnappschuss]({link.snapshot_url}){dated} — selbst prüfen"


def _standing_section(dossier: Dossier) -> list[str]:
    """What the article's existing sourcing rests on.

    Free to compute, and it answers a question the reference count alone
    cannot: an article sourced entirely to unrated hosts is a different problem
    from one sourced to the federal statistics office.
    """
    lines = ["## Einstufung der zitierten Quellen", ""]
    shown = [s for s in dossier.reference_standing if s.verdict != "block"]
    if not dossier.reference_standing:
        lines += ["_Der Artikel zitiert keine externen Quellen._", ""]
        return lines
    if not shown:
        lines += ["_Alle zitierten Quellen sind ausgeschlossen - siehe unten._", ""]
        return lines

    lines += [
        "Nur eine Einstufung, kein Urteil: „nicht eingestuft“ heisst, dass diese",
        "Domain auf keiner Liste steht - nicht, dass mit ihr etwas nicht stimmt.",
        "",
        "| Domain | Einstufung | Belege |",
        "| --- | --- | ---: |",
    ]
    for item in shown:
        note = f" — {_escape(item.reason)}" if item.verdict == "note" and item.reason else ""
        lines.append(f"| `{_escape(item.host)}` | {_escape(item.label)}{note} | {item.references} |")
    lines.append("")
    return lines


def _excluded_section(dossier: Dossier) -> list[str]:
    """Every drop, with the reason it was dropped.

    A silently shortened source list is the same lie as a silently empty
    section. If something is missing from this dossier because of a decision
    somebody made, the decision is printed here.
    """
    blocked = [s for s in dossier.reference_standing if s.verdict == "block"]
    if not blocked:
        return []

    lines = ["## Ausgeschlossene Quellen", ""]
    lines += [
        "Von dir früher ausgeschlossen. Die Begründung steht dabei, damit niemand",
        "rekonstruieren muss, warum etwas fehlt - auch du nicht.",
        "",
    ]
    for item in blocked:
        decided = f" ({item.decided.isoformat()})" if item.decided else ""
        lines.append(
            f"- `{_escape(item.host)}` — {_escape(item.reason)}{decided}"
            f" · {item.references} Beleg(e) im Artikel"
        )
    lines.append("")
    return lines


# -------------------------------------------------------------------- values
def _value(value: str | None, as_of: int | None) -> str:
    if value is None:
        return "—"
    rendered = _escape(value)
    return f"{rendered} (Stand {as_of})" if as_of is not None else rendered


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------- the agent
#: Stated inside the section, not only in the file header. The header is read
#: once, by whoever opens the file; this section is what gets scrolled to,
#: screenshotted and pasted, and CLAUDE.md requires the warning to travel with
#: it. Every figure below came out of a language model.
FINDINGS_NOTICE = (
    "> **Jede Angabe in diesem Abschnitt ist ungeprüft.** Sie stammt von einem\n"
    "> Sprachmodell, das ein Dokument gelesen hat - nicht von einer Person, die es\n"
    "> beurteilt hat. Das Zitat wurde maschinell wörtlich im Dokument wiedergefunden;\n"
    "> das heisst, dass es dort steht, und sonst nichts. Ob es die Angabe im Artikel\n"
    "> wirklich überholt, weisst du erst, wenn du den Beleg selbst geöffnet hast."
)

STATUS_LABELS = {
    "supersedes_with_newer_value": "neuerer Wert",
    "contradicts_current": "widerspricht dem Artikel",
    "confirms_current": "bestätigt den Artikel",
}


def _findings_section(dossier: Dossier) -> list[str]:
    """What the research agent found - or the honest reason there is nothing.

    Absent entirely when the agent never ran, which is the normal case. An
    empty section would say "we looked and found nothing" about a search that
    was never made.
    """
    run = dossier.agent
    if run is None:
        return []

    lines = ["## Wahrscheinlich veraltet", ""]
    if not dossier.findings:
        lines += [_no_findings(dossier), ""]
        lines += _unexamined(dossier)
        return lines

    lines += [FINDINGS_NOTICE, ""]
    for finding in dossier.findings:
        lines += _finding(dossier, finding)
    lines += _unexamined(dossier)
    return lines


def _origin(finding: Finding) -> str:
    if finding.interwiki_lang:
        return f"{finding.interwiki_lang}wiki"
    return "Beleg des Artikels" if finding.from_reference else "Websuche"


def _finding(dossier: Dossier, finding: Finding) -> list[str]:
    label = STATUS_LABELS.get(finding.status, finding.status)
    lines = [f"### {_escape(finding.claim_text)}", "", f"*{label} · {_origin(finding)}*", ""]
    if finding.interwiki_lang:
        # Said on the row, not only in the section notice: a reader who lands
        # here from a link or a screenshot has to be told before they read the
        # number that this is another wiki, which is not a source at all.
        lines += [
            f"- **Kein Beleg:** eine andere Sprachversion ist selbst Wikipedia. "
            f"Verwertbar ist hier nicht die Zahl, sondern was {finding.interwiki_lang}wiki "
            f"dafür zitiert.",
        ]
    if finding.matches_wikidata:
        # Foreign infobox figures are frequently bot-imported. Two wikis
        # agreeing because one copied the other is not a second opinion.
        lines += [
            "- **Nicht unabhängig:** dieselbe Zahl führt auch Wikidata - vermutlich von dort "
            "übernommen, also keine zweite Bestätigung.",
        ]
    if finding.demoted:
        # Kept, but never sold as an update: two figures can differ because the
        # source is the older one.
        lines += [f"- **Kein Update:** {_escape(finding.demoted)}"]
    if finding.current_value:
        value = _value(finding.current_value, finding.as_of)
        # "Laut Quelle" is a claim about the document, so it is only written
        # when the document carries the figure. Where the model reached the
        # number by inference the label says so: the pointer is still worth
        # following, it is just not what the source says.
        said = "Laut Quelle" if finding.quote_supports_value else "Schluss des Modells (nicht im Zitat)"
        if finding.interwiki_lang and finding.quote_supports_value:
            said = f"Laut {finding.interwiki_lang}wiki"
        lines += [f"- **{said}:** {value}"]
    lines += [
        f"- **{'Fundstelle' if finding.interwiki_lang else 'Beleg'}:** <{finding.url}> "
        f"— {_escape(finding.standing)}",
        f"- **Zitat:** „{_escape(finding.quote)}“",
    ]
    if finding.cited_sources:
        # The point of the whole comparison: a citable document dewiki does not
        # have yet. Presented as something to open, because that is the only
        # thing that turns it into a source.
        lines += [
            "- **Dort zitiert** — das ist der Teil, der etwas wert ist, und er ist ungeprüft:",
        ]
        lines += [f"  - <{url}>" for url in finding.cited_sources]
    lines.append("")
    return lines


def _no_findings(dossier: Dossier) -> str:
    run = dossier.agent
    if run is None:  # pragma: no cover - guarded by the caller
        return ""
    if run.failed:
        # Loudest of the three, because it is the only one where the stage did
        # not merely come up empty - it stopped. Reading this section as "all
        # current" would be reading a crash as a clean bill of health.
        return (
            f"_Die Recherche ist abgebrochen: `{_escape(run.failed)}`. Was hier steht, "
            "ist der deterministische Teil des Dossiers; der Sprachmodell-Teil hat "
            "**nicht** stattgefunden. Das ist **kein** Hinweis darauf, dass alles "
            "aktuell oder belegt ist._"
        )
    if run.budget_exhausted:
        return (
            "_Das Aufruf-Budget war aufgebraucht, bevor etwas geprüft werden konnte. "
            "Das ist **kein** Hinweis darauf, dass alles aktuell ist._"
        )
    if not run.documents:
        return (
            "_Kein Dokument konnte gelesen werden - der Artikel verlinkt keine "
            "abrufbaren Belege, Weblinks oder Literaturangaben, und die Suche hat "
            "nichts geliefert. Es wurde also **nichts** geprüft._"
        )
    where = "den Belegen des Artikels" + (" und der Websuche" if run.searched else "")
    return f"_Nichts gefunden: in {where} stand zu diesen Angaben nichts Neueres._"


#: Why a claim on the agenda produced no finding. One line per reason, because
#: they are not the same fact and used to be printed as if they were: a claim
#: whose answer a gate refused was reported as "keine Quelle sagte etwas dazu",
#: which is the opposite of what happened.
OUTCOME_REASONS = {
    "nothing_found": "keine der gelesenen Quellen sagte etwas dazu",
    "dropped": "eine Antwort kam, wurde aber von den Prüfungen verworfen - siehe Protokoll",
    "budget": "das Aufruf-Budget war aufgebraucht",
    "not_asked": "es wurde gar nicht gefragt",
}


def _unexamined(dossier: Dossier) -> list[str]:
    """The claims that produced no finding, grouped by *why*.

    Without these lines a short findings section reads as "little to find"
    when it may mean "we stopped early" - or, worse, "a source said something
    and the machine threw it away". They are named by id, which is what the
    Angaben zum Prüfen table is keyed on.
    """
    run = dossier.agent
    if run is None:
        return []

    if not run.outcomes:
        # A dossier written before outcomes were recorded. The old, coarser
        # sentence is still the honest one for it: there is nothing finer in
        # the file to say.
        if not run.unexamined:
            return []
        legacy = ", ".join(f"`{claim_id}`" for claim_id in run.unexamined)
        why = "das Budget war aufgebraucht" if run.budget_exhausted else "keine Quelle sagte etwas dazu"
        return [f"_Nicht abschliessend geprüft ({why}): {legacy}._", ""]

    grouped: dict[str, list[str]] = {}
    for outcome in run.outcomes:
        if outcome.outcome == "found":
            continue
        grouped.setdefault(outcome.outcome, []).append(outcome.claim_id)

    lines: list[str] = []
    for key, reason in OUTCOME_REASONS.items():
        ids = sorted(grouped.get(key, []))
        if ids:
            named = ", ".join(f"`{claim_id}`" for claim_id in ids)
            lines.append(f"_Nicht abschliessend geprüft ({reason}): {named}._")
    return [*lines, ""] if lines else []


def _agent_section(dossier: Dossier) -> list[str]:
    """What the model layer cost and what its gates threw away.

    The drop counts are the part worth reading: a run where the quote check
    rejected most answers is a run whose survivors deserve more suspicion.
    """
    run = dossier.agent
    if run is None:
        return []

    lines = ["## Recherche-Metadaten", ""]
    if run.failed:
        lines += [f"**Abgebrochen:** `{_escape(run.failed)}`", ""]
    lines += [
        f"Modell `{run.model}`, Effort `{run.effort}` · {run.calls} Aufruf(e) "
        f"(davon {run.cached_calls} aus dem Cache) von höchstens {run.budget}",
        "",
        f"{run.documents} Dokument(e) gelesen, {run.reference_documents} davon Belege "
        f"des Artikels selbst" + (", danach Websuche" if run.searched else ", ohne Websuche"),
        "",
    ]

    counts: dict[str, int] = {}
    for drop in run.dropped:
        counts[drop.gate] = counts.get(drop.gate, 0) + 1
    if counts:
        labels = {
            "quote": "Zitat nicht im Dokument",
            "provenance": "Dokument gibt es nicht",
            "circularity": "Kopie des Artikels",
            "source_standing": "Quelle ausgeschlossen",
            "schema": "unbrauchbare Antwort",
            "section_provenance": "Abschnitt gibt es nicht",
            "section_empty": "Abschnitt ohne Stichpunkte",
            "unreadable": "Dokument nicht lesbar",
        }
        parts = [f"{labels.get(gate, gate)}: {count}" for gate, count in sorted(counts.items())]
        lines += ["Von den Prüfungen verworfen — " + " · ".join(parts), ""]

    if run.transcript:
        lines += [f"[Vollständiges Protokoll]({run.transcript})", ""]
    return lines


def _section_notes_block(dossier: Dossier) -> list[str]:
    """Bullet points on sections this article does not have.

    A summary of the *other edition's* section, not of the subject: the link
    goes to the text the bullets were written from, so the summary can be
    checked the same way everything else here can.
    """
    if not dossier.section_notes:
        if dossier.agent is not None and dossier.agent.sections_skipped:
            # There were sections to summarise and the budget refused the call.
            # The headings above are still listed, so without this line their
            # missing summaries read as "nothing worth saying about them".
            return [
                "",
                "_Zu diesen Abschnitten wurde keine Zusammenfassung erstellt: das "
                "Aufruf-Budget war aufgebraucht. Das heisst **nicht**, dass dort "
                "nichts steht._",
                "",
            ]
        return []
    lines = [
        "",
        "Worum es in diesen Abschnitten anderswo geht - zusammengefasst von einem",
        "Sprachmodell aus dem dortigen Text, nicht aus eigenem Wissen, und deshalb",
        "ebenso ungeprüft wie alles andere Maschinelle hier:",
        "",
    ]
    for note in dossier.section_notes:
        lines.append(f"- **[{_escape(note.heading)}]({note.source})** ({note.lang}wiki)")
        lines += [f"  - {_escape(bullet)}" for bullet in note.bullets]
    lines.append("")
    return lines
