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
from wp_todo.agent import _agenda, _urls_beside, run_agent
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


def test_an_editors_own_veraltet_flag_goes_to_the_front(scope: ScopeConfig) -> None:
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
    agenda = _agenda(mixed, (), REFERENCE, ScopeConfig.model_validate(scope.model_dump()).research)
    assert [item.claim.id for item in agenda] == ["veraltet1", "infobox1"]


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


# ------------------------------------------------------- what became of a claim
def outcome_for(outcome: Any, claim_id: str) -> str:
    found = next((o for o in outcome.run.outcomes if o.claim_id == claim_id), None)
    assert found is not None, f"{claim_id} is not accounted for at all"
    return str(found.outcome)


def test_a_refused_answer_is_not_reported_as_nothing_found(scope: ScopeConfig, tmp_path: Path) -> None:
    """The distinction the dossier used to lose.

    A source said something and the quote gate threw it away. Reporting that as
    "keine Quelle sagte etwas dazu" is not a rounding error - it is the
    opposite of what happened, and it sends the reader away from a transcript
    that is holding the answer.
    """
    outcome = execute(scope, tmp_path, [verdict(quote="So steht es nirgends im Dokument.")], budget=1)

    assert outcome.findings == ()
    assert outcome_for(outcome, "abc12345") == "dropped"
    assert [drop.gate for drop in outcome.dropped] == ["quote"]


def test_an_honest_no_is_not_confused_with_a_question_never_asked(scope: ScopeConfig, tmp_path: Path) -> None:
    """`nothing_found` is a result. Silence is not, and they used to print the
    same sentence."""
    asked = execute(scope, tmp_path, [verdict(status="nothing_found")], budget=1)
    assert outcome_for(asked, "abc12345") == "nothing_found"

    # No fetchable reference at all, so nothing was ever put to the model.
    unreachable = claims().model_copy(update={"references": ReferenceSummary(total=0)})
    silent = execute(scope, tmp_path, [], article_claims=unreachable, budget=1)
    assert outcome_for(silent, "abc12345") == "not_asked"
    assert silent.run is not None and silent.run.calls == 0


def test_the_budget_is_named_as_the_reason_it_was_the_reason(scope: ScopeConfig, tmp_path: Path) -> None:
    """A claim skipped on the ceiling says so, rather than borrowing the
    sentence meant for a claim that was asked and came back empty."""
    many = claims().model_copy(
        update={
            "claims": tuple(
                Claim(
                    id=f"claim{n:04d}",
                    kind="infobox_field",
                    text=f"Angabe {n}",
                    line_no=n + 10,
                    field="EINWOHNER",
                    asserted_value=str(n),
                    as_of_year=2018,
                )
                for n in range(3)
            )
        }
    )
    outcome = execute(scope, tmp_path, [verdict(status="nothing_found")], article_claims=many, budget=1)

    assert outcome_for(outcome, "claim0000") == "nothing_found", "the one that was asked"
    assert outcome_for(outcome, "claim0001") == "budget"
    assert outcome_for(outcome, "claim0002") == "budget"
    assert outcome.run is not None and outcome.run.budget_exhausted


# ------------------------------------------------------ the Wikidata contradiction
def conflicted() -> tuple[ArticleClaims, Delta]:
    """An undated infobox value that Wikidata disagrees with - the Horgen case."""
    article = claims().model_copy(
        update={
            "claims": (
                Claim(
                    id="mayor001",
                    kind="infobox_field",
                    text="GEMEINDEPRAESIDENT = Beat Muster (FDP)",
                    line_no=9,
                    field="GEMEINDEPRAESIDENT",
                    asserted_value="Beat Muster (FDP)",
                ),
            )
        }
    )
    delta = Delta(
        kind="wikidata",
        claim_id="mayor001",
        field="GEMEINDEPRAESIDENT",
        label="Leitung der Verwaltung",
        article_value="Beat Muster (FDP)",
        external_value="Q114366642",
        source="https://www.wikidata.org/wiki/Q1#P6",
        agrees=False,
    )
    return article, delta


def test_a_wikidata_disagreement_is_actually_put_to_the_model(scope: ScopeConfig, tmp_path: Path) -> None:
    """It was computed, rendered, and never asked.

    Both values were in hand and the article's own official website was already
    fetched. The prompt has to carry the contradiction - an ordinary "is this
    still current?" question throws away the sharpest signal the deterministic
    half produces.
    """
    article, delta = conflicted()
    model = ScriptedLlm(
        [verdict(status="nothing_found")],
        cache=ResponseCache(tmp_path / "llm"),
        budget=LlmBudget(limit=1),
    )
    execute(scope, tmp_path, [], article_claims=article, deltas=(delta,), llm=model)

    assert model.sent, "the conflict was not put to the model at all"
    asked = model.sent[0]
    assert "Beat Muster (FDP)" in asked and "Q114366642" in asked
    assert "Wikidata sagt" in asked, "the question has to be the contradiction, not the date"


