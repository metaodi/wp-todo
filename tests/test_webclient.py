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


def test_offline_document_fetch_raises_instead_of_reaching_the_network(
    meta: MetaConfig, tmp_path: Path
) -> None:
    """This is what keeps the test suite honest: a missing fixture is an error,
    never a silent live request.

    `respect_robots=False` matters here. There are two offline checks - one on
    the robots path and one on the document path - and with robots on, this
    test passes off the robots one even if the document one is deleted. Pinning
    the document path is the whole point.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("offline client must not make a request")

    with (
        make_client(meta, tmp_path, handler, offline=True, respect_robots=False) as client,
        pytest.raises(OfflineCacheMissError),
    ):
        client.fetch("https://example.org/seite")


def test_offline_robots_lookup_raises_too(meta: MetaConfig, tmp_path: Path) -> None:
    """The other half: robots.txt is a request like any other."""

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


# --------------------------------------------------------------------- PDF
def minimal_pdf(text: str) -> bytes:
    """A real, valid PDF with one text-bearing page.

    Built by hand rather than mocked: the point of the test is that the
    shipping extractor reads a genuine file, and a fixture that is not really
    a PDF would prove nothing about that.
    """
    stream = f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 300]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>stream\n" % len(stream) + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, start)
    return bytes(out)


def test_a_pdf_reference_is_read_rather_than_skipped(meta: MetaConfig, tmp_path: Path) -> None:
    """The largest recall hole this scope had: the statistical offices publish
    PDF, and the reference behind a dated figure was fetched, filtered out on
    its content type, and never mentioned."""
    body = minimal_pdf("Wohnbevoelkerung 9240 Personen")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"Content-Type": "application/pdf"})

    with make_client(meta, tmp_path, handler, respect_robots=False) as client:
        document = client.fetch("https://statistik.example/zahlen.pdf")

    assert document is not None, "a PDF reference must not be silently skipped"
    assert "Wohnbevoelkerung 9240 Personen" in document.text
    assert document.content_type == "application/pdf"


def test_the_extracted_pdf_text_is_what_a_replay_gets(meta: MetaConfig, tmp_path: Path) -> None:
    """The quote gate checks against the stored text, so the extraction is
    cached rather than recomputed - if it could drift between runs the check
    would be worth nothing."""
    body = minimal_pdf("Wohnbevoelkerung 9240 Personen")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=body, headers={"Content-Type": "application/pdf"})

    cache = ResponseCache(tmp_path / "web")
    for _ in range(2):
        with WebClient(
            meta=meta,
            cache=cache,
            delay_s=0.0,
            respect_robots=False,
            transport=httpx.MockTransport(handler),
            reference_date=REFERENCE,
        ) as client:
            document = client.fetch("https://statistik.example/zahlen.pdf")
            assert document is not None
            assert "9240" in document.text

    assert len(calls) == 1, "the second run replayed the stored extraction"


def test_a_pdf_with_no_text_layer_says_which_problem_it_is(meta: MetaConfig, tmp_path: Path) -> None:
    """ "No text in the PDF" sends a reader to the document; "we cannot read
    PDF" sends them to the install instructions. Not the same message."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"%PDF-1.4\nnot really a pdf", headers={"Content-Type": "application/pdf"}
        )

    url = "https://statistik.example/scan.pdf"
    with make_client(meta, tmp_path, handler, respect_robots=False) as client:
        assert client.fetch(url) is None
        assert "PDF" in client.skips[url]


# ------------------------------------------------------------ skips, reported
def test_the_reason_a_url_was_skipped_is_kept(meta: MetaConfig, tmp_path: Path) -> None:
    """A skip is a normal outcome. A silent one is not: the dossier reports
    every document it could not read, and this is where it learns why."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("weg"):
            return httpx.Response(404)
        return html_response("<p>ein Bild</p>", "image/png")

    with make_client(meta, tmp_path, handler, respect_robots=False) as client:
        assert client.fetch("https://example.org/weg") is None
        assert client.fetch("https://example.org/bild") is None

    assert client.skips["https://example.org/weg"] == "HTTP 404"
    assert "image/png" in client.skips["https://example.org/bild"]


def test_a_replayed_skip_still_says_why(meta: MetaConfig, tmp_path: Path) -> None:
    """A dossier built from the cache has to say the same thing as the run that
    paid for it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    cache = ResponseCache(tmp_path / "web")
    url = "https://example.org/weg"
    with make_client(meta, tmp_path, handler, respect_robots=False) as client:
        assert client.fetch(url) is None

    with WebClient(
        meta=meta,
        cache=cache,
        delay_s=0.0,
        respect_robots=False,
        offline=True,
        reference_date=REFERENCE,
    ) as replay:
        assert replay.fetch(url) is None
        assert replay.skips[url] == "HTTP 404"
