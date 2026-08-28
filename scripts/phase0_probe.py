#!/usr/bin/env python3
"""Phase 0 discovery probe for wp-todo.

Runs every open question from the Phase 0 gate against the live de.wikipedia
API and writes the raw evidence to docs/phase0-probe-output.json.

Read-only: every request is a GET against action=query / list=search /
prop=revisions / the pageviews REST endpoint. Nothing here can write.

Usage:
    export WP_TODO_CONTACT="you@example.com"      # goes into the User-Agent
    python3 scripts/phase0_probe.py               # stdlib only, no deps
    python3 scripts/phase0_probe.py --only P3     # single probe

Requests are serialised with a delay between them (API:Etiquette).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://de.wikipedia.org/w/api.php"
PAGEVIEWS = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"

CONTACT = os.environ.get("WP_TODO_CONTACT", "UNSET-CONTACT")
UA = f"wp-todo-phase0-probe/0.1 ({CONTACT}) python-urllib/{sys.version_info.major}.{sys.version_info.minor}"

DELAY_S = 1.0

# Tiles covering the Bezirk Horgen / left Zürichsee shore. Approximate; the
# point of P2 is to see what comes back, not to be exhaustive yet.
HORGEN_TILES = [
    (47.2588, 8.5900, "Horgen"),
    (47.2906, 8.5661, "Thalwil"),
    (47.3200, 8.5500, "Adliswil/Kilchberg"),
    (47.2200, 8.6300, "Waedenswil/Richterswil"),
]

results: dict[str, Any] = {}


def get(url: str, *, note: str = "") -> dict[str, Any]:
    """One GET. Records status, elapsed time and body (or the error body)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    req.add_header("Accept-Encoding", "identity")  # keep the probe dependency-free
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
            headers = dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status, headers = exc.code, dict(exc.headers)
    except Exception as exc:  # noqa: BLE001 - probe reports, never raises
        return {"url": url, "note": note, "error": f"{type(exc).__name__}: {exc}"}
    elapsed = round(time.monotonic() - started, 3)
    time.sleep(DELAY_S)
    try:
        body: Any = json.loads(raw)
    except json.JSONDecodeError:
        body = raw[:2000]
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


# ---------------------------------------------------------------- P1 geosearch
def p1_geosearch() -> Any:
    out = {}
    base = dict(action="query", generator="geosearch", ggscoord="47.2906|8.5661")

    out["radius_10000"] = get(q(**base, ggsradius=10000, ggslimit=10), note="documented max radius")
    out["radius_10001"] = get(q(**base, ggsradius=10001, ggslimit=10), note="expect an error naming the real max")
    out["limit_500"] = get(q(**base, ggsradius=10000, ggslimit=500), note="documented non-bot max")
    out["limit_501"] = get(q(**base, ggsradius=10000, ggslimit=501), note="expect a warning or clamp")
    out["limit_max"] = get(q(**base, ggsradius=10000, ggslimit="max"), note="what does 'max' resolve to")

    # The real question: generator + prop=categories in ONE request.
    combined = q(
        **base,
        ggsradius=10000,
        ggslimit=500,
        prop="categories|revisions",
        clshow="hidden",
        cllimit="max",
        rvprop="timestamp|user|flags|comment|size",
    )
    out["combined_first_page"] = get(combined, note="generator+prop=categories+prop=revisions in one request")

    # Continuation: follow the continue blob once and record its shape.
    body = out["combined_first_page"].get("body")
    if isinstance(body, dict) and "continue" in body:
        cont = {k: v for k, v in body["continue"].items()}
        nxt = combined + "&" + urllib.parse.urlencode(cont)
        out["combined_second_page"] = get(nxt, note=f"continued with {sorted(cont)}")
        out["continue_keys_observed"] = sorted(cont)
    else:
        out["combined_second_page"] = None
        out["continue_keys_observed"] = []
    return out


# ------------------------------------------------- P2 hidden maintenance cats
def p2_hidden_categories() -> Any:
    counts: dict[str, int] = {}
    pages_seen: set[str] = set()
    per_tile = {}
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
        while url and guard < 25:
            guard += 1
            r = get(url, note=f"tile {label}")
            body = r.get("body")
            if not isinstance(body, dict):
                per_tile[label] = {"error": r}
                break
            for page in body.get("query", {}).get("pages", []):
                if page.get("ns") != 0:
                    continue
                pages_seen.add(page["title"])
                for cat in page.get("categories", []) or []:
                    name = cat["title"]
                    tile_counts[name] = tile_counts.get(name, 0) + 1
                    counts[name] = counts.get(name, 0) + 1
            if "continue" in body:
                url = q(**params) + "&" + urllib.parse.urlencode(body["continue"])
            else:
                url = ""
        per_tile[label] = dict(sorted(tile_counts.items(), key=lambda kv: -kv[1]))
    return {
        "articles_seen": len(pages_seen),
        "note": "counts are page-occurrences, deduped per (page,category) only within a tile",
        "totals_sorted": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "per_tile": per_tile,
    }


# ------------------------------------------------------- P3 CirrusSearch verbs
def p3_search_keywords() -> Any:
    out = {}
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
            q(action="query", list="search", srsearch=srsearch, srlimit=10, srprop="snippet|timestamp|size"),
            note=srsearch,
        )
    # srinfo=totalhits|suggestion plus a deliberately expensive regex, to see
    # what a timeout actually looks like on the wire.
    out["regex_timeout_shape"] = get(
        q(action="query", list="search", srsearch="insource:/[Ss]tand:? *20[0-2][0-9]/", srlimit=1),
        note="expect timeout or partial-result warning; record elapsed_s",
    )
    return out


