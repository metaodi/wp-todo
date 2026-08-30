"""Read-only access to hosts outside Wikimedia.

A deliberate sibling of `WikiClient`, not an extension of it. The two share
their politeness machinery (`_http.py`) and their cache, and nothing else:

* the Wikimedia User-Agent must not be claimed when talking to a cantonal
  statistics office, and the Wikimedia maxlag convention means nothing there;
* `robots.txt` is not a consideration on the action API and is one here;
* the action API answers JSON of known shape, the open web answers whatever it
  likes, at whatever size it likes.

This client is GET-only by construction: no other verb is implemented, so none
can be called. That is the same kind of guarantee `ALLOWED_ACTIONS` gives on the
action-API side, and the project's first rule is unchanged by this module's
existence - see CLAUDE.md.

Fetched documents are cached as an envelope carrying the *extracted text*
alongside the raw body. The extraction is stored rather than recomputed on read
because it is what the research stage's quote check is verified against: if the
text a quote was checked against could drift between runs, the check would be
worth nothing.
"""

from __future__ import annotations

import datetime as dt
import html
import io
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Self
from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx

from ._http import (
    ClientStats,
    OfflineCacheMissError,
    RequestBudget,
    RequestPacer,
    log_progress,
    retry_after,
    sleep_with_backoff,
)
from .cache import ResponseCache, cache_key
from .config import MetaConfig

log = logging.getLogger(__name__)

#: Content types worth extracting text from. Anything else is recorded as
#: skipped rather than fetched, so the dossier can say why a source is absent.
#:
#: PDF is here because leaving it out was the largest recall hole in the
#: research stage: the cantonal and federal statistical offices this scope
#: leans on publish PDF, and the one reference behind Horgen's ARBEITSLOSE
#: figure - exactly the kind of dated claim the agenda targets - was fetched,
#: filtered out on its content type, cached as a skip, and never mentioned.
PDF_TYPE = "application/pdf"
TEXT_TYPES = ("text/html", "text/plain", "application/xhtml+xml", PDF_TYPE)
JSON_TYPES = ("application/json", "application/sparql-results+json")

#: Wikibase's REST read API. Not the action API, so no `action` parameter is
#: involved and the frozen ALLOWED_ACTIONS allowlist is not implicated.
WIKIDATA_REST = "https://www.wikidata.org/w/rest.php/wikibase/v1"

#: Documented API endpoints, which robots.txt does not govern.
#:
#: Found the hard way: Wikimedia's robots.txt carries `Disallow: /w/`, aimed at
#: crawlers walking the wiki through its script paths. The Wikibase REST API
#: lives at `/w/rest.php`, so the politeness gate was refusing our own API call
#: and every Wikidata comparison silently returned nothing - see
#: `docs/api-notes.md` §8.
#:
#: robots.txt is a convention for crawling a *site*. Calling a published API,
#: at the documented rate, with a User-Agent that names a contact, is a
#: different activity and the Wikimedia API policy is what governs it. The
#: pacing, the budget and the User-Agent all still apply here; only the
#: crawl-exclusion check is skipped, and only for these exact prefixes.
API_PREFIXES: tuple[str, ...] = (
    "https://www.wikidata.org/w/rest.php/",
    "https://www.wikidata.org/w/api.php",
    "https://wikimedia.org/api/rest_v1/",
)

