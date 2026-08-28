#!/usr/bin/env python3
"""Phase 0 discovery probe for wp-todo.

Answers the six Phase 0 questions against the live de.wikipedia API and writes:

  docs/phase0-probe-output.json   raw evidence, every request and response
  docs/phase0-probe-summary.md    the compact answer sheet

READ-ONLY BY CONSTRUCTION. Every request is a GET against action=query,
action=paraminfo, or the pageviews REST endpoint. There is no code path here
that can write to any wiki, and there must never be one.

Usage:
    export WP_TODO_CONTACT="https://github.com/metaodi/wp-todo"
    python3 scripts/phase0_probe.py            # stdlib only, no dependencies
    python3 scripts/phase0_probe.py --only P3
    python3 scripts/phase0_probe.py --summarize docs/phase0-probe-output.json

Requests are serialised with a delay between them, per API:Etiquette.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://de.wikipedia.org/w/api.php"
PAGEVIEWS = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"

CONTACT = os.environ.get("WP_TODO_CONTACT", "").strip()
UA = (
    f"wp-todo-phase0-probe/0.1 ({CONTACT or 'UNSET-CONTACT'}) "
    f"python-urllib/{sys.version_info.major}.{sys.version_info.minor}"
)

DELAY_S = float(os.environ.get("WP_TODO_DELAY_S", "1.0"))

# Tiles over the Bezirk Horgen / Zuerichsee shore. Approximate on purpose: the
# point of P2 is to see what comes back, not to be exhaustive yet.
HORGEN_TILES = [
    (47.2588, 8.5900, "Horgen"),
    (47.2906, 8.5661, "Thalwil"),
    (47.3200, 8.5500, "Adliswil/Kilchberg"),
    (47.2200, 8.6300, "Waedenswil/Richterswil"),
]

# Names the brief assumes exist. The probe checks each rather than trusting it.
CANDIDATE_CATEGORIES = [
    "Kategorie:Wikipedia:Veraltet",
    "Kategorie:Wikipedia:Lückenhaft",
    "Kategorie:Wikipedia:Überarbeiten",
    "Kategorie:Wikipedia:Belege fehlen",
    "Kategorie:Wikipedia:Weblink offline",
    "Kategorie:Wikipedia:Defekter Weblink",
    "Kategorie:Wikipedia:Zukunft",
    "Kategorie:Wikipedia:Veraltet seit 2024",
    "Kategorie:Wikipedia:Veraltet seit 2025",
]


def get(url: str, *, note: str = "") -> dict[str, Any]:
    """One GET. Records status, elapsed time and body (or the error body)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Encoding": "identity", "Accept": "application/json"}
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status, headers = resp.status, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status, headers = exc.code, dict(exc.headers)
    except Exception as exc:  # noqa: BLE001 - a probe reports failures, never raises
        return {"url": url, "note": note, "error": f"{type(exc).__name__}: {exc}"}
    elapsed = round(time.monotonic() - started, 3)
    time.sleep(DELAY_S)
    try:
        body: Any = json.loads(raw)
    except json.JSONDecodeError:
        body = {"_non_json_body": raw[:2000]}
    return {
        "url": url,
        "note": note,
        "status": status,
        "elapsed_s": elapsed,
        "retry_after": headers.get("Retry-After"),
        "body": body,
    }


def q(**params: Any) -> str:
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    return f"{API}?{urllib.parse.urlencode(params)}"


def dig(obj: Any, *path: Any, default: Any = None) -> Any:
    """Walk dicts/lists defensively; probe output is untrusted shape."""
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        elif isinstance(cur, list) and isinstance(key, int) and -len(cur) <= key < len(cur):
            cur = cur[key]
        else:
            return default
    return cur


def api_error(resp: dict[str, Any]) -> str:
    """One-line rendering of whatever went wrong, or ''."""
    if "error" in resp and isinstance(resp["error"], str):
        return resp["error"]
    code = dig(resp, "body", "error", "code")
    if code:
        return f"{code}: {dig(resp, 'body', 'error', 'info', default='')}"
    return ""


def warnings_of(resp: dict[str, Any]) -> str:
    warn = dig(resp, "body", "warnings", default={})
    if isinstance(warn, dict):
        parts = []
        for mod, val in warn.items():
            text = val.get("warnings") if isinstance(val, dict) else val
            parts.append(f"{mod}: {text}")
        return " | ".join(parts)[:500]
    return str(warn)[:500]


