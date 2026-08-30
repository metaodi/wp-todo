"""The gates, which are the whole reason this stage is allowed to exist.

Nothing here talks to a model or to the network. `ScriptedLlm` replaces exactly
one method - the HTTP hop - so the cache keys, the budget accounting and the
envelope parsing all run for real, and a test that passes is evidence about the
code that ships rather than about a mock of it.

Each gate test is written so that removing the gate makes it fail. That is the
practice that found the offline hole in M1 and the untested honesty fix in the
live-fixes round: a test that passes with its subject deleted is not a test.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx
import pytest

from wp_todo._http import OfflineCacheMissError
from wp_todo.agent import run_agent
from wp_todo.cache import ResponseCache
from wp_todo.config import ScopeConfig, load_scope
from wp_todo.llm import LlmBudget, LlmClient
from wp_todo.models import ArticleClaims, Claim, Delta, ReferenceSummary
from wp_todo.sources import SourceLedger, make_verdict
from wp_todo.webclient import WebClient

FIXTURES = Path(__file__).parent / "fixtures"
REFERENCE = dt.date(2026, 8, 1)

#: The document every "good" reply is checked against. The sentence the model
#: is meant to quote is in here verbatim; the paraphrase in the bad reply is not.
PAGE = """<html><body>
<h1>Gemeinde Musterwil</h1>
<p>Die Wohnbevoelkerung betrug am 31. Dezember 2025 genau 9240 Personen.</p>
<p>Weitere Zahlen erscheinen jaehrlich im Maerz 2026.</p>
</body></html>"""

QUOTE = "Die Wohnbevoelkerung betrug am 31. Dezember 2025 genau 9240 Personen."


@pytest.fixture(scope="module")
def scope() -> ScopeConfig:
    return load_scope(FIXTURES / "scope.toml")


class ScriptedLlm(LlmClient):
    """A real `LlmClient` with the one network call replaced by a queue.

    Everything else - the cache lookup, the budget check, `_record`, the
    envelope shape - is the shipping code. Replies are consumed in order; an
    empty queue is an error rather than a silent empty answer, because a test
    that runs out of script is a test that is no longer testing what it says.
    """

    def __init__(self, replies: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.replies = list(replies)
        self.sent: list[str] = []

    def _send(
        self,
        system: str,
        context: str,
        prompt: str,
        schema: dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.sent.append(prompt)
        if not self.replies:
            raise AssertionError("the agent made more calls than the script has replies")
        return self.replies.pop(0)


def verdict(
    *,
    status: str = "supersedes_with_newer_value",
    document: int = 1,
    quote: str = QUOTE,
    as_of: int | None = 2025,
    value: str = "9240",
    confidence: float = 0.9,
) -> dict[str, Any]:
    """One `_envelope`-shaped payload, as `_send` would return it."""
    return {
        "json": {
            "status": status,
            "document": document,
            "current_value": value,
            "as_of": as_of,
            "quote": quote,
            "confidence": confidence,
            "reasoning": "steht so im Dokument",
        },
        "urls": [],
        "input_tokens": 100,
        "output_tokens": 50,
    }


def claims(*, url: str = "https://beispiel-gemeinde.example/zahlen") -> ArticleClaims:
    """One stale infobox claim, and one reference that might answer it."""
    return ArticleClaims(
        pageid=1,
        title="Musterwil",
        infobox="Infobox Gemeinde in der Schweiz",
        sections=("Geschichte",),
        claims=(
            Claim(
                id="abc12345",
                kind="infobox_field",
                text="Einwohner: 8500",
                line_no=4,
                field="EINWOHNER",
                asserted_value="8500",
                as_of_year=2018,
            ),
        ),
        references=ReferenceSummary(total=1, with_year=1, newest_year=2018, external_urls=(url,)),
    )


def web_client(scope: ScopeConfig, tmp_path: Path, body: str = PAGE) -> WebClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.encode("utf-8"), headers={"Content-Type": "text/html"})

    return WebClient(
        meta=scope.meta,
        cache=ResponseCache(tmp_path / "web"),
        delay_s=0.0,
        respect_robots=False,
        transport=httpx.MockTransport(handler),
        reference_date=REFERENCE,
    )


def execute(
    scope: ScopeConfig,
    tmp_path: Path,
    replies: list[dict[str, Any]],
    *,
    article_claims: ArticleClaims | None = None,
    wikitext: str = "Die Gemeinde hat 8500 Einwohner.",
    ledger: SourceLedger | None = None,
    body: str = PAGE,
    budget: int = 10,
    llm: LlmClient | None = None,
    deltas: tuple[Delta, ...] = (),
    foreign_texts: dict[str, tuple[str, str]] | None = None,
    cache_dir: Path | None = None,
) -> Any:
    model = llm or ScriptedLlm(
        replies,
        cache=ResponseCache(cache_dir or (tmp_path / "llm")),
        budget=LlmBudget(limit=budget),
    )
    with web_client(scope, tmp_path, body) as web:
        return run_agent(
            claims=article_claims or claims(),
            wikitext=wikitext,
            deltas=deltas,
            foreign_texts=foreign_texts or {},
            config=scope,
            reference=REFERENCE,
            web=web,
            llm=model,
            ledger=ledger or SourceLedger(),
        )


# --------------------------------------------------------------- the happy path
def test_a_quoted_answer_from_a_cited_reference_becomes_a_finding(scope: ScopeConfig, tmp_path: Path) -> None:
    """The whole point: the article already cited the answer, so no search."""
    outcome = execute(scope, tmp_path, [verdict()])

    assert len(outcome.findings) == 1
    finding = outcome.findings[0]
    assert finding.current_value == "9240"
    assert finding.quote == QUOTE
    assert finding.from_reference, "this came from a source the article already cites"
    assert outcome.run is not None
    assert not outcome.run.searched, "the references answered, so the open web was never asked"
    assert not outcome.dropped


def test_whitespace_differences_do_not_reject_a_real_quote(scope: ScopeConfig, tmp_path: Path) -> None:
    """Extraction collapses whitespace; a quote copied across a line break is
    still the same quote, and rejecting it would make the gate useless."""
    wrapped = QUOTE.replace(" genau ", "\n   genau  ")
    outcome = execute(scope, tmp_path, [verdict(quote=wrapped)])

    assert len(outcome.findings) == 1


# ---------------------------------------------------------------- gate: quote
def test_a_paraphrase_is_dropped_however_plausible_it_reads(scope: ScopeConfig, tmp_path: Path) -> None:
    """The gate that makes fabricated sourcing structurally impossible.

    This reply is *true* - the document really does say 9240 - and it is still
    refused, because "true-sounding" is exactly the property a fabricated
    citation also has. Only literal presence counts.
    """
    outcome = execute(
        scope,
        tmp_path,
        [verdict(quote="Die Bevölkerung lag Ende 2025 bei 9240 Personen.")],
        budget=1,
    )

    assert not outcome.findings
    assert [d.gate for d in outcome.dropped] == ["quote"]


def test_an_empty_quote_is_not_treated_as_a_match(scope: ScopeConfig, tmp_path: Path) -> None:
    """The empty string is in every document. Without this the gate is a no-op."""
    outcome = execute(scope, tmp_path, [verdict(quote="")], budget=1)

    assert not outcome.findings
    assert [d.gate for d in outcome.dropped] == ["quote"]


def test_case_is_not_folded_when_checking_a_quote(scope: ScopeConfig, tmp_path: Path) -> None:
    """A quote is meant to be copied. Folding case is the first step on the
    road to "close enough", and close enough is how a paraphrase gets in."""
    outcome = execute(scope, tmp_path, [verdict(quote=QUOTE.upper())], budget=1)

    assert not outcome.findings
    assert [d.gate for d in outcome.dropped] == ["quote"]


# ----------------------------------------------------------- gate: provenance
@pytest.mark.parametrize("document", [0, 7, -1], ids=["none", "out-of-range", "negative"])
def test_a_document_that_was_never_fetched_cannot_carry_a_finding(
    scope: ScopeConfig, tmp_path: Path, document: int
) -> None:
    """The model picks by index out of a list we built, so it cannot name a URL
    at all. An index that resolves to nothing has nothing to check against."""
    outcome = execute(scope, tmp_path, [verdict(document=document)], budget=1)

    assert not outcome.findings
    assert [d.gate for d in outcome.dropped] == ["provenance"]


# -------------------------------------------------------------- gate: recency
def test_a_source_older_than_the_article_is_demoted_not_reported_as_an_update(
    scope: ScopeConfig, tmp_path: Path
) -> None:
    """Two figures can differ because the *source* is the stale one. Selling
    that as an update is the same false comfort the Wikidata section had."""
    outcome = execute(scope, tmp_path, [verdict(as_of=2011)])

    assert len(outcome.findings) == 1, "still worth knowing about - just not an update"
    assert outcome.findings[0].demoted
    assert "2011" in outcome.findings[0].demoted


# ---------------------------------------------------------- gate: circularity
MIRROR = f"<html><body><p>Die Gemeinde hat 8500 Einwohner.</p><p>{QUOTE}</p></body></html>"


def test_a_copy_of_the_article_is_dropped_even_with_a_perfect_quote(
    scope: ScopeConfig, tmp_path: Path
) -> None:
    """The one failure a human check does not catch, because the text looks
    perfect - it is the article's own text."""
    article_text = "Die Gemeinde Musterwil hat 8500 Einwohner. " * 20
    outcome = execute(
        scope,
        tmp_path,
        [verdict()],
        wikitext=article_text,
        body=f"<html><body><p>{article_text}</p><p>{QUOTE}</p></body></html>",
        budget=1,
    )

    assert not outcome.findings
    assert [d.gate for d in outcome.dropped] == ["circularity"]