def test_a_contradiction_outranks_a_merely_dated_figure(scope: ScopeConfig, tmp_path: Path) -> None:
    """Something has already been shown to disagree with it. That is a sharper
    question than "this carries an old Stand", and it should not queue behind
    one."""
    article, delta = conflicted()
    both = article.model_copy(update={"claims": (*claims().claims, *article.claims)})
    model = ScriptedLlm(
        [verdict(status="nothing_found"), verdict(status="nothing_found")],
        cache=ResponseCache(tmp_path / "llm"),
        budget=LlmBudget(limit=2),
    )
    execute(scope, tmp_path, [], article_claims=both, deltas=(delta,), llm=model)

    assert "Wikidata sagt" in model.sent[0], "the contradiction goes first"
    assert "Einwohner: 8500" in model.sent[1]


# --------------------------------------------------------- the undated infobox
def test_an_undated_value_is_asked_of_the_references_and_never_of_a_search(
    scope: ScopeConfig, tmp_path: Path
) -> None:
    """The official website is fetched and paid for by the time phase 1 runs,
    so asking it costs one call. A web search cannot settle "the mayor is X,
    no date given" cheaply, which is why it is never asked one."""
    article, _ = conflicted()
    model = ScriptedLlm(
        [verdict(status="nothing_found")],
        cache=ResponseCache(tmp_path / "llm"),
        budget=LlmBudget(limit=10),
    )
    outcome = execute(scope, tmp_path, [], article_claims=article, llm=model)

    assert len(model.sent) == 1, "asked once, of the references"
    assert "Die Dokumente sind die Belege" in model.sent[0]
    assert outcome.run is not None
    assert not outcome.run.searched, "an undated value must not reach the discovery call"


def test_a_recent_dated_figure_is_still_left_alone(scope: ScopeConfig, tmp_path: Path) -> None:
    """The undated agenda must not become a back door for every infobox field:
    a figure with a recent `Stand` is deliberately not worth a call, and it has
    a date, so it is not undated either."""
    fresh = claims().model_copy(
        update={
            "claims": (
                claims().claims[0].model_copy(update={"as_of_year": REFERENCE.year, "id": "fresh001"}),
            )
        }
    )
    outcome = execute(scope, tmp_path, [], article_claims=fresh)
    assert outcome.run is not None and outcome.run.calls == 0


# ------------------------------------------------------------ the reserved call
def test_the_sections_are_not_starved_by_the_claim_loop(scope: ScopeConfig, tmp_path: Path) -> None:
    """One call, and the most substantial part of the committed dossiers - and
    it ran last, so the per-claim loops ate it first and said nothing."""
    many = claims().model_copy(
        update={
            "claims": tuple(
                Claim(
                    id=f"claim{n:04d}",
                    kind="infobox_field",
                    text=f"Angabe {n}",
                    line_no=n + 10,
                    field="EINWOHNER",
                    asserted_value=str(n),
                    as_of_year=2018,
                )
                for n in range(4)
            )
        }
    )
    sections = {
        "json": {"sections": [{"section": 1, "bullets": ["Zwei Betriebe"]}]},
        "urls": [],
        "input_tokens": 300,
        "output_tokens": 90,
    }
    outcome = execute(
        scope,
        tmp_path,
        [verdict(status="nothing_found"), sections],
        article_claims=many,
        deltas=(
            Delta(
                kind="interwiki_section",
                label="enwiki",
                external_value="Economy",
                source="https://en.wikipedia.org/wiki/Musterwil",
            ),
        ),
        foreign_texts={"en": ("Musterwil", "== Economy ==\nTwo firms.\n")},
        budget=2,
    )

    assert [n.heading for n in outcome.section_notes] == ["Economy"]
    assert outcome.run is not None and outcome.run.budget_exhausted
    assert not outcome.run.sections_skipped


