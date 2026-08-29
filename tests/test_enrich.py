"""Comparison against Wikidata and other language editions.

The Wikibase REST payloads here follow the documented v1 shape. That shape has
NOT been confirmed against the live service from this sandbox - there is no
egress - so `docs/api-notes.md` carries it as an open item, and these tests
pin our handling of it rather than claiming it is right. The parsing is written
to return nothing on a shape it does not recognise, so a wrong guess produces
an empty section, never an invented delta.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from wp_todo.cache import ResponseCache
from wp_todo.claims import extract_claims
from wp_todo.client import WikiClient
from wp_todo.config import MetaConfig, ScopeConfig, load_scope
from wp_todo.enrich import _heading_key, _same, langlinks, wikidata_deltas
from wp_todo.models import Article
from wp_todo.webclient import WebClient

REFERENCE = dt.date(2026, 8, 1)


@pytest.fixture
def config() -> ScopeConfig:
    return load_scope(Path("config/scope.toml"))


def statement(value: Any, *, rank: str = "normal", point_in_time: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "rank": rank,
        "value": {"type": "value", "content": value},
        "qualifiers": [],
    }
    if point_in_time:
        entry["qualifiers"] = [
            {
                "property": {"id": "P585", "data_type": "time"},
                "value": {"type": "value", "content": {"time": f"+{point_in_time}T00:00:00Z"}},
            }
        ]
    return entry


def web_for(payload: dict[str, Any], tmp_path: Path, meta: MetaConfig) -> WebClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    return WebClient(
        meta=meta,
        cache=ResponseCache(tmp_path / "web"),
        delay_s=0.0,
        respect_robots=False,
        transport=httpx.MockTransport(handler),
        reference_date=REFERENCE,
    )


def claims_for(wikitext: str, config: ScopeConfig) -> Any:
    return extract_claims(
        Article(pageid=1, title="Testort", scope_label="Test", wikitext=wikitext), config, REFERENCE
    )


POPULATION_ARTICLE = (
    "{{Infobox Ort in der Schweiz\n| EINWOHNER = 8500\n| STAND_EINWOHNER = 31. Dezember 2018\n}}\n"
)


def test_a_newer_wikidata_value_becomes_a_delta(
    config: ScopeConfig, tmp_path: Path, meta: MetaConfig
) -> None:
    payload = {"P1082": [statement({"amount": "+9240", "unit": "1"}, point_in_time="2025-12-31")]}
    with web_for(payload, tmp_path, meta) as web:
        deltas, _ = wikidata_deltas(claims_for(POPULATION_ARTICLE, config), "Q68166", web)

    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.article_value == "8500"
    assert delta.external_value == "9240"
    assert delta.article_as_of == 2018
    assert delta.external_as_of == 2025
    assert delta.agrees is False
    assert delta.source == "https://www.wikidata.org/wiki/Q68166#P1082"


def test_agreement_is_reported_too(config: ScopeConfig, tmp_path: Path, meta: MetaConfig) -> None:
    """That a figure has already been checked is worth an editor's time."""
    payload = {"P1082": [statement({"amount": "+8500", "unit": "1"}, point_in_time="2018-12-31")]}
    with web_for(payload, tmp_path, meta) as web:
        deltas, _ = wikidata_deltas(claims_for(POPULATION_ARTICLE, config), "Q1", web)

    assert deltas[0].agrees is True


def test_the_newest_statement_wins(config: ScopeConfig, tmp_path: Path, meta: MetaConfig) -> None:
    """Wikidata holds a decade of population figures; the useful one is current."""
    payload = {
        "P1082": [
            statement({"amount": "+8100", "unit": "1"}, point_in_time="2016-12-31"),
            statement({"amount": "+9240", "unit": "1"}, point_in_time="2025-12-31"),
            statement({"amount": "+8700", "unit": "1"}, point_in_time="2020-12-31"),
        ]
    }
    with web_for(payload, tmp_path, meta) as web:
        deltas, _ = wikidata_deltas(claims_for(POPULATION_ARTICLE, config), "Q1", web)

    assert deltas[0].external_value == "9240"


def test_preferred_rank_beats_a_newer_normal_one(
    config: ScopeConfig, tmp_path: Path, meta: MetaConfig
) -> None:
    payload = {
        "P1082": [
            statement({"amount": "+9240", "unit": "1"}, point_in_time="2025-12-31"),
            statement({"amount": "+9100", "unit": "1"}, rank="preferred", point_in_time="2024-12-31"),
        ]
    }
    with web_for(payload, tmp_path, meta) as web:
        deltas, _ = wikidata_deltas(claims_for(POPULATION_ARTICLE, config), "Q1", web)

    assert deltas[0].external_value == "9100"


def test_deprecated_statements_are_ignored(config: ScopeConfig, tmp_path: Path, meta: MetaConfig) -> None:
    payload = {
        "P1082": [
            statement({"amount": "+99999", "unit": "1"}, rank="deprecated", point_in_time="2026-01-01"),
            statement({"amount": "+9240", "unit": "1"}, point_in_time="2025-12-31"),
        ]
    }
    with web_for(payload, tmp_path, meta) as web:
        deltas, _ = wikidata_deltas(claims_for(POPULATION_ARTICLE, config), "Q1", web)

    assert deltas[0].external_value == "9240"


