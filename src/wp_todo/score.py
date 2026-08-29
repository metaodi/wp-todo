"""The scoring stage: a pure function of the fetch artefact.

Every signal contributes its own subscore and its own reasons, so the output can
always answer "why did this surface?". Attention is a multiplier, not a term: a
stale high-traffic article outranks a stale stub without being able to invent
staleness on its own.
"""

from __future__ import annotations

import datetime as dt
import math
import re

from .config import MarkerRule, ScopeConfig, ScoringConfig
from .models import Article, FetchResult, Reason, Revision, ScoredArticle, ScoreResult, Subscores

EDIT_URL = "https://de.wikipedia.org/w/index.php?title={title}&action=edit"

#: {{Veraltet|seit=...}} - the value is free text in practice: a bare year, a
#: YYYY-MM, German prose, or empty. Parse leniently, keep the raw string.
VERALTET_TEMPLATE = re.compile(r"\{\{\s*veraltet\b[^{}]{0,400}\}\}", re.IGNORECASE | re.DOTALL)
SEIT_PARAM = re.compile(r"seit\s*=\s*([^|}\n]*)", re.IGNORECASE)
YEAR = re.compile(r"\b(19|20)\d{2}\b")
#: The dated category families, e.g. "Kategorie:Wikipedia:Veraltet seit 2024".
CATEGORY_YEAR = re.compile(r"Veraltet (?:seit|nach)\D{0,12}((?:19|20)\d{2})")

ROUND = 2


def score_corpus(result: FetchResult, config: ScopeConfig) -> ScoreResult:
    scored = [_score_article(article, result, config.scoring) for article in result.articles]
    # Highest first; title breaks ties so equal scores never reorder between runs.
    scored.sort(key=lambda a: (-a.score, a.title))
    return ScoreResult(
        reference_date=result.reference_date,
        pageviews_window=f"{result.pageviews_start}-{result.pageviews_end}",
        articles=tuple(scored),
    )


def _score_article(article: Article, result: FetchResult, scoring: ScoringConfig) -> ScoredArticle:
    reference = result.reference_date
    reasons: list[Reason] = []

    maintenance = _score_maintenance(article, reference, scoring, reasons)
    edit_age, last_substantive, age_days = _score_edit_age(article, result, scoring, reasons)
    markers = _score_markers(article, reference, scoring, reasons)
    monthly_views, attention = _score_attention(article, scoring, reasons)

    base = maintenance + edit_age + markers
    total = base * attention

    latest = article.latest_revision
    reasons.sort(key=lambda r: (-r.points, r.code))
    return ScoredArticle(
        pageid=article.pageid,
        title=article.title,
        scope_label=article.scope_label,
        score=round(total, ROUND),
        base_score=round(base, ROUND),
        subscores=Subscores(
            maintenance=round(maintenance, ROUND),
            edit_age=round(edit_age, ROUND),
            markers=round(markers, ROUND),
            attention=round(attention, ROUND),
        ),
        reasons=tuple(reasons),
        last_substantive_edit=last_substantive,
        last_substantive_edit_days=age_days,
        last_edit=latest.timestamp.date() if latest else None,
        monthly_pageviews=monthly_views,
        edit_url=EDIT_URL.format(title=article.title.replace(" ", "_")),
    )


# ------------------------------------------------------- 1. maintenance templates
def _score_maintenance(
    article: Article, reference: dt.date, scoring: ScoringConfig, reasons: list[Reason]
) -> float:
    total = 0.0
    for category in article.categories:
        match = scoring.maintenance_weight(category)
        if match is None:
            continue
        _prefix, weight = match
        if weight == 0.0:
            continue
        total += weight
        reasons.append(
            Reason(code="maintenance", detail=category.removeprefix("Kategorie:Wikipedia:"), points=weight)
        )

    bonus, evidence = _veraltet_seit_bonus(article, reference, scoring)
    if bonus > 0.0:
        total += bonus
        reasons.append(Reason(code="veraltet_seit", detail=evidence or "", points=round(bonus, ROUND)))
    return total


def _veraltet_seit_bonus(
    article: Article, reference: dt.date, scoring: ScoringConfig
) -> tuple[float, str | None]:
    """The older the `seit=`, the more overdue. Malformed values score nothing
    but are still reported, because a broken `seit=` is itself worth seeing."""
    raw_value: str | None = None
    if article.wikitext:
        template = VERALTET_TEMPLATE.search(article.wikitext)
        if template:
            param = SEIT_PARAM.search(template.group(0))
            if param:
                raw_value = param.group(1).strip()

    year: int | None = None
    if raw_value:
        found = YEAR.search(raw_value)
        if found:
            year = int(found.group(0))
    if year is None:
        for category in article.categories:
            found_cat = CATEGORY_YEAR.search(category)
            if found_cat:
                year = int(found_cat.group(1))
                raw_value = raw_value or category.removeprefix("Kategorie:Wikipedia:")
                break

    if year is None:
        return (0.0, f"seit={raw_value!r} (unparsable)" if raw_value is not None else None)

    years_stale = max(0, reference.year - year)
    bonus = min(years_stale * scoring.veraltet_seit_bonus_per_year, scoring.veraltet_seit_bonus_cap)
    return (bonus, f"seit {year} ({years_stale}a)")


