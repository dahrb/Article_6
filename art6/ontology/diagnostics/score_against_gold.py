"""
score_against_gold.py
----------------------
Mechanical (no-LLM) event_exists precision/recall/F1 for an ablation arm
against the human-corrected `annotation/test/*.yaml` reference, for the 10
pilot documents in `data/art6_domestic_test_set.json`.

This replaces having an agent re-read gold + rendered judge/*.md by hand each
time an arm changes: that approach is slow, costs real money, is not
reproducible run to run, and (2026-09-08) can simply fail outright on a rate
limit mid-run. Everything here is computed from files on disk with no model
call, so a report's numbers can always be regenerated and diffed.

Matching rule, from annotation_guide.txt Sec 8 "IDENTITY -- WHEN ARE TWO
MENTIONS THE SAME EVENT": two proceedings are the same event when they share
the same deciding authority (naming variants count as the same body) and the
same decision date (a bare year matches a full date in that year). A FILING
event (no decision, only a start date, Sec 3.1) matches another filing event
on authority + start_date instead, and never matches a decision event.

Authority matching is a normalise-and-substring heuristic, not semantic
matching -- it will occasionally over- or under-match on a body named very
differently in gold vs. the arm (a real limitation, not a bug pretending not
to be one; see NOTES at the bottom of this file). Date matching is precision-
aware: a bare year, a year-month, and a full date all compare at whichever
precision is the COARSER of the two sides.

Usage:
  uv run python -m art6.ontology.diagnostics.score_against_gold \\
      --run-dir results/ablation_test_review_split_v2 \\
      --arms C3,C4 \\
      --out results/ablation_test_review_split_v2/gold_score.json
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import yaml
from rdflib import Graph, Namespace

ECHR = Namespace("https://growgraph.dev/echr#")

REPO_ROOT = Path(__file__).resolve().parents[3]
# Overridable via --gold-dir: annotation/test is the 10-doc set the prompts
# were tuned on, annotation/gold is the 20-doc set that was never tuned
# against -- see annotation/test/manifest.json's own note on the distinction.
GOLD_DIR = REPO_ROOT / "annotation" / "test"
MANIFEST = GOLD_DIR / "manifest.json"

ARM_PATHS = {
    "C1": ("C1_C2", "raw", "input"),
    "C2": ("C1_C2", "repaired", "input"),
    "C3": ("C3_C4", "raw", "bundles"),
    "C4": ("C3_C4", "repaired", "bundles"),
    "C0": ("C0", None, "input"),  # handled separately in score_arm: JSON, not TTL
}


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_STOPWORDS = {"the", "a", "an", "of", "at", "for"}


def norm_authority(text: str | None) -> str:
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[’'\".,()]", "", text)
    tokens = [t for t in re.split(r"\s+", text.strip()) if t and t not in _STOPWORDS]
    return " ".join(tokens)


def authorities_match(a: str | None, b: str | None) -> bool:
    """Naming-variant heuristic: substring match after normalisation, in
    either direction ("regional court" in "sofia regional court")."""
    na, nb = norm_authority(a), norm_authority(b)
    if not na or not nb:
        return False
    return na in nb or nb in na


_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


def parse_date(text: str | None) -> tuple[int, int | None, int | None] | None:
    if not text:
        return None
    m = _DATE_RE.match(str(text).strip())
    if not m:
        return None
    y, mo, d = m.groups()
    return (int(y), int(mo) if mo else None, int(d) if d else None)


def dates_match(a: str | None, b: str | None) -> bool:
    """Compare at whichever precision is the COARSER of the two sides."""
    pa, pb = parse_date(a), parse_date(b)
    if pa is None or pb is None:
        return False
    ya, ma, da = pa
    yb, mb, db = pb
    if ya != yb:
        return False
    if ma is None or mb is None:
        return True  # one side is bare-year -- year match is enough
    if ma != mb:
        return False
    if da is None or db is None:
        return True  # one side is year-month -- month match is enough
    return da == db


# ---------------------------------------------------------------------------
# Event records, one shape for gold and for an arm's graph
# ---------------------------------------------------------------------------


@dataclass
class Event:
    body: str | None
    decision_date: str | None
    start_date: str | None
    quote: str | None = None
    gold_id: object = None  # only set on gold events, for diagnostics

    @property
    def is_filing(self) -> bool:
        """A Sec 3.1 filing half: a start date but no decision -- matches
        another filing on authority + start_date, never a decision event."""
        return self.decision_date is None and self.start_date is not None


def load_gold(case_id: str) -> list[Event]:
    path = GOLD_DIR / f"{case_id}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out = []
    for p in data.get("proceedings") or []:
        out.append(
            Event(
                body=p.get("body"),
                decision_date=p.get("decision_date"),
                start_date=p.get("start_date"),
                quote=p.get("quote"),
                gold_id=p.get("id"),
            )
        )
    return out


def load_arm(ttl_path: Path) -> list[Event] | None:
    if not ttl_path.exists():
        return None
    graph = Graph()
    try:
        graph.parse(ttl_path, format="turtle")
    except Exception:  # noqa: BLE001 -- a malformed graph is a data point
        return None
    # Same selection rule as render_for_judge / ablation_auto_eval: a
    # proceeding is identified by what it HAS, not by asserted rdf:type,
    # because most proceedings are typed by subclass.
    subjects = {
        s
        for s in set(graph.subjects())
        if graph.value(s, ECHR.hasDecisionDate) is not None
        or graph.value(s, ECHR.hasProceedingStartDate) is not None
        or graph.value(s, ECHR.hasInstanceLevel) is not None
    }
    out = []
    for s in subjects:
        court = graph.value(s, ECHR.hasCourt)
        body = str(graph.value(court, ECHR.hasAuthorityName)) if court else None
        decision_date = graph.value(s, ECHR.hasDecisionDate)
        start_date = graph.value(s, ECHR.hasProceedingStartDate)
        quote = graph.value(s, ECHR.hasSupportingQuote)
        out.append(
            Event(
                body=body,
                decision_date=str(decision_date) if decision_date else None,
                start_date=str(start_date) if start_date else None,
                quote=str(quote) if quote else None,
            )
        )
    return out


def load_c0(path: Path) -> list[Event] | None:
    """C0's O1 schema-light JSON: no start_date field at all (see
    art6/conditions/run_conditions.py's o1 schema), so every C0 event is
    decision-shaped -- it can satisfy a gold decision entry but never a gold
    filing-only entry, same as any other arm with no start_date on that node."""
    if not path.exists():
        return None
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- a malformed C0 output is a data point
        return None
    out = []
    for r in records:
        out.append(
            Event(
                body=r.get("deciding_body"),
                decision_date=r.get("decision_date"),
                start_date=None,
                quote=r.get("supporting_quote"),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Matching: greedy one-to-one, gold events processed in list order
# ---------------------------------------------------------------------------


def event_identity(gold: Event, arm: Event) -> bool:
    """`gold` decides which field the identity is checked on -- its own
    decision_date, or its own start_date when it is a Sec 3.1 filing-only
    entry. The ARM SIDE IS NOT REQUIRED TO BE THE SAME SHAPE: the pipeline's
    own extraction rule (facts.txt) puts a filing's start date and its
    decision's date on ONE node unless the filing is separately narrated, so
    a merged arm node legitimately satisfies a gold filing entry on its start
    date alone. Comparing gold.start_date against arm.decision_date (or vice
    versa) is never done -- that would equate two different moments -- so a
    genuine filing-vs-decision mismatch still correctly fails.
    """
    if not authorities_match(gold.body, arm.body):
        return False
    if gold.is_filing:
        return dates_match(gold.start_date, arm.start_date)
    return dates_match(gold.decision_date, arm.decision_date)


@dataclass
class DocResult:
    case_id: str
    matched: int = 0  # gold events with >=1 matching arm event (recall numerator)
    gold_only: int = 0
    arm_matched: int = (
        0  # arm events with >=1 matching gold event (precision numerator)
    )
    arm_only: int = 0
    gold_only_ids: list = field(default_factory=list)
    no_output: bool = False


def score_document(
    case_id: str, gold: list[Event], arm: list[Event] | None
) -> DocResult:
    """Existence checks on each side independently, NOT a one-to-one
    bipartite match. A single arm node that legitimately carries both a
    filing's start date and its decision's date (the pipeline's own,
    correct, non-split representation) can satisfy BOTH the gold filing
    entry and the gold decision entry for the same real proceeding --
    consuming it for only one of them would undercount recall for a
    genuinely correct extraction. The two counts (matched / arm_matched)
    are kept separate because they need not agree: they only coincide when
    every match happens to be 1:1.
    """
    r = DocResult(case_id=case_id)
    if arm is None:
        r.no_output = True
        r.gold_only = len(gold)
        r.gold_only_ids = [g.gold_id for g in gold]
        return r
    for g in gold:
        if any(event_identity(g, a) for a in arm):
            r.matched += 1
        else:
            r.gold_only += 1
            r.gold_only_ids.append(g.gold_id)
    for a in arm:
        if any(event_identity(g, a) for g in gold):
            r.arm_matched += 1
        else:
            r.arm_only += 1
    return r


def quote_sim(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, " ".join(a.split()), " ".join(b.split())).ratio()


# Character-level similarity, not exact equality, as the date gate below.
# Motivated by a real, confirmed pipeline bug (2026-09-09, case 001-101173):
# gemma's stage-1 date spans occasionally lose their leading digit at the
# character-offset level ("16 March 2004" -> "6 March 2004", still a valid
# substring of the source so it passes span-verbatim checks silently) --
# exact-match date scoring turns an otherwise-correct event into a hard zero
# over one dropped character. A high-similarity ISO string ("2004-03-16" vs
# "2004-03-06") still catches that as effectively the same date without
# opening the gate to genuinely different dates.
DATE_SIM_THRESHOLD = 0.6


def date_sim(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def score_document_date_quote(
    case_id: str, gold: list[Event], arm: list[Event] | None
) -> DocResult:
    """Alternate alignment, requested to isolate whether the default scorer's
    authority-substring gate (authorities_match) is what makes C0 look
    artificially strong or the pipeline look artificially weak. Two changes
    from score_document/event_identity:

      1. NO AUTHORITY GATE AT ALL -- a pair is a candidate purely on date.
      2. DATE NEEDS ONLY date_sim >= DATE_SIM_THRESHOLD (0.6), not exact
         equality and not dates_match's bare-year/year-month leniency --
         a plain character-similarity gate on the ISO strings instead.

    This is also a genuine 1:1 bipartite match (score_document's independent
    existence check lets one arm node satisfy many gold entries at once,
    which a plain P/R/F1 table does not assume): among candidates clearing
    the date-similarity gate, ties are broken by quote similarity, but there
    is no separate minimum quote-similarity threshold -- the date gate is
    what admits a pair as a candidate, quote is only for ranking among them.
    """
    r = DocResult(case_id=case_id)
    if arm is None:
        r.no_output = True
        r.gold_only = len(gold)
        r.gold_only_ids = [g.gold_id for g in gold]
        return r
    pairs = []
    for gi, g in enumerate(gold):
        for ai, a in enumerate(arm):
            if (
                g.is_filing != a.is_filing
            ):  # filing never matches decision (guide Sec 8)
                continue
            key = "start_date" if g.is_filing else "decision_date"
            if getattr(g, key) is None or getattr(a, key) is None:
                continue
            if (
                date_sim(str(getattr(g, key)), str(getattr(a, key)))
                < DATE_SIM_THRESHOLD
            ):
                continue
            pairs.append((quote_sim(g.quote, a.quote), gi, ai))
    pairs.sort(key=lambda t: t[0], reverse=True)
    gu, au = set(), set()
    for _, gi, ai in pairs:
        if gi in gu or ai in au:
            continue
        gu.add(gi)
        au.add(ai)
    r.matched = r.arm_matched = len(gu)
    r.gold_only = len(gold) - len(gu)
    r.gold_only_ids = [g.gold_id for i, g in enumerate(gold) if i not in gu]
    r.arm_only = len(arm) - len(au)
    return r


def prf1(matched: int, gold_only: int, arm_matched: int, arm_only: int) -> dict:
    precision = (
        arm_matched / (arm_matched + arm_only) if (arm_matched + arm_only) else None
    )
    recall = matched / (matched + gold_only) if (matched + gold_only) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and (precision + recall) > 0
        else None
    )
    return {"precision": precision, "recall": recall, "f1": f1}


def score_arm(
    run_dir: Path, arm: str, docs: list[dict], mode: str = "existence"
) -> dict:
    subdir, stage, stem = ARM_PATHS[arm]
    total_matched = total_gold_only = total_arm_matched = total_arm_only = 0
    per_doc = []
    score_doc = score_document if mode == "existence" else score_document_date_quote
    for doc in docs:
        case_id, line_id = doc["case_id"], doc["line_id"]
        gold = load_gold(case_id)
        if arm == "C0":
            arm_events = load_c0(run_dir / subdir / f"{stem}.{line_id}.o1.json")
        else:
            ttl_path = run_dir / subdir / stage / f"{stem}.{line_id}.facts.ttl"
            arm_events = load_arm(ttl_path)
        r = score_doc(case_id, gold, arm_events)
        total_matched += r.matched
        total_gold_only += r.gold_only
        total_arm_matched += r.arm_matched
        total_arm_only += r.arm_only
        per_doc.append(
            {
                "case_id": case_id,
                "matched": r.matched,
                "gold_only": r.gold_only,
                "arm_matched": r.arm_matched,
                "arm_only": r.arm_only,
                "no_output": r.no_output,
                **prf1(r.matched, r.gold_only, r.arm_matched, r.arm_only),
            }
        )
    return {
        "matched": total_matched,
        "gold_only": total_gold_only,
        "arm_matched": total_arm_matched,
        "arm_only": total_arm_only,
        **prf1(total_matched, total_gold_only, total_arm_matched, total_arm_only),
        "per_document": per_doc,
    }


def main() -> None:
    global GOLD_DIR, MANIFEST
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--arms", default="C3,C4", help="comma-separated: C0,C1,C2,C3,C4")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--gold-dir",
        type=Path,
        default=GOLD_DIR,
        help="annotation/test (default, 10-doc tuning set) or annotation/gold (20-doc, never tuned on)",
    )
    ap.add_argument(
        "--mode",
        choices=["existence", "date_quote"],
        default="existence",
        help="existence (default): per-guide-Sec-8 authority+date, independent "
        "existence check each side. date_quote: no authority gate, EXACT "
        "date required, quote similarity only breaks ties in a 1:1 match.",
    )
    args = ap.parse_args()

    GOLD_DIR = args.gold_dir
    MANIFEST = GOLD_DIR / "manifest.json"

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    docs = manifest["documents"]
    # annotation/gold's manifest carries no line_id (it predates that field) --
    # derive it from position, valid only because data/art6_domestic_gold20.json
    # lists cases in the same order as this manifest (checked 2026-09-09).
    if docs and "line_id" not in docs[0]:
        docs = [{**d, "line_id": f"L{i}"} for i, d in enumerate(docs, 1)]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    results = {}
    for arm in arms:
        if arm not in ARM_PATHS:
            raise SystemExit(
                f"unknown arm {arm!r}, expected one of {sorted(ARM_PATHS)}"
            )
        results[arm] = score_arm(args.run_dir, arm, docs, mode=args.mode)
        s = results[arm]
        print(
            f"{arm}: matched={s['matched']} gold_only={s['gold_only']} "
            f"arm_only={s['arm_only']}  P={s['precision']:.1%} "
            f"R={s['recall']:.1%} F1={s['f1']:.1%}"
        )

    out_path = args.out or (args.run_dir / "gold_score.json")
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
