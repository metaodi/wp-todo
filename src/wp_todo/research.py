"""Assemble a research dossier for one article.

This is the orchestration layer: it decides what to consult and in what order,
and hands the result to `dossier.py` to render. It never writes to Wikipedia,
never drafts article prose, and never posts anything anywhere - see CLAUDE.md
and `docs/research-policy.md`.

Every stage below is deterministic given the cache, which is what lets the
whole artefact be reproduced by replay rather than merely regenerated.
"""

from __future__ import annotations

import logging

from .claims import extract_claims
from .client import WikiClient
from .config import ScopeConfig
from .enrich import interwiki_deltas, langlinks, wikibase_items, wikidata_deltas
from .models import Article, ArticleClaims, Delta, Dossier, FetchResult, SourceStanding
from .score import EDIT_URL
from .sources import TIERS, SourceLedger, Standing, host_of, standing
from .webclient import WebClient

log = logging.getLogger(__name__)

#: One action-API endpoint per language edition we compare against.
FOREIGN_API = "https://{lang}.wikipedia.org/w/api.php"


def research_article(
    article: Article,
    corpus: FetchResult,
    config: ScopeConfig,
    wiki: WikiClient,
    web: WebClient,
    foreign: dict[str, WikiClient] | None = None,
    ledger: SourceLedger | None = None,
) -> Dossier:
    """Build the dossier for one article.

    `foreign` maps a language code to a client pointed at that wiki's action
    API. It is passed in rather than built here so the caller owns every client
    that can make a request, and the tests can hand over offline ones.
    """
    reference = corpus.reference_date
    claims = extract_claims(article, config, reference)
    log.info("%s: %d claim(s) from wikitext", article.title, len(claims.claims))

    item_id: str | None = None
    deltas: list[Delta] = []

    if config.research.compare_wikidata:
        item_id = wikibase_items(wiki, [article.title]).get(article.title)
        if item_id:
            deltas.extend(wikidata_deltas(claims, item_id, web))
        else:
            log.info("%s: no Wikidata item", article.title)

    compared: tuple[str, ...] = ()
    if foreign:
        links = langlinks(wiki, [article.title], config.research.compare_languages).get(article.title, {})
        deltas.extend(interwiki_deltas(claims, links, foreign, config, reference))
        compared = tuple(lang for lang in config.research.compare_languages if lang in links)
        log.info("%s: compared against %s", article.title, ", ".join(compared) or "no other edition")

    return Dossier(
        pageid=article.pageid,
        title=article.title,
        scope_label=article.scope_label,
        reference_date=reference,
        wikidata_item=item_id,
        claims=claims,
        deltas=tuple(deltas),
        interwiki_checked=bool(foreign),
        compared_languages=compared,
        reference_standing=reference_standing(claims, ledger or SourceLedger()),
        edit_url=EDIT_URL.format(title=article.title.replace(" ", "_")),
    )


def reference_standing(claims: ArticleClaims, ledger: SourceLedger) -> tuple[SourceStanding, ...]:
    """Standing for the hosts the article already cites.

    Costs nothing - no request, no model - and answers a question worth asking
    before any research happens: what is this article actually resting on? An
    article sourced entirely to `unrated` hosts is a different problem from one
    sourced to the federal statistics office, and neither is visible from the
    reference count alone.

    It is also how the standing machinery gets exercised against real data
    before the open-web stage exists to feed it.
    """
    urls = claims.references.external_urls
    if not urls:
        return ()

    official = _official_website(claims)
    counts: dict[str, int] = {}
    for url in urls:
        host = host_of(url)
        if host:
            counts[host] = counts.get(host, 0) + 1

    # `cited_hosts` is deliberately not passed here. Within this section
    # "already cited" is the premise, not a finding - every row would carry it,
    # which is noise. The signal earns its keep in M2, where it distinguishes a
    # newly discovered domain that the article already trusts from one it does
    # not.
    found = [
        _as_model(standing(host, ledger=ledger, article_official=official), references=count)
        for host, count in counts.items()
    ]
    return tuple(sorted(found, key=lambda s: (_rank(s), -s.references, s.host)))


def _official_website(claims: ArticleClaims) -> str:
    for claim in claims.claims:
        if claim.field == "WEBSITE" and claim.asserted_value:
            return claim.asserted_value
    return ""


def _as_model(found: Standing, *, references: int) -> SourceStanding:
    return SourceStanding(
        host=found.host,
        tier=found.tier,
        signals=found.signals,
        verdict=found.verdict.verdict if found.verdict else None,
        reason=found.verdict.reason if found.verdict else "",
        decided=found.verdict.decided if found.verdict else None,
        references=references,
        label=found.describe(),
    )


def _rank(found: SourceStanding) -> tuple[int, int]:
    trusted = found.verdict == "trust"
    tier = TIERS.index(found.tier) if found.tier in TIERS else len(TIERS)
    return (tier, -((2 if trusted else 0) + len(found.signals)))


def foreign_clients(config: ScopeConfig, template: WikiClient) -> dict[str, WikiClient]:
    """A client per compared language, sharing the template's cache and budget.

    Built from an existing client rather than from config so there is one place
    - the CLI - that decides what politeness settings any client in the run
    gets.
    """
    clients: dict[str, WikiClient] = {}
    for lang in config.research.compare_languages:
        clients[lang] = WikiClient(
            meta=template.meta,
            cache=template.cache,
            delay_s=template.delay_s,
            max_retries=template.max_retries,
            maxlag=template.maxlag,
            timeout_s=template.timeout_s,
            max_requests=template.max_requests,
            api_url=FOREIGN_API.format(lang=lang),
            dry_run=template.dry_run,
            offline=template.offline,
            transport=template.transport,
        )
    return clients
