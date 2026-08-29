"""Compare the article against sources that are already structured.

The cheapest high-precision answer to "is this number out of date" does not
need a model or a web search: Wikidata very often already holds the current
value, with a point-in-time qualifier and a reference, and the other language
editions of the same article often cover things dewiki does not.

Two deliberate constraints:

* **The action allowlist is not widened.** `action=wbgetentities` is not on it
  and stays off it. Wikidata is read through the Wikibase REST API, which is a
  plain GET with no `action` parameter, via `WebClient`. The item id itself
  comes from `prop=pageprops`, which is an ordinary `action=query`.
* **Nothing here infers.** A delta is a pair of values and where each came
  from. Deciding whether the article or Wikidata is right is the editor's job,
  and Wikidata is frequently the one that is wrong.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any

from .claims import extract_claims
from .client import WikiClient
from .config import ScopeConfig
from .models import Article, ArticleClaims, Delta
from .webclient import WIKIDATA_REST, WebClient

log = logging.getLogger(__name__)

#: Infobox parameter -> the Wikidata property that says the same thing.
#: Deliberately short: a mapping nobody has checked is a source of confident
#: nonsense, and every entry here is one somebody can verify by hand.
PROPERTY_FOR_FIELD: dict[str, str] = {
    "EINWOHNER": "P1082",
    "FLÄCHE": "P2046",
    "STADTPRÄSIDENT": "P6",
    "STADTPRÄSIDENTIN": "P6",
    "GEMEINDEPRÄSIDENT": "P6",
    "GEMEINDEPRÄSIDENTIN": "P6",
    "STADTAMMANN": "P6",
    "WEBSITE": "P856",
    "EINWOHNERZAHL": "P1082",
}

PROPERTY_LABELS: dict[str, str] = {
    "P1082": "Einwohnerzahl",
    "P2046": "Fläche",
    "P6": "Leitung der Verwaltung",
    "P856": "offizielle Website",
}

#: Point in time. The qualifier that turns a bare number into a dated claim.
POINT_IN_TIME = "P585"

#: Language editions worth comparing against, in the order they are reported.
COMPARE_LANGUAGES = ("en", "fr", "it")

#: Swiss thousands separators: the ASCII apostrophe and U+2019, both used.
_NUMBER = re.compile(r"-?\d[\d'\u2019., \xa0]*")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
#: Section headings that exist to hold apparatus rather than content. A dewiki
#: article "missing" an Einzelnachweise section is not missing anything.
BOILERPLATE_SECTIONS = frozenset(
    {
        "einzelnachweise",
        "weblinks",
        "literatur",
        "siehe auch",
        "anmerkungen",
        "references",
        "external links",
        "see also",
        "notes",
        "further reading",
        "bibliography",
        "sources",
        "notes et références",
        "voir aussi",
        "liens externes",
        "bibliographie",
        "annexes",
        "note",
        "bibliografia",
        "altri progetti",
        "collegamenti esterni",
        "voci correlate",
    }
)


def wikidata_deltas(claims: ArticleClaims, item_id: str | None, web: WebClient) -> tuple[Delta, ...]:
    """Compare the article's infobox claims against the item's statements."""
    if not item_id:
        return ()

    statements = _statements(item_id, web)
    if not statements:
        return ()

    deltas: list[Delta] = []
    for claim in claims.claims:
        if claim.kind != "infobox_field" or claim.field is None:
            continue
        prop = PROPERTY_FOR_FIELD.get(claim.field)
        if prop is None:
            continue
        best = _preferred_statement(statements.get(prop, []))
        if best is None:
            continue
        value, as_of = best
        deltas.append(
            Delta(
                kind="wikidata",
                claim_id=claim.id,
                field=claim.field,
                label=PROPERTY_LABELS.get(prop, prop),
                article_value=claim.asserted_value,
                external_value=value,
                article_as_of=claim.as_of_year,
                external_as_of=as_of,
                source=f"https://www.wikidata.org/wiki/{item_id}#{prop}",
                agrees=_same(claim.asserted_value, value),
            )
        )
    return tuple(sorted(deltas, key=lambda d: (d.agrees, d.field or "", d.label)))


def interwiki_deltas(
    claims: ArticleClaims,
    links: dict[str, str],
    clients: dict[str, WikiClient],
    config: ScopeConfig,
    reference: dt.date,
) -> tuple[Delta, ...]:
    """Section headings other language editions have and this one does not.

    A heading is a weak signal on its own - the same content often lives under
    a different name - so this is reported as "worth a look", never as "the
    article is missing a section".
    """
    ours = {_normalise_heading(section) for section in claims.sections}
    deltas: list[Delta] = []

    for lang in COMPARE_LANGUAGES:
        title = links.get(lang)
        client = clients.get(lang)
        if not title or client is None:
            continue
        wikitext = _foreign_wikitext(client, title)
        if wikitext is None:
            continue
        foreign = extract_claims(
            Article(pageid=0, title=title, scope_label=lang, wikitext=wikitext), config, reference
        )
        extra = [
            section
            for section in foreign.sections
            if _normalise_heading(section) not in ours
            and _normalise_heading(section) not in BOILERPLATE_SECTIONS
        ]
        if not extra:
            continue
        deltas.append(
            Delta(
                kind="interwiki_section",
                label=f"{lang}wiki",
                external_value="; ".join(extra[:12]),
                source=f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}",
                detail=f"{len(extra)} Abschnitt(e) ohne Entsprechung hier",
            )
        )
    return tuple(deltas)


def wikibase_items(client: WikiClient, titles: list[str]) -> dict[str, str]:
    """dewiki title -> Wikidata item id, via `prop=pageprops`.

    An ordinary `action=query`: no allowlist implications.
    """
    pages = client.query_by_titles(titles, cache_scope="pageprops", prop="pageprops", ppprop="wikibase_item")
    items: dict[str, str] = {}
    for title, page in pages.items():
        item = page.get("pageprops", {}).get("wikibase_item")
        if isinstance(item, str):
            items[title] = item
    return items


def langlinks(client: WikiClient, titles: list[str]) -> dict[str, dict[str, str]]:
    """dewiki title -> {language code: foreign title}."""
    pages = client.query_by_titles(
        titles, cache_scope="langlinks", prop="langlinks", lllimit="max", lllang="|".join(COMPARE_LANGUAGES)
    )
    out: dict[str, dict[str, str]] = {}
    for title, page in pages.items():
        links = page.get("langlinks")
        if not isinstance(links, list):
            continue
        out[title] = {
            str(link.get("lang")): str(link.get("title", ""))
            for link in links
            if link.get("lang") in COMPARE_LANGUAGES and link.get("title")
        }
    return out


# ------------------------------------------------------------------ wikidata
def _statements(item_id: str, web: WebClient) -> dict[str, list[dict[str, Any]]]:
    payload = web.get_json(f"{WIKIDATA_REST}/entities/items/{item_id}/statements")
    return {key: value for key, value in payload.items() if isinstance(value, list)}


def _preferred_statement(statements: list[dict[str, Any]]) -> tuple[str, int | None] | None:
    """The statement to compare against: preferred rank, then newest as-of.

    Wikidata routinely holds a decade of population figures on one item. The
    one worth showing an editor is the most recent, not the first in the list.
    """
    usable: list[tuple[int, int, str, int | None]] = []
    for statement in statements:
        if statement.get("rank") == "deprecated":
            continue
        rendered = _render_value(statement.get("value"))
        if rendered is None:
            continue
        as_of = _qualifier_year(statement.get("qualifiers"))
        rank = 1 if statement.get("rank") == "preferred" else 0
        usable.append((rank, as_of or 0, rendered, as_of))

    if not usable:
        return None
    best = max(usable, key=lambda row: (row[0], row[1]))
    return best[2], best[3]


def _render_value(value: Any) -> str | None:
    """A Wikibase value as the text a human would compare against.

    Only the shapes actually mapped above are handled. An unrecognised shape
    returns None rather than a guess: a delta nobody can check is worse than no
    delta.
    """
    if not isinstance(value, dict) or value.get("type") != "value":
        return None  # "somevalue"/"novalue" say nothing worth reporting
    content = value.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        amount = content.get("amount")
        if isinstance(amount, str):
            return amount.lstrip("+")
        time = content.get("time")
        if isinstance(time, str):
            return time.lstrip("+")[:10]
        label = content.get("id")
        if isinstance(label, str):
            return label  # an item reference; rendered as its Q-id
    return None


def _qualifier_year(qualifiers: Any) -> int | None:
    if not isinstance(qualifiers, list):
        return None
    for qualifier in qualifiers:
        if not isinstance(qualifier, dict):
            continue
        prop = qualifier.get("property")
        if not isinstance(prop, dict) or prop.get("id") != POINT_IN_TIME:
            continue
        rendered = _render_value(qualifier.get("value"))
        if rendered:
            found = _YEAR.search(rendered)
            if found:
                return int(found.group(0))
    return None


def _same(article_value: str | None, external_value: str) -> bool:
    """Do these two say the same thing?

    Compared numerically when both sides are numbers, because `7.79`, `7,79`
    and `7.79 km²` are the same area written three ways; otherwise on a
    normalised string, because `www.adliswil.ch` and `https://www.adliswil.ch/`
    are the same website.
    """
    if article_value is None:
        return False
    ours, theirs = _number(article_value), _number(external_value)
    if ours is not None and theirs is not None:
        scale = max(abs(ours), abs(theirs), 1.0)
        return abs(ours - theirs) / scale < 0.005
    return _normalise_text(article_value) == _normalise_text(external_value)


def _number(text: str) -> float | None:
    found = _NUMBER.search(text)
    if found is None:
        return None
    cleaned = re.sub(r"['\u2019 \xa0]", "", found.group(0).strip())
    if cleaned.count(",") == 1 and cleaned.count(".") == 0:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalise_text(text: str) -> str:
    stripped = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)
    stripped = re.sub(r"^https?://", "", stripped.strip().rstrip("/"), flags=re.IGNORECASE)
    return " ".join(stripped.lower().split())


# ------------------------------------------------------------------ interwiki
def _foreign_wikitext(client: WikiClient, title: str) -> str | None:
    pages = client.query_by_titles(
        [title],
        cache_scope=f"content:{client.api_url}",
        prop="revisions",
        rvprop="content",
        rvslots="main",
    )
    page = pages.get(title)
    if not page:
        return None
    revisions = page.get("revisions")
    if not isinstance(revisions, list) or not revisions:
        return None
    slots = revisions[0].get("slots", {})
    main = slots.get("main", {}) if isinstance(slots, dict) else {}
    content = main.get("content") if isinstance(main, dict) else None
    return content if isinstance(content, str) else None


def _normalise_heading(heading: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", heading).lower().split())