_SCRIPT_STYLE = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_BLOCK_END = re.compile(r"</(p|div|li|tr|h[1-6]|section|article|br)\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_BLANK_RUN = re.compile(r"\n{3,}")
#: The literal is a non-breaking space; German pages are full of them in numbers.
_SPACE_RUN = re.compile(r"[ \t\xa0]+")


class RobotsDisallowedError(RuntimeError):
    """A host's robots.txt refuses this path to our User-Agent."""


class DocumentTooLargeError(RuntimeError):
    """A response exceeded the configured size cap and was abandoned."""


@dataclass
class Document:
    """One fetched document, as stored in the cache.

    `text` is the extracted rendition; it is what a quote is checked against.
    """

    url: str
    final_url: str
    status: int
    content_type: str
    text: str
    fetched_on: dt.date
    truncated: bool = False

    @classmethod
    def from_cache(cls, payload: dict[str, Any]) -> Document:
        return cls(
            url=str(payload["url"]),
            final_url=str(payload.get("final_url", payload["url"])),
            status=int(payload.get("status", 200)),
            content_type=str(payload.get("content_type", "")),
            text=str(payload.get("text", "")),
            fetched_on=dt.date.fromisoformat(str(payload["fetched_on"])),
            truncated=bool(payload.get("truncated", False)),
        )

    def to_cache(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "status": self.status,
            "content_type": self.content_type,
            "text": self.text,
            "fetched_on": self.fetched_on.isoformat(),
            "truncated": self.truncated,
        }


@dataclass
class WebClient:
    """Serial, polite, cached GET access to the open web."""

    meta: MetaConfig
    cache: ResponseCache
    #: Minimum gap between requests *to the same host*. Hosts are paced
    #: independently: one slow site's politeness delay should not become an
    #: allowance to hammer the next one.
    delay_s: float = 2.0
    max_retries: int = 3
    timeout_s: float = 20.0
    max_requests: int = 0
    max_bytes: int = 2_000_000
    progress_every: int = 50
    respect_robots: bool = True
    dry_run: bool = False
    offline: bool = False
    transport: httpx.BaseTransport | None = None
    #: Fixed "today" for the cached envelope, so a dossier's retrieval dates do
    #: not move on a replay. Defaults to the corpus reference date in practice.
    reference_date: dt.date = field(default_factory=dt.date.today)
    _client: httpx.Client | None = field(default=None, init=False, repr=False)
    stats: ClientStats = field(default_factory=ClientStats)
    #: Why a URL produced no document, keyed by URL. Skipping is a normal
    #: outcome here, but a silent one is not: the research stage reports every
    #: document it could not read, and this is where it learns the reason.
    skips: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _pacers: dict[str, RequestPacer] = field(default_factory=dict, init=False, repr=False)
    _robots: dict[str, RobotFileParser | None] = field(default_factory=dict, init=False, repr=False)
    _budget: RequestBudget = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._budget = RequestBudget(self.max_requests, setting="research.max_fetches")

    @property
    def user_agent(self) -> str:
        """Its own identity, and honest about what it is doing.

        A site owner reading their logs should be able to tell that this is a
        research aid reading a handful of pages, and how to reach a human about
        it. `MetaConfig` refuses a placeholder contact, so there is always one.
        """
        from . import __version__

        return (
            f"{self.meta.user_agent_product}-research/{__version__} "
            f"({self.meta.contact}) httpx/{httpx.__version__}"
        )

    def __enter__(self) -> Self:
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout_s,
            follow_redirects=True,
            transport=self.transport,
        )
        return self

    def __exit__(self, *exc: object) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # --------------------------------------------------------------- public
    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """A JSON GET. Used for the Wikibase REST API."""
        document = self.fetch(url, params=params, expect=JSON_TYPES)
        if document is None:
            return {}
        try:
            parsed = json.loads(document.text)
        except ValueError:
            log.warning("non-JSON body from %s", url)
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def fetch(
        self, url: str, *, params: dict[str, Any] | None = None, expect: tuple[str, ...] = TEXT_TYPES
    ) -> Document | None:
        """Fetch one URL. Returns None when it was skipped, never a partial.

        Skipping - robots, wrong content type, oversized, a 4xx - is a normal
        outcome that the dossier reports, not an exception that stops the run.
        """
        params = params or {}
        key = cache_key("WEB", url, params)
        cached = self.cache.get(key)
        if cached is not None:
            self.stats.cache_hits += 1
            if not cached or "skipped" in cached:
                # A skip recorded by an earlier run. Replaying the *reason*
                # matters as much as replaying the skip: a dossier built from
                # the cache has to say the same thing as the run that paid for
                # it. Envelopes written before the reason was stored replay as
                # an empty dict, hence the fallback.
                self.skips[url] = str(cached.get("skipped", "")) if cached else "übersprungen"
                return None
            return Document.from_cache(cached)

        if self.offline:
            raise OfflineCacheMissError(f"no recorded document for {url} {sorted(params.items())}")

        if self.dry_run:
            log.info("dry-run: would GET %s %s", url, sorted(params.items()))
            return None

        if self.respect_robots and not self._robots_allow(url):
            log.info("robots.txt disallows %s", url)
            self._skip(key, url, "robots.txt verbietet den Abruf")
            return None

        document, reason = self._get_with_retries(url, params, expect)
        if document is None:
            # A skip is cached as an envelope carrying its reason: it is a real
            # answer about this URL, and re-asking the host on every rerun
            # would be the rude thing.
            self._skip(key, url, reason)
            return None
        self.cache.put(key, document.to_cache())
        return document

    def _skip(self, key: str, url: str, reason: str) -> None:
        self.skips[url] = reason
        self.cache.put(key, {"skipped": reason})

    # ---------------------------------------------------------------- core
    def _get_with_retries(
        self, url: str, params: dict[str, Any], expect: tuple[str, ...]
    ) -> tuple[Document | None, str]:
        """The document, or None *and why*.

        The reason travels with the None because the caller stores it: a
        document the research stage never saw is reported in the dossier, and
        "HTTP 404" and "Inhaltstyp application/pdf" send a reader to very
        different next steps.
        """
        if self._client is None:
            raise RuntimeError("WebClient must be used as a context manager")

        backoff = 1.0
        for attempt in range(1, self.max_retries + 1):
            self._budget.check(self.stats)
            pacer = self._pacer_for(url)
            pacer.wait(self.stats)
            pacer.mark()
            self.stats.requests += 1
            log_progress(self.stats, self.progress_every)
            try:
                with self._client.stream("GET", url, params=params) as response:
                    if response.status_code in (429, 500, 502, 503, 504):
                        log.warning(
                            "retriable %s from %s (%s/%s)",
                            response.status_code,
                            url,
                            attempt,
                            self.max_retries,
                        )
                        backoff = max(backoff, retry_after(response))
                    elif response.status_code >= 400:
                        log.info("skipping %s: HTTP %s", url, response.status_code)
                        return None, f"HTTP {response.status_code}"
                    else:
                        return self._read(url, response, expect)
            except httpx.HTTPError as exc:
                log.warning("request failed (%s/%s): %s", attempt, self.max_retries, exc)

            sleep_with_backoff(backoff, self.stats)
            backoff *= 2

        log.warning("giving up on %s after %s attempts", url, self.max_retries)
        return None, f"nach {self.max_retries} Versuch(en) nicht erreichbar"

    def _read(
        self, url: str, response: httpx.Response, expect: tuple[str, ...]
    ) -> tuple[Document | None, str]:
        content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith(expect):
            log.info("skipping %s: content-type %s", url, content_type)
            return None, f"Inhaltstyp {content_type} wird nicht gelesen"

        body = bytearray()
        truncated = False
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > self.max_bytes:
                # Stop reading rather than buffer an unbounded response. The
                # prefix is kept: a truncated page is still worth something,
                # and the dossier says it was truncated.
                truncated = True
                break

        payload = bytes(body[: self.max_bytes])
        if content_type.startswith(PDF_TYPE):
            text, reason = extract_pdf_text(payload)
            if not text:
                return None, reason
        else:
            raw = payload.decode(response.encoding or "utf-8", errors="replace")
            text = extract_text(raw) if content_type.startswith(TEXT_TYPES) else raw
        return (
            Document(
                url=url,
                final_url=str(response.url),
                status=response.status_code,
                content_type=content_type,
                text=text,
                fetched_on=self.reference_date,
                truncated=truncated,
            ),
            "",
        )

    def _pacer_for(self, url: str) -> RequestPacer:
        host = urlparse(url).netloc.lower()
        pacer = self._pacers.get(host)
        if pacer is None:
            pacer = RequestPacer(self.delay_s)
            self._pacers[host] = pacer
        return pacer

    # -------------------------------------------------------------- robots
    def _robots_allow(self, url: str) -> bool:
        if url.startswith(API_PREFIXES):
            # A published API, not a site to crawl. See API_PREFIXES.
            return True
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host not in self._robots:
            self._robots[host] = self._load_robots(parsed.scheme or "https", host)
        parser = self._robots[host]
        if parser is None:  # unreachable or absent robots.txt: not a prohibition
            return True
        return parser.can_fetch(self.user_agent, url)

    def _load_robots(self, scheme: str, host: str) -> RobotFileParser | None:
        """Fetch robots.txt through our own client, so it is paced and cached.

        `RobotFileParser.read()` would fetch it itself, uncached and unpaced and
        with urllib's User-Agent - three things this module exists to avoid.
        """
        url = urlunparse((scheme, host, "/robots.txt", "", "", ""))
        key = cache_key("ROBOTS", url, {})
        cached = self.cache.get(key)
        if cached is None:
            if self.offline:
                raise OfflineCacheMissError(f"no recorded robots.txt for {host}")
            document, _ = self._get_with_retries(url, {}, ("text/plain", "text/html"))
            cached = {"text": document.text} if document else {}
            self.cache.put(key, cached)
        else:
            self.stats.cache_hits += 1

        text = str(cached.get("text", ""))
        if not text:
            return None
        parser = RobotFileParser()
        parser.parse(text.splitlines())
        return parser