def pages_of(resp: dict[str, Any]) -> list[dict[str, Any]]:
    pages = dig(resp, "body", "query", "pages", default=[])
    return pages if isinstance(pages, list) else list(pages.values())


# ---------------------------------------------------------------- P1 geosearch
def p1_geosearch() -> Any:
    out: dict[str, Any] = {}
    base = dict(action="query", generator="geosearch", ggscoord="47.2906|8.5661")

    out["radius_10000"] = get(q(**base, ggsradius=10000, ggslimit=10), note="documented max radius")
    out["radius_10001"] = get(q(**base, ggsradius=10001, ggslimit=10), note="expect an error naming the real max")
    out["limit_500"] = get(q(**base, ggsradius=10000, ggslimit=500), note="documented non-bot max")
    out["limit_501"] = get(q(**base, ggsradius=10000, ggslimit=501), note="expect a warning or a clamp")
    out["limit_max"] = get(q(**base, ggsradius=10000, ggslimit="max"), note="what does 'max' resolve to")

    # The question that shapes the fetch layer: generator + props in ONE request.
    combined_params = dict(
        **base,
        ggsradius=10000,
        ggslimit=500,
        prop="categories|revisions",
        clshow="hidden",
        cllimit="max",
        rvprop="timestamp|user|flags|comment|size",
    )
    combined = q(**combined_params)
    out["combined_first_page"] = get(combined, note="generator + prop=categories + prop=revisions in one request")

    body = out["combined_first_page"].get("body")
    if isinstance(body, dict) and isinstance(body.get("continue"), dict):
        cont = dict(body["continue"])
        out["combined_second_page"] = get(
            combined + "&" + urllib.parse.urlencode(cont), note=f"continued with {sorted(cont)}"
        )
        out["continue_keys_observed"] = sorted(cont)
    else:
        out["combined_second_page"] = None
        out["continue_keys_observed"] = []
    return out


# ------------------------------------------------- P2 hidden maintenance cats
def p2_hidden_categories() -> Any:
    counts: dict[str, int] = {}
    pages_seen: set[str] = set()
    per_tile: dict[str, Any] = {}
    veraltet_candidates: list[str] = []
    requests_made = 0

    for lat, lon, label in HORGEN_TILES:
        tile_counts: dict[str, int] = {}
        params = dict(
            action="query",
            generator="geosearch",
            ggscoord=f"{lat}|{lon}",
            ggsradius=10000,
            ggslimit=500,
            prop="categories",
            clshow="hidden",
            cllimit="max",
        )
        url = q(**params)
        guard = 0
        while url and guard < 30:
            guard += 1
            requests_made += 1
            resp = get(url, note=f"tile {label}")
            body = resp.get("body")
            if not isinstance(body, dict) or "query" not in body:
                per_tile[label] = {"_error": api_error(resp) or resp.get("error") or "no query in response"}
                break
            for page in pages_of(resp):
                if page.get("ns") != 0:
                    continue
                pages_seen.add(page["title"])
                for cat in page.get("categories") or []:
                    name = cat.get("title", "?")
                    tile_counts[name] = tile_counts.get(name, 0) + 1
                    counts[name] = counts.get(name, 0) + 1
                    if "Veraltet" in name and page["title"] not in veraltet_candidates:
                        veraltet_candidates.append(page["title"])
            cont = body.get("continue")
            url = q(**params) + "&" + urllib.parse.urlencode(cont) if isinstance(cont, dict) else ""
        else:
            per_tile.setdefault(label, {})
        if label not in per_tile or "_error" not in per_tile[label]:
            per_tile[label] = dict(sorted(tile_counts.items(), key=lambda kv: (-kv[1], kv[0])))

    # Do the candidate category names in the brief actually exist?
    existence = get(
        q(action="query", titles="|".join(CANDIDATE_CATEGORIES), prop="categoryinfo"),
        note="which assumed category names are real, and how many members each has",
    )

    # Does {{Veraltet|seit=}} surface as a category, or only in the wikitext?
    if not veraltet_candidates:
        members = get(
            q(action="query", list="categorymembers", cmtitle="Kategorie:Wikipedia:Veraltet", cmlimit=5, cmnamespace=0),
            note="fallback sample: any dewiki article carrying the Veraltet template",
        )
        veraltet_candidates = [m["title"] for m in dig(members, "body", "query", "categorymembers", default=[])][:3]
    else:
        members = None

    wikitext = None
    seit_findings: list[dict[str, Any]] = []
    if veraltet_candidates:
        sample = veraltet_candidates[:3]
        wikitext = get(
            q(
                action="query",
                prop="revisions",
                titles="|".join(sample),
                rvprop="content",
                rvslots="main",
                rvlimit=1,
            ),
            note="read the Veraltet invocation to see whether seit= is present and how",
        )
        for page in pages_of(wikitext):
            text = dig(page, "revisions", 0, "slots", "main", "content", default="") or ""
            for match in re.finditer(r"\{\{\s*[Vv]eraltet[^}]{0,400}\}\}", text, re.S):
                snippet = " ".join(match.group(0).split())
                seit_findings.append(
                    {
                        "page": page.get("title"),
                        "invocation": snippet[:300],
                        "has_seit": bool(re.search(r"seit\s*=", snippet)),
                    }
                )

    return {
        "articles_seen": len(pages_seen),
        "requests_made": requests_made,
        "note": "counts are page-occurrences; a page is counted once per category per tile",
        "totals_sorted": dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "per_tile": per_tile,
        "candidate_category_existence": existence,
        "veraltet_sample_pages": veraltet_candidates[:3],
        "veraltet_categorymembers_fallback": members,
        "veraltet_wikitext_probe": wikitext,
        "veraltet_seit_findings": seit_findings,
    }


