"""Source standing: tiering, recorded verdicts, and circularity.

The module's whole justification is that a blocklist's costs are visible where
an allowlist's are not. That only holds if the blocklist blocks what it says it
blocks and nothing else, so most of what follows is about the edges of matching
rather than the happy path.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from wp_todo.sources import (
    SourceLedger,
    SourceVerdictError,
    circularity,
    cited_hosts,
    format_entry,
    host_matches,
    host_of,
    load_ledger,
    make_verdict,
    standing,
)

DECIDED = dt.date(2026, 3, 14)


def ledger_of(*domains: tuple[str, str]) -> SourceLedger:
    return SourceLedger(verdicts=tuple(make_verdict(d, k, "Grund", DECIDED) for d, k in domains))


# ------------------------------------------------------------------ matching
class TestHostMatching:
    """A blocklist that over-matches blocks the wrong sites, and nobody notices
    because the evidence is what is *missing* from the dossier."""

    @pytest.mark.parametrize(
        ("pattern", "host"),
        [
            ("example.ch", "example.ch"),
            ("example.ch", "www.example.ch"),
            ("example.ch", "a.b.example.ch"),
            ("admin.ch", "bfs.admin.ch"),
            ("Example.CH", "WWW.EXAMPLE.CH"),
            ("example.ch.", "example.ch"),
        ],
    )
    def test_matches(self, pattern: str, host: str) -> None:
        assert host_matches(pattern, host) is True

    @pytest.mark.parametrize(
        ("pattern", "host"),
        [
            ("example.ch", "notexample.ch"),
            ("example.ch", "myexample.ch"),
            ("example.ch", "example.ch.evil.example"),
            ("example.ch", "example.chx"),
            ("example.ch", ""),
            ("", "example.ch"),
        ],
    )
    def test_does_not_match(self, pattern: str, host: str) -> None:
        assert host_matches(pattern, host) is False

    def test_the_dangerous_one(self) -> None:
        """`example.ch.evil.example` is a hostile lookalike. Plain `endswith`
        would let it through a trust entry and past a block entry alike."""
        assert host_matches("example.ch", "example.ch.evil.example") is False


class TestHostOf:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.Example.CH/a?b=1", "www.example.ch"),
            ("http://user:pw@host.example:8080/x", "host.example"),
            ("www.adliswil.ch", "www.adliswil.ch"),
            ("https://example.ch./x", "example.ch"),
            ("", ""),
        ],
    )
    def test_hosts(self, url: str, expected: str) -> None:
        assert host_of(url) == expected


# ------------------------------------------------------------------ standing
class TestStanding:
    def test_official_suffixes_tier(self) -> None:
        assert standing("https://www.bfs.admin.ch/x", ledger=SourceLedger()).tier == "official"

    def test_press_suffixes_tier(self) -> None:
        assert standing("https://www.nzz.ch/x", ledger=SourceLedger()).tier == "press_academic"

    def test_unknown_is_unrated_not_condemned(self) -> None:
        found = standing("https://irgendwas.example/x", ledger=SourceLedger())
        assert found.tier == "unrated"
        assert found.blocked is False

    def test_the_articles_own_website_is_a_signal(self) -> None:
        """Free evidence: the infobox names it, so no list has to."""
        found = standing(
            "https://www.adliswil.ch/verwaltung",
            ledger=SourceLedger(),
            article_official="www.adliswil.ch",
        )
        assert "article_official" in found.signals

    def test_a_domain_the_article_already_cites_is_a_signal(self) -> None:
        found = standing(
            "https://geschichtsverein.example/x",
            ledger=SourceLedger(),
            cited_hosts=cited_hosts(("https://geschichtsverein.example/y",)),
        )
        assert "already_cited" in found.signals

    def test_tier_and_signals_are_separate_axes(self) -> None:
        """A domain can be unrated by suffix and still be one the article cites.
        Collapsing these into one enum would lose that."""
        found = standing(
            "https://irgendwas.example/x",
            ledger=SourceLedger(),
            cited_hosts=cited_hosts(("https://irgendwas.example/y",)),
        )
        assert found.tier == "unrated"
        assert found.signals == ("already_cited",)

    def test_a_block_is_reported_on_the_standing(self) -> None:
        found = standing("https://beispiel.example/x", ledger=ledger_of(("beispiel.example", "block")))
        assert found.blocked is True
        assert found.verdict is not None
        assert found.verdict.reason == "Grund"

    def test_trust_sorts_above_an_equal_tier(self) -> None:
        trusted = standing("https://a.example/x", ledger=ledger_of(("a.example", "trust")))
        plain = standing("https://b.example/x", ledger=SourceLedger())
        assert trusted.sort_key < plain.sort_key

    def test_official_sorts_above_unrated_even_when_trusted(self) -> None:
        """Tier dominates. A trusted blog is still a blog."""
        official = standing("https://bfs.admin.ch/x", ledger=SourceLedger())
        trusted_blog = standing("https://blog.example/x", ledger=ledger_of(("blog.example", "trust")))
        assert official.sort_key < trusted_blog.sort_key

    def test_the_most_specific_verdict_wins_not_the_first(self) -> None:
        """Otherwise file order would decide, and appending would change meaning."""
        ledger = ledger_of(("beispiel.example", "block"), ("daten.beispiel.example", "trust"))
        assert standing("https://daten.beispiel.example/x", ledger=ledger).blocked is False
        assert standing("https://www.beispiel.example/x", ledger=ledger).blocked is True


# ------------------------------------------------------------------- verdicts
class TestVerdicts:
    def test_a_reason_is_required(self) -> None:
        """The NPOV mitigation: 'unreliable' and 'I disagree with it' are easy
        to conflate, and only a written reason tells them apart later."""
        with pytest.raises(SourceVerdictError, match="reason is required"):
            make_verdict("beispiel.example", "block", "   ", DECIDED)

    def test_a_url_is_not_a_domain(self) -> None:
        with pytest.raises(SourceVerdictError, match="not a domain"):
            make_verdict("https://beispiel.example/x", "block", "Grund", DECIDED)

    def test_an_unknown_verdict_is_refused(self) -> None:
        with pytest.raises(SourceVerdictError, match="unknown verdict"):
            make_verdict("beispiel.example", "banish", "Grund", DECIDED)

    def test_a_missing_file_is_an_empty_ledger_not_an_error(self, tmp_path: Path) -> None:
        assert load_ledger(tmp_path / "nope.toml").verdicts == ()

    def test_round_trip_through_the_file(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.toml"
        path.write_text("# a hand-written comment\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(format_entry(make_verdict("beispiel.example", "note", 'mit "Zitat"', DECIDED)))

        loaded = load_ledger(path)
        assert len(loaded.verdicts) == 1
        assert loaded.verdicts[0].domain == "beispiel.example"
        assert loaded.verdicts[0].reason == 'mit "Zitat"'
        assert loaded.verdicts[0].decided == DECIDED
        assert "# a hand-written comment" in path.read_text(encoding="utf-8")

    def test_appending_preserves_earlier_entries_and_comments(self, tmp_path: Path) -> None:
        """The CLI appends rather than rewrites precisely so this holds."""
        path = tmp_path / "sources.toml"
        path.write_text("# keep me\n", encoding="utf-8")
        for domain in ("eins.example", "zwei.example"):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(format_entry(make_verdict(domain, "block", "Grund", DECIDED)))

        text = path.read_text(encoding="utf-8")
        assert "# keep me" in text
        assert {v.domain for v in load_ledger(path).verdicts} == {"eins.example", "zwei.example"}

    def test_an_entry_without_a_reason_is_rejected_on_load(self, tmp_path: Path) -> None:
        """A hand-edited file gets the same rule as the CLI."""
        path = tmp_path / "sources.toml"
        path.write_text(
            '[[source]]\ndomain = "x.example"\nverdict = "block"\ndecided = "2026-03-14"\n',
            encoding="utf-8",
        )
        with pytest.raises(SourceVerdictError, match="reason"):
            load_ledger(path)

    def test_of_kind_filters(self) -> None:
        ledger = ledger_of(("a.example", "block"), ("b.example", "trust"))
        assert [v.domain for v in ledger.of_kind("trust")] == ["b.example"]


# --------------------------------------------------------------- circularity
ARTICLE = (
    "Die Gemeinde '''Adliswil''' liegt im Bezirk Horgen des Kantons Zürich und "
    "zählt rund 8500 Einwohner auf einer Fläche von 7.79 Quadratkilometern. "
    "Der Ort wird im Jahr 1225 erstmals urkundlich erwähnt und gehörte lange "
    "zum Kloster Sankt Blasien im Schwarzwald."
)


class TestCircularity:
    """The one failure a human check will not catch, because the text looks
    perfect - it is the article's own text."""

    def test_a_verbatim_copy_is_caught(self) -> None:
        mirror = "Adliswil\n\n" + ARTICLE.replace("'''", "") + "\n\nKategorien: Gemeinde"
        assert circularity(mirror, ARTICLE, span=150) is not None

    def test_an_unrelated_document_is_not(self) -> None:
        other = "Die Stadt meldet für 2025 einen Bestand von 9240 Personen. " * 8
        assert circularity(other, ARTICLE, span=150) is None

    def test_a_known_mirror_domain_is_a_fast_path(self) -> None:
        assert circularity("egal", ARTICLE, host="www.wikiwand.com", mirror_domains=("wikiwand.com",))

    def test_attribution_to_wikipedia_is_enough(self) -> None:
        text = "Quelle: Wikipedia, lizenziert unter CC BY-SA. " + "Anderer Text. " * 40
        assert circularity(text, ARTICLE, span=150) is not None

    def test_markup_does_not_hide_the_overlap(self) -> None:
        """The mirror renders the article; we hold the wikitext. Comparing them
        raw would miss every copy that strips markup - which is all of them."""
        wikitext = (
            "Die '''Gemeinde''' [[Adliswil]] liegt im [[Bezirk Horgen]] des Kantons "
            "Zürich<ref>{{Literatur |Titel=X |Datum=2020}}</ref> und zählt rund 8500 "
            "Einwohner auf einer Fläche von 7.79 Quadratkilometern."
        )
        rendered = (
            "Die Gemeinde Adliswil liegt im Bezirk Horgen des Kantons Zürich und "
            "zählt rund 8500 Einwohner auf einer Fläche von 7.79 Quadratkilometern."
        )
        assert circularity(rendered, wikitext, span=100) is not None

    def test_a_short_shared_phrase_is_not_circularity(self) -> None:
        """Two sources describing the same place will share phrases. Only a long
        verbatim run means one was copied from the other."""
        quoting = "Laut Gemeinde liegt Adliswil im Bezirk Horgen. " + "Eigener Text. " * 40
        assert circularity(quoting, ARTICLE, span=200) is None

    def test_an_empty_document_is_not_circular(self) -> None:
        assert circularity("", ARTICLE) is None

    def test_trust_does_not_override_circularity(self) -> None:
        """A trusted mirror is still a mirror.

        `circularity` takes no ledger, so this cannot be bypassed by a verdict
        even in principle - which is the point. The test exists so a later
        refactor cannot quietly wire the two together: trusting a mirror is
        always an error, no matter who records the trust.
        """
        mirror_text = ARTICLE.replace("'''", "")
        ledger = ledger_of(("spiegel.example", "trust"))

        found = standing("https://spiegel.example/x", ledger=ledger)
        assert found.verdict is not None and found.verdict.verdict == "trust"

        assert circularity(mirror_text, ARTICLE, host="spiegel.example", span=150) is not None
