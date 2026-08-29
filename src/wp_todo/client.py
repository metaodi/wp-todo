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
    delay_s: float = 0.5
    max_retries: int = 4
    maxlag: int = 5
    timeout_s: float = 60.0
    dry_run: bool = False
    #: Refuse to touch the network; a cache miss is an error. Used by the tests.
    offline: bool = False
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

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
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
            self._sleep_between_requests()
            self._last_request_at = time.monotonic()
            self.stats.requests += 1
            try:
                response = self._client.get(url, params=params)
            except httpx.HTTPError as exc:  # network-level failure
                last_error = exc
                log.warning("request failed (%s/%s): %s", attempt, self.max_retries, exc)
            else:
                retriable = self._retriable_reason(response)
                if retriable is None:
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

    def query_list(self, list_key: str, **params: Any) -> list[dict[str, Any]]:
        """Run a ``list=`` query to exhaustion and concatenate its rows."""
        rows: list[dict[str, Any]] = []
        for batch in self.query(**params):
            rows.extend(batch.get("query", {}).get(list_key, []))
        return rows

    # ---------------------------------------------------------- REST: views
    def pageviews(self, title: str, *, access: str, agent: str, start: str, end: str) -> list[dict[str, Any]]:
        """Monthly pageviews. Returns [] when the endpoint has no data (404).

        Encoding is spaces-to-underscores then percent-encoding, verified live
        against umlauts, spaces and accents.
        """
        encoded = quote(title.replace(" ", "_"), safe="")
        url = f"{PAGEVIEWS_URL}/de.wikipedia.org/{access}/{agent}/{encoded}/monthly/{start}/{end}"
        key = cache_key("GET", url, {})
        cached = self.cache.get(key)
        if cached is not None:
            self.stats.cache_hits += 1
            return _items(cached)

        if self.offline:
            raise OfflineCacheMissError(f"no recorded pageviews for {title}")
        if self.dry_run:
            return []
        if self._client is None:
            raise RuntimeError("WikiClient must be used as a context manager")

        self._sleep_between_requests()
        self._last_request_at = time.monotonic()
        self.stats.requests += 1
        response = self._client.get(url)
        if response.status_code == 404:
            # Verified live: a title with no data 404s with a JSON body. That is
            # ordinary missing data, not an error.
            self.cache.put(key, {"items": []})
            return []
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        self.cache.put(key, payload)
        return _items(payload)


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