# ------------------------------------------------------- P3 CirrusSearch verbs
def p3_search_keywords() -> Any:
    out: dict[str, Any] = {}
    probes = {
        "deepcat": 'deepcat:"Bezirk Horgen"',
        "deepcategory": 'deepcategory:"Bezirk Horgen"',
        "hastemplate": 'hastemplate:"Veraltet" incategory:"Kanton Zürich"',
        "insource_plain": 'insource:"Stand: 2019" incategory:"Kanton Zürich"',
        "insource_regex_scoped": 'incategory:"Kanton Zürich" insource:"Stand" insource:/Stand: 20(1[0-9])/',
        "insource_regex_bare": "insource:/Stand: 201[0-9]/",
        "combined": 'deepcat:"Bezirk Horgen" hastemplate:"Veraltet"',
    }
    for name, srsearch in probes.items():
        out[name] = get(
            q(
                action="query",
                list="search",
                srsearch=srsearch,
                srlimit=10,
                srprop="snippet|timestamp|size",
                srinfo="totalhits|suggestion",
            ),
            note=srsearch,
        )
    out["regex_timeout_shape"] = get(
        q(action="query", list="search", srsearch="insource:/[Ss]tand:? *20[0-2][0-9]/", srlimit=1),
        note="deliberately expensive: does a timeout error, or silently truncate? watch elapsed_s",
    )
    return out


# --------------------------------------------------------- P4 revision flags
def p4_revisions() -> Any:
    out: dict[str, Any] = {}
    out["revisions_multi_title"] = get(
        q(
            action="query",
            prop="revisions",
            titles="Thalwil|Horgen|Wädenswil",
            rvprop="ids|timestamp|user|flags|comment|size|tags",
            rvlimit=50,
        ),
        note="rvlimit>1 with multiple titles: expect an error, confirming the per-page fetch shape",
    )
    out["revisions_single"] = get(
        q(
            action="query",
            prop="revisions",
            titles="Thalwil",
            rvprop="ids|timestamp|user|userid|flags|comment|size|tags",
            rvlimit=50,
        ),
        note="does ANY revision object carry a 'bot' key, or only 'minor'?",
    )
    out["recentchanges_bot"] = get(
        q(
            action="query",
            list="recentchanges",
            rcprop="title|timestamp|user|flags|comment|sizes|tags",
            rclimit=20,
            rcshow="bot",
        ),
        note="rc does carry a real bot flag",
    )
    out["recentchanges_oldest"] = get(
        q(action="query", list="recentchanges", rcprop="timestamp", rclimit=1, rcdir="newer"),
        note="oldest rc entry = the retention window we would be relying on",
    )
    out["user_groups"] = get(
        q(action="query", list="users", ususers="Xqbot|Aka|InternetArchiveBot", usprop="groups|editcount"),
        note="fallback bot detection: current group membership",
    )
    out["botlist"] = get(
        q(action="query", list="allusers", augroup="bot", aulimit=500),
        note="full dewiki bot roster; is caching it viable?",
    )
    return out


