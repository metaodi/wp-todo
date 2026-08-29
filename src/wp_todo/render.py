"""Rendering. Both artefacts must be byte-identical between runs over the same
data, so nothing here reads a clock."""

from __future__ import annotations

import json
from collections.abc import Sequence

from .models import ScoredArticle, ScoreResult

MAX_REASONS = 3


def render_markdown(result: ScoreResult, *, label_order: Sequence[str] = ()) -> str:
    groups: dict[str, list[ScoredArticle]] = {}
    for article in result.articles:
        groups.setdefault(article.scope_label, []).append(article)

    ordered = [label for label in label_order if label in groups]
    ordered += sorted(label for label in groups if label not in ordered)

    lines: list[str] = [
        "# Wikipedia-TODO",
        "",
        "Artikel im konfigurierten Bereich, die vermutlich eine Aktualisierung brauchen,",
        "sortiert nach Score. Der Score ist "
        "`(Wartungsbausteine + Bearbeitungsalter + Textmarker) × Aufmerksamkeit`.",
        "",
        f"Insgesamt {len(result.articles)} Artikel in {len(ordered)} Bereichen.",
        "",
    ]

    for label in ordered:
        articles = groups[label]
        scored = [a for a in articles if a.score > 0]
        lines.append(f"## {label}")
        lines.append("")
        if not scored:
            lines.append("_Keine Auffälligkeiten._")
            lines.append("")
            continue
        lines.append("| Artikel | Score | Gründe | Letzte inhaltliche Änderung | Aufrufe/Monat | |")
        lines.append("| --- | ---: | --- | --- | ---: | --- |")
        for article in scored:
            lines.append(
                "| [{title}](https://de.wikipedia.org/wiki/{link}) | {score} | {reasons} "
                "| {edit} | {views} | [bearbeiten]({url}) |".format(
                    title=_escape(article.title),
                    link=article.title.replace(" ", "_"),
                    score=_number(article.score),
                    reasons=_reasons(article),
                    edit=article.last_substantive_edit.isoformat() if article.last_substantive_edit else "—",
                    views=article.monthly_pageviews if article.monthly_pageviews is not None else "—",
                    url=article.edit_url,
                )
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_json(result: ScoreResult) -> str:
    payload = result.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _reasons(article: ScoredArticle) -> str:
    parts: list[str] = []
    for reason in article.reasons[:MAX_REASONS]:
        if reason.points <= 0:
            continue
        detail = f" ({_escape(reason.detail)})" if reason.detail else ""
        parts.append(f"{reason.code}{detail} +{_number(reason.points)}")
    if article.subscores.attention != 1.0:
        parts.append(f"×{_number(article.subscores.attention)}")
    return "; ".join(parts) if parts else "—"


def _number(value: float) -> str:
    """Fixed precision keeps the diff quiet when a float wobbles in the last bit."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _escape(text: str) -> str:
    return text.replace("|", "\\|")