def test_somevalue_produces_no_delta(config: ScopeConfig, tmp_path: Path, meta: MetaConfig) -> None:
    """ "Some value, unknown which" is not something to show an editor."""
    payload = {"P1082": [{"rank": "normal", "value": {"type": "somevalue"}, "qualifiers": []}]}
    with web_for(payload, tmp_path, meta) as web:
        # checked=True: the statements were retrieved, they just say nothing
        # comparable. That is a real "nothing to report", unlike a failed fetch.
        assert wikidata_deltas(claims_for(POPULATION_ARTICLE, config), "Q1", web) == ((), True)


def test_an_unrecognised_payload_shape_yields_nothing_rather_than_a_guess(
    config: ScopeConfig, tmp_path: Path, meta: MetaConfig
) -> None:
    """The REST shape is unverified from this sandbox. If it is not what we
    expect, the right outcome is an empty section, not an invented figure."""
    payload = {"P1082": [{"mainsnak": {"datavalue": {"value": {"amount": "+9240"}}}}]}
    with web_for(payload, tmp_path, meta) as web:
        assert wikidata_deltas(claims_for(POPULATION_ARTICLE, config), "Q1", web) == ((), True)


def test_no_item_id_means_no_requests(config: ScopeConfig, tmp_path: Path, meta: MetaConfig) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not fetch without an item id")

    client = WebClient(
        meta=meta,
        cache=ResponseCache(tmp_path / "web"),
        delay_s=0.0,
        transport=httpx.MockTransport(handler),
    )
    assert wikidata_deltas(claims_for(POPULATION_ARTICLE, config), None, client) == ((), False)


def test_unmapped_fields_are_not_compared(config: ScopeConfig, tmp_path: Path, meta: MetaConfig) -> None:
    """Only properties somebody has checked by hand are mapped. An unmapped
    field is silently skipped rather than matched against a plausible guess."""
    article = "{{Infobox Ort in der Schweiz\n| ARBEITSLOSE = 3,1 % (2019)\n}}\n"
    with web_for({"P1082": [statement({"amount": "+9240", "unit": "1"})]}, tmp_path, meta) as web:
        assert wikidata_deltas(claims_for(article, config), "Q1", web) == ((), True)


class TestValueComparison:
    """`7.79`, `7,79` and `7.79 km²` are one area written three ways."""

    @pytest.mark.parametrize(
        ("article_value", "external_value"),
        [
            ("7.79", "7.79"),
            ("7,79", "7.79"),
            ("7.79 km²", "7.79"),
            ("8'500", "8500"),
            ("8\u2019500", "8500"),
            ("www.adliswil.ch", "https://www.adliswil.ch/"),
            ("[[FDP.Die Liberalen|FDP]]", "fdp"),
        ],
    )
    def test_equivalent_values_agree(self, article_value: str, external_value: str) -> None:
        assert _same(article_value, external_value) is True

    @pytest.mark.parametrize(
        ("article_value", "external_value"),
        [("8500", "9240"), ("7.79", "8.12"), ("www.a.ch", "www.b.ch"), (None, "9240")],
    )
    def test_different_values_disagree(self, article_value: str | None, external_value: str) -> None:
        assert _same(article_value, external_value) is False


class TestLanglinks:
    """`lllang` is not multi-valued - verified live by probe P10.

    `lllang=en|fr|it` is accepted without a warning and returns nothing, so the
    bug renders as "no other-language version linked" on every article: an
    answer rather than an error, and therefore invisible. These tests pin the
    request shape, not just the parsing.
    """

    @staticmethod
    def wiki(tmp_path: Path, meta: MetaConfig, payload: dict[str, Any], seen: list[httpx.URL]) -> WikiClient:
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url)
            return httpx.Response(200, json=payload)

        return WikiClient(
            meta=meta,
            cache=ResponseCache(tmp_path / "http"),
            delay_s=0.0,
            transport=httpx.MockTransport(handler),
        )

    def test_lllang_is_never_sent(self, tmp_path: Path, meta: MetaConfig) -> None:
        """The regression that matters: sending it at all loses every link."""
        seen: list[httpx.URL] = []
        payload = {
            "batchcomplete": True,
            "query": {"pages": [{"pageid": 1, "title": "Thalwil", "langlinks": []}]},
        }
        with self.wiki(tmp_path, meta, payload, seen) as client:
            langlinks(client, ["Thalwil"])

        assert seen, "a request should have been made"
        assert all("lllang" not in str(url) for url in seen), f"lllang must not be sent: {seen}"
        assert any("lllimit=max" in str(url) for url in seen)

    def test_only_the_wanted_languages_survive(self, tmp_path: Path, meta: MetaConfig) -> None:
        """Filtering moved client-side, so it has to actually filter."""
        seen: list[httpx.URL] = []
        payload = {
            "batchcomplete": True,
            "query": {
                "pages": [
                    {
                        "pageid": 1,
                        "title": "Thalwil",
                        "langlinks": [
                            {"lang": "en", "title": "Thalwil"},
                            {"lang": "fr", "title": "Thalwil (FR)"},
                            {"lang": "ceb", "title": "Thalwil (CEB)"},
                            {"lang": "azb", "title": "Thalwil (AZB)"},
                        ],
                    }
                ]
            },
        }
        with self.wiki(tmp_path, meta, payload, seen) as client:
            found = langlinks(client, ["Thalwil"], ("en", "fr", "it"))

        assert found["Thalwil"] == {"en": "Thalwil", "fr": "Thalwil (FR)"}

    def test_an_article_with_no_other_editions_yields_an_empty_map(
        self, tmp_path: Path, meta: MetaConfig
    ) -> None:
        seen: list[httpx.URL] = []
        payload = {"batchcomplete": True, "query": {"pages": [{"pageid": 1, "title": "Nur DE"}]}}
        with self.wiki(tmp_path, meta, payload, seen) as client:
            assert langlinks(client, ["Nur DE"]) == {}


