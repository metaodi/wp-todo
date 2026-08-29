"""Politeness machinery shared by every client in this package.

`WikiClient` talks to the Wikimedia action API; `WebClient` talks to the open
web. What they fetch and how they parse it have nothing in common, but *how
often they are allowed to ask* does, and that part is the part with a policy
attached. It lives here so there is exactly one implementation of it.

Deliberately not shared: the request loop itself. Each client keeps its own,
because what counts as a retriable response and what to do with a successful
one differ - the action API signals overload as HTTP 200 with an error in the
body, the open web does not. A callback abstraction spanning both would hide
that difference rather than express it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)


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
class RequestPacer:
    """A floor on the gap between the start of one request and the next.

    Requests are serialised, so this floor is the hard ceiling on the request
    rate: `delay_s = 1.0` means at most one request per second. Per-host
    instances are how the open-web client avoids letting one slow host's
    politeness delay subsidise hammering another.
    """

    delay_s: float
    _last_request_at: float = field(default=0.0, init=False, repr=False)

    def wait(self, stats: ClientStats) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.delay_s - elapsed
        if remaining > 0:
            time.sleep(remaining)
            stats.seconds_waiting += remaining

    def mark(self) -> None:
        self._last_request_at = time.monotonic()


@dataclass
class RequestBudget:
    """A per-run ceiling, so a scope change cannot become an unbounded crawl.

    Hitting it stops the run loudly rather than continuing to make requests
    nobody has budgeted for. 0 disables the ceiling.
    """

    max_requests: int = 0
    #: Named in the error so the message can point at the right config key.
    setting: str = "http.max_requests"

    def check(self, stats: ClientStats) -> None:
        if self.max_requests and stats.requests >= self.max_requests:
            raise RequestBudgetExceededError(
                f"request budget of {self.max_requests} exhausted; raise {self.setting} "
                f"in scope.toml if this scope really needs more"
            )


def sleep_with_backoff(seconds: float, stats: ClientStats) -> None:
    stats.retries += 1
    time.sleep(seconds)
    stats.seconds_waiting += seconds


def retry_after(response: httpx.Response, default: float = 1.0) -> float:
    """Seconds to wait, honouring `Retry-After` when the server sends one.

    A maxlag rejection does not (verified live - see docs/api-notes.md), which
    is why there is a default at all.
    """
    raw = response.headers.get("Retry-After")
    if raw is None:
        return default
    try:
        return max(default, float(raw))
    except ValueError:
        return default


def log_progress(stats: ClientStats, every: int) -> None:
    if every and stats.requests % every == 0:
        log.info(
            "%d requests (%d cache hits, %d retries, %.0fs spent waiting)",
            stats.requests,
            stats.cache_hits,
            stats.retries,
            stats.seconds_waiting,
        )
