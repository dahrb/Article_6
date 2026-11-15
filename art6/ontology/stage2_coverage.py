"""Catch stage-2 graphs that silently dropped events, so stage 2 can retry them.

The bridge numbers the events it hands OntoCast (`EVENT e1` .. `EVENT eN` in
each bundle record), and the stage-2 prompt asks for exactly one event node per
block. Sometimes the model renders far fewer anyway, with no error and no
truncation: on 001-188177 (2026-11-11) it minted all 35 event IRIs but filled
in only 10, leaving 25 nodes with nothing but rdf:type. Those match no stage-1
event, the stage-3 re-mint did not recover them, and nothing downstream noticed.

    thin    for each graph in RAW_DIR whose event count is below
            (1 - TOLERANCE) x the bundle's, move its outputs into
            RAW_DIR/thinned/a<ATTEMPT>/ and print the record line. stage2.sh's
            next attempt then sees the document as missing and renders it again
    settle  after the last attempt: for any record still without a graph,
            restore the fullest thinned version kept, and print one
            `THINNED <case> <got>/<want>` line per document that never reached
            the tolerance (the run log keeps them; stage 3 still tries to
            re-mint what is missing)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from rdflib import Graph

from art6.ontology.reconcile import ECHR, graph_events

CONTENT = (
    "hasSupportingQuote",
    "hasCourt",
    "hasDecisionDate",
    "hasProceedingStartDate",
)

EVENT_BLOCK = re.compile(r"(?m)^EVENT e\d+\b")
DEFAULT_TOLERANCE = 0.2


def records(bundle: Path) -> dict[int, dict]:
    """Line number -> record, for the non-blank lines (OntoCast's numbering)."""
    out = {}
    for n, line in enumerate(bundle.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            out[n] = json.loads(line)
    return out


def expected(record: dict) -> int:
    return len(EVENT_BLOCK.findall(record.get("text") or ""))


def rendered(ttl: Path) -> int | None:
    """Event nodes that carry content. A node with only its rdf:type is an
    event the model named but never rendered (25 of 35 on 001-188177), and it
    matches nothing in stage 3, so it counts as dropped."""
    try:
        g = Graph().parse(ttl, format="turtle")
    except Exception:  # noqa: BLE001 -- unparseable counts as nothing usable
        return None
    return sum(
        1 for s in graph_events(g) if any((s, ECHR[p], None) in g for p in CONTENT)
    )


def outputs(raw_dir: Path, stem: str, n: int) -> list[Path]:
    return sorted(raw_dir.glob(f"{stem}.L{n}.*"))


def is_thin(got: int | None, want: int, tolerance: float) -> bool:
    return want > 0 and (got is None or got < (1 - tolerance) * want)


def thin(bundle: Path, raw_dir: Path, attempt: int, tolerance: float) -> list[int]:
    moved = []
    for n, rec in records(bundle).items():
        ttl = raw_dir / f"{bundle.stem}.L{n}.facts.ttl"
        if not ttl.exists():
            continue
        got, want = rendered(ttl), expected(rec)
        if not is_thin(got, want, tolerance):
            continue
        dest = raw_dir / "thinned" / f"a{attempt}"
        dest.mkdir(parents=True, exist_ok=True)
        for f in outputs(raw_dir, bundle.stem, n):
            shutil.move(f, dest / f.name)
        moved.append(n)
    return moved


def settle(bundle: Path, raw_dir: Path, tolerance: float) -> list[str]:
    report = []
    kept = raw_dir / "thinned"
    for n, rec in records(bundle).items():
        want = expected(rec)
        ttl = raw_dir / f"{bundle.stem}.L{n}.facts.ttl"
        if not ttl.exists() and kept.is_dir():
            versions = [
                (rendered(d / ttl.name) or 0, d)
                for d in sorted(kept.iterdir())
                if (d / ttl.name).exists()
            ]
            if versions:
                _, best = max(versions, key=lambda v: v[0])
                for f in sorted(best.glob(f"{bundle.stem}.L{n}.*")):
                    shutil.copy2(f, raw_dir / f.name)
        if ttl.exists():
            got = rendered(ttl)
            if is_thin(got, want, tolerance):
                report.append(f"THINNED {rec.get('case_id')} {got or 0}/{want}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("cmd", choices=("thin", "settle"))
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--raw-dir", required=True, type=Path)
    ap.add_argument("--attempt", type=int, default=0)
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    a = ap.parse_args()
    if a.cmd == "thin":
        moved = thin(a.input, a.raw_dir, a.attempt, a.tolerance)
        print(" ".join(f"L{n}" for n in moved))
    else:
        for line in settle(a.input, a.raw_dir, a.tolerance):
            print(line)


if __name__ == "__main__":
    main()
