"""The link check, and the ways it is allowed to be wrong.

Every test here exists because the alternative is a checker that reports a live
link as dead. An editor acting on that replaces a working reference with an
archive copy, which makes the article worse - so the tests that matter most are
the ones asserting what the checker refuses to conclude.

No network: every response comes from an httpx MockTransport.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx
import pytest

from wp_todo.cache import ResponseCache
from wp_todo.config import MetaConfig, ResearchConfig
from wp_todo.links import check_links
from wp_todo.models import ArticleClaims, ReferenceSummary
from wp_todo.webclient import WebClient

REFERENCE = dt.date(2026, 8, 1)
LIVE = "https://amt.example/zahlen"
GONE = "https://amt.example/weg"


@pytest.fixture
def meta() -> MetaConfig:
    return MetaConfig(contact="mail@beispiel.ch")


def research(**kwargs: Any) -> ResearchConfig:
    return ResearchConfig(suggest_archives=False, **kwargs)


def article(*, cited: tuple[str, ...] = (GONE,), linked: tuple[str, ...] = ()) -> ArticleClaims:
    return ArticleClaims(
        pageid=1,
        title="Musterwil",
        references=ReferenceSummary(total=len(cited), external_urls=cited, linked_urls=linked),
    )


def client(meta: MetaConfig, tmp_path: Path, handler: Any, **kwargs: Any) -> WebClient:
    kwargs.setdefault("respect_robots", False)
    return WebClient(
        meta=meta,
        cache=ResponseCache(tmp_path / "web"),
        delay_s=0.0,
        transport=httpx.MockTransport(handler),
        reference_date=REFERENCE,
        **kwargs,
    )


def run(
    meta: MetaConfig,
    tmp_path: Path,
    handler: Any,
    *,
    claims: ArticleClaims | None = None,
    wikitext: str = "",
    config: ResearchConfig | None = None,
    **kwargs: Any,
) -> Any:
    with client(meta, tmp_path, handler, **kwargs) as web:
        return check_links(claims or article(), wikitext, web, config or research())


def status_handler(codes: dict[str, int]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(codes.get(str(request.url), 200), content=b"<p>hi</p>")

    return handler


# --------------------------------------------------------- what it concludes
def test_a_404_is_the_one_verdict_that_says_the_document_is_gone(meta: MetaConfig, tmp_path: Path) -> None:
    links, summary = run(meta, tmp_path, status_handler({GONE: 404}))

    assert [link.verdict for link in links] == ["tot"]
    assert summary.dead == 1
    assert summary.checked == 1


def test_a_403_is_never_reported_as_dead(meta: MetaConfig, tmp_path: Path) -> None:
    """The single most important test in this file.

    A host refusing *us* says nothing about whether the page is there. Report
    it as dead and an editor replaces a live reference with an archive copy -
    the exact damage bot-driven dead-link tagging has already done on dewiki.
    """
    links, summary = run(meta, tmp_path, status_handler({GONE: 403}))

    assert links[0].verdict == "gesperrt"
    assert summary.dead == 0, "a refusal is not a death"
    assert "kann trotzdem existieren" in links[0].detail


def test_a_server_error_that_recovers_on_retry_is_reachable(meta: MetaConfig, tmp_path: Path) -> None:
    attempts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(str(request.url))
        return httpx.Response(200 if len(attempts) > 1 else 503, content=b"<p>hi</p>")

    links, _ = run(meta, tmp_path, handler)
    assert links[0].verdict == "erreichbar"
    assert len(attempts) == 2


def test_a_server_error_that_never_recovers_is_unreachable_not_dead(meta: MetaConfig, tmp_path: Path) -> None:
    links, summary = run(meta, tmp_path, status_handler({GONE: 500}), max_retries=2)

    assert links[0].verdict == "nicht erreichbar"
    assert summary.dead == 0, "not reachable now is not the same as gone"


def test_a_redirect_to_the_homepage_is_reported_with_where_it_landed(
    meta: MetaConfig, tmp_path: Path
) -> None:
    """The quiet way a site says a page is gone: 200, but at the root."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, content=b"<p>Startseite</p>")
        return httpx.Response(302, headers={"Location": "https://amt.example/"})

    links, summary = run(meta, tmp_path, handler)
    assert links[0].verdict == "umgeleitet"
    assert links[0].final_url == "https://amt.example/"
    assert summary.dead == 0


def test_a_redirect_that_only_tidies_the_url_is_not_reported(meta: MetaConfig, tmp_path: Path) -> None:
    """A trailing slash or a www prefix is not a finding, and reporting it
    would bury the redirects that mean something."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://www."):
            return httpx.Response(200, content=b"<p>hi</p>")
        return httpx.Response(301, headers={"Location": "https://www.amt.example/zahlen"})

    links, _ = run(meta, tmp_path, handler, claims=article(cited=(LIVE,)))
    assert links[0].verdict == "erreichbar"


def test_a_robots_refusal_is_not_a_verdict_about_the_link(meta: MetaConfig, tmp_path: Path) -> None:
    """We did not look. That must never render as "checked and fine", and it
    must certainly never render as dead."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, content=b"User-agent: *\nDisallow: /", headers={"Content-Type": "text/plain"}
            )
        return httpx.Response(200, content=b"<p>should never be read</p>")

    with client(meta, tmp_path, handler, respect_robots=True) as web:
        links, summary = check_links(article(), "", web, research())

    assert links[0].verdict == "nicht geprüft"
    assert summary.dead == 0
    assert summary.checked == 0


