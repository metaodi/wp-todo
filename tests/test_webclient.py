"""WebClient behaviour, all of it enforced rather than promised.

No network: every response here comes from an httpx MockTransport. The point of
most of these tests is that a rule the module claims in prose - GET only,
robots honoured, size capped, budget enforced - actually fails closed.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx
import pytest

from wp_todo._http import OfflineCacheMissError, RequestBudgetExceededError
from wp_todo.cache import ResponseCache
from wp_todo.config import MetaConfig
from wp_todo.webclient import WebClient, extract_text

REFERENCE = dt.date(2026, 8, 1)


def make_client(meta: MetaConfig, tmp_path: Path, handler: Any, **kwargs: Any) -> WebClient:
    return WebClient(
        meta=meta,
        cache=ResponseCache(tmp_path / "web"),
        delay_s=0.0,
        transport=httpx.MockTransport(handler),
        reference_date=REFERENCE,
        **kwargs,
    )


def html_response(body: str, content_type: str = "text/html; charset=utf-8") -> httpx.Response:
    return httpx.Response(200, content=body.encode("utf-8"), headers={"Content-Type": content_type})


def test_user_agent_is_distinct_from_the_wikimedia_one(meta: MetaConfig, tmp_path: Path) -> None:
    """Claiming the Wikimedia UA at a cantonal website would be a lie about who
    is calling. The research client identifies itself separately."""
    client = make_client(meta, tmp_path, lambda request: html_response("<p>hi</p>"))
    assert "-research/" in client.user_agent
    assert meta.contact in client.user_agent


def test_robots_disallow_stops_the_fetch(meta: MetaConfig, tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return html_response("User-agent: *\nDisallow: /geheim/", "text/plain")
        return html_response("<p>should never be read</p>")

    with make_client(meta, tmp_path, handler) as client:
        assert client.fetch("https://example.org/geheim/seite") is None

    assert seen == ["/robots.txt"]


def test_robots_allow_lets_the_fetch_through(meta: MetaConfig, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return html_response("User-agent: *\nDisallow: /geheim/", "text/plain")
        return html_response("<p>Einwohnerzahl: 9240</p>")

    with make_client(meta, tmp_path, handler) as client:
        document = client.fetch("https://example.org/statistik")

    assert document is not None
    assert "Einwohnerzahl: 9240" in document.text


def test_missing_robots_is_not_a_prohibition(meta: MetaConfig, tmp_path: Path) -> None:
    """An absent or unreachable robots.txt means the host has no rules on
    record, not that everything is forbidden."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return html_response("<p>frei</p>")

    with make_client(meta, tmp_path, handler) as client:
        assert client.fetch("https://example.org/seite") is not None


def test_robots_is_fetched_once_per_host(meta: MetaConfig, tmp_path: Path) -> None:
    robots_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal robots_requests
        if request.url.path == "/robots.txt":
            robots_requests += 1
            return html_response("User-agent: *\nAllow: /", "text/plain")
        return html_response("<p>seite</p>")

    with make_client(meta, tmp_path, handler) as client:
        client.fetch("https://example.org/eins")
        client.fetch("https://example.org/zwei")

    assert robots_requests == 1


def test_oversized_response_is_truncated_not_buffered(meta: MetaConfig, tmp_path: Path) -> None:
    body = "<p>" + ("x" * 50_000) + "</p>"

    with make_client(meta, tmp_path, lambda r: html_response(body), max_bytes=1_000) as client:
        document = client.fetch("https://example.org/gross")

    assert document is not None
    assert document.truncated is True
    assert len(document.text) <= 1_000