# ------------------------------------------------------------- P5 pageviews
def p5_pageviews() -> Any:
    out: dict[str, Any] = {}
    cases = {
        "ascii": "Thalwil",
        "umlaut": "Wädenswil",
        "space": "Bezirk Horgen",
        "accent": "Rüschlikon",
        "definitely_missing": "Wp Todo Nonexistent Article Xyzzy",
    }
    for name, title in cases.items():
        enc = urllib.parse.quote(title.replace(" ", "_"), safe="")
        out[name] = get(
            f"{PAGEVIEWS}/de.wikipedia.org/all-access/all-agents/{enc}/monthly/20250101/20251201",
            note=f"title={title!r} encoded={enc}",
        )
    out["date_format_hh"] = get(
        f"{PAGEVIEWS}/de.wikipedia.org/all-access/all-agents/Thalwil/monthly/2025010100/2025120100",
        note="does the monthly endpoint accept YYYYMMDDHH as well as YYYYMMDD",
    )
    out["agent_user"] = get(
        f"{PAGEVIEWS}/de.wikipedia.org/all-access/user/Thalwil/monthly/20250101/20251201",
        note="agent=user excludes spiders; compare the numbers against all-agents",
    )
    out["aqs2_host"] = get(
        "https://api.wikimedia.org/wiki/rest_v1/metrics/pageviews/per-article"
        "/de.wikipedia.org/all-access/user/Thalwil/monthly/20250101/20251201",
        note="is the newer AQS host reachable without auth, or is rest_v1 still the path",
    )
    return out


# ------------------------------------------------------- P6 etiquette/maxlag
def p6_etiquette() -> Any:
    out: dict[str, Any] = {}
    out["maxlag_5"] = get(q(action="query", meta="siteinfo", siprop="general", maxlag=5), note="maxlag on a read")
    out["maxlag_neg1"] = get(
        q(action="query", meta="siteinfo", siprop="general", maxlag=-1),
        note="force the lag error to see the 503 + Retry-After shape",
    )
    out["siteinfo"] = get(
        q(action="query", meta="siteinfo", siprop="general|statistics"), note="server time, wiki id, article count"
    )
    out["paraminfo"] = get(
        q(action="paraminfo", modules="query+geosearch|query+search|query+revisions|query+categories"),
        note="AUTHORITATIVE limits: read min/max/highmax out of this, not out of the wiki docs",
    )
    return out