def test_trusting_a_mirror_does_not_let_it_through(scope: ScopeConfig, tmp_path: Path) -> None:
    """`trust` orders sources; it never admits one. Trusting a mirror is always
    an error, so the verdict must not be able to express it."""
    ledger = SourceLedger(
        verdicts=(make_verdict("beispiel-gemeinde.example", "trust", "amtlich, selbst geprüft", REFERENCE),)
    )
    article_text = "Die Gemeinde Musterwil hat 8500 Einwohner. " * 20
    outcome = execute(
        scope,
        tmp_path,
        [verdict()],
        wikitext=article_text,
        body=f"<html><body><p>{article_text}</p><p>{QUOTE}</p></body></html>",
        ledger=ledger,
        budget=1,
    )

    assert not outcome.findings
    assert [d.gate for d in outcome.dropped] == ["circularity"]


# ------------------------------------------------------- gate: source standing
def test_a_blocked_host_is_never_fetched_and_is_still_reported(scope: ScopeConfig, tmp_path: Path) -> None:
    """The block saves a request *and* is announced. A silently shortened
    source list is the same lie as a silently empty section."""
    ledger = SourceLedger(
        verdicts=(make_verdict("beispiel-gemeinde.example", "block", "Datendump von 2015", REFERENCE),)
    )
    outcome = execute(scope, tmp_path, [], ledger=ledger, budget=1)

    assert not outcome.findings
    assert [d.gate for d in outcome.dropped] == ["source_standing"]
    assert outcome.dropped[0].detail == "Datendump von 2015"
    assert outcome.run is not None
    assert outcome.run.documents == 0, "a blocked host must not cost a request"