# --------------------------------------------- 2. time since substantive edit
def _score_edit_age(
    article: Article, result: FetchResult, scoring: ScoringConfig, reasons: list[Reason]
) -> tuple[float, dt.date | None, int | None]:
    revision = _last_substantive_revision(article, set(result.bot_accounts), scoring)
    if revision is None:
        reasons.append(Reason(code="no_substantive_edit", detail="none in fetched history", points=0.0))
        return (0.0, None, None)

    age_days = (result.reference_date - revision.timestamp.date()).days
    # Saturating: the difference between 3 and 10 years matters less than the
    # difference between 3 months and 3 years.
    factor = 1.0 - math.pow(0.5, max(age_days, 0) / scoring.edit_age_half_life_days)
    points = scoring.edit_age_weight * factor
    if points > 0.0:
        reasons.append(
            Reason(
                code="stale_edit",
                detail=f"{age_days}d since a substantive edit ({revision.timestamp.date().isoformat()})",
                points=round(points, ROUND),
            )
        )
    return (points, revision.timestamp.date(), age_days)


def _last_substantive_revision(article: Article, bots: set[str], scoring: ScoringConfig) -> Revision | None:
    """Newest revision that is neither a bot edit nor a trivial one.

    Revisions carry no bot flag, so authorship is decided against the wiki's bot
    group. Size delta does the rest of the work: prolific humans who make
    thousands of typo fixes look exactly like bots by any other measure.
    """
    revisions = article.revisions
    for index, revision in enumerate(revisions):
        if revision.user is not None and revision.user in bots:
            continue
        older = revisions[index + 1] if index + 1 < len(revisions) else None
        if older is None:
            # Oldest fetched revision: no delta available, fall back to the flag.
            if not revision.minor:
                return revision
            continue
        if abs(revision.size - older.size) >= scoring.substantive_min_bytes:
            return revision
    return None


# ---------------------------------------------------- 3. in-text staleness markers
def _score_markers(
    article: Article, reference: dt.date, scoring: ScoringConfig, reasons: list[Reason]
) -> float:
    if not article.wikitext:
        return 0.0
    total = 0.0
    for rule in scoring.markers:
        hit = _first_marker_hit(article.wikitext, rule, reference)
        if hit is None:
            continue
        year, line = hit
        total += rule.weight
        reasons.append(
            Reason(
                code=f"marker:{rule.code}",
                detail=f"{year} is {reference.year - year}a old",
                points=rule.weight,
                evidence=line,
            )
        )
    return min(total, scoring.marker_cap)


def _first_marker_hit(wikitext: str, rule: MarkerRule, reference: dt.date) -> tuple[int, str] | None:
    """Report the oldest year found for the rule, with its line as evidence."""
    pattern = rule.compiled()
    best: tuple[int, str] | None = None
    for line in wikitext.splitlines():
        match = pattern.search(line)
        if match is None:
            continue
        year = _year_for_match(match, line)
        if year is None:
            continue
        if reference.year - year <= rule.max_age_years:
            continue
        if best is None or year < best[0]:
            best = (year, " ".join(line.split())[:200])
    return best


def _year_for_match(match: re.Match[str], line: str) -> int | None:
    groups = match.groupdict()
    if groups.get("year"):
        return int(groups["year"])
    # Bare adverbs ("derzeit", "aktuell") only mean something next to a year.
    nearby = YEAR.search(line)
    return int(nearby.group(0)) if nearby else None


# ------------------------------------------------------- 4. attention multiplier
def _score_attention(
    article: Article, scoring: ScoringConfig, reasons: list[Reason]
) -> tuple[int | None, float]:
    if not article.pageviews:
        # Missing data must not punish or reward: neutral multiplier.
        reasons.append(Reason(code="no_pageview_data", points=0.0))
        return (None, 1.0)
    monthly = sorted(article.pageviews.items())
    mean_views = round(sum(v for _, v in monthly) / len(monthly))
    config = scoring.pageviews
    multiplier = 1.0 + config.strength * math.log10(1.0 + mean_views / config.pivot)
    multiplier = min(multiplier, config.max_multiplier)
    return (mean_views, multiplier)
