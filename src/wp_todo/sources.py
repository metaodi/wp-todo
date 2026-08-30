"""How much standing a source has, and what you decided about it last time.

The editor checks every source at its source. That settles what this module is
*for*: not gatekeeping quality - the human does that - but saving the human's
time, and making the time they spend compound instead of repeat.

Which is why this is a blocklist and not an allowlist. An allowlist's costs are
invisible: you never see the cantonal report you did not find. A blocklist's
costs are visible: junk in a dossier is obvious, and one line removes it
forever. An allowlist also cannot bootstrap - the only way to discover what
belongs on it is to run without one.

So the default is open, and almost all the work is done by *ordering* rather
than filtering. Sorting an official statistics office above a local paper above
somebody's blog saves the same time as filtering and costs no recall: the blog
is still there, further down. Only an explicit `block` removes anything, and a
block is always reported.

Two axes, deliberately separate:

* **tier** - one of `official`, `press_academic`, `unrated`, from the host.
* **signals** - any number, from this article and from recorded verdicts. A
  domain can be `unrated` by suffix and still be one the article already cites.

The two free signals are worth more than any suffix table: the article names its
own official website in its infobox, and a domain an editor already accepted as
a reference *here* has passed a human once, on this subject.
"""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

#: Tiers, best first. The order is the sort order.
TIERS = ("official", "press_academic", "unrated")

#: Government, statistical and intergovernmental. Swiss-first because the
#: configured scope is, but nothing here is scope-specific in kind.
OFFICIAL_SUFFIXES = (
    "admin.ch",
    "bfs.admin.ch",
    "zh.ch",
    "statistik.zh.ch",
    "bger.ch",
    "parlament.ch",
    "europa.eu",
    "gov",
    "gov.uk",
    "bund.de",
)

#: Established publishers, archives and academia. Deliberately short: a list
#: nobody has checked is a source of confident nonsense, and every entry here
#: is one somebody can argue with.
PRESS_ACADEMIC_SUFFIXES = (
    "nzz.ch",
    "srf.ch",
    "tagesanzeiger.ch",
    "zsz.ch",
    "swissinfo.ch",
    "hls-dhs-dss.ch",
    "e-periodica.ch",
    "doi.org",
    "edu",
    "ac.uk",
    "uzh.ch",
    "ethz.ch",
)

VERDICTS = ("block", "note", "trust")

_LABEL = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


class SourceVerdictError(ValueError):
    """A verdict file, or a verdict being recorded, is not usable."""


@dataclass(frozen=True)
class Verdict:
    """One recorded decision about a domain.

    `reason` is mandatory everywhere it is constructed. A blocklist encodes its
    author's judgement, and "unreliable" and "I disagree with it" are easy to
    conflate; a written reason plus the dossier's reporting keeps the dropped
    set auditable - by the editor, and by anyone they show the work to.
    """

    domain: str
    verdict: str
    reason: str
    decided: dt.date


@dataclass(frozen=True)
class Standing:
    """What we can say about a host before anybody reads it."""

    host: str
    tier: str = "unrated"
    signals: tuple[str, ...] = ()
    verdict: Verdict | None = None

    @property
    def blocked(self) -> bool:
        return self.verdict is not None and self.verdict.verdict == "block"

    @property
    def sort_key(self) -> tuple[int, int, str]:
        """Best first. Signals break ties within a tier, host breaks the rest.

        An explicit `trust` is worth more than any suffix table, because a
        person put it there on purpose.
        """
        trusted = self.verdict is not None and self.verdict.verdict == "trust"
        rank = TIERS.index(self.tier) if self.tier in TIERS else len(TIERS)
        weight = (2 if trusted else 0) + len(self.signals)
        return (rank, -weight, self.host)

    def describe(self) -> str:
        """One short German phrase for the dossier."""
        labels = {
            "official": "amtlich",
            "press_academic": "Presse/Wissenschaft",
            "unrated": "nicht eingestuft",
        }
        parts = [labels.get(self.tier, self.tier)]
        parts.extend(SIGNAL_LABELS.get(signal, signal) for signal in self.signals)
        if self.verdict is not None:
            parts.append(f"{VERDICT_LABELS.get(self.verdict.verdict, self.verdict.verdict)}")
        return " · ".join(parts)


SIGNAL_LABELS = {
    "article_official": "offizielle Website des Artikelgegenstands",
    "already_cited": "im Artikel bereits zitiert",
}
VERDICT_LABELS = {
    "block": "ausgeschlossen",
    "note": "mit Notiz",
    "trust": "als verlässlich vermerkt",
}