def test_a_summary_the_budget_refused_is_reported_not_silently_absent(
    scope: ScopeConfig, tmp_path: Path
) -> None:
    """ "Möglicherweise fehlend" still lists the headings, so an absent summary
    otherwise reads as "nothing worth saying about them"."""
    model = ScriptedLlm([], cache=ResponseCache(tmp_path / "llm"), budget=LlmBudget(limit=0))
    outcome = execute(
        scope,
        tmp_path,
        [],
        deltas=(
            Delta(
                kind="interwiki_section",
                label="enwiki",
                external_value="Economy",
                source="https://en.wikipedia.org/wiki/Musterwil",
            ),
        ),
        foreign_texts={"en": ("Musterwil", "== Economy ==\nTwo firms.\n")},
        llm=model,
    )

    assert outcome.section_notes == ()
    assert outcome.run is not None and outcome.run.sections_skipped


# ------------------------------------------------------- numbers, and the quote
def test_a_figure_the_quote_does_not_contain_is_demoted(scope: ScopeConfig, tmp_path: Path) -> None:
    """The Horgen case. The quote gate proved a sentence was on the page; the
    dossier then printed "mindestens 31 Titel" beside it, which the page did
    not say anywhere. The inference may be right - so this demotes rather than
    drops - but it must not print as "Laut Quelle"."""
    outcome = execute(scope, tmp_path, [verdict(value="mindestens 44 Titel", as_of=None)])

    assert len(outcome.findings) == 1, "kept: the pointer is still worth following"
    finding = outcome.findings[0]
    assert not finding.quote_supports_value
    assert "44" in finding.demoted


def test_a_thousands_separator_is_not_a_reason_to_doubt_a_figure(scope: ScopeConfig, tmp_path: Path) -> None:
    """German sources write 9'240, 9.240 and 9 240 for the same number, and a
    false rejection here would be exactly the failure the quote gate is careful
    to avoid."""
    outcome = execute(scope, tmp_path, [verdict(value="9'240 Personen", as_of=2025)])

    assert len(outcome.findings) == 1
    assert outcome.findings[0].quote_supports_value
    assert outcome.findings[0].demoted == ""


def test_a_value_with_no_digits_is_not_treated_as_unsupported(scope: ScopeConfig, tmp_path: Path) -> None:
    """This gate only speaks about digits, because digits are the part it can
    check. Plenty of true findings are names."""
    outcome = execute(scope, tmp_path, [verdict(value="Wohnbevoelkerung", as_of=None, quote=QUOTE)])

    assert len(outcome.findings) == 1
    assert outcome.findings[0].quote_supports_value