# ------------------------------------------------------------------ requests
def test_a_document_already_fetched_costs_no_request(meta: MetaConfig, tmp_path: Path) -> None:
    """The agent stage and the link check share their work: a reference the
    agent already downloaded is a completed check."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"<p>hi</p>", headers={"Content-Type": "text/html"})

    cache = ResponseCache(tmp_path / "web")
    with client(meta, tmp_path, handler) as web:
        assert web.fetch(LIVE) is not None
    assert len(calls) == 1

    with WebClient(
        meta=meta,
        cache=cache,
        delay_s=0.0,
        respect_robots=False,
        transport=httpx.MockTransport(handler),
        reference_date=REFERENCE,
    ) as web:
        links, _ = check_links(article(cited=(LIVE,)), "", web, research())

    assert links[0].verdict == "erreichbar"
    assert len(calls) == 1, "the stored document answered it"


def test_a_rerun_makes_no_requests_and_says_the_same_thing(meta: MetaConfig, tmp_path: Path) -> None:
    """Rule 3: a replay is byte-identical, which is what makes a weekly diff
    mean something."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404)

    cache = ResponseCache(tmp_path / "web")
    seen = []
    for _ in range(2):
        with WebClient(
            meta=meta,
            cache=cache,
            delay_s=0.0,
            respect_robots=False,
            transport=httpx.MockTransport(handler),
            reference_date=REFERENCE,
        ) as web:
            links, _ = check_links(article(), "", web, research())
            seen.append([(link.url, link.verdict) for link in links])

    assert seen[0] == seen[1]
    assert len(calls) == 1, "the second run replayed the recorded status"


def test_the_request_ceiling_stops_the_check_and_keeps_the_dossier(meta: MetaConfig, tmp_path: Path) -> None:
    """The deterministic half is already paid for by the time this runs.
    Losing it to an optional extra would be the wrong trade - and the ones it
    never got to must not read as fine."""
    urls = tuple(f"https://amt.example/{n}" for n in range(4))
    links, summary = run(
        meta,
        tmp_path,
        status_handler({}),
        claims=article(cited=urls),
        max_requests=2,
    )

    assert summary.budget_exhausted
    assert summary.checked == 2
    unchecked = [link for link in links if link.verdict == "nicht geprüft"]
    assert len(unchecked) == 2
    assert all("Budget" in link.detail for link in unchecked)


def test_links_beyond_the_cap_are_named_rather_than_dropped(meta: MetaConfig, tmp_path: Path) -> None:
    urls = tuple(f"https://amt.example/{n}" for n in range(5))
    links, summary = run(
        meta,
        tmp_path,
        status_handler({}),
        claims=article(cited=urls),
        config=research(max_link_checks=2),
    )

    assert summary.total == 5
    assert summary.checked == 2
    assert len([link for link in links if link.verdict == "nicht geprüft"]) == 3


def test_the_check_can_be_turned_off_entirely(meta: MetaConfig, tmp_path: Path) -> None:
    """`max_link_checks = 0` is how you get back the run that asks nothing of
    anybody outside Wikimedia."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200)

    links, summary = run(meta, tmp_path, handler, config=research(max_link_checks=0))
    assert links == ()
    assert summary.total == 1 and summary.checked == 0
    assert calls == []


# ------------------------------------------------------------------- archive
def wayback(handler_status: int = 404, *, snapshot: bool = True) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if "archive.org" in str(request.url):
            payload: dict[str, Any] = {"archived_snapshots": {}}
            if snapshot:
                payload = {
                    "archived_snapshots": {
                        "closest": {
                            "available": True,
                            "url": "http://web.archive.org/web/20190302134501/https://amt.example/weg",
                            "timestamp": "20190302134501",
                        }
                    }
                }
            return httpx.Response(200, json=payload, headers={"Content-Type": "application/json"})
        return httpx.Response(handler_status)

    return handler


def test_a_dead_link_gets_a_snapshot_with_its_date(meta: MetaConfig, tmp_path: Path) -> None:
    links, _ = run(meta, tmp_path, wayback(), config=ResearchConfig())

    assert links[0].verdict == "tot"
    assert links[0].snapshot_url.startswith("http://web.archive.org/")
    assert links[0].snapshot_date == "2019-03-02"


def test_no_snapshot_is_invented_when_the_archive_has_none(meta: MetaConfig, tmp_path: Path) -> None:
    links, _ = run(meta, tmp_path, wayback(snapshot=False), config=ResearchConfig())

    assert links[0].verdict == "tot"
    assert links[0].snapshot_url == ""
    assert links[0].snapshot_date == ""


def test_a_live_link_is_never_looked_up_in_the_archive(meta: MetaConfig, tmp_path: Path) -> None:
    """A reachable link needs no snapshot, and asking would spend a request on
    a question nobody has."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"<p>hi</p>")

    run(meta, tmp_path, handler, claims=article(cited=(LIVE,)), config=ResearchConfig())
    assert not any("archive.org" in call for call in calls)


# ------------------------------------------------------- already handled work
def test_a_reference_the_article_already_archived_is_labelled_and_sorted_last(
    meta: MetaConfig, tmp_path: Path
) -> None:
    """The reader's question is what is *new* work. It is still checked, though
    - a marker goes stale and an archive link can itself be wrong."""
    other = "https://amt.example/auch-weg"
    wikitext = (
        f"Text.<ref>{{{{Internetquelle |url={GONE} "
        f"|archiv-url=https://web.archive.org/web/2019/{GONE} |offline=ja}}}}</ref>\n"
        f"Mehr.<ref>{other}</ref>\n"
    )
    links, summary = run(
        meta,
        tmp_path,
        status_handler({GONE: 404, other: 404}),
        claims=article(cited=(GONE, other)),
        wikitext=wikitext,
    )

    assert summary.dead == 2, "both are checked"
    assert [link.url for link in links] == [other, GONE], "unhandled work first"
    assert links[1].archived_in_article
    assert not links[0].archived_in_article