# ------------------------------------------------------------------ matching
def host_of(url: str) -> str:
    """The lowercased host of a URL, without port or userinfo, or ''."""
    try:
        netloc = urlparse(url if "//" in url else f"//{url}").netloc
    except ValueError:
        return ""
    host = netloc.rsplit("@", 1)[-1].split(":", 1)[0].strip().lower()
    return host.rstrip(".")


def host_matches(pattern: str, host: str) -> bool:
    """Suffix match on label boundaries.

    `example.ch` matches `example.ch` and `www.example.ch`, and does **not**
    match `notexample.ch`. Plain suffix matching would match the last of those,
    which is how a blocklist quietly starts blocking the wrong sites.

    No public-suffix dependency: an entry like `co.uk` would over-match, and the
    answer to that is to write entries that are as specific as makes sense.
    """
    pattern = pattern.strip().lower().lstrip(".").rstrip(".")
    host = host.strip().lower().rstrip(".")
    if not pattern or not host:
        return False
    return host == pattern or host.endswith(f".{pattern}")


def _first_match(patterns: tuple[str, ...], host: str) -> bool:
    return any(host_matches(pattern, host) for pattern in patterns)


# ------------------------------------------------------------------ verdicts
@dataclass
class SourceLedger:
    """The recorded verdicts, keyed by the domain they were recorded against."""

    verdicts: tuple[Verdict, ...] = ()
    path: Path | None = None

    def for_host(self, host: str) -> Verdict | None:
        """The most specific verdict that matches, or None.

        Most specific wins so `beispiel.example` can be blocked while
        `daten.beispiel.example` is separately trusted, rather than the file's
        line order deciding.
        """
        matching = [v for v in self.verdicts if host_matches(v.domain, host)]
        if not matching:
            return None
        return max(matching, key=lambda v: (len(v.domain), v.domain))

    def of_kind(self, verdict: str) -> tuple[Verdict, ...]:
        return tuple(v for v in self.verdicts if v.verdict == verdict)


def load_ledger(path: Path) -> SourceLedger:
    """Read the verdict file. A missing file is an empty ledger, not an error."""
    if not path.exists():
        return SourceLedger(path=path)
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    entries = raw.get("source", [])
    if not isinstance(entries, list):
        raise SourceVerdictError(f"{path}: [[source]] must be a list of tables")

    verdicts: list[Verdict] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise SourceVerdictError(f"{path}: entry {index} is not a table")
        verdicts.append(
            Verdict(
                domain=_require(entry, "domain", path, index).strip().lower(),
                verdict=_require_verdict(entry, path, index),
                reason=_require(entry, "reason", path, index),
                decided=_decided(entry, path, index),
            )
        )
    return SourceLedger(verdicts=tuple(verdicts), path=path)


def format_entry(verdict: Verdict) -> str:
    """One `[[source]]` block, ready to append.

    Appending rather than rewriting is what keeps hand-written comments and
    ordering intact when the CLI records a decision.
    """
    return (
        "\n[[source]]\n"
        f'domain  = "{verdict.domain}"\n'
        f'verdict = "{verdict.verdict}"\n'
        f"reason  = {_toml_string(verdict.reason)}\n"
        f'decided = "{verdict.decided.isoformat()}"\n'
    )


def make_verdict(domain: str, verdict: str, reason: str, decided: dt.date) -> Verdict:
    """Validate a decision before it reaches the file."""
    domain = domain.strip().lower().lstrip(".").rstrip(".")
    if not _LABEL.match(domain):
        raise SourceVerdictError(
            f"{domain!r} is not a domain: give a host like 'beispiel.ch', not a URL or a path"
        )
    if verdict not in VERDICTS:
        raise SourceVerdictError(f"unknown verdict {verdict!r}; expected one of {', '.join(VERDICTS)}")
    if not reason.strip():
        raise SourceVerdictError(
            "a reason is required. A blocklist encodes your judgement, and "
            "'unreliable' and 'I disagree with it' are easy to conflate - "
            "write down which one this is."
        )
    return Verdict(domain=domain, verdict=verdict, reason=reason.strip(), decided=decided)


# ------------------------------------------------------------------ standing
def standing(
    url_or_host: str,
    *,
    ledger: SourceLedger,
    article_official: str = "",
    cited_hosts: frozenset[str] = frozenset(),
) -> Standing:
    """Everything known about a host before anybody reads what it says."""
    host = host_of(url_or_host)
    if not host:
        return Standing(host="")

    if _first_match(OFFICIAL_SUFFIXES, host):
        tier = "official"
    elif _first_match(PRESS_ACADEMIC_SUFFIXES, host):
        tier = "press_academic"
    else:
        tier = "unrated"

    signals: list[str] = []
    official_host = host_of(article_official)
    if official_host and (host == official_host or host_matches(official_host, host)):
        signals.append("article_official")
    if host in cited_hosts:
        signals.append("already_cited")

    return Standing(host=host, tier=tier, signals=tuple(signals), verdict=ledger.for_host(host))


