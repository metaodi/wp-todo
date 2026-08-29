"""On-disk response cache, keyed by the request itself.

Development reruns must not re-hit the API. The key is a hash of the canonical
request (host, path and sorted query), so it is stable across runs and across
machines, and a changed parameter is a different entry rather than a stale hit.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CACHE_VERSION = "1"


def cache_key(method: str, url: str, params: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"v": CACHE_VERSION, "method": method.upper(), "url": url, "params": _stringify(params)},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stringify(params: dict[str, Any]) -> dict[str, str]:
    return {str(k): str(v) for k, v in sorted(params.items())}


class ResponseCache:
    """A flat directory of JSON files. Small, greppable, easy to throw away."""

    def __init__(self, directory: Path, *, refresh: bool = False) -> None:
        self.directory = directory
        self.refresh = refresh
        self.hits = 0
        self.misses = 0

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        if self.refresh:
            return None
        path = self.path_for(key)
        if not path.exists():
            self.misses += 1
            return None
        try:
            with path.open(encoding="utf-8") as handle:
                payload: dict[str, Any] = json.load(handle)
        except (OSError, json.JSONDecodeError):
            # A corrupt entry is a cache miss, never a crash.
            self.misses += 1
            return None
        self.hits += 1
        return payload

    def put(self, key: str, payload: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(key)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
        tmp.replace(path)
