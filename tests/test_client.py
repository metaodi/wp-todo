"""Client behaviour that exists because of something the live API actually does.

No network: every response here comes from an httpx MockTransport.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from wp_todo.cache import ResponseCache, cache_key
from wp_todo.client import (
    OfflineCacheMissError,
    ReadOnlyViolationError,
    WikiClient,
)
from wp_todo.config import MetaConfig


def make_client(meta: MetaConfig, tmp_path: Path, handler: Any, **kwargs: Any) -> WikiClient:
    return WikiClient(
        meta=meta,
        cache=ResponseCache(tmp_path / "http"),
        delay_s=0.0,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_user_agent_carries_contact(meta: MetaConfig, tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": []}})

    with make_client(meta, tmp_path, handler) as client:
        list(client.query(meta="siteinfo"))
    assert "wp-todo-tests/" in seen[0]
    assert "https://github.com/metaodi/wp-todo" in seen[0]


def test_write_actions_are_refused(meta: MetaConfig, tmp_path: Path) -> None:
    """The core promise of the project, enforced in code."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("a write action must never reach the network")

    with make_client(meta, tmp_path, handler) as client, pytest.raises(ReadOnlyViolationError):
        list(client.query(action="edit", title="Thalwil"))


def test_maxlag_rejection_is_retried_despite_http_200(meta: MetaConfig, tmp_path: Path) -> None:
    """Verified live: maxlag comes back as HTTP 200 with an error in the body
    and no Retry-After. Branching on the status code alone would read it as a
    successful empty result."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                200,
                json={"error": {"code": "maxlag", "info": "Waiting for 10.64.0.50: 1.01 seconds lagged."}},
            )
        return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": [{"pageid": 1}]}})

    with make_client(meta, tmp_path, handler, max_retries=3) as client:
        client.delay_s = 0.0
        batches = list(client.query(titles="Thalwil"))
    assert len(calls) == 2
    assert batches[0]["query"]["pages"] == [{"pageid": 1}]


def test_api_error_is_raised_and_not_cached(meta: MetaConfig, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"error": {"code": "invalidparammix", "info": "rvlimit may only be used on a single page"}},
        )

    from wp_todo.client import WikiApiError

    with make_client(meta, tmp_path, handler) as client, pytest.raises(WikiApiError, match="invalidparammix"):
        list(client.query(titles="A|B", rvlimit=50))
    assert not list((tmp_path / "http").glob("*.json"))


def test_continuation_merges_pages_by_id(meta: MetaConfig, tmp_path: Path) -> None:
    """Continuation re-sends the same pages carrying more of their properties.

    Appending would duplicate the article and truncate its categories.
    """
    pages_batch_1 = [
        {"pageid": 10, "title": "Thalwil", "categories": [{"title": "Kategorie:A"}]},
        {"pageid": 11, "title": "Horgen", "categories": [{"title": "Kategorie:B"}]},
    ]
    pages_batch_2 = [
        {"pageid": 10, "title": "Thalwil", "categories": [{"title": "Kategorie:C"}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if "clcontinue" in request.url.params:
            return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": pages_batch_2}})
        return httpx.Response(
            200,
            json={"continue": {"clcontinue": "10|C", "continue": "||"}, "query": {"pages": pages_batch_1}},
        )

    with make_client(meta, tmp_path, handler) as client:
        merged = client.query_pages(titles="Thalwil|Horgen", prop="categories")

    assert sorted(merged) == [10, 11]
    assert [c["title"] for c in merged[10]["categories"]] == ["Kategorie:A", "Kategorie:C"]
    assert merged[10]["title"] == "Thalwil"


def test_continuation_loop_is_broken(meta: MetaConfig, tmp_path: Path) -> None:
    """A server that keeps handing back the same token must not hang the run."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"continue": {"clcontinue": "same", "continue": "||"}, "query": {"pages": []}}
        )

    with make_client(meta, tmp_path, handler) as client:
        batches = list(client.query(titles="Thalwil"))
    assert len(batches) == 2


def test_cache_hit_avoids_a_second_request(meta: MetaConfig, tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": []}})

    with make_client(meta, tmp_path, handler) as client:
        list(client.query(titles="Thalwil"))
        list(client.query(titles="Thalwil"))
    assert len(calls) == 1
    assert client.stats.cache_hits == 1


def test_offline_client_raises_instead_of_requesting(meta: MetaConfig, tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("offline client must not reach the network")

    client = WikiClient(
        meta=meta,
        cache=ResponseCache(tmp_path / "http"),
        offline=True,
        transport=httpx.MockTransport(handler),
    )
    with client, pytest.raises(OfflineCacheMissError):
        list(client.query(titles="Thalwil"))


def test_missing_pageviews_is_empty_not_an_error(meta: MetaConfig, tmp_path: Path) -> None:
    """Verified live: an article with no data 404s with a JSON body."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"status": 404, "title": "Not Found", "detail": "no data"})

    with make_client(meta, tmp_path, handler) as client:
        views = client.pageviews(
            "Nonexistent", access="all-access", agent="user", start="20250101", end="20251201"
        )
    assert views == []


def test_pageview_titles_are_encoded(meta: MetaConfig, tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"items": [{"timestamp": "2025010100", "views": 5}]})

    with make_client(meta, tmp_path, handler) as client:
        client.pageviews("Bezirk Horgen", access="all-access", agent="user", start="20250101", end="20251201")
        client.pageviews("Wädenswil", access="all-access", agent="user", start="20250101", end="20251201")
    assert "Bezirk_Horgen" in seen[0]
    assert "W%C3%A4denswil" in seen[1]


def test_cache_key_is_stable_and_parameter_sensitive() -> None:
    a = cache_key("GET", "https://example.org/api", {"b": 2, "a": 1})
    b = cache_key("GET", "https://example.org/api", {"a": 1, "b": 2})
    c = cache_key("GET", "https://example.org/api", {"a": 1, "b": 3})
    assert a == b
    assert a != c


def test_corrupt_cache_entry_is_a_miss(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    key = "deadbeef"
    cache.put(key, {"ok": True})
    cache.path_for(key).write_text("{not json", encoding="utf-8")
    assert cache.get(key) is None


def test_cache_roundtrip(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    cache.put("k", {"a": [1, 2], "b": "ä"})
    assert cache.get("k") == {"a": [1, 2], "b": "ä"}
    assert json.loads(cache.path_for("k").read_text(encoding="utf-8"))["b"] == "ä"