# ---------------------------------------------------------------- the budget
def test_running_out_of_budget_is_announced_rather_than_hidden(scope: ScopeConfig, tmp_path: Path) -> None:
    """A short findings list because the ceiling was hit is a different fact
    from a short findings list because there was little to find."""
    many = ArticleClaims(
        pageid=1,
        title="Musterwil",
        claims=tuple(
            Claim(
                id=f"claim{n:04d}",
                kind="infobox_field",
                text=f"Angabe {n}",
                line_no=n,
                field="EINWOHNER",
                asserted_value=str(n),
                as_of_year=2018,
            )
            for n in range(4)
        ),
        references=ReferenceSummary(total=1, external_urls=("https://beispiel-gemeinde.example/z",)),
    )
    outcome = execute(
        scope,
        tmp_path,
        [verdict(status="nothing_found")],
        article_claims=many,
        budget=1,
    )

    assert outcome.run is not None
    assert outcome.run.budget_exhausted
    assert len(outcome.run.unexamined) == 4, "every claim it never got to is named"


def test_the_budget_is_not_spent_on_a_search_whose_results_cannot_be_read(
    scope: ScopeConfig, tmp_path: Path
) -> None:
    """Discovery plus verification is two calls. With one left, searching would
    buy a list of URLs nobody ever asks a question about."""
    outcome = execute(scope, tmp_path, [verdict(status="nothing_found")], budget=1)

    assert outcome.run is not None
    assert not outcome.run.searched


