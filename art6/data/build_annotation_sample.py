"""
build_annotation_sample.py
--------------------------
Draws the human annotation subsample from the JURIX evaluation sample, and
writes one blank annotation template per drawn case.

The subsample is NESTED inside the 250-document evaluation sample rather than
drawn fresh, so every annotated document already has pipeline output under every
arm and the judge can be calibrated on exactly the documents that were
annotated. Drawing a separate set would mean paying to run the judge on the
annotation documents a second time.

EVEN ACROSS COURT LEVEL, PROPORTIONAL OVER TIME WITHIN LEVEL
-----------------------------------------------------------
Same design as the parent draw (art6/data/build_evaluation_sample.py) and for
the same reason: equal power per formation. 20 does not divide by 3, so the
remainder goes to the levels in alphabetical order -- CHAMBER 7, COMMITTEE 7,
GRANDCHAMBER 6 -- which is arbitrary but fixed and printed.

Within a level the draw is stratified over period by scikit-learn's
train_test_split, exactly as the parent sampler does. With only 6-7 documents
per level a period stratum can hold too few members to stratify; the script
falls back to a plain seeded draw for that level and says so rather than
failing.

ONE DOCUMENT PER CASE
---------------------
The parent sample deliberately keeps both members of a Chamber/Grand Chamber
pair -- the same domestic proceedings described twice, 1-2 years apart. That is
a measurement worth having for the pipeline, but annotating both members means
reading the same domestic history twice for one document's worth of reference,
so the frame is collapsed to one document per `case_group` before allocation.
Pass --keep-pairs to disable.

Usage:
  uv run python -m art6.data.build_annotation_sample
  uv run python -m art6.data.build_annotation_sample --n 20 --seed 42
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from sklearn.model_selection import train_test_split

TEMPLATE = """\
# =============================================================================
# {case_name}
# {case_id} | {court_level} | {year} | {respondent}
#
# Annotate against annotation/annotation_guide.txt.
# Read the whole judgment first, then fill in one record per domestic event,
# ordered by decision_date, earliest first.
#
# Source text: results/ablation_250_mv1/input.jsonl  (case_id {case_id})
# Do not open any pipeline output before this file is finished.
# =============================================================================

case_id: "{case_id}"
case_name: "{case_name}"
court_level: "{court_level}"
annotator: ""
annotated_utc: ""
mode: cold
guide_version: "1.0"

proceedings:

  # Copy this block for each event. Delete any key the document gives you
  # nothing for -- an omitted key and an empty value are not the same thing.
  # Closed vocabularies are in guide §6; write the short form.
  - id: 1
    type:                            # §6.5  DomesticProceeding | AdministrativeAction
                                     #       | EnforcementAction | ProsecutorialReview
    body: ""                         # authority name, verbatim from this passage
    authority_kind:                  # §6.6  Judicial | Prosecutorial | Administrative
                                     #       | Disciplinary | Unknown
    start_date:                      # "YYYY-MM-DD" | "YYYY-MM" | "YYYY"  -- QUOTED
    decision_date:                   # "YYYY-MM-DD" | "YYYY-MM" | "YYYY"  -- QUOTED
    level:                           # §6.1  Investigative | AdministrativeReview
                                     #       | FirstInstance | Appeal | Cassation
                                     #       | SupervisoryReview | Reopening | Unknown
    proceeding_type:                 # §6.2  Criminal | Civil | Administrative
                                     #       | Constitutional | Disciplinary | Other | Unknown
    outcome:                         # §6.3  MeritsDecided | Inadmissible
                                     #       | SettledOrWithdrawn | Remitted | Other | Unknown
    outcome_direction:               # §6.4  FavourableToApplicant
                                     #       | UnfavourableToApplicant | Mixed | Unknown
    final_domestic_decision:         # true on the terminal event of each chain; omit otherwise
    parties: []                      # - name: ""
                                     #   side:              # §6.7 Initiating | Responding
                                     #                      #      | ThirdParty | Unknown
                                     #   natural_person:    # true for ONE human being
                                     # the deciding body is never a party to its own event
    follows:                         # id of the event this procedurally continues, or null
    borderline:                      # true if you could not decide whether it belongs (§4.4)
    borderline_reason: ""
    quote: ""                        # verbatim span, COPIED not retyped
    note: ""

