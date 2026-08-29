"""What the two committed artefacts say about the machine's work.

Both files land in a public repository, where a page can be found on its own and
read as though it were authoritative. So the properties tested here are about
what they *refuse* to imply: that a figure is checked, that nothing was found
when nothing was looked for, that a rejected answer is a finding.
"""

from __future__ import annotations

import datetime as dt

import pytest

from wp_todo.agent import AgentOutcome
from wp_todo.dossier import FINDINGS_NOTICE, render_markdown
from wp_todo.llm import LlmCall
from wp_todo.models import (
    AgentRun,
    ArticleClaims,
    Claim,
    Dossier,
    DroppedFinding,
    Finding,
    SectionNote,
)
from wp_todo.transcript import HEADER_NOTICE, render_transcript

REFERENCE = dt.date(2026, 8, 1)


def claims() -> ArticleClaims:
    return ArticleClaims(
        pageid=42,
        title="Musterwil",
        claims=(
            Claim(
                id="abc12345",
                kind="infobox_field",
                text="Einwohner: 8500",
                line_no=4,
                as_of_year=2018,
            ),
        ),
    )


def dossier(**overrides: object) -> Dossier:
    base: dict[str, object] = {
        "pageid": 42,
        "title": "Musterwil",
        "reference_date": REFERENCE,
        "claims": claims(),
        "edit_url": "https://de.wikipedia.org/w/index.php?title=Musterwil&action=edit",
    }
    base.update(overrides)
    return Dossier.model_validate(base)


def run(**overrides: object) -> AgentRun:
    base: dict[str, object] = {"model": "claude-opus-5", "effort": "medium", "calls": 1, "budget": 10}
    base.update(overrides)
    return AgentRun.model_validate(base)


FINDING = Finding(
    claim_id="abc12345",
    claim_text="Einwohner: 8500",
    status="supersedes_with_newer_value",
    current_value="9240",
    as_of=2025,
    quote="Die Wohnbevölkerung betrug 9240 Personen.",
    url="https://statistik.example/musterwil",
    host="statistik.example",
    standing="amtlich",
    from_reference=True,
    confidence=0.9,
)


# ------------------------------------------------------------------- dossier
def test_a_dossier_without_the_agent_has_no_findings_section_at_all() -> None:
    """The rule this project keeps relearning: "we did not look" must never be
    rendered the same way as "we looked and found nothing". An empty findings
    section in a run that never asked would be exactly that."""
    markdown = render_markdown(dossier())

    assert "Wahrscheinlich veraltet" not in markdown
    assert "Recherche-Metadaten" not in markdown


def test_the_findings_section_says_inside_itself_that_nothing_is_checked() -> None:
    """CLAUDE.md requires the warning to travel with the section, not only to
    sit in the file header. The header is read once by whoever opens the file;
    this section is the part that gets scrolled to and screenshotted."""
    markdown = render_markdown(dossier(agent=run(), findings=(FINDING,)))

    heading = markdown.index("## Wahrscheinlich veraltet")
    first_figure = markdown.index("9240")
    assert heading < markdown.index(FINDINGS_NOTICE) < first_figure


def test_an_empty_findings_section_says_which_kind_of_empty_it_is() -> None:
    """Three different nothings, and a reader has to be able to tell them
    apart: nothing found, nothing readable, nothing affordable."""
    searched = render_markdown(dossier(agent=run(documents=3, searched=True)))
    assert "Nichts gefunden" in searched

    nothing_read = render_markdown(dossier(agent=run(documents=0)))
    assert "**nichts** geprüft" in nothing_read

    broke = render_markdown(dossier(agent=run(budget_exhausted=True, documents=2)))
    assert "Budget" in broke
    assert "kein** Hinweis" in broke


def test_a_claim_that_was_never_examined_is_named_rather_than_left_looking_done() -> None:
    markdown = render_markdown(
        dossier(agent=run(unexamined=("abc12345",), documents=1, budget_exhausted=True), findings=())
    )

    assert "`abc12345`" in markdown
    assert "Nicht abschliessend geprüft" in markdown


def test_a_demoted_finding_is_never_sold_as_an_update() -> None:
    """A source older than the article is context. Rendering it beside the
    newer figures without saying so is the stale-agreement bug again."""
    demoted = FINDING.model_copy(update={"demoted": "Quelle von 2011 ist älter als die Angabe (2018)"})
    markdown = render_markdown(dossier(agent=run(), findings=(demoted,)))

    assert "Kein Update" in markdown
    assert markdown.index("Kein Update") < markdown.index("Laut Quelle")