# ------------------------------------------------------------ references first
def test_the_open_web_is_only_asked_about_what_the_references_could_not_answer(
    scope: ScopeConfig, tmp_path: Path
) -> None:
    """The design the editor asked for, and the reason this stage is cheap."""
    search = {
        "json": {"queries": ["Musterwil Einwohnerzahl 2025"], "note": "gesucht"},
        "urls": ["https://statistik.zh.ch/musterwil"],
        "input_tokens": 200,
        "output_tokens": 40,
    }
    outcome = execute(scope, tmp_path, [verdict(status="nothing_found"), search, verdict()])

    assert outcome.run is not None
    assert outcome.run.searched
    assert len(outcome.findings) == 1
    assert not outcome.findings[0].from_reference, "this one came from the search"
    assert outcome.run.reference_documents == 1
    assert outcome.run.documents == 2


def test_a_url_the_model_only_talked_about_never_reaches_the_fetcher(
    scope: ScopeConfig, tmp_path: Path
) -> None:
    """URLs come out of the structured search-result blocks, never out of prose.

    Here the model's own JSON names a URL and the result blocks are empty. If
    the prose were read, that invented host would be fetched.
    """
    search = {
        "json": {"queries": ["https://erfunden.example/quelle"], "note": "siehe erfunden.example"},
        "urls": [],
        "input_tokens": 200,
        "output_tokens": 40,
    }
    outcome = execute(scope, tmp_path, [verdict(status="nothing_found"), search])

    assert outcome.run is not None
    assert outcome.run.documents == 1, "only the article's own reference was ever read"
    assert not outcome.findings


# ------------------------------------------------------------- section notes
def test_a_section_the_agent_did_not_find_cannot_be_summarised(scope: ScopeConfig, tmp_path: Path) -> None:
    """Provenance again, on the other half of the stage: a heading the model
    produced out of nowhere is not one another edition actually has."""
    deltas = (
        Delta(
            kind="interwiki_section",
            label="enwiki",
            external_value="Economy",
            source="https://en.wikipedia.org/wiki/Musterwil",
            detail="1 Abschnitt(e) ohne Entsprechung hier",
        ),
    )
    reply = {
        "json": {
            "sections": [
                {"section": 1, "bullets": ["Zwei Betriebe", "Pendleranteil"]},
                {"section": 7, "bullets": ["nichts davon"]},
            ]
        },
        "urls": [],
        "input_tokens": 300,
        "output_tokens": 90,
    }
    outcome = execute(
        scope,
        tmp_path,
        [verdict(status="nothing_found"), reply],
        deltas=deltas,
        foreign_texts={"en": ("Musterwil", "== Economy ==\nTwo firms and a lot of commuters.\n")},
        budget=2,
    )

    headings = [note.heading for note in outcome.section_notes]
    assert headings == ["Economy"]
    assert outcome.section_notes[0].bullets == ("Zwei Betriebe", "Pendleranteil")
    assert outcome.section_notes[0].source.endswith("#Economy")
    # And the invented one is reported, not quietly forgotten.
    assert [drop.gate for drop in outcome.dropped] == ["section_provenance"]