def test_unexpected_content_type_is_skipped(meta: MetaConfig, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x00\x01\x02", headers={"Content-Type": "image/png"})

    with make_client(meta, tmp_path, handler, respect_robots=False) as client:
        assert client.fetch("https://example.org/bild.png") is None


def test_client_error_is_skipped_and_the_skip_is_cached(meta: MetaConfig, tmp_path: Path) -> None:
    """Re-asking a host for a 404 on every rerun would be the rude thing."""
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(404)

    with make_client(meta, tmp_path, handler, respect_robots=False) as client:
        assert client.fetch("https://example.org/weg") is None
        assert client.fetch("https://example.org/weg") is None

    assert requests == 1


def test_cache_hit_makes_no_request(meta: MetaConfig, tmp_path: Path) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return html_response("<p>einmal</p>")

    with make_client(meta, tmp_path, handler, respect_robots=False) as client:
        first = client.fetch("https://example.org/seite")
        second = client.fetch("https://example.org/seite")

    assert requests == 1
    assert first is not None and second is not None
    assert first.text == second.text


def test_offline_raises_instead_of_reaching_the_network(meta: MetaConfig, tmp_path: Path) -> None:
    """This is what keeps the test suite honest: a missing fixture is an error,
    never a silent live request."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("offline client must not make a request")

    with (
        make_client(meta, tmp_path, handler, offline=True) as client,
        pytest.raises(OfflineCacheMissError),
    ):
        client.fetch("https://example.org/seite")


def test_budget_stops_the_run(meta: MetaConfig, tmp_path: Path) -> None:
    with make_client(
        meta, tmp_path, lambda r: html_response("<p>x</p>"), respect_robots=False, max_requests=2
    ) as client:
        client.fetch("https://example.org/eins")
        client.fetch("https://example.org/zwei")
        with pytest.raises(RequestBudgetExceededError):
            client.fetch("https://example.org/drei")


def test_server_error_is_retried_then_succeeds(meta: MetaConfig, tmp_path: Path) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return html_response("<p>endlich</p>")

    with make_client(meta, tmp_path, handler, respect_robots=False) as client:
        document = client.fetch("https://example.org/wackelig")

    assert attempts == 2
    assert document is not None
    assert "endlich" in document.text


def test_hosts_are_paced_independently(meta: MetaConfig, tmp_path: Path) -> None:
    """One host's politeness delay is not an allowance to hammer the next."""
    client = WebClient(meta=meta, cache=ResponseCache(tmp_path / "web"), delay_s=5.0)
    first = client._pacer_for("https://a.example/eins")
    again = client._pacer_for("https://a.example/zwei")
    other = client._pacer_for("https://b.example/eins")

    assert first is again
    assert first is not other


def test_json_get_parses_the_body(meta: MetaConfig, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"id": "Q42"}', headers={"Content-Type": "application/json"})

    with make_client(meta, tmp_path, handler, respect_robots=False) as client:
        assert client.get_json("https://www.wikidata.org/w/rest.php/wikibase/v1/x") == {"id": "Q42"}


def test_fetched_on_uses_the_reference_date_not_the_clock(meta: MetaConfig, tmp_path: Path) -> None:
    """A retrieval date that moved every run would churn every dossier diff."""
    with make_client(meta, tmp_path, lambda r: html_response("<p>x</p>"), respect_robots=False) as c:
        document = c.fetch("https://example.org/seite")

    assert document is not None
    assert document.fetched_on == REFERENCE


class TestExtractText:
    def test_drops_scripts_and_styles(self) -> None:
        raw = "<style>p{color:red}</style><p>sichtbar</p><script>var x=1</script>"
        assert extract_text(raw) == "sichtbar"

    def test_unescapes_entities(self) -> None:
        assert extract_text("<p>Z&uuml;rich &amp; Umgebung</p>") == "Zürich & Umgebung"

    def test_collapses_non_breaking_spaces(self) -> None:
        assert extract_text("<p>9\xa0240 Einwohner</p>") == "9 240 Einwohner"

    def test_block_ends_become_line_breaks(self) -> None:
        assert extract_text("<li>eins</li><li>zwei</li>") == "eins\nzwei"

    def test_keeps_navigation_rather_than_guessing_at_main_content(self) -> None:
        """Dropping the chrome risks dropping the sentence a quote needs to be
        checked against; a noisy document is the safer failure."""
        raw = "<nav>Startseite</nav><p>Die Zahl betrug 9240.</p>"
        assert "Startseite" in extract_text(raw)
        assert "Die Zahl betrug 9240." in extract_text(raw)
