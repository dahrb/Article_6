"""Event alignment: the one matching rule every score and agreement figure uses.

Two lists of events (gold and a system, two systems, two annotators) are
paired 1:1. A start act only pairs with a start act and a decision with a
decision; a pair must agree on the date (`date_sim` >= DATE_SIM_THRESHOLD);
among the pairs that pass, the most similar quotes win (greedy bipartite
assignment). Precision, recall and F1 are counted over the pairs.

The date gate: the same date at the coarser of the two precisions ("2000" vs
"2000-11-20") agrees outright; otherwise the ISO strings must be at least 0.9
similar, which admits one differing character (a model dropping a digit,
"16 March" -> "6 March") and nothing looser.

Edges (`follows`, `reviews`) are scored only between aligned events: an edge
is right when both its ends aligned and gold has the same edge between them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

DATE_SIM_THRESHOLD = 0.9
_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


@dataclass
class Node:
    """One event as the aligner sees it."""

    key: object
    body: str | None
    decision_date: str | None
    start_date: str | None
    quote: str | None = None
    level: str | None = None
    edges: dict[str, set] = field(default_factory=lambda: defaultdict(set))

    @property
    def is_start(self) -> bool:
        return self.decision_date is None and self.start_date is not None


def parse_date(text: str | None) -> tuple[int, int | None, int | None] | None:
    if not text:
        return None
    m = _DATE_RE.match(str(text).strip())
    if not m:
        return None
    y, mo, d = m.groups()
    return int(y), int(mo) if mo else None, int(d) if d else None


def dates_match(a: str | None, b: str | None) -> bool:
    """Equal at whichever precision is the coarser of the two."""
    pa, pb = parse_date(a), parse_date(b)
    if pa is None or pb is None:
        return False
    for x, y in zip(pa, pb, strict=True):
        if x is None or y is None:
            return True
        if x != y:
            return False
    return True


def date_sim(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    if dates_match(a, b):
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def quote_sim(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, " ".join(a.split()), " ".join(b.split())).ratio()


def align(gold: list[Node], other: list[Node]) -> dict:
    """1:1 alignment; returns {other key: gold key}."""
    pairs = []
    for gi, g in enumerate(gold):
        for oi, o in enumerate(other):
            if g.is_start != o.is_start:
                continue
            key = "start_date" if g.is_start else "decision_date"
            gv, ov = getattr(g, key), getattr(o, key)
            if (
                gv is None
                or ov is None
                or date_sim(str(gv), str(ov)) < DATE_SIM_THRESHOLD
            ):
                continue
            pairs.append((quote_sim(g.quote, o.quote), gi, oi))
    pairs.sort(key=lambda t: t[0], reverse=True)
    gold_used, other_used, mapping = set(), set(), {}
    for _, gi, oi in pairs:
        if gi in gold_used or oi in other_used:
            continue
        gold_used.add(gi)
        other_used.add(oi)
        mapping[other[oi].key] = gold[gi].key
    return mapping


def score_edges(gold: list[Node], other: list[Node], mapping: dict, kind: str) -> dict:
    """Edge agreement. An edge of `other` counts only if both ends aligned."""
    gold_keys = {n.key for n in gold}
    other_keys = {n.key for n in other}
    gold_edges = {
        (n.key, t) for n in gold for t in n.edges.get(kind, set()) if t in gold_keys
    }
    other_edges = {
        (n.key, t) for n in other for t in n.edges.get(kind, set()) if t in other_keys
    }
    projected = {
        (mapping[s], mapping[t])
        for s, t in other_edges
        if s in mapping and t in mapping
    }
    return {
        "gold_edges": len(gold_edges),
        "arm_edges": len(other_edges),
        "correct": len(projected & gold_edges),
    }