# Anything about the judgment as a whole: an unusual structure, a section you
# judged out of scope, a chain you could not resolve.
document_note: ""
"""


def allocate(n: int, levels: list[str]) -> dict[str, int]:
    """Even across levels; the remainder goes to levels in alphabetical order."""
    base, extra = divmod(n, len(levels))
    out = {level: base for level in levels}
    for level in sorted(levels)[:extra]:
        out[level] += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sample", type=Path, default=Path("data/art6_eval_sample_judgments.json")
    )
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-dir", type=Path, default=Path("annotation/gold"))
    ap.add_argument("--text-dir", type=Path, default=Path("annotation/gold_text"))
    ap.add_argument(
        "--keep-pairs",
        action="store_true",
        help="do not collapse to one document per case_group",
    )
    args = ap.parse_args()

    payload = json.loads(args.sample.read_text(encoding="utf-8"))
    docs = payload["documents"]
    print(f"parent sample: {len(docs)} documents, seed {payload['seed']}")

    if not args.keep_pairs:
        seen: dict[str, dict] = {}
        for doc in sorted(docs, key=lambda d: d["case_id"]):
            seen.setdefault(doc["case_group"], doc)
        dropped = len(docs) - len(seen)
        docs = list(seen.values())
        print(f"collapsed to one document per case: {len(docs)} ({dropped} dropped)")

    by_level: dict[str, list[dict]] = defaultdict(list)
    for doc in docs:
        by_level[doc["court_level"]].append(doc)
    levels = sorted(by_level)
    quota = allocate(args.n, levels)
    print(f"allocation (even across level, remainder alphabetical): {quota}")

    drawn: list[dict] = []
    for level in levels:
        pool = sorted(by_level[level], key=lambda d: d["case_id"])
        k = quota[level]
        periods = [d["period"] for d in pool]
        counts = Counter(periods)
        stratifiable = (
            len(counts) > 1 and min(counts.values()) >= 2 and k >= len(counts)
        )
        if stratifiable:
            picked, _ = train_test_split(
                pool, train_size=k, random_state=args.seed, stratify=periods
            )
            how = "stratified over period"
        else:
            picked, _ = train_test_split(pool, train_size=k, random_state=args.seed)
            how = f"plain draw (periods {dict(counts)} cannot stratify at n={k})"
        drawn.extend(picked)
        print(f"  {level}: {k} of {len(pool)} -- {how}")

    drawn.sort(key=lambda d: (d["court_level"], d["case_id"]))
    assert len({d["case_id"] for d in drawn}) == len(drawn) == args.n

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for doc in drawn:
        path = args.out_dir / f"{doc['case_id']}.yaml"
        if path.exists():
            print(f"  SKIP existing {path}")
            continue
        path.write_text(
            TEMPLATE.format(
                **{
                    k: doc.get(k, "")
                    for k in (
                        "case_id",
                        "case_name",
                        "court_level",
                        "year",
                        "respondent",
                    )
                }
            ),
            encoding="utf-8",
        )

    # The source text, exactly as the pipeline was given it -- no header, no
    # wrapper. A quote copied out of this file is therefore byte-identical to
    # what the arms were extracting from, so the verbatim check compares like
    # with like. Anything prepended here would end up inside a copied span.
    args.text_dir.mkdir(parents=True, exist_ok=True)
    for doc in drawn:
        (args.text_dir / f"{doc['case_id']}.txt").write_text(
            doc["text"], encoding="utf-8"
        )

    manifest = {
        "n": args.n,
        "seed": args.seed,
        "design": "even across court level, proportional over time within level",
        "nested_in": str(args.sample),
        "one_document_per_case": not args.keep_pairs,
        "allocation": quota,
        "documents": [
            {
                k: d[k]
                for k in (
                    "case_id",
                    "case_name",
                    "court_level",
                    "year",
                    "period",
                    "respondent",
                    "case_group",
                )
            }
            for d in drawn
        ],
    }
    manifest_path = args.out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"\nby period: {Counter(d['period'] for d in drawn)}")
    print(f"chars: median {sorted(len(d['text']) for d in drawn)[len(drawn) // 2]:,}")
    print(f"wrote {len(drawn)} template(s) + manifest -> {args.out_dir}")
    print(f"wrote {len(drawn)} source text(s) -> {args.text_dir}")


if __name__ == "__main__":
    main()