def test_the_metadata_counts_what_the_gates_threw_away() -> None:
    """A run where the quote check rejected most answers is a run whose
    survivors deserve more suspicion - which is only visible if it is counted."""
    dropped = (
        DroppedFinding(claim_id="a", gate="quote", detail="nicht im Dokument"),
        DroppedFinding(claim_id="b", gate="quote", detail="nicht im Dokument"),
        DroppedFinding(claim_id="c", gate="circularity", detail="Spiegel"),
    )
    markdown = render_markdown(dossier(agent=run(dropped=dropped), findings=(FINDING,)))

    assert "Zitat nicht im Dokument: 2" in markdown
    assert "Kopie des Artikels: 1" in markdown


def test_section_bullets_say_they_are_a_machine_summary_and_link_the_text() -> None:
    note = SectionNote(
        heading="Economy",
        lang="en",
        source="https://en.wikipedia.org/wiki/Musterwil#Economy",
        bullets=("Zwei Betriebe", "Hoher Pendleranteil"),
    )
    from wp_todo.models import Delta

    markdown = render_markdown(
        dossier(
            agent=run(),
            section_notes=(note,),
            interwiki_checked=True,
            compared_languages=("en",),
            deltas=(
                Delta(
                    kind="interwiki_section",
                    label="enwiki",
                    external_value="Economy",
                    source="https://en.wikipedia.org/wiki/Musterwil",
                    detail="1 Abschnitt(e) ohne Entsprechung hier",
                ),
            ),
        )
    )

    assert "Sprachmodell" in markdown
    assert "ungeprüft" in markdown
    assert "#Economy" in markdown
    assert "- Zwei Betriebe" in markdown


# ---------------------------------------------------------------- transcript
def outcome(**overrides: object) -> AgentOutcome:
    call = LlmCall(
        ordinal=1,
        purpose="reference_check",
        subject="abc12345",
        prompt="Sagt eines der Dokumente etwas Neueres?",
        reply={"status": "supersedes_with_newer_value", "document": 1},
        cached=False,
        input_tokens=120,
        output_tokens=40,
    )
    built = AgentOutcome(findings=(FINDING,), run=run(), calls=(call,))
    for key, value in overrides.items():
        setattr(built, key, value)
    return built


def test_the_transcript_warns_that_it_contains_rejected_answers_too() -> None:
    """Somebody finding this file on its own must not read a discarded model
    answer as a finding. It is a louder document than the dossier, not a
    quieter one."""
    text = render_transcript(dossier(agent=run()), outcome())

    assert text.index(HEADER_NOTICE) < text.index("## Aufrufe")
    assert "verworfen" in HEADER_NOTICE


def test_every_rejected_answer_appears_with_the_gate_that_rejected_it() -> None:
    dropped = [
        DroppedFinding(claim_id="abc12345", gate="quote", detail="frei formuliert", url="https://x.example"),
        DroppedFinding(gate="source_standing", detail="Datendump von 2015", url="https://alt.example"),
    ]
    text = render_transcript(dossier(agent=run()), outcome(dropped=dropped))

    assert "Zitatprüfung" in text
    assert "frei formuliert" in text
    assert "Quelle ausgeschlossen" in text
    assert "https://alt.example" in text


def test_the_transcript_says_when_the_budget_cut_the_run_short() -> None:
    exhausted = run(budget_exhausted=True)
    text = render_transcript(dossier(agent=exhausted), outcome(run=exhausted))

    assert "Budget war aufgebraucht" in text
    assert "nicht, weil es" in text


def test_a_replayed_call_is_marked_as_replayed() -> None:
    """Whether an answer was paid for or replayed from the cache changes how
    much a reader should trust the run's date, so it is on the face of it."""
    call = LlmCall(ordinal=1, purpose="reference_check", subject="x", prompt="?", reply={}, cached=True)
    text = render_transcript(dossier(agent=run()), outcome(calls=(call,)))

    assert "aus dem Cache" in text


@pytest.mark.parametrize("field", ["prompt", "reply"])
def test_the_transcript_is_reproducible_from_the_same_outcome(field: str) -> None:
    """Same input, same bytes - the guarantee `research/` actually gives."""
    built = outcome()
    assert render_transcript(dossier(agent=run()), built) == render_transcript(dossier(agent=run()), built)
    assert getattr(built.calls[0], field) is not None