def test_a_translated_heading_does_not_lose_the_summary(scope: ScopeConfig, tmp_path: Path) -> None:
    """The Küsnachter Dorfbach regression. Every prompt in this stage is in
    German, so the model translated `Historical floods` to `Historische
    Hochwasser`; matching on the heading dropped both summaries in silence and
    left the dossier showing bare English headings. A number survives that."""
    deltas = (
        Delta(
            kind="interwiki_section",
            label="enwiki",
            external_value="Historical floods",
            source="https://en.wikipedia.org/wiki/Musterwil",
            detail="1 Abschnitt(e) ohne Entsprechung hier",
        ),
    )
    reply = {
        "json": {"sections": [{"section": 1, "bullets": ["1778: 63 Todesopfer", "Danach begradigt"]}]},
        "urls": [],
        "input_tokens": 300,
        "output_tokens": 90,
    }
    outcome = execute(
        scope,
        tmp_path,
        [verdict(status="nothing_found"), reply],
        deltas=deltas,
        foreign_texts={"en": ("Musterwil", "== Historical floods ==\nIn 1778 a flood killed 63.\n")},
        budget=2,
    )

    assert len(outcome.section_notes) == 1
    note = outcome.section_notes[0]
    assert note.heading == "Historical floods", "the heading comes from our list, not from the model"
    assert note.bullets == ("1778: 63 Todesopfer", "Danach begradigt")
    assert not outcome.dropped


# ------------------------------------------------------------------- replay
def test_a_rerun_replays_the_cache_and_costs_nothing(scope: ScopeConfig, tmp_path: Path) -> None:
    """Reproducibility by replay, which is what CLAUDE.md promises for
    `research/`. The second run has an empty script *and* offline set: if a
    single call missed the cache it would raise rather than pass quietly."""
    cache_dir = tmp_path / "llm"
    first = execute(scope, tmp_path, [verdict()], cache_dir=cache_dir)

    replayed = ScriptedLlm([], cache=ResponseCache(cache_dir), budget=LlmBudget(limit=10), offline=True)
    second = execute(scope, tmp_path, [], llm=replayed, cache_dir=cache_dir)

    assert first.findings == second.findings
    assert second.run is not None
    assert second.run.cached_calls == second.run.calls
    assert replayed.budget.spent == 0, "a replay is free, so it must not touch the budget"


def test_offline_raises_rather_than_quietly_spending_money(scope: ScopeConfig, tmp_path: Path) -> None:
    """The discipline the whole test suite rests on: no test may ever pay for a
    model call by accident."""
    empty = ScriptedLlm([], cache=ResponseCache(tmp_path / "cold"), budget=LlmBudget(limit=10), offline=True)

    with pytest.raises(OfflineCacheMissError):
        execute(scope, tmp_path, [], llm=empty)


# -------------------------------------------------------------------- agenda
def test_a_claim_with_no_date_is_not_worth_a_call(scope: ScopeConfig, tmp_path: Path) -> None:
    """ "The article says X, with no date" is a question the open web cannot
    settle cheaply, and asking it anyway is how a tight budget disappears."""
    undated = ArticleClaims(
        pageid=1,
        title="Musterwil",
        claims=(Claim(id="undated1", kind="infobox_field", text="Website: beispiel.example", line_no=2),),
        references=ReferenceSummary(total=1, external_urls=("https://beispiel-gemeinde.example/z",)),
    )
    outcome = execute(scope, tmp_path, [], article_claims=undated)

    assert outcome.run is not None
    assert outcome.run.calls == 0
    assert not outcome.findings