def cited_hosts(external_urls: tuple[str, ...]) -> frozenset[str]:
    """Hosts the article already cites. Free evidence, from the wikitext."""
    return frozenset(host for host in (host_of(url) for url in external_urls) if host)


# --------------------------------------------------------------- circularity
def circularity(
    text: str,
    article_wikitext: str,
    *,
    host: str = "",
    mirror_domains: tuple[str, ...] = (),
    span: int = 200,
    openly_wiki: bool = False,
) -> str | None:
    """Why this document is a copy of the article, or None.

    Circular sourcing is the quiet way an open-web pipeline "confirms" a stale
    fact using a copy of the stale fact. It is the one failure a human check
    will *not* catch, because the text looks perfect - it is the article's own
    text.

    So the domain list is only a fast path. What actually decides is a shared
    verbatim span: new mirrors appear constantly and no hand-kept list keeps up.

    `openly_wiki` is for a document that is *declared* to be a wiki - another
    language edition of this very article, shown to the model as such and
    rendered as such. The two fast paths are heuristics for "this is secretly
    Wikipedia", and the secret is the part that makes them useful: a document
    that says so on its own row is not deceiving anybody, and every Wikipedia
    article's wikitext mentions Wikipedia somewhere, so applying the credit
    heuristic there would drop all of them.

    The verbatim-span check still runs, and it is the one that matters: a
    foreign edition that is a straight copy of this article is exactly as
    circular as any mirror, and is dropped the same way.
    """
    if not openly_wiki and host and _first_match(mirror_domains, host):
        return "bekannter Wikipedia-Spiegel"

    normalised = _normalise(text)
    if not normalised:
        return None

    if not openly_wiki and _credits_wikipedia(normalised):
        return "nennt Wikipedia bzw. CC-BY-SA als Quelle"

    shared = _longest_shared_span(normalised, _normalise(_strip_markup(article_wikitext)), span)
    if shared >= span:
        return f"{shared} Zeichen wörtlich aus dem Artikel"
    return None


_WIKI_CREDIT = re.compile(r"wikipedia|wikimedia|cc[- ]by[- ]sa|creative commons", re.IGNORECASE)
_MARKUP = re.compile(r"<ref[^>]*>.*?</ref\s*>|\{\{[^{}]*\}\}|<[^>]+>|'{2,}|\[\[|\]\]", re.DOTALL)
_WHITESPACE = re.compile(r"\s+")


def _credits_wikipedia(text: str) -> bool:
    return _WIKI_CREDIT.search(text) is not None


def _strip_markup(wikitext: str) -> str:
    return _MARKUP.sub(" ", wikitext)


def _normalise(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().casefold()


def _longest_shared_span(needle_source: str, article: str, span: int) -> int:
    """Length of the longest run of `article` appearing verbatim in the document.

    Walks the article in `span`-sized windows and only measures precisely once a
    window hits, so an unrelated document costs a scan rather than a quadratic
    comparison.
    """
    if len(article) < span:
        return 0
    step = max(1, span // 2)
    for start in range(0, len(article) - span + 1, step):
        window = article[start : start + span]
        if window in needle_source:
            return _extend(needle_source, article, start, span)
    return 0


def _extend(document: str, article: str, start: int, span: int) -> int:
    end = start + span
    while end < len(article) and article[start : end + 1] in document:
        end += 1
    return end - start


# ------------------------------------------------------------------- helpers
def _require(entry: dict[str, object], key: str, path: Path, index: int) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SourceVerdictError(f"{path}: entry {index} needs a non-empty {key!r}")
    return value.strip()


def _require_verdict(entry: dict[str, object], path: Path, index: int) -> str:
    value = _require(entry, "verdict", path, index).lower()
    if value not in VERDICTS:
        raise SourceVerdictError(
            f"{path}: entry {index} has verdict {value!r}; expected one of {', '.join(VERDICTS)}"
        )
    return value


def _decided(entry: dict[str, object], path: Path, index: int) -> dt.date:
    value = entry.get("decided")
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip())
        except ValueError as exc:
            raise SourceVerdictError(f"{path}: entry {index} has an unparsable 'decided'") from exc
    raise SourceVerdictError(f"{path}: entry {index} needs a 'decided' date")


def _toml_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{escaped}"'
