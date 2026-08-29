"""The one place that talks to Wikimedia.

Every behaviour here is a consequence of something observed against the live
API and recorded in docs/api-notes.md:

* a maxlag rejection arrives as HTTP 200 with error.code == "maxlag" and no
  Retry-After header, so the response body is inspected on every request;
* generator continuation re-sends the same pages carrying more of their
  list-valued properties, so pages are merged by pageid rather than appended;
* batchcomplete, not the absence of results, signals a finished batch.

The client is read-only by construction: an action outside ALLOWED_ACTIONS
raises. Do not widen that set - see CLAUDE.md.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Self
from urllib.parse import quote

import httpx

from .cache import ResponseCache, cache_key
from .config import MetaConfig

log = logging.getLogger(__name__)

API_URL = "https://de.wikipedia.org/w/api.php"
PAGEVIEWS_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"

#: Read-only actions. Adding to this set would break the project's core promise.
ALLOWED_ACTIONS = frozenset({"query", "paraminfo"})

#: Verified live: the action API tops out at 500 for every list and prop module
#: we use (highmax 5000 needs apihighlimits, which we do not have).
MAX_LIMIT = 500

#: Titles per request for batched prop queries without apihighlimits.
TITLES_PER_REQUEST = 50


class WikiApiError(RuntimeError):
    """The API returned an error in the response body."""

    def __init__(self, code: str, info: str, params: dict[str, Any]) -> None:
        super().__init__(f"{code}: {info}")
        self.code = code
        self.info = info
        self.params = params


class ReadOnlyViolationError(RuntimeError):
    """Something tried to make a non-read request. This must never happen."""


class RequestBudgetExceededError(RuntimeError):
    """The run asked for more requests than it was budgeted."""


class OfflineCacheMissError(RuntimeError):
    """An offline client needed a request it has no recorded response for.

    The test suite runs the real pipeline this way: the recorded cache is the
    fixture set, so a miss means a fixture is missing, never a silent network
    call.
    """


@dataclass
class ClientStats:
    requests: int = 0
    cache_hits: int = 0
    retries: int = 0
    seconds_waiting: float = 0.0


@dataclass
class WikiClient:
    """Serial, polite, cached access to the de.wikipedia API."""

    meta: MetaConfig
    cache: ResponseCache
    #: Minimum gap between the start of one request and the next. Requests are
    #: serialised, so this is the hard ceiling on our request rate.
    delay_s: float = 1.0
    max_retries: int = 4
    maxlag: int = 5
    timeout_s: float = 60.0
    #: Hard ceiling for one run. 0 disables it.
    max_requests: int = 0
    progress_every: int = 250
    dry_run: bool = False
    #: Refuse to touch the network; a cache miss is an error. Used by the tests.
    offline: bool = False
    #: Injectable transport, so the retry and continuation logic can be tested
    #: without a network.
    transport: httpx.BaseTransport | None = None
    _client: httpx.Client | None = field(default=None, init=False, repr=False)
    stats: ClientStats = field(default_factory=ClientStats)
    _last_request_at: float = field(default=0.0, init=False, repr=False)

    @property
    def user_agent(self) -> str:
        from . import __version__

        return f"{self.meta.user_agent_product}/{__version__} ({self.meta.contact}) httpx/{httpx.__version__}"

    def __enter__(self) -> Self:
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            timeout=self.timeout_s,
            follow_redirects=True,
            transport=self.transport,
        )
        return self

    def __exit__(self, *exc: object) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------ core
    def _sleep_between_requests(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.delay_s - elapsed
        if remaining > 0:
            time.sleep(remaining)
            self.stats.seconds_waiting += remaining

    def _get_json(self, url: str, params: dict[str, Any], *, empty_on_404: bool = False) -> dict[str, Any]:
        """Every outbound request goes through here: one at a time, spaced by
        `delay_s`, retried with exponential backoff, counted against the budget.

        The pageviews endpoint is a different service on a different host, but it
        gets the same treatment - it is where most of the volume is, so it is the
        last place that should be allowed to hammer.
        """
        key = cache_key("GET", url, params)
        cached = self.cache.get(key)
        if cached is not None:
            self.stats.cache_hits += 1
            return cached

        if self.offline:
            raise OfflineCacheMissError(f"no recorded response for {url} {sorted(params.items())}")

        if self.dry_run:
            log.info("dry-run: would GET %s %s", url, sorted(params))
            return {}

        if self._client is None:
            raise RuntimeError("WikiClient must be used as a context manager")

        backoff = 1.0
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            self._check_budget()
            self._sleep_between_requests()
            self._last_request_at = time.monotonic()
            self.stats.requests += 1
            self._log_progress()
            try:
                response = self._client.get(url, params=params)
            except httpx.HTTPError as exc:  # network-level failure
                last_error = exc
                log.warning("request failed (%s/%s): %s", attempt, self.max_retries, exc)
            else:
                if empty_on_404 and response.status_code == 404:
                    # Verified live: a title with no pageview data 404s with a
                    # JSON body. Ordinary missing data, not an error.
                    self.cache.put(key, {"items": []})
                    return {"items": []}
                retriable = self._retriable_reason(response)
                if retriable is None:
                    response.raise_for_status()
                    payload: dict[str, Any] = response.json()
                    if "error" not in payload:
                        self.cache.put(key, payload)
                    return payload
                last_error = WikiApiError(retriable, response.text[:200], params)
                log.warning("retriable response (%s/%s): %s", attempt, self.max_retries, retriable)
                backoff = max(backoff, self._retry_after(response))

            self.stats.retries += 1
            time.sleep(backoff)
            self.stats.seconds_waiting += backoff
            backoff *= 2

        raise RuntimeError(f"giving up on {url} after {self.max_retries} attempts") from last_error

    def _check_budget(self) -> None:
        """A scope change should not be able to turn into an unbounded crawl.

        Hitting the ceiling stops the run loudly rather than continuing to make
        requests nobody has budgeted for.
        """
        if self.max_requests and self.stats.requests >= self.max_requests:
            raise RequestBudgetExceededError(
                f"request budget of {self.max_requests} exhausted; raise http.max_requests "
                f"in scope.toml if this scope really needs more"
            )

    def _log_progress(self) -> None:
        if self.stats.requests % self.progress_every == 0:
            log.info(
                "%d requests (%d cache hits, %d retries, %.0fs spent waiting)",
                self.stats.requests,
                self.stats.cache_hits,
                self.stats.retries,
                self.stats.seconds_waiting,
            )

    def _retriable_reason(self, response: httpx.Response) -> str | None:
        """A maxlag rejection is HTTP 200 with an error in the body - verified live."""
        if response.status_code in (429, 500, 502, 503, 504):
            return f"http_{response.status_code}"
        try:
            body = response.json()
        except ValueError:
            return "non_json_body"
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict) and error.get("code") in {"maxlag", "readonly", "internal_api_error"}:
                return str(error.get("code"))
        return None

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return 1.0
        try:
            return max(1.0, float(raw))
        except ValueError:
            return 1.0

    def _raise_for_api_error(self, payload: dict[str, Any], params: dict[str, Any]) -> None:
        error = payload.get("error")
        if isinstance(error, dict):
            raise WikiApiError(str(error.get("code", "unknown")), str(error.get("info", "")), params)

    # ------------------------------------------------------------- action API
    def query(self, **params: Any) -> Iterator[dict[str, Any]]:
        """Yield every batch of an action=query request, following continuation.

        Continuation is handled here and nowhere else.
        """
        action = str(params.get("action", "query"))
        if action not in ALLOWED_ACTIONS:
            raise ReadOnlyViolationError(f"action={action!r} is not read-only")

        request = dict(params)
        request.setdefault("action", "query")
        request.setdefault("format", "json")
        request.setdefault("formatversion", "2")
        request["maxlag"] = self.maxlag

        seen_continuations: set[str] = set()
        while True:
            payload = self._get_json(API_URL, request)
            if not payload:  # dry run
                return
            self._raise_for_api_error(payload, request)
            for warning in _warnings(payload):
                log.warning("api warning: %s", warning)
            yield payload

            cont = payload.get("continue")
            if not isinstance(cont, dict) or not cont:
                return
            token = repr(sorted(cont.items()))
            if token in seen_continuations:
                log.error("continuation loop detected, stopping: %s", token)
                return
            seen_continuations.add(token)
            request = {**request, **cont}

    def query_pages(self, **params: Any) -> dict[int, dict[str, Any]]:
        """Run a query to exhaustion and merge its pages by page id.

        Continuation of a prop module re-sends the same page objects carrying
        the next slice of their list-valued properties. Appending would
        duplicate articles and truncate their categories, so list values are
        concatenated per page instead.
        """
        merged: dict[int, dict[str, Any]] = {}
        for batch in self.query(**params):
            for page in batch.get("query", {}).get("pages", []):
                pageid = page.get("pageid")
                if pageid is None:  # missing or invalid title
                    pageid = -abs(hash(page.get("title", ""))) or -1
                target = merged.get(pageid)
                if target is None:
                    merged[pageid] = dict(page)
                    continue
                for key, value in page.items():
                    if isinstance(value, list):
                        existing = target.get(key)
                        target[key] = (existing if isinstance(existing, list) else []) + value
                    else:
                        target.setdefault(key, value)
        return merged

    def query_by_titles(
        self, titles: list[str], *, cache_scope: str, **params: Any
    ) -> dict[str, dict[str, Any]]:
        """A batched `titles=` query whose cache is keyed per title, not per batch.

        Batching is what keeps these queries cheap, but a batch's cache key
        depends on which titles happen to be in it, so adding one article to
        scope.toml would otherwise re-fetch every batch it touches. Caching each
        page separately means a scope change only costs requests for the pages
        that are actually new.
        """
        pages: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        for title in titles:
            key = cache_key("TITLE", cache_scope, {**params, "title": title})
            cached = self.cache.get(key)
            if cached is None:
                missing.append(title)
            else:
                self.stats.cache_hits += 1
                pages[title] = cached

        for chunk in _chunks(missing, TITLES_PER_REQUEST):
            fetched = self.query_pages(titles="|".join(chunk), **params)
            by_title = {str(page.get("title", "")): page for page in fetched.values()}
            for title in chunk:
                page = by_title.get(title, {"title": title, "missing": True})
                self.cache.put(cache_key("TITLE", cache_scope, {**params, "title": title}), page)
                pages[title] = page
        return pages

    def query_list(self, list_key: str, **params: Any) -> list[dict[str, Any]]:
        """Run a ``list=`` query to exhaustion and concatenate its rows."""
        rows: list[dict[str, Any]] = []
        for batch in self.query(**params):
            rows.extend(batch.get("query", {}).get(list_key, []))
        return rows

    # ---------------------------------------------------------- REST: views
    def pageviews(self, title: str, *, access: str, agent: str, start: str, end: str) -> list[dict[str, Any]]:
        """Monthly pageviews. Returns [] when the endpoint has no data.

        Encoding is spaces-to-underscores then percent-encoding, verified live
        against umlauts, spaces and accents.
        """
        encoded = quote(title.replace(" ", "_"), safe="")
        url = f"{PAGEVIEWS_URL}/de.wikipedia.org/{access}/{agent}/{encoded}/monthly/{start}/{end}"
        if self.dry_run and self.cache.get(cache_key("GET", url, {})) is None:
            return []
        return _items(self._get_json(url, {}, empty_on_404=True))


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items")
    return items if isinstance(items, list) else []


def _warnings(payload: dict[str, Any]) -> list[str]:
    warnings = payload.get("warnings")
    if not isinstance(warnings, dict):
        return []
    out: list[str] = []
    for module, value in sorted(warnings.items()):
        text = value.get("warnings") if isinstance(value, dict) else value
        out.append(f"{module}: {text}")
    return out
