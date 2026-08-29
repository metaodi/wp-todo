"""The fetch stage: turn a scope into a cached corpus.

Separate from scoring on purpose - re-scoring cached data must not touch the
network.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable, Iterator

from .client import MAX_LIMIT, WikiClient
from .config import CategoryScope, GeoScope, ScopeConfig
from .models import Article, FetchResult, Revision
from .score import provisional_score

log = logging.getLogger(__name__)

REVISION_PROPS = "ids|timestamp|user|flags|comment|size|tags"


def fetch(config: ScopeConfig, client: WikiClient, *, limit: int | None = None) -> FetchResult:
    reference_date = _server_date(client)
    start, end = pageview_window(reference_date, config.scoring.pageviews.months)

    candidates = _discover(config, client)
    articles = _apply_limit(_apply_excludes(candidates, config), limit)

    bots = _bot_accounts(client)
    detailed, provisional = _split_by_detail_budget(articles, config, reference_date)
    if provisional:
        log.info(
            "detailing %d of %d articles; %d stay at discovery detail (about %d requests saved)",
            len(detailed),
            len(articles),
            len(provisional),
            2 * len(provisional),
        )

    detailed = _add_wikitext(detailed, client)
    detailed = [_add_history(article, client, config) for article in detailed]
    detailed = [_add_pageviews(article, client, config, start, end) for article in detailed]
    articles = detailed + [a.model_copy(update={"detailed": False}) for a in provisional]

    return FetchResult(
        reference_date=reference_date,
        pageviews_start=start,
        pageviews_end=end,
        bot_accounts=bots,
        # Stable order regardless of which tile found what first.
        articles=tuple(sorted(articles, key=lambda a: (a.scope_label, a.title))),
    )


def fetch_one(config: ScopeConfig, client: WikiClient, title: str) -> FetchResult:
    """Fetch exactly one article, by title, without discovering anything.

    The research stage needs an article, not a worklist, and building a whole
    corpus to look at one page costs about 1 600 requests. This is the same
    enrichment the full fetch applies, minus discovery: roughly five requests.

    An empty `articles` means no such mainspace article - `_article_from_page`
    already returns None for `missing` and for other namespaces, so a typo or a
    `Kategorie:` prefix lands here rather than producing an empty dossier.

    Two things `fetch()` does are deliberately skipped:

    * **Exclusions.** They exist to keep lists and disambiguation pages out of a
      *discovered* worklist. A title somebody typed was not discovered, and
      dropping it because it matches `exclude.title_patterns` would answer a
      different question than the one asked.
    * **The detail budget.** One article is always worth detailing.
    """
    reference_date = _server_date(client)
    start, end = pageview_window(reference_date, config.scoring.pageviews.months)

    articles = list(_pages_by_title([title], client, "Recherche", "page"))
    if not articles:
        return FetchResult(reference_date=reference_date, pageviews_start=start, pageviews_end=end)

    articles = _add_wikitext(articles, client)
    articles = [_add_history(article, client, config) for article in articles]
    articles = [_add_pageviews(article, client, config, start, end) for article in articles]

    return FetchResult(
        reference_date=reference_date,
        pageviews_start=start,
        pageviews_end=end,
        bot_accounts=_bot_accounts(client),
        articles=tuple(articles),
    )


# ------------------------------------------------------------------ discovery
def _discover(config: ScopeConfig, client: WikiClient) -> list[Article]:
    """Collect candidates from every scope entry, keeping the first label seen."""
    found: dict[int, Article] = {}
    for tile in config.geo:
        for article in _geo_candidates(tile, client):
            found.setdefault(article.pageid, article)
    for category in config.category:
        for article in _category_candidates(category, client):
            found.setdefault(article.pageid, article)
    if config.pages:
        for article in _explicit_candidates(config.pages, client):
            found.setdefault(article.pageid, article)
    log.info("discovered %d candidate articles", len(found))
    return list(found.values())


def _geo_candidates(tile: GeoScope, client: WikiClient) -> Iterator[Article]:
    """One request per tile in the common case.

    Categories are fetched unfiltered: `clshow=hidden` would drop
    `Kategorie:Wikipedia:Veraltet seit YYYY`, which is not a hidden category.
    """
    pages = client.query_pages(
        generator="geosearch",
        ggscoord=f"{tile.lat}|{tile.lon}",
        ggsradius=tile.radius_m,
        ggslimit=MAX_LIMIT,
        ggsnamespace=0,
        prop="categories|revisions",
        cllimit="max",
        rvprop=REVISION_PROPS,
    )
    for page in pages.values():
        article = _article_from_page(page, tile.label, "geo")
        if article is not None:
            yield article


def _category_candidates(scope: CategoryScope, client: WikiClient) -> Iterator[Article]:
    """Walk the tree ourselves: deterministic, cacheable, no SPARQL dependency."""
    label = scope.label or scope.name.removeprefix("Kategorie:")
    for title in sorted(_walk_category(scope.name, scope.depth, client)):
        yield from _pages_by_title([title], client, label, "category")


def _walk_category(root: str, depth: int, client: WikiClient) -> set[str]:
    """Breadth-first over subcategories, returning article titles."""
    articles: set[str] = set()
    frontier = [root]
    seen_categories = {root}
    for _ in range(depth + 1):
        next_frontier: list[str] = []
        for category in frontier:
            members = client.query_list(
                "categorymembers",
                list="categorymembers",
                cmtitle=category,
                cmlimit=MAX_LIMIT,
                cmtype="page|subcat",
            )
            for member in members:
                title = str(member.get("title", ""))
                if member.get("ns") == 14:
                    if title not in seen_categories:
                        seen_categories.add(title)
                        next_frontier.append(title)
                elif member.get("ns") == 0:
                    articles.add(title)
        frontier = next_frontier
        if not frontier:
            break
    return articles


def _explicit_candidates(pages: Iterable[str], client: WikiClient) -> Iterator[Article]:
    yield from _pages_by_title(list(pages), client, "Explizit", "page")


def _pages_by_title(titles: list[str], client: WikiClient, label: str, kind: str) -> Iterator[Article]:
    pages = client.query_by_titles(
        titles,
        cache_scope="discovery",
        prop="categories|revisions",
        cllimit="max",
        rvprop=REVISION_PROPS,
    )
    for page in pages.values():
        article = _article_from_page(page, label, kind)
        if article is not None:
            yield article


def _article_from_page(page: dict[str, object], label: str, kind: str) -> Article | None:
    if page.get("missing") or page.get("ns") != 0:
        return None
    pageid = page.get("pageid")
    if not isinstance(pageid, int):
        return None
    raw_categories = page.get("categories")
    categories = tuple(
        sorted(str(c["title"]) for c in raw_categories if isinstance(c, dict) and "title" in c)
        if isinstance(raw_categories, list)
        else ()
    )
    raw_revisions = page.get("revisions")
    revisions = (
        tuple(Revision.from_api(r) for r in raw_revisions if isinstance(r, dict))
        if isinstance(raw_revisions, list)
        else ()
    )
    return Article(
        pageid=pageid,
        title=str(page.get("title", "")),
        scope_label=label,
        scope_kind=kind,
        categories=categories,
        revisions=revisions,
    )


# ------------------------------------------------------------------ enrichment
def _apply_excludes(articles: Iterable[Article], config: ScopeConfig) -> list[Article]:
    kept: list[Article] = []
    for article in articles:
        reason = config.is_excluded(article.title, article.categories)
        if reason is None:
            kept.append(article)
        else:
            log.debug("excluded %s (%s)", article.title, reason)
    log.info("%d candidates after exclusions", len(kept))
    return kept


def _split_by_detail_budget(
    articles: list[Article], config: ScopeConfig, reference: dt.date
) -> tuple[list[Article], list[Article]]:
    """Spend the per-article request budget where it can change the answer.

    Detail costs roughly two requests per article, which is the whole cost of a
    region-sized run. Articles are ranked by a lower-bound score computed from
    discovery data alone, and the budget goes to the top of that ranking.
    """
    budget = config.fetch.detail_top_n
    if budget <= 0 or len(articles) <= budget:
        return (articles, [])
    ranked = sorted(
        articles,
        key=lambda a: (-provisional_score(a, reference, config.scoring), a.title),
    )
    return (ranked[:budget], ranked[budget:])


def _apply_limit(articles: list[Article], limit: int | None) -> list[Article]:
    if limit is None:
        return articles
    # Deterministic subset, so --limit runs are comparable with each other.
    return sorted(articles, key=lambda a: (a.scope_label, a.title))[:limit]


def _add_wikitext(articles: list[Article], client: WikiClient) -> list[Article]:
    """Latest-revision content, batched. rvprop=content takes many titles as
    long as rvlimit is not set."""
    by_title = {a.title: a for a in articles}
    pages = client.query_by_titles(
        sorted(by_title),
        cache_scope="wikitext",
        prop="revisions",
        rvprop="content",
        rvslots="main",
    )
    for title, page in pages.items():
        article = by_title.get(title)
        if article is None:
            continue
        revisions = page.get("revisions")
        if isinstance(revisions, list) and revisions:
            slots = revisions[0].get("slots", {}) if isinstance(revisions[0], dict) else {}
            main = slots.get("main", {}) if isinstance(slots, dict) else {}
            content = main.get("content") if isinstance(main, dict) else None
            if isinstance(content, str):
                by_title[title] = article.model_copy(update={"wikitext": content})
    return [by_title[a.title] for a in articles]


def _add_history(article: Article, client: WikiClient, config: ScopeConfig) -> Article:
    """Revision history. One request per article: rvlimit is single-page only.

    Deliberately does not follow continuation. rvlimit caps a *batch*, not the
    query, so continuing would walk the entire history of the article - hundreds
    of requests for a well-tended one. history_depth is the whole budget.
    """
    for batch in client.query(
        titles=article.title,
        prop="revisions",
        rvprop=REVISION_PROPS,
        rvlimit=config.scoring.history_depth,
    ):
        for page in batch.get("query", {}).get("pages", []):
            raw = page.get("revisions")
            if isinstance(raw, list) and raw:
                revisions = tuple(Revision.from_api(r) for r in raw if isinstance(r, dict))
                return article.model_copy(update={"revisions": revisions})
        break  # first batch only
    return article


def _add_pageviews(
    article: Article, client: WikiClient, config: ScopeConfig, start: str, end: str
) -> Article:
    views = client.pageviews(
        article.title,
        access=config.scoring.pageviews.access,
        agent=config.scoring.pageviews.agent,
        start=start,
        end=end,
    )
    if not views:
        return article
    monthly = {str(item["timestamp"])[:6]: int(item.get("views", 0)) for item in views}
    return article.model_copy(update={"pageviews": monthly})


def _bot_accounts(client: WikiClient) -> tuple[str, ...]:
    """The wiki's bot group. One cacheable request; ~65 accounts on dewiki."""
    rows = client.query_list("allusers", list="allusers", augroup="bot", aulimit=MAX_LIMIT)
    return tuple(sorted(str(row["name"]) for row in rows if "name" in row))


# ----------------------------------------------------------------- utilities
def _server_date(client: WikiClient) -> dt.date:
    """Fixed reference point for every age calculation in the run."""
    for batch in client.query(meta="siteinfo", siprop="general"):
        raw = batch.get("query", {}).get("general", {}).get("time")
        if isinstance(raw, str):
            return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    return dt.datetime.now(dt.UTC).date()


def pageview_window(reference: dt.date, months: int) -> tuple[str, str]:
    """The `months` complete calendar months before the reference month.

    Whole months only, and never "the last N months from today" - otherwise the
    numbers, and the diff, churn on every run.
    """
    end_year, end_month = reference.year, reference.month
    end_month -= 1
    if end_month == 0:
        end_year, end_month = end_year - 1, 12
    start_index = (end_year * 12 + end_month - 1) - (months - 1)
    start_year, start_month = divmod(start_index, 12)
    start_month += 1
    return f"{start_year:04d}{start_month:02d}01", f"{end_year:04d}{end_month:02d}01"
