"""Serialise rdflib's SPARQL parsing so graph work can run in threads.

rdflib parses every SPARQL string -- including the ones pyshacl issues during
validation -- with one module-level pyparsing grammar, and pyparsing's parse
actions are not safe to run concurrently: two threads parsing at once fail with
`Param.postParse2() missing 1 required positional argument: 'tokenList'`.
Measured 2026-10-21: reconcile with 10 worker threads failed on every graph of
an arm within a second.

Only the PARSE is guarded; evaluation, model calls and everything else stay
parallel. Parsing is milliseconds against minutes of model time, so the lock
costs nothing measurable. Importing this module installs the guard once.
"""

from __future__ import annotations

import functools
import threading

from rdflib.plugins.sparql import parser, processor

_LOCK = threading.RLock()


def _guarded(fn):
    if getattr(fn, "_art6_guarded", False):
        return fn

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)

    wrapper._art6_guarded = True  # type: ignore[attr-defined]
    return wrapper


# processor imported the names directly, so both modules are patched.
for _module in (parser, processor):
    for _name in ("parseQuery", "parseUpdate"):
        setattr(_module, _name, _guarded(getattr(_module, _name)))
