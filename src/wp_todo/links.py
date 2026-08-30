"""Which of an article's links still resolve.

The one part of the research stage that needs no model at all, and the reason
it is worth having: *"11 of this article's 86 references are dead, and here is
an archived copy of each"* is a fact, not an inference. Nothing here is
demoted, gated or hedged, because nothing here is a guess.

Except in one respect, and it is the whole design. **A link checker that is
confidently wrong is worse than none**, because an editor acting on it replaces
working links with archive copies. dewiki has already lived through that with
bot-driven dead-link tagging - it is why `scope.toml` weights the
`Weblink offline` maintenance categories near zero as bot noise. So the verdict
is not a boolean:

* ``tot`` - 404 or 410. The only verdict that says the document is gone.
* ``gesperrt`` - 401, 403, 429. The host refused **us**. It says nothing at all
  about whether the page is still there, and it is the single most common way
  a checker earns a false "dead".
* ``nicht erreichbar`` - 5xx, a timeout, a connection failure. Not reachable
  *now*.
* ``umgeleitet`` - 200, but the URL we ended at lost its path or changed host.
  That is the shape of a soft 404. Reported with the final URL and never called
  dead.
* ``erreichbar`` - it resolves. Which is a fact about the URL, not about
  whether the page still says what it was cited for.
* ``nicht geprüft`` - robots.txt refused, the ceiling was reached, or the
  scheme is one we do not fetch. Not a verdict; we did not look.

A soft 404 that answers 200 with a "Seite nicht gefunden" body is **not**
detected. Only the redirect shape is. The dossier says so inside the section,
because a reader who thinks the list is exhaustive will trust ``erreichbar``
further than it has earned.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from ._http import RequestBudgetExceededError
from .config import ResearchConfig
from .models import ArticleClaims, LinkStatus, LinkSummary
from .webclient import WAYBACK_AVAILABLE, WebClient

log = logging.getLogger(__name__)

DEAD = "tot"
BLOCKED = "gesperrt"
UNREACHABLE = "nicht erreichbar"
REDIRECTED = "umgeleitet"
REACHABLE = "erreichbar"
UNCHECKED = "nicht geprüft"

#: Worst first. Also the render order, and the tie-break tail is the URL, so
#: two links with the same verdict never swap places between runs.
SEVERITY = (DEAD, UNREACHABLE, BLOCKED, REDIRECTED, UNCHECKED, REACHABLE)

#: Verdicts worth asking the Internet Archive about. A reachable link needs no
#: snapshot, and a blocked one is probably still there - looking it up would
#: spend a request to answer a question nobody asked.
WORTH_ARCHIVING = frozenset({DEAD, UNREACHABLE})

#: What an editor writes when they have already dealt with a dead link. Any of
#: these next to a URL means the work is done, or at least started.
_ARCHIVE_MARKERS = re.compile(
    r"archiv-url\s*=|\{\{\s*Webarchiv|\{\{\s*Toter[ _]Link|web\.archive\.org|archive\.today",
    re.IGNORECASE,
)
_REF = re.compile(r"<ref[^>/]*>(.*?)</ref\s*>", re.IGNORECASE | re.DOTALL)
_URL = re.compile(r"https?://[^\s\|\]\}<>\"']+")


def check_links(
    claims: ArticleClaims,
    wikitext: str,
    web: WebClient,
    research: ResearchConfig,
) -> tuple[tuple[LinkStatus, ...], LinkSummary]:
    """Check the article's links, worst first, within the request ceiling.

    Returns the statuses *and* a summary, because the counts have to carry
    whether the check finished: a short dead-link list because the ceiling
    stopped it is a different fact from a short list because the links are
    fine, and rendering those identically would be the same lie the dossier
    spends so much effort not telling elsewhere.
    """
    urls = _ordered(claims)
    if not urls or not research.max_link_checks:
        return (), LinkSummary(total=len(urls))

    archived = _already_archived(wikitext)
    statuses: list[LinkStatus] = []
    exhausted = False

    for url, cited in urls[: research.max_link_checks]:
        if exhausted:
            break
        try:
            statuses.append(_check(url, cited, url in archived, web, research))
        except RequestBudgetExceededError as exc:
            # The ceiling stops the check, never the dossier. Everything above
            # it is already fetched and already paid for, and losing it to an
            # optional extra would be the wrong trade - the same reasoning as
            # `agent.failed_outcome`.
            log.warning("the link check stopped at the request ceiling: %s", exc)
            exhausted = True

    checked = {status.url for status in statuses}
    for url, cited in urls:
        if url not in checked:
            statuses.append(
                LinkStatus(
                    url=url,
                    verdict=UNCHECKED,
                    cited=cited,
                    archived_in_article=url in archived,
                    detail=(
                        "das Anfrage-Budget war aufgebraucht"
                        if exhausted
                        else f"über {research.max_link_checks} geprüfte Links hinaus"
                    ),
                )
            )

    statuses.sort(key=_order)
    return tuple(statuses), _summarise(statuses, total=len(urls), exhausted=exhausted)


def _check(url: str, cited: bool, archived: bool, web: WebClient, research: ResearchConfig) -> LinkStatus:
    status, final_url, reason = web.head_status(url)
    verdict, detail = _classify(status, reason)
    moved = _moved(url, final_url) if verdict == REACHABLE else ""
    if moved:
        verdict, detail = REDIRECTED, moved

    found = LinkStatus(
        url=url,
        verdict=verdict,
        status=status,
        final_url=final_url if moved else "",
        cited=cited,
        archived_in_article=archived,
        detail=detail,
    )
    if research.suggest_archives and verdict in WORTH_ARCHIVING:
        snapshot_url, snapshot_date = _snapshot(url, web)
        if snapshot_url:
            found = found.model_copy(update={"snapshot_url": snapshot_url, "snapshot_date": snapshot_date})
    return found


def _classify(status: int | None, reason: str) -> tuple[str, str]:
    """An HTTP answer, read as conservatively as it deserves."""
    if status is None:
        # No status at all: robots, a dry run, a timeout, a refused connection.
        # Only the last of those is about the document, and none of them is
        # evidence that it is gone.
        if "robots" in reason:
            return UNCHECKED, reason
        return (UNREACHABLE, reason) if reason else (UNCHECKED, "keine Antwort")
    if status in (404, 410):
        return DEAD, ""
    if status in (401, 403, 429):
        # The host refused us. Somebody opening the URL in a browser may well
        # see the page - so this is never reported as dead, and the detail
        # says why rather than leaving the reader to guess.
        return BLOCKED, "der Host hat die Anfrage abgelehnt, die Seite kann trotzdem existieren"
    if status >= 400:
        # Every other error, 5xx included. Not reachable now; whether the
        # document is gone is a question this answer does not settle.
        return UNREACHABLE, f"HTTP {status}"
    return REACHABLE, ""


def _moved(url: str, final_url: str) -> str:
    """Whether a 200 landed somewhere materially different.

    Two shapes count: a different host, and a path collapsed to the root. Both
    are how a site says "that page is gone" without saying it in the status
    line. Anything smaller - a trailing slash, http to https, an added query -
    is not worth reporting and would bury the real ones.
    """
    if not final_url or final_url == url:
        return ""
    was, now = urlparse(url), urlparse(final_url)
    if was.netloc.lower().removeprefix("www.") != now.netloc.lower().removeprefix("www."):
        return f"landet auf einem anderen Host: {now.netloc}"
    if was.path.strip("/") and not now.path.strip("/"):
        return "landet auf der Startseite - oft die Art, wie eine Seite ihr Verschwinden meldet"
    return ""


def _snapshot(url: str, web: WebClient) -> tuple[str, str]:
    """The closest Wayback snapshot, or nothing.

    A *candidate*, never a replacement. The snapshot may itself have captured
    a soft 404, or predate the content that was cited - which is why the
    dossier prints its date and sends the editor to open it, and why nothing
    here writes the `{{Webarchiv}}` call that would invite pasting it unread.
    """
    payload = web.get_json(WAYBACK_AVAILABLE, {"url": url})
    snapshots = payload.get("archived_snapshots")
    closest = snapshots.get("closest") if isinstance(snapshots, dict) else None
    if not isinstance(closest, dict) or not closest.get("available"):
        return "", ""
    found = str(closest.get("url", ""))
    stamp = str(closest.get("timestamp", ""))
    return (found, _readable(stamp)) if found else ("", "")


def _readable(timestamp: str) -> str:
    """`20190302134501` as `2019-03-02`. The time of day is noise here."""
    if len(timestamp) < 8 or not timestamp[:8].isdigit():
        return ""
    return f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}"


def _ordered(claims: ArticleClaims) -> list[tuple[str, bool]]:
    """Every link the article carries, cited ones first, deduplicated.

    The cited/linked split is the one `ReferenceSummary` already draws, for the
    same reason: a dead `Weblinks` entry is untidy, a dead `<ref>` is an
    unsourced statement. They are not the same problem.
    """
    seen: set[str] = set()
    ordered: list[tuple[str, bool]] = []
    for url, cited in (
        *((u, True) for u in claims.references.external_urls),
        *((u, False) for u in claims.references.linked_urls),
    ):
        if url not in seen:
            seen.add(url)
            ordered.append((url, cited))
    return ordered


def _already_archived(wikitext: str) -> frozenset[str]:
    """URLs the article already carries an archive link or a marker for.

    Checked anyway - a marker goes stale and an archive link can be wrong - but
    sorted below the rest, because the reader's question is what is *new* work.

    Matched per `<ref>` body, reusing the shape `claims._references` already
    parses: an archive marker applies to the URLs in the same reference, not to
    the whole page.
    """
    found: set[str] = set()
    for match in _REF.finditer(wikitext):
        body = match.group(1) or ""
        if _ARCHIVE_MARKERS.search(body):
            found.update(_URL.findall(body))
    for line in wikitext.splitlines():
        if _ARCHIVE_MARKERS.search(line):
            found.update(_URL.findall(line))
    # The archive URL itself is not a reference that needs archiving.
    return frozenset(u for u in found if "web.archive.org" not in u and "archive.today" not in u)


def _order(status: LinkStatus) -> tuple[int, int, int, str]:
    """Worst first, new work before work already done, then the URL.

    The URL tail is what keeps a weekly diff readable: two links with the same
    verdict never swap places between runs.
    """
    severity = SEVERITY.index(status.verdict) if status.verdict in SEVERITY else len(SEVERITY)
    return (severity, 1 if status.archived_in_article else 0, 0 if status.cited else 1, status.url)


def _summarise(statuses: list[LinkStatus], *, total: int, exhausted: bool) -> LinkSummary:
    counted = {verdict: 0 for verdict in SEVERITY}
    for status in statuses:
        counted[status.verdict] = counted.get(status.verdict, 0) + 1
    return LinkSummary(
        total=total,
        checked=sum(1 for s in statuses if s.verdict != UNCHECKED),
        dead=counted[DEAD],
        blocked=counted[BLOCKED],
        unreachable=counted[UNREACHABLE],
        redirected=counted[REDIRECTED],
        reachable=counted[REACHABLE],
        budget_exhausted=exhausted,
    )
