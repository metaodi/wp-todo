"""What does this article assert, and as of when?

Wholly deterministic and offline: a pure function of the wikitext already in the
corpus. It makes no requests and involves no model, which is why the sections of
a dossier built from it are byte-identical across runs.

The scoring stage already knows how to find staleness markers, and this module
reuses that walk rather than growing a second, subtly different one - see
`score.all_marker_hits`. What it adds is the infobox, which is where the
highest-yield claims live: a number with an explicit as-of year beside it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Callable

from .config import ScopeConfig
from .models import Article, ArticleClaims, Claim, ReferenceSummary
from .score import CATEGORY_YEAR, SEIT_PARAM, VERALTET_TEMPLATE, YEAR, ZUKUNFT_TEMPLATE, all_marker_hits

#: Infobox parameters worth treating as dated claims, by the article kind they
#: appear in. Values are the canonical parameter name and the parameter holding
#: its as-of date, where the template has one.
DATED_FIELDS: dict[str, str | None] = {
    "EINWOHNER": "STAND_EINWOHNER",
    "AUSLÄNDER": "STAND_EINWOHNER",
    "ARBEITSLOSE": "STAND_EINWOHNER",
    "FLÄCHE": None,
    "STADTPRÄSIDENT": None,
    "STADTPRÄSIDENTIN": None,
    "GEMEINDEPRÄSIDENT": None,
    "GEMEINDEPRÄSIDENTIN": None,
    "STADTAMMANN": None,
    "WEBSITE": None,
    "EINWOHNERZAHL": "STAND",
    "MITARBEITERZAHL": "STAND",
    "UMSATZ": "STAND",
}

#: Sections whose external links are worth reading even though they are not
#: `<ref>` citations. On de.wikipedia `Weblinks` is where the subject's own
#: site lives and `Literatur` is where a digitised monograph does, so an
#: article can carry forty references, none of them fetchable, and still link
#: the two documents most worth reading. `Sanatorium Kilchberg` does exactly
#: that: 46 print references, zero URLs among them, and a municipal
#: 150-year monograph sitting under `Literatur` that nothing ever looked at.
LINK_SECTIONS = frozenset({"weblinks", "literatur"})

#: `{{Belege fehlen}}` and the redirects editors actually type. It lives here
#: rather than in `score.py` deliberately: `Kategorie:Wikipedia:Belege fehlen`
#: already scores this signal, and a second scoring path would count it twice.
#: As a *claim* it is new - an editor writing "this page is not sourced" is a
#: question worth putting to whatever documents we can find.
BELEGE_TEMPLATE = re.compile(
    r"\{\{\s*(?:Belege|Quellen)(?:\s+fehlen)?\s*(?:\|[^{}]{0,400})?\}\}",
    re.IGNORECASE | re.DOTALL,
)

_HEADING = re.compile(r"^\s*(={2,6})\s*(.+?)\s*\1\s*$")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
#: The lookahead is load-bearing: without it `<references />` matches as a
#: self-closing `<ref/>`, because `[^>]*?` happily eats "erences ". Nearly
#: every article ends with that tag, so every reference count was one too
#: high - `Sanatorium Kilchberg` reported 46 where it has 45.
_REF = re.compile(r"<ref(?=[\s/>])[^>]*?(?:/>|>(.*?)</ref\s*>)", re.IGNORECASE | re.DOTALL)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_URL = re.compile(r"https?://[^\s\|\]\}<>\"']+")
_DATE_PARAM = re.compile(r"\|\s*(?:Datum|Jahr|Date|Year|Zugriff|Abruf)\s*=\s*([^\|\}\n]+)", re.IGNORECASE)
_TEMPLATE_REASON = re.compile(r"\|\s*(?:1\s*=\s*)?(.+?)\s*\}\}\s*$", re.DOTALL)


def extract_claims(article: Article, config: ScopeConfig, reference: dt.date) -> ArticleClaims:
    """Everything the deterministic pass can work out from one article."""
    wikitext = article.wikitext or ""
    infobox_name, infobox = _first_infobox(wikitext)

    claims = (
        _maintenance_claims(article, wikitext, reference)
        + _infobox_claims(infobox)
        + _marker_claims(wikitext, config, reference)
    )

    # Content-addressed ids collide only when two claims really are the same
    # claim, so de-duplicate on them rather than emitting both.
    unique: dict[str, Claim] = {}
    for claim in claims:
        unique.setdefault(claim.id, claim)

    return ArticleClaims(
        pageid=article.pageid,
        title=article.title,
        infobox=infobox_name,
        sections=_sections(wikitext),
        claims=tuple(sorted(unique.values(), key=lambda c: (c.line_no, c.kind, c.id))),
        references=_references(wikitext),
    )


# ------------------------------------------------------------- dated templates
def _maintenance_claims(article: Article, wikitext: str, reference: dt.date) -> list[Claim]:
    """`{{Veraltet|seit=}}` and `{{Zukunft|YYYY|MM}}` as claims.

    These are the strongest signal the worklist has - an editor looked at the
    page and said *this needs updating*, sometimes with a date attached - and
    for the first live runs they were missing from the dossier entirely,
    because only the regex marker rules were walked. `Küsnachter Dorfbach`
    ranked first on `maintenance (Veraltet nach Mai 2025) +45` and its briefing
    never mentioned it.

    The parsing mirrors `score._dated_staleness` deliberately: the same three
    sources in the same order of precision. A malformed `seit=` is reported
    rather than dropped, because a broken date is itself worth seeing.
    """
    claims: list[Claim] = []
    line_of = _line_of(wikitext)

    template = VERALTET_TEMPLATE.search(wikitext)
    if template:
        param = SEIT_PARAM.search(template.group(0))
        raw = param.group(1).strip() if param else ""
        found = YEAR.search(raw) if raw else None
        detail = f"seit {found.group(0)}" if found else (f"seit={raw!r} (nicht lesbar)" if param else "")
        claims.append(
            _claim(
                kind="veraltet_template",
                text=f"{{{{Veraltet}}}}: als veraltet markiert{f' - {detail}' if detail else ''}",
                line_no=line_of(template.start()),
                as_of_year=int(found.group(0)) if found else None,
            )
        )

    zukunft = ZUKUNFT_TEMPLATE.search(wikitext)
    if zukunft:
        year, month = int(zukunft.group(1)), int(zukunft.group(2) or 1)
        due = dt.date(year, min(max(month, 1), 12), 1)
        overdue = due <= reference
        claims.append(
            _claim(
                kind="zukunft_template",
                text=(
                    f"{{{{Zukunft}}}}: dieser Abschnitt sollte "
                    f"{'seit' if overdue else 'ab'} {due.isoformat()} überprüft werden"
                ),
                line_no=line_of(zukunft.start()),
                as_of_year=year if overdue else None,
            )
        )

    # Only when the templates said nothing: the categories are the same signal
    # observed from outside, and reporting both would be reporting it twice.
    if not claims:
        for category in article.categories:
            found_cat = CATEGORY_YEAR.search(category)
            if found_cat:
                claims.append(
                    _claim(
                        kind="veraltet_kategorie",
                        text=f"Kategorie: {category.removeprefix('Kategorie:Wikipedia:')}",
                        line_no=1,
                        as_of_year=int(found_cat.group(1)),
                    )
                )
                break

    # Appended *after* the fallback above, never before it: the fallback is
    # guarded by `if not claims`, so adding this any earlier would silence a
    # real `Veraltet seit YYYY` category on every page that also carries
    # `{{Belege fehlen}}`.
    #
    # It is a claim without a year, and that is not an oversight. "Nothing on
    # this page is sourced" has no as-of date to go stale; what it has is an
    # editor asking for a citation, which is a question the documents we can
    # already reach might answer.
    belege = BELEGE_TEMPLATE.search(wikitext)
    if belege:
        reason = _template_reason(belege.group(0))
        claims.append(
            _claim(
                kind="belege_fehlen",
                text=(
                    "{{Belege fehlen}}: als unzureichend belegt markiert" + (f" - {reason}" if reason else "")
                ),
                line_no=line_of(belege.start()),
            )
        )
    return claims


def _template_reason(template: str) -> str:
    """The free-text argument an editor left on a maintenance template.

    `{{Belege fehlen|Teilweise: Nur spärliche Einzelnachweise}}` says far more
    than the bare template does, and it is the editor's own words about what
    is wrong - worth carrying into the claim rather than dropping.
    """
    match = _TEMPLATE_REASON.search(template)
    return _condense(_COMMENT.sub("", match.group(1))) if match else ""


def _line_of(wikitext: str) -> Callable[[int], int]:
    """Character offset -> 1-based line number."""

    def line_no(offset: int) -> int:
        return wikitext.count("\n", 0, offset) + 1

    return line_no


# --------------------------------------------------------------------- infobox
def _first_infobox(wikitext: str) -> tuple[str | None, dict[str, tuple[str, int]]]:
    """The first `{{Infobox …}}` on the page, as name plus param -> (value, line).

    Brace and bracket depth are tracked rather than split on `|`, because
    parameter values routinely contain both - `[[FDP.Die Liberalen|FDP]]` would
    otherwise become two parameters, one of them nonsense.
    """
    start = re.search(r"\{\{\s*Infobox\b", wikitext, re.IGNORECASE)
    if start is None:
        return None, {}

    body = _balanced_template(wikitext, start.start())
    if body is None:
        return None, {}

    line_offset = wikitext.count("\n", 0, start.start())
    parts = _split_params(body)
    if not parts:
        return None, {}

    name = " ".join(parts[0].split())
    params: dict[str, tuple[str, int]] = {}
    consumed = 0
    for part in parts[1:]:
        # Line numbers are approximate for a wrapped value, and only ever used
        # to point a reader at roughly the right place.
        line_no = line_offset + body.count("\n", 0, consumed) + 1
        consumed += len(part) + 1
        key, sep, value = part.partition("=")
        if not sep:
            continue
        params[key.strip().upper()] = (value.strip(), line_no)
    return name, params


def _balanced_template(wikitext: str, start: int) -> str | None:
    depth = 0
    for index in range(start, len(wikitext)):
        if wikitext.startswith("{{", index):
            depth += 1
        elif wikitext.startswith("}}", index):
            depth -= 1
            if depth == 0:
                return wikitext[start + 2 : index]
    return None


def _split_params(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    braces = brackets = 0
    index = 0
    while index < len(body):
        if body.startswith("{{", index) or body.startswith("[[", index):
            if body[index] == "{":
                braces += 1
            else:
                brackets += 1
            current.append(body[index : index + 2])
            index += 2
            continue
        if body.startswith("}}", index) or body.startswith("]]", index):
            if body[index] == "}":
                braces -= 1
            else:
                brackets -= 1
            current.append(body[index : index + 2])
            index += 2
            continue
        if body[index] == "|" and braces == 0 and brackets == 0:
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(body[index])
        index += 1
    parts.append("".join(current))
    return parts


def _infobox_claims(infobox: dict[str, tuple[str, int]]) -> list[Claim]:
    claims: list[Claim] = []
    for field, stand_field in DATED_FIELDS.items():
        entry = infobox.get(field)
        if entry is None:
            continue
        raw, line_no = entry
        value = _COMMENT.sub("", raw).strip()
        if not value:
            # Empty, or an HTML comment saying a centralised template fills it
            # in. Swiss municipality population is maintained that way, and
            # flagging it as stale would send an editor after a number that is
            # already someone else's job. Not a claim.
            continue
        as_of = None
        if stand_field:
            stand_entry = infobox.get(stand_field)
            if stand_entry:
                found = _YEAR.search(_COMMENT.sub("", stand_entry[0]))
                as_of = int(found.group(0)) if found else None
        if as_of is None:
            found = _YEAR.search(value)
            as_of = int(found.group(0)) if found else None
        claims.append(
            _claim(
                kind="infobox_field",
                text=f"{field} = {_condense(value)}",
                line_no=line_no,
                field=field,
                asserted_value=_condense(value),
                as_of_year=as_of,
            )
        )
    return claims


# --------------------------------------------------------------------- markers
def _marker_claims(wikitext: str, config: ScopeConfig, reference: dt.date) -> list[Claim]:
    section_at = _section_at(wikitext)
    claims: list[Claim] = []
    for rule in config.scoring.markers:
        for hit in all_marker_hits(wikitext, rule, reference):
            claims.append(
                _claim(
                    kind=f"marker:{hit.code}",
                    text=hit.line,
                    line_no=hit.line_no,
                    section=section_at.get(hit.line_no),
                    as_of_year=hit.year,
                )
            )
    return claims


# ------------------------------------------------------------------ references
def _references(wikitext: str) -> ReferenceSummary:
    bodies = [match.group(1) or "" for match in _REF.finditer(wikitext)]
    years: list[int] = []
    urls: set[str] = set()
    for body in bodies:
        urls.update(_URL.findall(body))
        dated = _DATE_PARAM.search(body)
        candidate = _YEAR.search(dated.group(1)) if dated else _YEAR.search(body)
        if candidate:
            years.append(int(candidate.group(0)))
    return ReferenceSummary(
        total=len(bodies),
        with_year=len(years),
        newest_year=max(years) if years else None,
        oldest_year=min(years) if years else None,
        external_urls=tuple(sorted(urls)),
        linked_urls=_linked_urls(wikitext, urls),
    )


def _linked_urls(wikitext: str, cited: set[str]) -> tuple[str, ...]:
    """External links from `Weblinks` and `Literatur`, minus the cited ones.

    Kept in their own field rather than merged into `external_urls`, because
    "the article cites this host" and "the article links it at the bottom" are
    different facts and only the first one is sourcing. The standing signal
    `already_cited` and the `Einstufung der zitierten Quellen` section both
    mean the first one, and both would start quietly overstating an article's
    sourcing if these were folded in.
    """
    found: set[str] = set()
    current = ""
    for line in _COMMENT.sub("", wikitext).splitlines():
        heading = _HEADING.match(line)
        if heading:
            current = heading.group(2).strip().casefold()
            continue
        if current in LINK_SECTIONS:
            found.update(_URL.findall(line))
    return tuple(sorted(found - cited))


# ----------------------------------------------------------------- structure
def _sections(wikitext: str) -> tuple[str, ...]:
    found = [match.group(2).strip() for line in wikitext.splitlines() if (match := _HEADING.match(line))]
    return tuple(found)


def _section_at(wikitext: str) -> dict[int, str]:
    """Line number -> the heading it falls under."""
    mapping: dict[int, str] = {}
    current = ""
    for line_no, line in enumerate(wikitext.splitlines(), start=1):
        heading = _HEADING.match(line)
        if heading:
            current = heading.group(2).strip()
        elif current:
            mapping[line_no] = current
    return mapping


def _claim(
    *,
    kind: str,
    text: str,
    line_no: int,
    section: str | None = None,
    field: str | None = None,
    asserted_value: str | None = None,
    as_of_year: int | None = None,
) -> Claim:
    digest = hashlib.sha256("|".join([kind, field or "", text]).encode("utf-8")).hexdigest()[:8]
    return Claim(
        id=f"{kind.replace(':', '_')}-{digest}",
        kind=kind,
        text=text,
        line_no=line_no,
        section=section,
        field=field,
        asserted_value=asserted_value,
        as_of_year=as_of_year,
    )


def _condense(text: str) -> str:
    return " ".join(text.split())[:200]