# --------------------------------------------- P7 follow-ups from run 1
def p7_followups() -> Any:
    """Closes the gaps left by the first run.

    Run 1 left four things open: two probes were malformed (rvlimit cannot be
    combined with multiple titles), hastemplate returned an inconclusive zero,
    and the combined generator query never needed continuation, so the continue
    keys were never observed.
    """
    out: dict[str, Any] = {}

    # (a) Does {{Veraltet}} carry seit=, and how do the dated categories arise?
    # One title per request this time: rvlimit is single-page only.
    samples: list[str] = []
    for cat in (
        "Kategorie:Wikipedia:Veraltet",
        "Kategorie:Wikipedia:Veraltet seit 2024",
        "Kategorie:Wikipedia:Veraltet nach Mai 2025",
        "Kategorie:Wikipedia:Veraltet in zwei bis drei Jahren",
    ):
        members = get(
            q(action="query", list="categorymembers", cmtitle=cat, cmlimit=3, cmnamespace=0),
            note=f"sample members of {cat}",
        )
        out[f"members_{cat}"] = members
        for m in dig(members, "body", "query", "categorymembers", default=[]) or []:
            if m["title"] not in samples:
                samples.append(m["title"])

    findings: list[dict[str, Any]] = []
    for title in samples[:6]:
        resp = get(
            q(action="query", prop="revisions", titles=title, rvprop="content", rvslots="main", rvlimit=1),
            note=f"wikitext of {title}",
        )
        out[f"wikitext_{title}"] = {"status": resp.get("status"), "error": api_error(resp)}
        for page in pages_of(resp):
            text = dig(page, "revisions", 0, "slots", "main", "content", default="") or ""
            for match in re.finditer(r"\{\{\s*[Vv]eraltet[^{}]{0,400}\}\}", text, re.S):
                snippet = " ".join(match.group(0).split())
                findings.append(
                    {
                        "page": page.get("title"),
                        "invocation": snippet[:300],
                        "has_seit": bool(re.search(r"seit\s*=", snippet)),
                        "seit_value": (re.search(r"seit\s*=\s*([^|}]+)", snippet) or [None, None])[1],
                    }
                )
        # And the hidden categories that same page ended up in.
        cats = get(
            q(action="query", titles=title, prop="categories", clshow="hidden", cllimit="max"),
            note=f"hidden categories of {title}",
        )
        out[f"cats_{title}"] = [
            c["title"] for p in pages_of(cats) for c in (p.get("categories") or []) if "Veraltet" in c["title"]
        ]
    out["veraltet_findings"] = findings

    # (b) hastemplate returned 0 in run 1 - which half of the query was empty?
    for name, srsearch in {
        "hastemplate_bare": "hastemplate:Veraltet",
        "hastemplate_quoted": 'hastemplate:"Veraltet"',
        "hastemplate_prefixed": 'hastemplate:"Vorlage:Veraltet"',
        "incategory_only": 'incategory:"Kanton Zürich"',
        "incategory_underscore": "incategory:Kanton_Zürich",
        "hastemplate_plus_deepcat": 'hastemplate:Veraltet deepcat:"Bezirk Horgen"',
    }.items():
        out[name] = get(
            q(action="query", list="search", srsearch=srsearch, srlimit=3, srinfo="totalhits"), note=srsearch
        )

    # (c) Force the categories prop past its limit to observe the continue keys.
    forced = dict(
        action="query",
        generator="geosearch",
        ggscoord="47.2906|8.5661",
        ggsradius=10000,
        ggslimit=500,
        prop="categories",
        clshow="hidden",
        cllimit=10,
    )
    first = get(q(**forced), note="cllimit=10 forces continuation; record the key names")
    out["forced_continuation_first"] = {
        "status": first.get("status"),
        "continue": dig(first, "body", "continue"),
        "limits": dig(first, "body", "limits"),
        "batchcomplete": "batchcomplete" in (first.get("body") or {}),
        "pages": len(pages_of(first)),
    }
    cont = dig(first, "body", "continue", default={})
    if isinstance(cont, dict) and cont:
        second = get(q(**forced) + "&" + urllib.parse.urlencode(cont), note="second page")
        out["forced_continuation_second"] = {
            "status": second.get("status"),
            "continue": dig(second, "body", "continue"),
            "batchcomplete": "batchcomplete" in (second.get("body") or {}),
            "pages": len(pages_of(second)),
        }

    # (d) maxlag: run 1 saw HTTP 200 for an artificial maxlag=-1. Check a
    # realistic threshold and record the status code and headers precisely.
    out["maxlag_0"] = get(q(action="query", meta="siteinfo", siprop="general", maxlag=0), note="realistic-ish trigger")

    # (e) The AQS 2.0 probe in run 1 hit a wiki portal page, not an API.
    out["aqs2_correct_base"] = get(
        "https://api.wikimedia.org/metrics/pageviews/per-article"
        "/de.wikipedia.org/all-access/user/Thalwil/monthly/20250101/20251201",
        note="the actual AQS 2.0 base path",
    )
    return out


# ------------------------------- P8 are the dated Veraltet categories hidden?
def p8_hidden_flag() -> Any:
    """P7 raised a design-critical question.

    Pages sampled *from* Kategorie:Wikipedia:Veraltet seit 2024 came back with
    no Veraltet category at all under clshow=hidden. If the dated categories
    are not hidden categories, then harvesting with clshow=hidden silently
    misses the most precise staleness signal we have.
    """
    out: dict[str, Any] = {}

    interesting = [
        "Kategorie:Wikipedia:Veraltet",
        "Kategorie:Wikipedia:Veraltet seit 2024",
        "Kategorie:Wikipedia:Veraltet nach Mai 2025",
        "Kategorie:Wikipedia:Veraltet in zwei bis drei Jahren",
        "Kategorie:Wikipedia:Weblink offline",
        "Kategorie:Wikipedia:Lückenhaft",
        "Kategorie:Wikipedia:Belege fehlen",
    ]
    out["categoryinfo"] = get(
        q(action="query", titles="|".join(interesting), prop="categoryinfo"),
        note="categoryinfo reports a 'hidden' flag per category",
    )

    # And the empirical check: same page, hidden-only vs all categories.
    samples: list[str] = []
    for cat in ("Kategorie:Wikipedia:Veraltet seit 2024", "Kategorie:Wikipedia:Veraltet nach Mai 2025"):
        members = get(q(action="query", list="categorymembers", cmtitle=cat, cmlimit=2, cmnamespace=0), note=cat)
        samples.extend(m["title"] for m in dig(members, "body", "query", "categorymembers", default=[]) or [])

    comparison: dict[str, Any] = {}
    for title in samples[:4]:
        hidden = get(
            q(action="query", titles=title, prop="categories", clshow="hidden", cllimit="max"), note=f"hidden {title}"
        )
        every = get(q(action="query", titles=title, prop="categories", cllimit="max"), note=f"all {title}")
        h = {c["title"] for p in pages_of(hidden) for c in (p.get("categories") or [])}
        a = {c["title"] for p in pages_of(every) for c in (p.get("categories") or [])}
        comparison[title] = {
            "veraltet_when_hidden_only": sorted(x for x in h if "Veraltet" in x),
            "veraltet_in_all_categories": sorted(x for x in a if "Veraltet" in x),
            "hidden_count": len(h),
            "all_count": len(a),
        }
    out["per_page_comparison"] = comparison
    return out