def extract_pdf_text(payload: bytes) -> tuple[str, str]:
    """A PDF's text, or an empty string and the reason there is none.

    Every guarantee the rest of this module makes is unchanged by this: the
    bytes were fetched with GET, after robots.txt, under the size cap, and the
    extracted text is stored in the cache exactly as an HTML rendition is - so
    the quote gate checks a quote against a PDF the same way it checks one
    against a page.

    A malformed, encrypted or image-only PDF is a skip with a reason, never an
    exception. `pypdf` is an optional dependency for the same reason
    `anthropic` is: the worklist does not need it.
    """
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - exercised by operators, not CI
        return "", "PDF nicht lesbar: `uv sync --extra pdf`"

    try:
        reader = PdfReader(io.BytesIO(payload))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pypdf raises a wide family on malformed input
        log.info("unreadable PDF: %s", exc)
        return "", f"PDF nicht lesbar ({type(exc).__name__})"

    text = _BLANK_RUN.sub("\n\n", "\n\n".join(page.strip() for page in pages if page.strip())).strip()
    if not text:
        # A scan with no text layer. Saying which it is matters: "no text in
        # the PDF" sends a reader to the document, "we cannot read PDF" sends
        # them to the install instructions.
        return "", "PDF ohne Textebene (vermutlich ein Scan)"
    return text, ""


def extract_text(raw: str) -> str:
    """HTML to plain text, without a parser dependency.

    Deliberately crude: drop script/style/comments, turn block ends into
    newlines, strip the remaining tags, unescape entities, collapse runs of
    whitespace. It is not trying to identify "main content" - dropping the
    navigation would risk dropping the sentence a quote needs to be checked
    against, and a false rejection there is worse than a noisy document.
    """
    without_scripts = _SCRIPT_STYLE.sub(" ", raw)
    without_comments = _COMMENT.sub(" ", without_scripts)
    with_breaks = _BLOCK_END.sub("\n", without_comments)
    stripped = _TAG.sub(" ", with_breaks)
    unescaped = html.unescape(stripped)
    spaced = _SPACE_RUN.sub(" ", unescaped)
    lines = [line.strip() for line in spaced.splitlines()]
    return _BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()
