"""Content-addressed cache for model calls, so a run can be replayed exactly.

    LLM_CACHE_ENABLED=true    # default: see run_ablation.sh
    ART6_LLM_CACHE_DIR=...    # default: <repo>/.cache/art6_llm

OntoCast has its own cache behind the same env var, which covers stage 2. This
one covers the two stages that call the endpoint directly and were therefore
never replayable at all: stage 1 (compress.py) and stage 3 (new_repair.py via
repair_facts._stream_patch_text). With all three on, a re-run reproduces a
result byte for byte, and a diff in the output is a diff you caused.

WHAT THE KEY INCLUDES, AND THE ONE THING IT DOES NOT
-----------------------------------------------------
Everything the model was asked: the prompt or message list, the model name,
and every sampling parameter (temperature, seed, the output cap). Change any
of them and you get a miss, which is what you want -- an edited prompt must
not be answered from cache.

NOT the base URL. This project has moved the endpoint between ports 8000,
8003 and 8005 while serving the same model, and keying on it would have
thrown the cache away each time. The consequence is real and worth stating:
if you ever serve a DIFFERENT model under a name this cache has already
seen, it will answer from the old model's bytes. Set ART6_LLM_CACHE_NS to
separate them, or delete the directory.

WHEN NOT TO USE IT
------------------
Two runs exist whose whole purpose is to observe the model's own behaviour,
and a cache silently invalidates both:

  * the §5.2 variance repeat -- run one config twice and count byte-identical
    documents. Served from cache the second run is a replay, so it measures
    zero variance by construction rather than by finding.
  * any timing or cost measurement -- a cache hit costs milliseconds. Three of
    the nine rows in the 2026-08-20 cost table were unusable for this reason.

Both should set LLM_CACHE_ENABLED=false. The run manifest records which mode
was used so a cached run cannot later be mistaken for either.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

_MISS = object()
_LOCK = threading.Lock()
_STATS = {"hits": 0, "misses": 0, "stores": 0}


def enabled() -> bool:
    return os.environ.get("LLM_CACHE_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def cache_root() -> Path:
    if custom := os.environ.get("ART6_LLM_CACHE_DIR"):
        return Path(custom)
    return Path(__file__).resolve().parents[2] / ".cache" / "art6_llm"


def make_key(kind: str, payload: dict[str, Any]) -> str:
    """sha256 over canonical JSON. Sorted keys and no whitespace, so an
    equivalent payload built in a different order still hits."""
    material = json.dumps(
        {"ns": os.environ.get("ART6_LLM_CACHE_NS", ""), "kind": kind, **payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _path_for(kind: str, key: str) -> Path:
    # Sharded on the first two hex chars: a 250-document run makes thousands of
    # entries and a single flat directory gets unpleasant to work with.
    return cache_root() / kind / key[:2] / f"{key}.json"


def load(kind: str, key: str) -> Any:
    """Cached value, or the module's _MISS sentinel. Never raises: a cache that
    cannot be read must fall through to a real call, not end the run."""
    if not enabled():
        return _MISS
    path = _path_for(kind, key)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))["value"]
    except (OSError, ValueError, KeyError):
        with _LOCK:
            _STATS["misses"] += 1
        return _MISS
    with _LOCK:
        _STATS["hits"] += 1
    return value


def store(kind: str, key: str, value: Any) -> None:
    """Never raises, for the same reason as load: a full disk should slow the
    run down, not lose it."""
    if not enabled():
        return
    path = _path_for(kind, key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: a run interrupted mid-write must not leave a
        # half-written entry that later reads as a hit.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"kind": kind, "value": value}, default=str), encoding="utf-8"
        )
        tmp.replace(path)
        with _LOCK:
            _STATS["stores"] += 1
    except OSError:
        pass


def stats() -> dict[str, int]:
    with _LOCK:
        return dict(_STATS)


def summary() -> str:
    s = stats()
    if not enabled():
        return "llm cache: off"
    total = s["hits"] + s["misses"]
    rate = f"{s['hits'] / total:.0%}" if total else "n/a"
    return (
        f"llm cache: {s['hits']} hit / {s['misses']} miss ({rate}), "
        f"{s['stores']} stored -> {cache_root()}"
    )


MISS = _MISS