def test_a_recent_figure_is_not_worth_a_call_either(scope: ScopeConfig, tmp_path: Path) -> None:
    """A figure from last year is not news, and `stale_after_years` says so."""
    fresh = claims().model_copy(
        update={
            "claims": (
                Claim(
                    id="fresh001",
                    kind="infobox_field",
                    text="Einwohner: 9240",
                    line_no=4,
                    field="EINWOHNER",
                    asserted_value="9240",
                    as_of_year=REFERENCE.year,
                ),
            )
        }
    )
    outcome = execute(scope, tmp_path, [], article_claims=fresh)

    assert outcome.run is not None
    assert outcome.run.calls == 0


def test_an_editors_own_veraltet_flag_goes_to_the_front(scope: ScopeConfig, tmp_path: Path) -> None:
    """Somebody wrote in the article that it is stale. That is the strongest
    signal the page carries, and it should not queue behind an infobox field."""
    from wp_todo.agent import _agenda

    mixed = ArticleClaims(
        pageid=1,
        title="Musterwil",
        claims=(
            Claim(
                id="infobox1",
                kind="infobox_field",
                text="Einwohner: 8500",
                line_no=4,
                as_of_year=2018,
            ),
            Claim(
                id="veraltet1",
                kind="veraltet_template",
                text="{{Veraltet|seit=2021}}",
                line_no=90,
                as_of_year=2021,
            ),
        ),
    )
    assert [c.id for c in _agenda(mixed, REFERENCE, 2)] == ["veraltet1", "infobox1"]


# ------------------------------------------------------------------ excerpts
def test_a_quote_is_checked_against_the_whole_document_not_the_excerpt(
    scope: ScopeConfig, tmp_path: Path
) -> None:
    """Only a window of a long page is put in front of the model, but the gate
    reads the stored text. Checking against the excerpt would turn the size cap
    into a way of rejecting true quotes."""
    from wp_todo.agent import EXCERPT_CHARS, _excerpt

    filler = "<p>Ein Satz ohne Jahreszahl, nur zum Auffuellen der Seite.</p>\n" * 400
    body = f"<html><body>{filler}<p>{QUOTE}</p></body></html>"
    outcome = execute(scope, tmp_path, [verdict()], body=body)

    stored = outcome.findings[0]
    assert stored.quote == QUOTE
    text = "Kein Jahr hier. " * 2000 + QUOTE
    assert len(_excerpt(text)) <= EXCERPT_CHARS
    assert QUOTE not in _excerpt(text), "the excerpt really does cut it off"


# ------------------------------------------------------------- what gets written
def test_the_transcript_is_written_before_the_dossier_that_links_it(tmp_path: Path) -> None:
    """The deliverable the editor asked for: a way to see what the agent did.

    A link to a file that is not there yet is the kind of small lie this
    project has spent a lot of effort not telling, so the transcript is written
    first - and the dossier that gets rendered is the one carrying the link,
    which is why `write_dossier` hands the updated dossier back.
    """
    from wp_todo.cli import write_dossier
    from wp_todo.models import Dossier

    outcome = execute(scope_for_writing(), tmp_path, [verdict()])
    built = Dossier(
        pageid=42,
        title="Musterwil",
        reference_date=REFERENCE,
        claims=claims(),
        agent=outcome.run,
        findings=outcome.findings,
    )

    out = tmp_path / "research"
    returned = write_dossier(out, built, outcome)

    transcript = out / "42-musterwil.transcript.md"
    dossier = out / "42-musterwil.md"
    assert transcript.exists()
    assert "Rohprotokoll" in transcript.read_text(encoding="utf-8")
    assert f"({transcript.name})" in dossier.read_text(encoding="utf-8")
    assert returned.agent is not None and returned.agent.transcript == transcript.name
    assert transcript.name in (out / "42-musterwil.json").read_text(encoding="utf-8")