PROBES = {
    "P1": ("geosearch generator limits, prop combination, continuation", p1_geosearch),
    "P2": ("hidden maintenance categories in the Bezirk Horgen area", p2_hidden_categories),
    "P3": ("CirrusSearch deepcat / hastemplate / insource regex", p3_search_keywords),
    "P4": ("revision flags: bot and minor edit detection", p4_revisions),
    "P5": ("pageviews REST endpoint shape and title encoding", p5_pageviews),
    "P6": ("User-Agent policy, maxlag on reads, paraminfo limits", p6_etiquette),
    "P7": ("follow-ups: Veraltet seit=, hastemplate, continuation keys, maxlag status", p7_followups),
    "P8": ("are the dated Veraltet categories hidden categories?", p8_hidden_flag),
}


# ------------------------------------------------------------------ summary
def _row(resp: Any) -> str:
    if not isinstance(resp, dict):
        return "not run"
    err = api_error(resp)
    bits = [f"HTTP {resp.get('status', '-')}", f"{resp.get('elapsed_s', '-')}s"]
    if err:
        bits.append(f"**error** `{err}`")
    warn = warnings_of(resp)
    if warn:
        bits.append(f"warn `{warn[:200]}`")
    return " · ".join(bits)


def summarize(raw: dict[str, Any]) -> str:
    probes = raw.get("probes", {})
    L: list[str] = []
    add = L.append

    add("# Phase 0 probe — answer sheet")
    add("")
    add(f"User-Agent sent: `{raw.get('user_agent', '?')}`")
    add("")
    add("Generated by `scripts/phase0_probe.py`. Raw evidence for every line is in")
    add("`docs/phase0-probe-output.json` (workflow artifact `phase0-probe-raw`).")
    add("")

    # -- P1
    p1 = dig(probes, "P1", "result", default={})
    if p1:
        add("## P1 — geosearch as a generator")
        add("")
        add("| probe | result | pages returned | categories present |")
        add("| --- | --- | --- | --- |")
        for key in ("radius_10000", "radius_10001", "limit_500", "limit_501", "limit_max", "combined_first_page"):
            resp = p1.get(key)
            pages = pages_of(resp) if isinstance(resp, dict) else []
            has_cats = any("categories" in p for p in pages)
            add(f"| `{key}` | {_row(resp)} | {len(pages)} | {'yes' if has_cats else 'no'} |")
        add("")
        add(f"- continuation keys observed on the combined query: `{p1.get('continue_keys_observed')}`")
        second = p1.get("combined_second_page")
        add(f"- second page: {_row(second) if second else 'no continuation returned'}")
        add("")

    # -- P2
    p2 = dig(probes, "P2", "result", default={})
    if p2:
        add("## P2 — hidden maintenance categories, Bezirk Horgen area")
        add("")
        add(f"Articles seen: **{p2.get('articles_seen')}** over {p2.get('requests_made')} requests.")
        add("")
        totals = p2.get("totals_sorted") or {}
        if totals:
            add("| hidden category | articles |")
            add("| --- | --- |")
            for name, count in list(totals.items())[:60]:
                add(f"| `{name}` | {count} |")
            if len(totals) > 60:
                add(f"| _… {len(totals) - 60} more, see raw JSON_ | |")
        else:
            add("_No hidden categories returned — check the raw output for errors._")
        add("")
        add("### Do the assumed category names exist?")
        add("")
        add("| candidate | exists | members |")
        add("| --- | --- | --- |")
        for page in pages_of(p2.get("candidate_category_existence", {})):
            missing = page.get("missing", False)
            pages_n = dig(page, "categoryinfo", "pages", default="-")
            add(f"| `{page.get('title')}` | {'no' if missing else 'yes'} | {pages_n} |")
        add("")
        add("### Does `{{Veraltet}}` carry `seit=`?")
        add("")
        findings = p2.get("veraltet_seit_findings") or []
        if findings:
            for f in findings[:10]:
                add(f"- **{f['page']}** — `seit=` {'present' if f['has_seit'] else 'ABSENT'} — `{f['invocation']}`")
        else:
            add("_No `{{Veraltet}}` invocation captured; see raw output._")
        add("")

    # -- P3
    p3 = dig(probes, "P3", "result", default={})
    if p3:
        add("## P3 — CirrusSearch keywords on dewiki")
        add("")
        add("| keyword probe | result | totalhits | results |")
        add("| --- | --- | --- | --- |")
        for key, resp in p3.items():
            hits = dig(resp, "body", "query", "searchinfo", "totalhits", default="-")
            n = len(dig(resp, "body", "query", "search", default=[]) or [])
            add(f"| `{key}` | {_row(resp)} | {hits} | {n} |")
        add("")

    # -- P4
    p4 = dig(probes, "P4", "result", default={})
    if p4:
        add("## P4 — bot and minor edit detection")
        add("")
        single = p4.get("revisions_single", {})
        revs = dig(pages_of(single), 0, "revisions", default=[]) or []
        keys: set[str] = set()
        for rev in revs:
            keys.update(rev.keys())
        add(f"- `revisions_single`: {_row(single)}, {len(revs)} revisions")
        add(f"- union of keys on revision objects: `{sorted(keys)}`")
        add(f"- **is there a `bot` key on revisions? {'YES' if 'bot' in keys else 'NO'}**")
        add(f"- revisions flagged `minor`: {sum(1 for r in revs if r.get('minor'))}/{len(revs)}")
        add(f"- multi-title `rvlimit=50`: {_row(p4.get('revisions_multi_title'))}")
        rc_old = dig(p4.get("recentchanges_oldest", {}), "body", "query", "recentchanges", 0, "timestamp", default="?")
        add(f"- oldest recentchanges entry (retention window): `{rc_old}`")
        bots = dig(p4.get("botlist", {}), "body", "query", "allusers", default=[]) or []
        add(f"- dewiki accounts in the `bot` group: **{len(bots)}**")
        groups = dig(p4.get("user_groups", {}), "body", "query", "users", default=[]) or []
        for u in groups:
            add(f"  - `{u.get('name')}` groups={u.get('groups', [])} edits={u.get('editcount', '?')}")
        add("")

    # -- P5
    p5 = dig(probes, "P5", "result", default={})
    if p5:
        add("## P5 — pageviews REST endpoint")
        add("")
        add("| case | result | months returned | first → last |")
        add("| --- | --- | --- | --- |")
        for key, resp in p5.items():
            items = dig(resp, "body", "items", default=[]) or []
            span = f"{items[0].get('timestamp')} → {items[-1].get('timestamp')}" if items else "-"
            add(f"| `{key}` | {_row(resp)} | {len(items)} | {span} |")
        add("")
        missing = p5.get("definitely_missing", {})
        add(f"- missing-title response body: `{json.dumps(missing.get('body'), ensure_ascii=False)[:400]}`")
        add(f"- AQS 2.0 host probe: {_row(p5.get('aqs2_host'))}")
        add("")

    # -- P6
    p6 = dig(probes, "P6", "result", default={})
    if p6:
        add("## P6 — etiquette, maxlag, authoritative limits")
        add("")
        add(f"- `maxlag=5` on a read: {_row(p6.get('maxlag_5'))}")
        neg = p6.get("maxlag_neg1", {})
        add(f"- `maxlag=-1` (forced lag error): {_row(neg)} · `Retry-After: {neg.get('retry_after')}`")
        add("")
        add("### paraminfo — the limits that actually apply")
        add("")
        add("| module | param | min | max | highmax |")
        add("| --- | --- | --- | --- | --- |")
        for module in dig(p6.get("paraminfo", {}), "body", "paraminfo", "modules", default=[]) or []:
            for param in module.get("parameters", []):
                if param.get("name") in {"radius", "limit"} or "max" in param:
                    add(
                        f"| `{module.get('name')}` | `{param.get('name')}` | {param.get('min', '-')} "
                        f"| {param.get('max', '-')} | {param.get('highmax', '-')} |"
                    )
        add("")

    # -- P7
    p7 = dig(probes, "P7", "result", default={})
    if p7:
        add("## P7 — follow-ups")
        add("")
        add("### `{{Veraltet}}` invocations and the categories they produce")
        add("")
        for f in (p7.get("veraltet_findings") or [])[:12]:
            seit = f"`seit={f['seit_value']}`" if f.get("has_seit") else "**no `seit=`**"
            add(f"- **{f['page']}** — {seit}")
            add(f"  - `{f['invocation']}`")
            add(f"  - lands in: `{p7.get('cats_' + f['page'], [])}`")
        add("")
        add("### hastemplate / incategory")
        add("")
        add("| query | result | totalhits |")
        add("| --- | --- | --- |")
        for key in (
            "hastemplate_bare",
            "hastemplate_quoted",
            "hastemplate_prefixed",
            "incategory_only",
            "incategory_underscore",
            "hastemplate_plus_deepcat",
        ):
            resp = p7.get(key)
            hits = dig(resp, "body", "query", "searchinfo", "totalhits", default="-")
            add(f"| `{key}` | {_row(resp)} | {hits} |")
        add("")
        add("### Continuation keys (forced with cllimit=10)")
        add("")
        add(f"- first page: `{json.dumps(p7.get('forced_continuation_first'), ensure_ascii=False)}`")
        add(f"- second page: `{json.dumps(p7.get('forced_continuation_second'), ensure_ascii=False)}`")
        add("")
        add(f"- `maxlag=0`: {_row(p7.get('maxlag_0'))} · `Retry-After: {dig(p7, 'maxlag_0', 'retry_after')}`")
        add(f"- AQS 2.0 correct base: {_row(p7.get('aqs2_correct_base'))}")
        add("")

    # -- P8
    p8 = dig(probes, "P8", "result", default={})
    if p8:
        add("## P8 — are the dated Veraltet categories hidden?")
        add("")
        add("| category | exists | hidden | members |")
        add("| --- | --- | --- | --- |")
        for page in pages_of(p8.get("categoryinfo", {})):
            info = page.get("categoryinfo") or {}
            add(
                f"| `{page.get('title')}` | {'no' if page.get('missing') else 'yes'} "
                f"| {'**yes**' if info.get('hidden') else '**NO**'} | {info.get('pages', '-')} |"
            )
        add("")
        add("| sample page | Veraltet cats under clshow=hidden | Veraltet cats in all categories |")
        add("| --- | --- | --- |")
        for title, cmp in (p8.get("per_page_comparison") or {}).items():
            add(
                f"| {title} | `{cmp['veraltet_when_hidden_only']}` "
                f"| `{cmp['veraltet_in_all_categories']}` |"
            )
        add("")

    add("---")
    add("")
    add("Probes that failed outright (transport errors):")
    add("")
    failures = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if "url" in node and "error" in node and "status" not in node:
                failures.append(f"- `{path}` — {node['error']}")
                return
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(probes, "")
    L.extend(failures or ["- none"])
    add("")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", choices=sorted(PROBES), help="run a subset of probes")
    ap.add_argument("--out", default="docs/phase0-probe-output.json")
    ap.add_argument("--summary-out", default="docs/phase0-probe-summary.md")
    ap.add_argument("--summarize", metavar="RAW_JSON", help="skip probing; re-render the summary from raw output")
    args = ap.parse_args()

    if args.summarize:
        with open(args.summarize, encoding="utf-8") as fh:
            raw = json.load(fh)
    else:
        if not CONTACT:
            print(
                "ERROR: set WP_TODO_CONTACT (email or project URL). The Wikimedia User-Agent\n"
                "policy requires contact information, and a default UA may be blocked outright.",
                file=sys.stderr,
            )
            return 2
        results: dict[str, Any] = {}
        for key in args.only or sorted(PROBES):
            label, fn = PROBES[key]
            print(f"[{key}] {label} ...", file=sys.stderr, flush=True)
            try:
                results[key] = {"label": label, "result": fn()}
            except Exception as exc:  # noqa: BLE001
                results[key] = {"label": label, "error": f"{type(exc).__name__}: {exc}"}
            print(f"[{key}] done", file=sys.stderr, flush=True)
        raw = {"user_agent": UA, "probes": results}
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"wrote {args.out}", file=sys.stderr)

    with open(args.summary_out, "w", encoding="utf-8") as fh:
        fh.write(summarize(raw))
    print(f"wrote {args.summary_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
