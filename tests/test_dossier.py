"""The dossier end to end: real fixture corpus in, briefing out.

The Wikimedia half runs offline from the recorded fixtures. The Wikidata half
runs against a mock transport, because there is no recorded fixture for the
Wikibase REST API yet - see the note in `test_enrich.py`.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from wp_todo.cache import ResponseCache
from wp_todo.client import WikiClient
from wp_todo.config import ScopeConfig, load_scope
from wp_todo.dossier import HEADER_NOTICE, render_json, render_markdown, slug
from wp_todo.fetch import fetch
from wp_todo.llm import LlmBudget, LlmClient, LlmUnavailableError
from wp_todo.models import Dossier, FetchResult
from wp_todo.research import research_article
from wp_todo.sources import SourceLedger, make_verdict
from wp_todo.webclient import WebClient

FIXTURES = Path(__file__).parent / "fixtures"
ARTICLE = "Küsnachter Dorfbach"


@pytest.fixture(scope="module")
def scope() -> ScopeConfig:
    return load_scope(FIXTURES / "scope.toml")


@pytest.fixture(scope="module")
def corpus(scope: ScopeConfig) -> FetchResult:
    with WikiClient(meta=scope.meta, cache=ResponseCache(FIXTURES / "http"), offline=True) as client:
        return fetch(scope, client)


def build(
    corpus: FetchResult,
    scope: ScopeConfig,
    tmp_path: Path,
    *,
    statements: dict[str, Any] | None = None,
    compare_wikidata: bool = False,
    ledger: SourceLedger | None = None,
    llm: LlmClient | None = None,
) -> Dossier:
    """A dossier for the acceptance article, with Wikimedia offline."""
    article = next(a for a in corpus.articles if a.title == ARTICLE)
    config = scope.model_copy(
        update={"research": scope.research.model_copy(update={"compare_wikidata": compare_wikidata})}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(statements or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    with (
        WikiClient(meta=scope.meta, cache=ResponseCache(FIXTURES / "http"), offline=True) as wiki,
        WebClient(
            meta=scope.meta,
            cache=ResponseCache(tmp_path / "web"),
            delay_s=0.0,
            respect_robots=False,
            transport=httpx.MockTransport(handler),
            reference_date=corpus.reference_date,
        ) as web,
    ):
        return research_article(article, corpus, config, wiki, web, None, ledger, llm)[0]


def test_the_dossier_leads_with_what_it_is_not(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """A file of "current" figures beside article text is exactly the thing
    that gets pasted in unchecked. The disclaimer is load-bearing."""
    markdown = render_markdown(build(corpus, scope, tmp_path))

    assert HEADER_NOTICE in markdown
    assert markdown.index(HEADER_NOTICE) < 200, "the notice must precede any finding"
    assert "bearbeitet die Wikipedia nicht" in markdown


def test_dated_claims_reach_the_dossier(corpus: FetchResult, scope: ScopeConfig, tmp_path: Path) -> None:
    dossier = build(corpus, scope, tmp_path)
    assert dossier.claims.claims, "the acceptance article has dated claims"

    markdown = render_markdown(dossier)
    assert "## Angaben zum Prüfen" in markdown
    for claim in dossier.claims.claims[:3]:
        assert str(claim.line_no) in markdown


def test_reference_age_is_reported(corpus: FetchResult, scope: ScopeConfig, tmp_path: Path) -> None:
    dossier = build(corpus, scope, tmp_path)
    markdown = render_markdown(dossier)

    assert "## Belege dieses Artikels" in markdown
    if dossier.claims.references.newest_year is not None:
        assert str(dossier.claims.references.newest_year) in markdown


def test_no_clock_is_read(corpus: FetchResult, scope: ScopeConfig, tmp_path: Path) -> None:
    """Ages are measured from the corpus reference date. A dossier that moved
    every week would make its own diff useless."""
    dossier = build(corpus, scope, tmp_path)
    markdown = render_markdown(dossier)

    assert corpus.reference_date.isoformat() in markdown
    assert dossier.reference_date == corpus.reference_date


def test_rendering_is_byte_identical_across_runs(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """The replay gate: same cache in, same bytes out."""
    first = build(corpus, scope, tmp_path)
    second = build(corpus, scope, tmp_path)

    assert render_markdown(first) == render_markdown(second)
    assert render_json(first) == render_json(second)


def test_json_carries_everything_the_markdown_summarises(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    payload = json.loads(render_json(build(corpus, scope, tmp_path)))

    assert payload["title"] == ARTICLE
    assert payload["claims"]["claims"]
    assert "reference_date" in payload
    assert payload["edit_url"].startswith("https://de.wikipedia.org/w/index.php")


def test_a_wikidata_delta_reaches_the_table(corpus: FetchResult, scope: ScopeConfig, tmp_path: Path) -> None:
    """`Küsnachter Dorfbach` has no mapped infobox field, so this drives the
    renderer directly rather than pretending the fixture has one."""
    from wp_todo.models import Delta

    dossier = build(corpus, scope, tmp_path).model_copy(
        update={
            "deltas": (
                Delta(
                    kind="wikidata",
                    label="Einwohnerzahl",
                    field="EINWOHNER",
                    article_value="8500",
                    external_value="9240",
                    article_as_of=2018,
                    external_as_of=2025,
                    source="https://www.wikidata.org/wiki/Q68166#P1082",
                    agrees=False,
                ),
            )
        }
    )
    markdown = render_markdown(dossier)

    assert "## Abweichungen gegenüber Wikidata" in markdown
    assert "8500 (Stand 2018)" in markdown
    assert "9240 (Stand 2025)" in markdown
    assert "https://www.wikidata.org/wiki/Q68166#P1082" in markdown
    assert "nicht automatisch im Recht" in markdown, "Wikidata is often the wrong one"


def test_an_empty_comparison_says_so_rather_than_going_quiet(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """A section that ran and found nothing must be distinguishable from a
    section that never ran.

    This test used to assert "_Keine Abweichungen gefunden._" here, which was
    the bug: the build helper never runs the comparison, so the honest answer
    is that it was not checked.
    """
    markdown = render_markdown(build(corpus, scope, tmp_path))

    assert "Nicht abgeglichen" in markdown
    assert "_Keine Abweichungen gefunden._" not in markdown


class TestWikidataEmptyStates:
    """Three different empty sections, three different sentences.

    For five live runs all three rendered as "keine Abweichungen gefunden" -
    "no differences found" - while our own robots gate was refusing the
    request. The dossier was asserting something false about the article.
    """

    def test_no_wikidata_item_says_so(self, corpus: FetchResult, scope: ScopeConfig, tmp_path: Path) -> None:
        dossier = build(corpus, scope, tmp_path).model_copy(
            update={"wikidata_item": None, "wikidata_checked": False}
        )
        assert "kein Wikidata-Objekt" in render_markdown(dossier)

    def test_a_failed_lookup_says_so_and_warns_against_reading_it_as_agreement(
        self, corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
    ) -> None:
        dossier = build(corpus, scope, tmp_path).model_copy(
            update={"wikidata_item": "Q42", "wikidata_checked": False}
        )
        markdown = render_markdown(dossier)

        assert "konnten nicht" in markdown
        assert "Q42" in markdown
        assert "kein** Hinweis darauf, dass alles stimmt" in markdown
        assert "_Keine Abweichungen gefunden._" not in markdown

    def test_a_real_comparison_with_no_differences_says_that_instead(
        self, corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
    ) -> None:
        dossier = build(corpus, scope, tmp_path).model_copy(
            update={"wikidata_item": "Q42", "wikidata_checked": True, "wikidata_comparable": 2}
        )
        markdown = render_markdown(dossier)

        assert "_Keine Abweichungen gefunden._" in markdown
        assert "Nicht abgeglichen" not in markdown
        assert "Nichts zu vergleichen" not in markdown

    def test_nothing_comparable_is_not_the_same_as_nothing_wrong(
        self, corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
    ) -> None:
        """Four of the first five live articles had no mapped infobox field, so
        nothing was compared - and the dossier said "keine Abweichungen
        gefunden", implying a comparison that never happened. Naming the
        infobox also makes the gap in PROPERTY_FOR_FIELD visible."""
        dossier = build(corpus, scope, tmp_path).model_copy(
            update={"wikidata_item": "Q42", "wikidata_checked": True, "wikidata_comparable": 0}
        )
        markdown = render_markdown(dossier)

        assert "Nichts zu vergleichen" in markdown
        assert "Infobox Fluss" in markdown, "say which infobox is unmapped"
        assert "_Keine Abweichungen gefunden._" not in markdown


class TestAgreementIsNotCurrency:
    """Adliswil's area agreed with a Wikidata figure from 2007, and the dossier
    called it "vermutlich aktuell". Two sources can agree because both are
    stale; saying otherwise turns a non-finding into false comfort."""

    @staticmethod
    def agreeing(external_as_of: int) -> Any:
        from wp_todo.models import Delta

        return Delta(
            kind="wikidata",
            label="Fläche",
            field="FLÄCHE",
            article_value="7.79",
            external_value="7.79",
            external_as_of=external_as_of,
            source="https://www.wikidata.org/wiki/Q68210#P2046",
            agrees=True,
        )

    def test_a_recent_agreement_is_reported_as_probably_current(
        self, corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
    ) -> None:
        dossier = build(corpus, scope, tmp_path).model_copy(
            update={"wikidata_checked": True, "wikidata_comparable": 1, "deltas": (self.agreeing(2025),)}
        )
        assert "vermutlich aktuell" in render_markdown(dossier)

    def test_agreement_with_an_old_figure_says_so_instead(
        self, corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
    ) -> None:
        dossier = build(corpus, scope, tmp_path).model_copy(
            update={"wikidata_checked": True, "wikidata_comparable": 1, "deltas": (self.agreeing(2007),)}
        )
        markdown = render_markdown(dossier)

        assert "vermutlich aktuell" not in markdown
        assert "beide können gemeinsam veraltet sein" in markdown


def test_not_looked_reads_differently_from_looked_and_found_nothing(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """The distinction the whole design rests on. A dossier that renders "we
    did not check" the same way as "we checked and it is fine" is lying by
    omission, and an editor would have no way to tell."""
    unchecked = build(corpus, scope, tmp_path)
    assert unchecked.interwiki_checked is False
    assert "_Nicht abgefragt._" in render_markdown(unchecked)

    checked = unchecked.model_copy(update={"interwiki_checked": True})
    assert "_Keine anderssprachige Fassung verlinkt._" in render_markdown(checked)

    with_links = checked.model_copy(update={"compared_languages": ("en", "fr")})
    assert "_Keine zusätzlichen Abschnitte in en, frwiki._" in render_markdown(with_links)


def test_a_pipe_in_a_claim_does_not_break_the_table(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    from wp_todo.models import Claim

    dossier = build(corpus, scope, tmp_path)
    claims = dossier.claims.model_copy(
        update={
            "claims": (
                Claim(
                    id="x-1",
                    kind="infobox_field",
                    text="WEBSITE = [[a|b]] | c",
                    line_no=3,
                    field="WEBSITE",
                ),
            )
        }
    )
    markdown = render_markdown(dossier.model_copy(update={"claims": claims}))

    row = next(line for line in markdown.splitlines() if "WEBSITE" in line and line.startswith("|"))
    separators = len(re.findall(r"(?<!\\)\|", row))
    assert separators == 5, f"escaped pipes must not add columns: {row}"
    assert r"[[a\|b]]" in row, "the pipe inside the value survives, escaped"


class TestSlug:
    def test_umlauts_are_transliterated_not_dropped(self) -> None:
        assert slug("Küsnachter Dorfbach") == "kuesnachter-dorfbach"

    def test_distinct_titles_do_not_collide(self) -> None:
        assert slug("Küsnacht") != slug("Ksnacht")

    def test_a_title_of_only_punctuation_still_yields_a_name(self) -> None:
        assert slug("!!!") == "artikel"


# ------------------------------------------------------------ source standing
def _ledger(domain: str, verdict: str, reason: str) -> SourceLedger:
    return SourceLedger(verdicts=(make_verdict(domain, verdict, reason, dt.date(2026, 3, 14)),))


def test_the_articles_own_sources_are_classified(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """Free to compute, and it answers what the reference count cannot: an
    article resting entirely on unrated hosts is a different problem from one
    resting on the federal statistics office."""
    dossier = build(corpus, scope, tmp_path)
    assert dossier.reference_standing, "the acceptance article cites external sources"

    markdown = render_markdown(dossier)
    assert "## Einstufung der zitierten Quellen" in markdown
    assert dossier.reference_standing[0].host in markdown


def test_unrated_is_not_presented_as_a_criticism(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """Most of the web is unrated. Saying so must not read as an accusation."""
    markdown = render_markdown(build(corpus, scope, tmp_path))
    assert "kein Urteil" in markdown
    assert "nicht, dass mit ihr etwas nicht stimmt" in markdown


def test_official_sources_sort_above_unrated_ones(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    tiers = [s.tier for s in build(corpus, scope, tmp_path).reference_standing]
    assert tiers == sorted(tiers, key=["official", "press_academic", "unrated"].index)


def test_a_blocked_host_moves_to_the_exclusions_with_its_reason(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """The rule the whole design rests on: something missing because of a
    decision must say which decision, or the dossier is lying by omission."""
    plain = build(corpus, scope, tmp_path)
    host = next(s.host for s in plain.reference_standing if s.tier == "unrated")

    blocked = build(corpus, scope, tmp_path, ledger=_ledger(host, "block", "Datendump von 2015"))
    markdown = render_markdown(blocked)

    assert "## Ausgeschlossene Quellen" in markdown
    assert "Datendump von 2015" in markdown
    assert "2026-03-14" in markdown

    table = markdown[markdown.index("## Einstufung") : markdown.index("## Ausgeschlossene")]
    assert f"`{host}`" not in table, "a blocked host belongs in the exclusions, not the table"


def test_the_exclusions_section_is_absent_when_nothing_was_dropped(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """An empty heading would imply something was considered and kept."""
    assert "## Ausgeschlossene Quellen" not in render_markdown(build(corpus, scope, tmp_path))


def test_a_note_travels_with_the_source(corpus: FetchResult, scope: ScopeConfig, tmp_path: Path) -> None:
    """This is the verdict that makes the checking compound: a source can be
    fine for one thing and useless for another."""
    plain = build(corpus, scope, tmp_path)
    host = next(s.host for s in plain.reference_standing if s.tier == "unrated")

    noted = build(corpus, scope, tmp_path, ledger=_ledger(host, "note", "gut für Öffnungszeiten"))
    markdown = render_markdown(noted)

    assert "gut für Öffnungszeiten" in markdown
    assert "## Ausgeschlossene Quellen" not in markdown, "a note is not an exclusion"


def test_standing_survives_the_json_round_trip(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    plain = build(corpus, scope, tmp_path)
    host = next(s.host for s in plain.reference_standing if s.tier == "unrated")
    payload = json.loads(render_json(build(corpus, scope, tmp_path, ledger=_ledger(host, "block", "x"))))

    entry = next(s for s in payload["reference_standing"] if s["host"] == host)
    assert entry["verdict"] == "block"
    assert entry["reason"] == "x"
    assert entry["decided"] == "2026-03-14"


# ------------------------------------------------------- when the agent dies
class BrokenLlm(LlmClient):
    """An `LlmClient` whose network hop fails the way run #14 failed.

    A real `BadRequestError` needs the SDK; the behaviour under test is that
    *any* exception out of the stage is survivable, so the exception class is
    deliberately not the interesting part.
    """

    def __init__(self, exc: Exception, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._exc = exc

    def _send(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._exc


def broken(tmp_path: Path, exc: Exception) -> BrokenLlm:
    return BrokenLlm(exc, cache=ResponseCache(tmp_path / "llm"), budget=LlmBudget(limit=10))


def test_a_failed_agent_does_not_take_the_dossier_with_it(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """Run #14 spent nine requests on Wikimedia, then lost all of it to a 400
    from a different service. The deterministic half is already paid for."""
    dossier = build(
        corpus,
        scope,
        tmp_path,
        llm=broken(tmp_path, RuntimeError("Error code: 400 - anthropic-workspace-id is required")),
    )

    assert dossier.claims.claims, "the deterministic half survived intact"
    assert dossier.agent is not None
    assert "400" in dossier.agent.failed
    assert not dossier.findings


def test_a_failed_agent_says_so_where_the_findings_would_have_been(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """ "Nothing found" and "the stage crashed before it looked" are not the
    same sentence, and only one of them is true here. This is the same lesson
    as the Wikidata clean-bill-of-health bug, one layer out."""
    markdown = render_markdown(build(corpus, scope, tmp_path, llm=broken(tmp_path, RuntimeError("kaputt"))))

    findings = markdown.split("## ")[1]
    assert "abgebrochen" in findings.lower()
    assert "kaputt" in findings
    assert "**nicht** stattgefunden" in findings
    assert "Nichts gefunden" not in findings


def test_an_unusable_agent_still_stops_the_run(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """A missing package or absent credentials is the operator's to fix before
    anything else, and keeps its clean exit rather than becoming a footnote."""
    with pytest.raises(LlmUnavailableError):
        build(corpus, scope, tmp_path, llm=broken(tmp_path, LlmUnavailableError("no credentials")))


# ------------------------------------------------- what the dossier says it did
def briefing(**agent: Any) -> Dossier:
    """A dossier carrying only what the agent reported, for the rendering."""
    from wp_todo.models import AgentRun, ArticleClaims

    return Dossier(
        pageid=1,
        title="Musterwil",
        reference_date=dt.date(2026, 8, 1),
        claims=ArticleClaims(pageid=1, title="Musterwil"),
        agent=AgentRun(model="claude-opus-5", effort="medium", **agent),
    )


def test_each_reason_a_claim_went_unreported_gets_its_own_line(tmp_path: Path) -> None:
    """Three different facts used to print as one sentence, and for a claim
    whose answer a gate refused that sentence was the opposite of the truth."""
    from wp_todo.models import ClaimOutcome

    markdown = render_markdown(
        briefing(
            outcomes=(
                ClaimOutcome(claim_id="a1", outcome="nothing_found", phase="web"),
                ClaimOutcome(claim_id="b2", outcome="dropped", phase="reference"),
                ClaimOutcome(claim_id="c3", outcome="budget"),
            ),
            budget_exhausted=True,
        )
    )

    assert "keine der gelesenen Quellen sagte etwas dazu): `a1`" in markdown
    assert "von den Prüfungen verworfen" in markdown and "`b2`" in markdown
    assert "Budget war aufgebraucht): `c3`" in markdown
    # And the refused answer is not swept in with the honest "nothing found".
    nothing = next(line for line in markdown.splitlines() if "keine der gelesenen" in line)
    assert "b2" not in nothing


def test_an_older_dossier_still_renders_its_coarser_sentence(tmp_path: Path) -> None:
    """`outcomes` did not exist when the committed dossiers were written. The
    old sentence is the only honest thing left to say about them."""
    markdown = render_markdown(briefing(unexamined=("a1",), budget_exhausted=False))
    assert "keine Quelle sagte etwas dazu): `a1`" in markdown


def test_laut_quelle_is_only_written_when_the_quote_carries_the_figure(tmp_path: Path) -> None:
    """The Horgen case: "mindestens 31 Titel" printed under a quote that
    contained no number at all."""
    from wp_todo.models import AgentRun, ArticleClaims, Finding

    def rendered(*, supports: bool) -> str:
        return render_markdown(
            Dossier(
                pageid=1,
                title="Musterwil",
                reference_date=dt.date(2026, 8, 1),
                claims=ArticleClaims(pageid=1, title="Musterwil"),
                agent=AgentRun(model="claude-opus-5", effort="medium"),
                findings=(
                    Finding(
                        claim_id="a1",
                        claim_text="30-facher Meister",
                        status="supersedes_with_newer_value",
                        current_value="mindestens 31 Titel",
                        quote="Die Durststrecke hat ein Ende",
                        url="https://example.org/x",
                        quote_supports_value=supports,
                    ),
                ),
            )
        )

    assert "**Laut Quelle:**" in rendered(supports=True)
    grounded = rendered(supports=False)
    assert "**Laut Quelle:**" not in grounded
    assert "Schluss des Modells (nicht im Zitat)" in grounded


def test_a_summary_the_budget_refused_is_said_out_loud(tmp_path: Path) -> None:
    """The headings are still listed above it, so silence there reads as
    "nothing worth saying about them"."""
    from wp_todo.models import Delta

    gap = Delta(
        kind="interwiki_section",
        label="enwiki",
        external_value="Economy",
        source="https://en.wikipedia.org/wiki/Musterwil",
        detail="1 Abschnitt(e) ohne Entsprechung hier",
    )
    listed = briefing(sections_skipped=True, budget_exhausted=True).model_copy(
        update={"deltas": (gap,), "interwiki_checked": True, "compared_languages": ("en",)}
    )
    markdown = render_markdown(listed)

    assert "Economy" in markdown, "the heading is listed"
    assert "keine Zusammenfassung erstellt" in markdown, "and its missing summary is explained"


# --------------------------------------------------------- reachability
def with_links(**kwargs: Any) -> Dossier:
    from wp_todo.models import ArticleClaims

    return Dossier(
        pageid=1,
        title="Musterwil",
        reference_date=dt.date(2026, 8, 1),
        claims=ArticleClaims(pageid=1, title="Musterwil"),
        **kwargs,
    )


def test_a_check_that_never_ran_renders_nothing_at_all(tmp_path: Path) -> None:
    """The same distinction `wikidata_checked` draws: an absent section must
    never be read as "checked, and everything resolves"."""
    assert "Erreichbarkeit" not in render_markdown(with_links())


def test_a_blocked_host_is_not_presented_to_the_reader_as_dead(tmp_path: Path) -> None:
    """The rendering half of the rule the checker enforces. An editor skimming
    this table must not come away thinking a 403 is a dead link."""
    from wp_todo.models import LinkStatus, LinkSummary

    markdown = render_markdown(
        with_links(
            link_summary=LinkSummary(total=1, checked=1, blocked=1),
            links=(
                LinkStatus(
                    url="https://zeitung.example/x",
                    verdict="gesperrt",
                    status=403,
                    detail="der Host hat die Anfrage abgelehnt, die Seite kann trotzdem existieren",
                ),
            ),
        )
    )

    assert "**gesperrt**" in markdown
    assert "0 tot" in markdown
    assert "im Browser oft trotzdem da" in markdown, "the glossary spells out what it is not"


def test_the_snapshot_is_offered_as_something_to_check_not_as_wikitext(tmp_path: Path) -> None:
    """`docs/research-policy.md`: the tool does not draft article text. A
    ready-to-paste {{Webarchiv}} is exactly what invites pasting it without
    opening the snapshot first."""
    from wp_todo.models import LinkStatus, LinkSummary

    markdown = render_markdown(
        with_links(
            link_summary=LinkSummary(total=1, checked=1, dead=1),
            links=(
                LinkStatus(
                    url="https://amt.example/weg",
                    verdict="tot",
                    status=404,
                    snapshot_url="http://web.archive.org/web/20190302/https://amt.example/weg",
                    snapshot_date="2019-03-02",
                ),
            ),
        )
    )

    assert "2019-03-02" in markdown
    assert "selbst prüfen" in markdown
    assert "{{Webarchiv" not in markdown, "no article text is drafted, here or anywhere"


def test_an_interrupted_check_says_so_rather_than_looking_complete(tmp_path: Path) -> None:
    from wp_todo.models import LinkStatus, LinkSummary

    markdown = render_markdown(
        with_links(
            link_summary=LinkSummary(total=9, checked=2, dead=1, budget_exhausted=True),
            links=(
                LinkStatus(url="https://amt.example/weg", verdict="tot", status=404),
                LinkStatus(
                    url="https://amt.example/spaeter",
                    verdict="nicht geprüft",
                    detail="das Anfrage-Budget war aufgebraucht",
                ),
            ),
        )
    )

    assert "2 von 9 Link(s) geprüft" in markdown
    assert "Anfrage-Budget war aufgebraucht" in markdown
    assert "**kein** Hinweis" in markdown


def test_the_section_says_which_dead_links_it_cannot_see(tmp_path: Path) -> None:
    """A reader who believes the list is exhaustive trusts `erreichbar` further
    than it has earned."""
    from wp_todo.models import LinkStatus, LinkSummary

    markdown = render_markdown(
        with_links(
            link_summary=LinkSummary(total=1, checked=1, reachable=1),
            links=(LinkStatus(url="https://amt.example/ok", verdict="erreichbar", status=200),),
        )
    )

    assert "wird **nicht** erkannt" in markdown, "the soft-404 blind spot is named"
    assert "Alle geprüften Links lösen auf." in markdown


def test_the_link_check_runs_in_the_real_pipeline(
    corpus: FetchResult, scope: ScopeConfig, tmp_path: Path
) -> None:
    """End to end through `research_article`, with no model anywhere: the
    reachability section is the part of a dossier that costs nothing and works
    without `--agent`."""
    built = build(corpus, scope, tmp_path)

    assert built.link_summary is not None
    assert built.link_summary.total == len(built.claims.references.external_urls) + len(
        built.claims.references.linked_urls
    )
    assert built.link_summary.checked == built.link_summary.total
    assert "## Erreichbarkeit der Belege" in render_markdown(built)


# ------------------------------------------------------ the other editions
def interwiki_finding(**kwargs: Any) -> Dossier:
    from wp_todo.models import AgentRun, ArticleClaims, Finding

    defaults: dict[str, Any] = {
        "claim_id": "a1",
        "claim_text": "FLÄCHE = 30.84",
        "status": "supersedes_with_newer_value",
        "current_value": "31.2",
        "as_of": 2024,
        "quote": "| area_total_km2 = 31.2",
        "url": "https://en.wikipedia.org/wiki/Musterwil",
        "standing": "andere Sprachversion dieses Artikels - kein Beleg, sondern ein Hinweis",
        "interwiki_lang": "en",
    }
    return Dossier(
        pageid=1,
        title="Musterwil",
        reference_date=dt.date(2026, 8, 1),
        claims=ArticleClaims(pageid=1, title="Musterwil"),
        agent=AgentRun(model="claude-opus-5", effort="medium"),
        findings=(Finding(**{**defaults, **kwargs}),),
    )


def test_another_edition_is_never_presented_as_a_source(tmp_path: Path) -> None:
    """Said on the row, not only in the section notice: a reader arriving from
    a link or a screenshot has to be told before they read the number."""
    markdown = render_markdown(interwiki_finding())

    assert "**Kein Beleg:**" in markdown
    assert "ist selbst Wikipedia" in markdown
    assert "**Beleg:** <https://en.wikipedia.org" not in markdown, "it is a Fundstelle, not a Beleg"
    assert "**Laut Quelle:**" not in markdown, "the source is enwiki, and it says so"
    assert "**Laut enwiki:**" in markdown


def test_what_the_other_edition_cites_is_the_part_offered_as_useful(tmp_path: Path) -> None:
    """The whole point of the comparison: a citable document dewiki lacks."""
    markdown = render_markdown(interwiki_finding(cited_sources=("https://amt.example/flaeche.pdf",)))

    assert "**Dort zitiert**" in markdown
    assert "https://amt.example/flaeche.pdf" in markdown
    assert "ungeprüft" in markdown


def test_a_bot_imported_figure_is_not_offered_as_a_second_opinion(tmp_path: Path) -> None:
    markdown = render_markdown(interwiki_finding(matches_wikidata=True))

    assert "**Nicht unabhängig:**" in markdown
    assert "keine zweite Bestätigung" in markdown