def test_without_the_agent_nothing_extra_is_written(tmp_path: Path) -> None:
    """The normal case, and the reason the flag exists: the dossier is still
    built, it just costs nothing and claims nothing about a model."""
    from wp_todo.cli import write_dossier
    from wp_todo.models import Dossier

    built = Dossier(pageid=42, title="Musterwil", reference_date=REFERENCE, claims=claims())
    out = tmp_path / "research"
    write_dossier(out, built, None)

    assert not list(out.glob("*.transcript.md"))
    assert "Wahrscheinlich veraltet" not in (out / "42-musterwil.md").read_text(encoding="utf-8")


def scope_for_writing() -> ScopeConfig:
    return load_scope(FIXTURES / "scope.toml")


# ------------------------------------------- Weblinks, Literatur, and sourcing
def sourcing_claims(*, linked: str = "https://beispiel-gemeinde.example/zahlen") -> ArticleClaims:
    """`Sanatorium Kilchberg` in miniature: print references that cite no URL,
    a `{{Belege fehlen}}` marker, and the one readable document under
    `Weblinks`. Before this pair of fixes the agenda and the document list were
    both empty and the stage made zero calls."""
    return ArticleClaims(
        pageid=2,
        title="Musterwil",
        sections=("Geschichte", "Weblinks"),
        claims=(
            Claim(
                id="belege_fehlen-abc12345",
                kind="belege_fehlen",
                text="{{Belege fehlen}}: als unzureichend belegt markiert",
                line_no=1,
            ),
        ),
        references=ReferenceSummary(total=46, external_urls=(), linked_urls=(linked,)),
    )


def test_a_weblinks_document_is_read_even_when_no_reference_cites_a_url(
    scope: ScopeConfig, tmp_path: Path
) -> None:
    """Remove `linked_urls` from `_ordered_references` and this goes to zero
    documents and zero calls - which is exactly what run #13 did."""
    outcome = execute(scope, tmp_path, [verdict(status="confirms_current")], article_claims=sourcing_claims())

    assert outcome.run is not None
    assert outcome.run.documents == 1, "the Weblinks entry is a document like any other"
    assert outcome.run.calls == 1, "and the sourcing claim is a question worth asking about it"
    assert len(outcome.findings) == 1
    assert outcome.findings[0].quote == QUOTE


def test_the_sourcing_claim_is_asked_a_sourcing_question(scope: ScopeConfig, tmp_path: Path) -> None:
    """Not "is this still true?" - the marker has no date to go stale. Asking
    the dated question here earns `nothing_found` and wastes the call."""
    outcome = execute(scope, tmp_path, [verdict(status="confirms_current")], article_claims=sourcing_claims())

    prompt = outcome.calls[0].prompt
    assert "Einzelnachweis verwenden" in prompt
    assert "etwas Neueres" not in prompt


def test_the_quote_gate_still_applies_to_a_sourcing_answer(scope: ScopeConfig, tmp_path: Path) -> None:
    """A different question, not a softer one."""
    outcome = execute(
        scope,
        tmp_path,
        [verdict(status="confirms_current", quote="So steht das nirgends im Dokument.")],
        article_claims=sourcing_claims(),
        # One call only: a dropped answer leaves the claim unanswered, and with
        # room to spare the stage would go on to the open web, which is a
        # different behaviour than the one under test here.
        budget=1,
    )

    assert not outcome.findings
    assert [drop.gate for drop in outcome.dropped] == ["quote"]


def test_a_dated_claim_is_asked_before_the_undated_sourcing_marker(
    scope: ScopeConfig, tmp_path: Path
) -> None:
    """`belege_fehlen` is the least specific question on the agenda, so it gets
    whatever budget is left rather than the first call."""
    both = claims().model_copy(
        update={
            "claims": (
                *claims().claims,
                Claim(
                    id="belege_fehlen-abc12345",
                    kind="belege_fehlen",
                    text="{{Belege fehlen}}: als unzureichend belegt markiert",
                    line_no=1,
                ),
            )
        }
    )
    outcome = execute(scope, tmp_path, [verdict(), verdict()], article_claims=both, budget=1)

    assert outcome.run is not None
    assert outcome.run.calls == 1
    assert outcome.calls[0].subject == "abc12345", "the dated infobox claim went first"
