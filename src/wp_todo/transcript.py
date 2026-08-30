"""What the agent actually asked, and what actually came back.

Committed next to the dossier it belongs to. The reason is simple: a findings
section is a summary of a conversation nobody else saw, and a summary of a
conversation with a language model is exactly the kind of thing that should not
have to be taken on trust. Everything the dossier asserts about the machine's
work - which claims were examined, what was shown to the model, what it said,
which gate refused what - is reconstructible from this file.

It is a *louder* document than the dossier, not a quieter one. A dossier says
"check this at the source"; a transcript contains raw model output, including
answers that were thrown away for being wrong, and somebody finding this file
on its own must not read a rejected answer as a finding. So the header says so
first, and every rejected answer is marked where it appears.

Deterministic in the same sense as everything else here: no clock is read, the
ordering is the order the calls happened in, and a replay of the same cache
produces the same bytes.
"""

from __future__ import annotations

import json

from .agent import AgentOutcome
from .dossier import slug
from .models import Dossier

HEADER_NOTICE = (
    "> **Rohprotokoll eines Sprachmodells. Kein Befund, keine Quelle, kein Artikeltext.**\n"
    ">\n"
    "> Diese Datei enthält, was das Modell gefragt wurde und was es geantwortet hat -\n"
    "> **einschliesslich der Antworten, die anschliessend maschinell verworfen wurden**,\n"
    "> weil das Zitat nicht im Dokument stand oder das Dokument eine Kopie des Artikels\n"
    "> war. Eine Antwort hier ist also nicht einmal ein Hinweis, solange sie unten nicht\n"
    "> als Befund im Dossier auftaucht - und auch dann gilt: erst am Beleg prüfen.\n"
    ">\n"
    "> Sie steht hier, damit nachvollziehbar ist, wie das Dossier zustande kam."
)

#: Prompts are long and mostly boilerplate. Enough is kept to see what was
#: asked; the documents themselves are named by URL rather than reproduced,
#: since they are in the cache and on the web.
PROMPT_CHARS = 1_200


def render_transcript(dossier: Dossier, outcome: AgentOutcome) -> str:
    run = outcome.run
    lines: list[str] = [
        f"# Protokoll: {dossier.title}",
        "",
        HEADER_NOTICE,
        "",
        f"[Dossier](./{slug_for(dossier)}.md)"
        f" · [Artikel](https://de.wikipedia.org/wiki/{dossier.title.replace(' ', '_')})",
        "",
    ]

    if run is not None:
        lines += [
            f"Modell: `{run.model}` · Effort: `{run.effort}` · "
            f"{run.calls} Aufruf(e), davon {run.cached_calls} aus dem Cache · "
            f"Budget: {run.budget}",
            "",
            f"Dokumente vorgelegt: {run.documents} "
            f"({run.reference_documents} davon Belege des Artikels selbst)"
            + (" · Websuche: ja" if run.searched else " · Websuche: nein"),
            "",
        ]
        if run.failed:
            lines += [
                "> **Die Recherche ist abgebrochen.** Was unten steht, sind die Aufrufe,",
                "> die vor dem Abbruch noch zustande kamen - nicht der vollständige Lauf.",
                ">",
                f"> `{run.failed}`",
                "",
            ]
        if run.budget_exhausted:
            lines += [
                "> **Das Budget war aufgebraucht, bevor alle Angaben geprüft waren.**",
                "> Das Dossier ist deshalb kürzer, als es sein müsste - nicht, weil es",
                "> nichts zu finden gab.",
                "",
            ]

    lines += _calls_section(outcome)
    lines += _dropped_section(outcome)
    return "\n".join(lines).rstrip() + "\n"


def slug_for(dossier: Dossier) -> str:
    return f"{dossier.pageid}-{slug(dossier.title)}"


def _calls_section(outcome: AgentOutcome) -> list[str]:
    lines = ["## Aufrufe", ""]
    if not outcome.calls:
        lines += ["_Es wurde kein Modell befragt._", ""]
        return lines

    for call in outcome.calls:
        source = "aus dem Cache" if call.cached else "neu angefragt"
        lines += [f"### {call.ordinal}. {call.purpose} — `{call.subject}` ({source})", ""]
        if call.refused:
            lines += [f"Das Modell hat die Antwort verweigert (`{call.refused}`).", ""]
        lines += [
            "<details><summary>Gefragt</summary>",
            "",
            "```",
            _clip(call.prompt),
            "```",
            "",
            "</details>",
            "",
        ]
        if call.found_urls:
            lines += [
                "Von der Websuche zurückgegeben (aus den Ergebnisblöcken gelesen, "
                "nicht aus dem Antworttext):",
                "",
            ]
            lines += [f"- <{url}>" for url in call.found_urls]
            lines.append("")
        if call.reply:
            lines += ["Geantwortet:", "", "```json", _reply(call.reply), "```", ""]
        if call.input_tokens or call.output_tokens:
            lines += [f"Tokens: {call.input_tokens} rein, {call.output_tokens} raus", ""]
    return lines


def _dropped_section(outcome: AgentOutcome) -> list[str]:
    """What the gates refused, and which gate refused it.

    Not an appendix. A run where the quote gate rejected most of the answers is
    a run whose survivors deserve more suspicion, and that is only visible if
    the rejections are counted where somebody will see them.
    """
    lines = ["## Von den Prüfungen verworfen", ""]
    if not outcome.dropped:
        lines += ["_Nichts verworfen._", ""]
        return lines

    labels = {
        "quote": "Zitatprüfung: das Zitat stand so nicht im Dokument",
        "provenance": "Herkunft: das genannte Dokument gab es nicht",
        "circularity": "Zirkelbezug: das Dokument ist eine Kopie des Artikels",
        "source_standing": "Quelle ausgeschlossen (von dir, früher)",
        "schema": "unbrauchbare Antwort",
        "section_provenance": "Herkunft: den genannten Abschnitt gab es nicht",
        "section_empty": "Abschnitt ohne verwertbare Stichpunkte",
    }
    lines += ["| Prüfung | Angabe | Detail | Dokument |", "| --- | --- | --- | --- |"]
    for drop in outcome.dropped:
        url = f"<{drop.url}>" if drop.url else "—"
        lines.append(
            f"| {_escape(labels.get(drop.gate, drop.gate))} | `{drop.claim_id or '—'}` "
            f"| {_escape(drop.detail) or '—'} | {url} |"
        )
    lines.append("")
    return lines


def _reply(reply: dict[str, object]) -> str:
    return json.dumps(reply, ensure_ascii=False, indent=2, sort_keys=True)


def _clip(text: str) -> str:
    if len(text) <= PROMPT_CHARS:
        return text
    return text[:PROMPT_CHARS] + "\n… (gekürzt)"


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
