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
#: The dated category families, e.g. "Kategorie:Wikipedia:Veraltet seit 2024"
#: and "Kategorie:Wikipedia:Veraltet nach Mai 2025".
CATEGORY_YEAR = re.compile(r"Veraltet (?:seit|nach)\D{0,12}((?:19|20)\d{2})")
#: {{Zukunft|YYYY|MM}} announces that a passage goes stale after that month. It
#: is what actually populates the "Veraltet nach <Monat> <Jahr>" categories -
#: Kategorie:Wikipedia:Zukunft itself is empty.
ZUKUNFT_TEMPLATE = re.compile(r"\{\{\s*Zukunft\s*\|\s*((?:19|20)\d{2})\s*(?:\|\s*(\d{1,2}))?", re.IGNORECASE)

ROUND = 2


def scoring_reference(fetched_on: dt.date) -> dt.date:
    """The "now" used for every age calculation, snapped to the month.

    The fetch date itself is recorded exactly, but scoring against it would move
    every article's edit-age subscore on every run: a weekly job would produce a
    diff in which every row changed and none of it meant anything. Snapping to
    the first of the month means a run only differs from the previous one where
    the wiki actually changed, with one deliberate shift at each month boundary.
    """
    return fetched_on.replace(day=1)


def score_corpus(result: FetchResult, config: ScopeConfig) -> ScoreResult:
    scored = [_score_article(article, result, config.scoring) for article in result.articles]
    # Highest first; title breaks ties so equal scores never reorder between runs.
    scored.sort(key=lambda a: (-a.score, a.title))
    return ScoreResult(
        reference_date=result.reference_date,
        pageviews_window=f"{result.pageviews_start}-{result.pageviews_end}",
        articles=tuple(scored),
    )


def provisional_score(article: Article, reference: dt.date, scoring: ScoringConfig) -> float:
    """A lower bound on the article's final score, from discovery data alone.

    Uses only what one geosearch request already returned: the categories and
    the latest revision. It is a lower bound because the true last *substantive*
    edit is never newer than the latest revision, markers can only add, and the
    attention multiplier is never below 1. So an article ranking low here can
    still rank higher once detailed - which is why the detail cut is generous
    and configurable.
    """
    ignored: list[Reason] = []
    maintenance = _score_maintenance(article, scoring_reference(reference), scoring, ignored)
    latest = article.latest_revision
    if latest is None:
        return maintenance
    age_days = max((scoring_reference(reference) - latest.timestamp.date()).days, 0)
    factor = 1.0 - math.pow(0.5, age_days / scoring.edit_age_half_life_days)
    return maintenance + scoring.edit_age_weight * factor


def _score_article(article: Article, result: FetchResult, scoring: ScoringConfig) -> ScoredArticle:
    reference = scoring_reference(result.reference_date)
    reasons: list[Reason] = []

    maintenance = _score_maintenance(article, reference, scoring, reasons)
    edit_age, last_substantive, age_days = _score_edit_age(article, result, scoring, reasons)
    markers = _score_markers(article, reference, scoring, reasons)
    monthly_views, attention = _score_attention(article, scoring, reasons)

    base = maintenance + edit_age + markers
    total = base * attention

    if not article.detailed:
        reasons.append(
            Reason(
                code="provisional",
                detail="ausserhalb des Detail-Budgets; Score ist eine Untergrenze",
                points=0.0,
            )
        )

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
        provisional=not article.detailed,
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

    dated = _dated_staleness(article, reference, scoring)
    if dated is not None:
        code, detail, bonus = dated
        total += bonus
        reasons.append(Reason(code=code, detail=detail, points=round(bonus, ROUND)))
    return total


def _dated_staleness(
    article: Article, reference: dt.date, scoring: ScoringConfig
) -> tuple[str, str, float] | None:
    """How overdue an explicitly dated staleness marker is.

    Three sources, in order of precision:

    * ``{{Veraltet|seit=...}}`` - free text in practice, so parsed leniently;
    * ``{{Zukunft|YYYY|MM}}`` - a passage that goes stale after that month;
    * the dated categories, when the wikitext is unavailable.

    A malformed value scores nothing but is still reported: a broken ``seit=``
    is itself worth seeing in the worklist.
    """
    wikitext = article.wikitext or ""

    template = VERALTET_TEMPLATE.search(wikitext)
    if template:
        param = SEIT_PARAM.search(template.group(0))
        raw = param.group(1).strip() if param else ""
        found = YEAR.search(raw) if raw else None
        if found:
            year = int(found.group(0))
            stale_years = max(0, reference.year - year)
            return ("veraltet_seit", f"seit {year} ({stale_years}a)", _bonus_years(stale_years, scoring))
        if param:
            return ("veraltet_seit_unparsable", f"seit={raw!r}", 0.0)

    zukunft = ZUKUNFT_TEMPLATE.search(wikitext)
    if zukunft:
        year = int(zukunft.group(1))
        month = int(zukunft.group(2) or 1)
        due = dt.date(year, min(max(month, 1), 12), 1)
        if due > reference:
            return ("zukunft_offen", f"faellig ab {due.isoformat()}", 0.0)
        elapsed = (reference - due).days / 365.25
        return (
            "zukunft_faellig",
            f"faellig seit {due.isoformat()} ({elapsed:.1f}a)",
            _bonus_years(elapsed, scoring),
        )

    for category in article.categories:
        found_cat = CATEGORY_YEAR.search(category)
        if found_cat:
            year = int(found_cat.group(1))
            label = category.removeprefix("Kategorie:Wikipedia:")
            stale_years = max(0, reference.year - year)
            return ("veraltet_kategorie", f"{label} ({stale_years}a)", _bonus_years(stale_years, scoring))

    return None


def _bonus_years(years: float, scoring: ScoringConfig) -> float:
    return min(max(years, 0.0) * scoring.veraltet_seit_bonus_per_year, scoring.veraltet_seit_bonus_cap)


# --------------------------------------------- 2. time since substantive edit
def _score_edit_age(
    article: Article, result: FetchResult, scoring: ScoringConfig, reasons: list[Reason]
) -> tuple[float, dt.date | None, int | None]:
    revision = _last_substantive_revision(article, set(result.bot_accounts), scoring)
    if revision is None:
        reasons.append(Reason(code="no_substantive_edit", detail="none in fetched history", points=0.0))
        return (0.0, None, None)

    age_days = (scoring_reference(result.reference_date) - revision.timestamp.date()).days
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
        year = _year_for_match(match, line, rule.year_window)
        if year is None:
            continue
        if reference.year - year <= rule.max_age_years:
            continue
        if best is None or year < best[0]:
            best = (year, " ".join(line.split())[:200])
    return best


def _year_for_match(match: re.Match[str], line: str, window: int) -> int | None:
    groups = match.groupdict()
    if groups.get("year"):
        return int(groups["year"])
    # Bare adverbs ("derzeit", "aktuell") only mean something next to a year,
    # and "next to" means nearby, not merely somewhere on the same line.
    start = max(0, match.start() - window)
    end = min(len(line), match.end() + window)
    nearby = YEAR.search(line[start:end])
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