# --------------------------------------------------------- P4 revision flags
def p4_revisions() -> Any:
    out = {}
    titles = "Thalwil|Horgen|Wädenswil"
    out["revisions"] = get(
        q(
            action="query",
            prop="revisions",
            titles=titles,
            rvprop="ids|timestamp|user|userid|flags|comment|size|tags",
            rvlimit=50,
            rvslots="main",
        ),
        note="single-page rvlimit>1 only works for one title; check the error",
    )
    out["revisions_single"] = get(
        q(
            action="query",
            prop="revisions",
            titles="Thalwil",
            rvprop="ids|timestamp|user|userid|flags|comment|size|tags",
            rvlimit=50,
        ),
        note="does any revision carry a 'bot' key, or only 'minor'?",
    )
    out["recentchanges_bot_flag"] = get(
        q(
            action="query",
            list="recentchanges",
            rcprop="title|timestamp|user|flags|comment|sizes|tags",
            rclimit=50,
            rcshow="bot",
        ),
        note="rc has a real bot flag - but limited retention; check how far rcstart can go",
    )
    out["user_groups"] = get(
        q(action="query", list="users", ususers="Xqbot|Aka|InternetArchiveBot", usprop="groups|editcount"),
        note="fallback bot detection: is the editor currently in the bot group?",
    )
    out["botlist"] = get(
        q(action="query", list="allusers", augroup="bot", aulimit=500),
        note="full dewiki bot list, cacheable; count them",
    )
    return out


# ------------------------------------------------------------- P5 pageviews
def p5_pageviews() -> Any:
    out = {}
    cases = {
        "ascii": "Thalwil",
        "umlaut": "Wädenswil",
        "space": "Bezirk Horgen",
        "slash_like": "Rüschlikon",
        "definitely_missing": "Wp Todo Nonexistent Article Xyzzy",
    }
    for name, title in cases.items():
        enc = urllib.parse.quote(title.replace(" ", "_"), safe="")
        url = f"{PAGEVIEWS}/de.wikipedia.org/all-access/all-agents/{enc}/monthly/20250101/20251201"
        out[name] = get(url, note=f"title={title!r} encoded={enc}")
    # Date-format tolerance: YYYYMMDD vs YYYYMMDDHH.
    out["date_format_hh"] = get(
        f"{PAGEVIEWS}/de.wikipedia.org/all-access/all-agents/Thalwil/monthly/2025010100/2025120100",
        note="does the monthly endpoint accept YYYYMMDDHH",
    )
    out["user_agent_only"] = get(
        f"{PAGEVIEWS}/de.wikipedia.org/user/all-agents/Thalwil/monthly/20250101/20251201",
        note="access=user, agent=user is the human-traffic filter we probably want",
    )
    out["agent_user"] = get(
        f"{PAGEVIEWS}/de.wikipedia.org/all-access/user/Thalwil/monthly/20250101/20251201",
        note="agent=user excludes spiders/automated",
    )
    return out


# ------------------------------------------------------- P6 etiquette/maxlag
def p6_etiquette() -> Any:
    out = {}
    out["maxlag_5"] = get(q(action="query", meta="siteinfo", siprop="general", maxlag=5), note="maxlag on a read")
    out["maxlag_neg1"] = get(
        q(action="query", meta="siteinfo", siprop="general", maxlag=-1),
        note="force a lag error to see the 503 + Retry-After shape",
    )
    out["siteinfo_limits"] = get(
        q(action="query", meta="siteinfo", siprop="general|namespaces|statistics"),
        note="server time, wiki id, article count",
    )
    out["paraminfo_geosearch"] = get(
        q(action="paraminfo", modules="query+geosearch|query+search|query+revisions|query+categories"),
        note="AUTHORITATIVE limits: read 'max'/'highmax'/'limit' out of this",
    )
    return out


PROBES = {
    "P1": ("geosearch generator limits + prop combination + continuation", p1_geosearch),
    "P2": ("hidden maintenance categories in the Bezirk Horgen area", p2_hidden_categories),
    "P3": ("CirrusSearch deepcat / hastemplate / insource regex", p3_search_keywords),
    "P4": ("revision flags, bot and minor edit detection", p4_revisions),
    "P5": ("pageviews REST endpoint shape and title encoding", p5_pageviews),
    "P6": ("User-Agent policy, maxlag on reads, paraminfo limits", p6_etiquette),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", choices=sorted(PROBES), help="run a subset")
    ap.add_argument("--out", default="docs/phase0-probe-output.json")
    args = ap.parse_args()

    if CONTACT == "UNSET-CONTACT":
        print("WARNING: set WP_TODO_CONTACT so the User-Agent carries real contact info", file=sys.stderr)

    selected = args.only or sorted(PROBES)
    for key in selected:
        label, fn = PROBES[key]
        print(f"[{key}] {label} ...", file=sys.stderr, flush=True)
        try:
            results[key] = {"label": label, "result": fn()}
        except Exception as exc:  # noqa: BLE001
            results[key] = {"label": label, "error": f"{type(exc).__name__}: {exc}"}
        print(f"[{key}] done", file=sys.stderr, flush=True)

    payload = {"user_agent": UA, "probes": results}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