class TestWikidataReachability:
    """The Wikidata comparison silently returned nothing for five live runs.

    Our own robots gate was refusing the request - Wikimedia's robots.txt
    carries `Disallow: /w/`, and the Wikibase REST API lives at `/w/rest.php`.
    The dossier reported "keine Abweichungen gefunden", which was false.
    """

    def test_the_wikibase_rest_path_is_not_treated_as_a_page_to_crawl(
        self, tmp_path: Path, meta: MetaConfig
    ) -> None:
        """robots.txt governs crawling a site, not calling its published API."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /w/\n")
            return httpx.Response(
                200,
                content=json.dumps(
                    {"P1082": [statement({"amount": "+9240", "unit": "1"}, point_in_time="2025-12-31")]}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )

        with WebClient(
            meta=meta,
            cache=ResponseCache(tmp_path / "web"),
            delay_s=0.0,
            respect_robots=True,
            transport=httpx.MockTransport(handler),
            reference_date=REFERENCE,
        ) as web:
            deltas, checked = wikidata_deltas(claims_for(POPULATION_ARTICLE, config_for()), "Q1", web)

        assert checked is True, "the API call must not be blocked by robots.txt"
        assert deltas and deltas[0].external_value == "9240"
        assert "/robots.txt" not in seen, "an API endpoint needs no crawl-exclusion check"

    def test_an_ordinary_page_still_honours_robots(self, tmp_path: Path, meta: MetaConfig) -> None:
        """The exemption is for named API prefixes only, not a general opt-out."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /w/\n")
            raise AssertionError("robots.txt disallows this path")

        with WebClient(
            meta=meta,
            cache=ResponseCache(tmp_path / "web"),
            delay_s=0.0,
            respect_robots=True,
            transport=httpx.MockTransport(handler),
            reference_date=REFERENCE,
        ) as web:
            assert web.fetch("https://beispiel.example/w/irgendwas") is None


def config_for() -> ScopeConfig:
    return load_scope(Path("config/scope.toml"))


class TestHeadingEquivalence:
    """Section gaps are compared across languages, so headings must be too.

    Before this, Adliswil's dossier listed 25 "missing" sections of which about
    twenty were present under a translated name. A checklist of gaps that are
    not gaps is worse than no checklist: somebody has to disprove every line.
    """

    @pytest.mark.parametrize(
        ("german", "foreign"),
        [
            ("Geschichte", "History"),
            ("Geschichte", "Histoire"),
            ("Geschichte", "Storia"),
            ("Verkehr", "Transportation"),
            ("Bevölkerung", "Demographics"),
            ("Bevölkerung", "Evoluzione demografica"),
            ("Wirtschaft", "Économie"),
            ("Persönlichkeiten", "Notable people"),
            ("Wappen und Fahne", "Héraldique"),
            ("Sehenswürdigkeiten", "Attractions"),
            ("Kirchen und Tempel", "Religion"),
            ("Geographie", "Geografia fisica"),
        ],
    )
    def test_translations_are_recognised_as_the_same_section(self, german: str, foreign: str) -> None:
        assert _heading_key(german) == _heading_key(foreign)

    def test_genuinely_different_sections_stay_different(self) -> None:
        """The map must not collapse everything into one bucket."""
        keys = {_heading_key(h) for h in ("Geschichte", "Verkehr", "Wirtschaft", "Bevölkerung")}
        assert len(keys) == 4

    def test_an_unmapped_heading_still_matches_itself(self) -> None:
        """The map is a shortlist, not a requirement. Anything not in it falls
        back to its own normalised form, so it is reported only when genuinely
        absent."""
        assert _heading_key("Vereine") == _heading_key("vereine")
        assert _heading_key("Höhlenforschung") != _heading_key("Geschichte")

    def test_a_real_gap_survives_the_map(self) -> None:
        """`Education` is a section dewiki's Adliswil really does lack."""
        german = {_heading_key(h) for h in ("Geographie", "Geschichte", "Verkehr", "Wirtschaft")}
        assert _heading_key("Education") not in german
