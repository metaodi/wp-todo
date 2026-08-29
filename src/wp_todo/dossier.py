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

from .models import Delta, Dossier

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
    lines += _standing_section(dossier)
    lines += _excluded_section(dossier)

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
    lines += [" · ".join(parts), ""]
    return lines


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