# ------------------------------------------------------- documents that failed
def test_a_document_that_could_not_be_read_is_reported(scope: ScopeConfig, tmp_path: Path) -> None:
    """A `block` was reported and everything else vanished with a bare
    `continue`: "10 Dokument(e) gelesen" with no mention of the six that were
    not."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with WebClient(
        meta=scope.meta,
        cache=ResponseCache(tmp_path / "web"),
        delay_s=0.0,
        respect_robots=False,
        transport=httpx.MockTransport(handler),
        reference_date=REFERENCE,
    ) as web:
        outcome = run_agent(
            claims=claims(),
            wikitext="Die Gemeinde hat 8500 Einwohner.",
            deltas=(),
            foreign_texts={},
            config=scope,
            reference=REFERENCE,
            web=web,
            llm=ScriptedLlm([], cache=ResponseCache(tmp_path / "llm"), budget=LlmBudget(limit=1)),
            ledger=SourceLedger(),
        )

    unreadable = [drop for drop in outcome.dropped if drop.gate == "unreadable"]
    assert len(unreadable) == 1
    assert unreadable[0].detail == "HTTP 404"
    assert unreadable[0].url == "https://beispiel-gemeinde.example/zahlen"


def test_the_reference_beside_the_claim_is_fetched_before_a_better_ranked_host(
    scope: ScopeConfig, tmp_path: Path
) -> None:
    """Standing alone would rank the official-suffix host first and the cap
    would stop there. The source an editor put in the same sentence as the
    stale figure is the one most likely to answer it."""
    from wp_todo.agent import _ordered_references

    beside = "https://verein-musterwil.example/titel"
    article = claims().model_copy(
        update={
            "references": ReferenceSummary(
                total=2, external_urls=("https://statistik.zh.ch/irgendwas", beside)
            )
        }
    )
    wikitext = "\n".join(["", "", "", f"Einwohner: 8500<ref>{beside}</ref>"])

    ranked = _ordered_references(article, SourceLedger())
    assert ranked[0].startswith("https://statistik.zh.ch"), "standing alone ranks the office first"

    agenda = _agenda(article, (), REFERENCE, scope.research)
    ranked = _ordered_references(article, SourceLedger(), _urls_beside(wikitext, agenda))
    assert ranked[0] == beside


# --------------------------------------------------------- the other editions
#: An enwiki article as wikitext, with a newer figure that carries its own
#: citation. The citation is the point: it is a document dewiki does not have.
ENWIKI = """{{Infobox settlement
| name = Musterwil
| area_total_km2 = 31.2<ref>{{cite web |url=https://amt.example/flaeche.pdf |title=Fläche 2024}}</ref>
| population_total = 9240
| population_as_of = 2025
}}
Musterwil is a municipality. The population was 9240 in 2025.<ref>https://statistik.example/2025</ref>
"""

FOREIGN = {"en": ("Musterwil", ENWIKI)}


def interwiki_reply(quote: str, *, value: str = "31.2", as_of: int | None = 2024) -> dict[str, Any]:
    """A verdict pointing at document 2 - the foreign edition, after the one
    reference the fixture article carries."""
    return verdict(status="supersedes_with_newer_value", document=2, quote=quote, value=value, as_of=as_of)


def test_another_edition_becomes_a_finding_that_names_its_language(
    scope: ScopeConfig, tmp_path: Path
) -> None:
    """The wikitext is already fetched for the section summaries and was then
    used for nothing else."""
    quote = "| population_total = 9240"
    outcome = execute(
        scope, tmp_path, [interwiki_reply(quote, value="9240", as_of=2025)], foreign_texts=FOREIGN
    )

    assert len(outcome.findings) == 1
    assert outcome.findings[0].interwiki_lang == "en"
    assert outcome.findings[0].url == "https://en.wikipedia.org/wiki/Musterwil"


def test_the_citation_the_other_edition_gives_is_extracted(scope: ScopeConfig, tmp_path: Path) -> None:
    """The whole reason the comparison is worth making.

    A Wikipedia is not a source; what an editor can act on is the document it
    cites. And the URL is pulled out of the verified quote by code, never
    named by the model, which is what keeps it from being invented.
    """
    quote = (
        "| area_total_km2 = 31.2<ref>{{cite web |url=https://amt.example/flaeche.pdf "
        "|title=Fläche 2024}}</ref>"
    )
    outcome = execute(scope, tmp_path, [interwiki_reply(quote)], foreign_texts=FOREIGN)

    assert outcome.findings[0].cited_sources == ("https://amt.example/flaeche.pdf",)


def test_a_citation_at_the_end_of_the_sentence_is_still_found(scope: ScopeConfig, tmp_path: Path) -> None:
    """An editor may put the ref after the sentence rather than beside the
    number, so the search widens from the quote to the line around it."""
    quote = "The population was 9240 in 2025."
    outcome = execute(
        scope, tmp_path, [interwiki_reply(quote, value="9240", as_of=2025)], foreign_texts=FOREIGN
    )

    assert outcome.findings[0].cited_sources == ("https://statistik.example/2025",)


def test_a_link_back_into_wikipedia_is_not_offered_as_a_citation(scope: ScopeConfig, tmp_path: Path) -> None:
    circular = {"en": ("Musterwil", "| population_total = 9240<ref>https://de.wikipedia.org/wiki/X</ref>\n")}
    outcome = execute(
        scope,
        tmp_path,
        [interwiki_reply("| population_total = 9240", value="9240", as_of=2025)],
        foreign_texts=circular,
    )

    assert outcome.findings[0].cited_sources == (), "a wiki citing a wiki is not a source"


def test_the_quote_gate_applies_to_another_edition_too(scope: ScopeConfig, tmp_path: Path) -> None:
    """Nothing softens because the document is a wiki. A paraphrase is a
    paraphrase."""
    outcome = execute(
        scope,
        tmp_path,
        [interwiki_reply("The population of Musterwil reached 9240 people in 2025.")],
        foreign_texts=FOREIGN,
        budget=1,
    )

    assert outcome.findings == ()
    assert [drop.gate for drop in outcome.dropped] == ["quote"]


def test_a_figure_wikidata_already_carries_is_marked_as_not_independent(
    scope: ScopeConfig, tmp_path: Path
) -> None:
    """Foreign infobox figures are frequently bot-imported. Two wikis agreeing
    because one copied the other is not a second opinion."""
    delta = Delta(
        kind="wikidata",
        claim_id="abc12345",
        field="EINWOHNER",
        label="Einwohnerzahl",
        article_value="8500",
        external_value="9240",
        agrees=False,
    )
    outcome = execute(
        scope,
        tmp_path,
        [interwiki_reply("| population_total = 9240", value="9240", as_of=2025)],
        foreign_texts=FOREIGN,
        deltas=(delta,),
    )

    assert outcome.findings[0].matches_wikidata


def test_a_figure_wikidata_does_not_carry_is_not_marked(scope: ScopeConfig, tmp_path: Path) -> None:
    """The guard on the test above: the label must mean something."""
    delta = Delta(
        kind="wikidata",
        claim_id="abc12345",
        field="EINWOHNER",
        label="Einwohnerzahl",
        article_value="8500",
        external_value="8500",
        agrees=True,
    )
    outcome = execute(
        scope,
        tmp_path,
        [interwiki_reply("| population_total = 9240", value="9240", as_of=2025)],
        foreign_texts=FOREIGN,
        deltas=(delta,),
    )

    assert not outcome.findings[0].matches_wikidata


def test_a_cited_document_outranks_another_wiki_saying_the_same(scope: ScopeConfig, tmp_path: Path) -> None:
    """A source that answers the question is worth more than a wiki asserting
    it, and the ordering should say so rather than leaving the standing label
    to carry it."""
    # Textually distinct on purpose: two claims with identical text produce
    # identical prompts, and the second call would be a cache hit on the first.
    two_claims = claims().model_copy(
        update={
            "claims": (
                claims().claims[0],
                Claim(
                    id="def67890",
                    kind="infobox_field",
                    text="FLÄCHE = 30.84",
                    line_no=9,
                    field="FLÄCHE",
                    asserted_value="30.84",
                    as_of_year=2018,
                ),
            )
        }
    )
    outcome = execute(
        scope,
        tmp_path,
        [
            verdict(document=1),
            interwiki_reply("| population_total = 9240", value="9240", as_of=2025),
        ],
        article_claims=two_claims,
        foreign_texts=FOREIGN,
    )

    assert len(outcome.findings) == 2
    assert outcome.findings[0].interwiki_lang == "", "the cited document leads"
    assert outcome.findings[1].interwiki_lang == "en"


def test_the_other_editions_cost_no_request_and_no_extra_call(scope: ScopeConfig, tmp_path: Path) -> None:
    """The claim the whole feature rests on: the wikitext is already in hand,
    and the documents join a question that was going to be asked anyway."""
    without = ScriptedLlm(
        [verdict(status="nothing_found")],
        cache=ResponseCache(tmp_path / "a"),
        budget=LlmBudget(limit=1),
    )
    execute(scope, tmp_path, [], llm=without)

    with_foreign = ScriptedLlm(
        [verdict(status="nothing_found")],
        cache=ResponseCache(tmp_path / "b"),
        budget=LlmBudget(limit=1),
    )
    outcome = execute(scope, tmp_path, [], foreign_texts=FOREIGN, llm=with_foreign)

    assert len(with_foreign.calls) == len(without.calls) == 1
    assert outcome.run is not None
    assert outcome.run.documents == 2, "one reference, one foreign edition"


def test_the_other_editions_can_be_turned_off(scope: ScopeConfig, tmp_path: Path) -> None:
    off = scope.model_copy(update={"research": scope.research.model_copy(update={"max_interwiki_docs": 0})})
    model = ScriptedLlm(
        [verdict(status="nothing_found")],
        cache=ResponseCache(tmp_path / "llm"),
        budget=LlmBudget(limit=1),
    )
    outcome = execute(off, tmp_path, [], foreign_texts=FOREIGN, llm=model)

    assert outcome.run is not None and outcome.run.documents == 1
    assert "en.wikipedia.org" not in model.sent[0]


def test_an_edition_that_is_a_straight_copy_is_still_dropped(scope: ScopeConfig, tmp_path: Path) -> None:
    """The guard on the circularity exemption.

    A foreign edition is openly a wiki, so the "secretly Wikipedia" heuristics
    are switched off for it - otherwise every edition would be dropped, since
    every Wikipedia article mentions Wikipedia. The verbatim-span check is not
    switched off, and this is what proves it: an edition that is a straight
    copy of this article is exactly as circular as any mirror.
    """
    copied = (
        "Die Gemeinde Musterwil liegt am See und zaehlte im Jahr 2018 rund 8500 Einwohner, "
        "verteilt auf mehrere Ortsteile entlang der Seestrasse und der alten Landstrasse "
        "hinauf zum Waldrand, wo die Gemeindegrenze verlaeuft und der Wanderweg beginnt."
    )
    assert len(copied) > scope.research.circularity_span

    outcome = execute(
        scope,
        tmp_path,
        [interwiki_reply(copied[:120], value="8500", as_of=None)],
        wikitext=copied,
        foreign_texts={"en": ("Musterwil", copied)},
        budget=1,
    )

    assert outcome.findings == ()
    assert [drop.gate for drop in outcome.dropped] == ["circularity"]
