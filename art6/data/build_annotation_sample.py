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

TOPPING UP AN EXISTING SET, WITHOUT TOUCHING WHAT IS ALREADY THERE
--------------------------------------------------------------------------------
--add N draws N further documents on top of whatever --out-dir/manifest.json
already lists, instead of redrawing everything from scratch. This matters
because re-running the original stratified draw at a larger --n does NOT
guarantee the smaller draw is a subset of the larger one -- train_test_split's
split changes with the requested size, not just the random_state -- so a naive
"redraw at 30" can silently pick a different 20 while leaving the original 20
annotation files orphaned on disk and unlisted in the new manifest.

--add instead: (1) reads the existing manifest and drops every case already
listed there from the candidate pool, so a previously-drawn document can never
be drawn again and its .yaml is never touched -- an already-annotated file is
never regenerated even blank, because the SKIP-existing guard below is now
backed by pool exclusion, not just a path check; (2) recomputes the even-
across-level quota for the NEW total (existing + N) using the same alphabetical-
remainder rule as a fresh draw, then subtracts what each level already has, so
the enlarged set stays balanced across court level as a whole, not just the
top-up; (3) draws only the shortfall per level from the reduced pool, same
seed; (4) merges the manifest (old documents + new, `n` becomes the new
total, an `additions` list records each top-up: seed, count, when, which
case_ids) rather than overwriting it wholesale; (5) regenerates the flat
case_id+text data file (data/art6_domestic_gold<N>.json) in the manifest's own
order, which is what a later ablation run's positional line_id numbering
depends on lining up with -- see score_against_gold.py's line_id-from-position
fallback.

Usage:
  uv run python -m art6.data.build_annotation_sample
  uv run python -m art6.data.build_annotation_sample --n 20 --seed 42
  uv run python -m art6.data.build_annotation_sample --add 10
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
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
                                     #       | CaseReview | Reopening | Unknown
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


def _draw_by_level(
    quota: dict[str, int], by_level: dict[str, list[dict]], seed: int
) -> list[dict]:
    """The stratified-draw loop, factored out so a fresh draw and a --add
    top-up run the identical per-level logic against different pools/quotas."""
    drawn: list[dict] = []
    for level in sorted(quota):
        pool = sorted(by_level.get(level, []), key=lambda d: d["case_id"])
        k = quota[level]
        if k == 0:
            continue
        if k > len(pool):
            raise SystemExit(
                f"{level}: need {k} more but only {len(pool)} undrawn candidate(s) "
                "remain in this level -- lower --add or draw from a larger parent sample"
            )
        periods = [d["period"] for d in pool]
        counts = Counter(periods)
        stratifiable = (
            len(counts) > 1 and min(counts.values()) >= 2 and k >= len(counts)
        )
        if k == len(pool):
            picked, how = pool, "took the entire remaining pool"
        elif stratifiable:
            picked, _ = train_test_split(
                pool, train_size=k, random_state=seed, stratify=periods
            )
            how = "stratified over period"
        else:
            picked, _ = train_test_split(pool, train_size=k, random_state=seed)
            how = f"plain draw (periods {dict(counts)} cannot stratify at n={k})"
        drawn.extend(picked)
        print(f"  {level}: {k} of {len(pool)} -- {how}")
    return drawn


def _write_templates_and_text(drawn: list[dict], out_dir: Path, text_dir: Path) -> None:
    # SKIP-existing is defense in depth, not the primary guarantee: in --add
    # mode the pool a document is drawn FROM has already excluded every
    # case_id in the existing manifest, so a previously-drawn document is
    # never a candidate here in the first place. This guard only protects
    # against re-running the exact same draw twice.
    out_dir.mkdir(parents=True, exist_ok=True)
    for doc in drawn:
        path = out_dir / f"{doc['case_id']}.yaml"
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
    text_dir.mkdir(parents=True, exist_ok=True)
    for doc in drawn:
        text_path = text_dir / f"{doc['case_id']}.txt"
        if text_path.exists():
            continue
        text_path.write_text(doc["text"], encoding="utf-8")


def _write_flat_data_json(
    documents: list[dict], text_by_case: dict[str, str], path: Path
) -> None:
    """case_id + text only, in the manifest's own document order -- this is
    what an ablation run's positional line_id (input.L1, input.L2, ...)
    numbering is built from, and score_against_gold.py's line_id-from-position
    fallback assumes this file and the manifest agree on that order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"case_id": d["case_id"], "text": text_by_case[d["case_id"]]} for d in documents
    ]
    path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(records)} record(s) -> {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sample", type=Path, default=Path("data/art6_eval_sample_judgments.json")
    )
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument(
        "--add",
        type=int,
        default=None,
        help="top up an existing --out-dir/manifest.json with N further "
        "documents instead of drawing --n fresh ones; see module docstring",
    )
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-dir", type=Path, default=Path("annotation/gold"))
    ap.add_argument("--text-dir", type=Path, default=Path("annotation/gold_text"))
    ap.add_argument(
        "--data-out",
        type=Path,
        default=None,
        help="flat case_id+text JSON to (re)write, manifest-ordered; default "
        "data/art6_domestic_gold<total-n>.json",
    )
    ap.add_argument(
        "--keep-pairs",
        action="store_true",
        help="do not collapse to one document per case_group",
    )
    args = ap.parse_args()

    payload = json.loads(args.sample.read_text(encoding="utf-8"))
    all_docs = payload["documents"]
    print(f"parent sample: {len(all_docs)} documents, seed {payload['seed']}")

    if not args.keep_pairs:
        seen: dict[str, dict] = {}
        for doc in sorted(all_docs, key=lambda d: d["case_id"]):
            seen.setdefault(doc["case_group"], doc)
        dropped = len(all_docs) - len(seen)
        all_docs = list(seen.values())
        print(
            f"collapsed to one document per case: {len(all_docs)} ({dropped} dropped)"
        )

    text_by_case = {d["case_id"]: d["text"] for d in all_docs}

    if args.add is not None:
        manifest_path = args.out_dir / "manifest.json"
        if not manifest_path.exists():
            raise SystemExit(
                f"--add requires an existing manifest at {manifest_path} -- "
                "run without --add first to draw the base set"
            )
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing_ids = {d["case_id"] for d in existing["documents"]}
        print(
            f"existing set: {existing['n']} documents ({len(existing_ids)} case_id(s))"
        )

        pool_docs = [d for d in all_docs if d["case_id"] not in existing_ids]
        by_level: dict[str, list[dict]] = defaultdict(list)
        for doc in pool_docs:
            by_level[doc["court_level"]].append(doc)

        target_total = existing["n"] + args.add
        # Same alphabetical-remainder rule as a fresh draw, applied to the
        # levels seen anywhere in the parent frame -- not just the levels
        # with remaining pool -- so a level already fully drawn still counts
        # toward the target's balance instead of silently vanishing from it.
        all_levels = sorted(
            {d["court_level"] for d in all_docs} | set(existing.get("allocation", {}))
        )
        quota_total = allocate(target_total, all_levels)
        existing_per_level = Counter(d["court_level"] for d in existing["documents"])
        quota_new = {
            level: max(0, quota_total.get(level, 0) - existing_per_level.get(level, 0))
            for level in all_levels
        }
        print(f"target total: {target_total}  allocation: {quota_total}")
        print(f"already have: {dict(existing_per_level)}")
        print(f"drawing:      {quota_new}")

        drawn = _draw_by_level(quota_new, by_level, args.seed)
        drawn.sort(key=lambda d: (d["court_level"], d["case_id"]))
        assert not (existing_ids & {d["case_id"] for d in drawn}), (
            "drew a case_id already in the existing manifest -- pool exclusion is broken"
        )
        assert len(drawn) == args.add, f"drew {len(drawn)}, expected --add {args.add}"

        _write_templates_and_text(drawn, args.out_dir, args.text_dir)

        new_doc_summaries = [
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
        ]
        manifest = dict(existing)
        manifest["n"] = target_total
        manifest["allocation"] = quota_total
        manifest["documents"] = existing["documents"] + new_doc_summaries
        manifest.setdefault("additions", []).append(
            {
                "n_added": args.add,
                "seed": args.seed,
                "added_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                "case_ids": sorted(d["case_id"] for d in drawn),
            }
        )
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

        print(f"\nnewly drawn by period: {Counter(d['period'] for d in drawn)}")
        print(f"wrote {len(drawn)} new template(s) -> {args.out_dir}")
        print(f"wrote {len(drawn)} new source text(s) -> {args.text_dir}")
        print(f"manifest now lists {target_total} document(s) -> {manifest_path}")

        data_out = args.data_out or Path(f"data/art6_domestic_gold{target_total}.json")
        _write_flat_data_json(manifest["documents"], text_by_case, data_out)
        return

    by_level = defaultdict(list)
    for doc in all_docs:
        by_level[doc["court_level"]].append(doc)
    levels = sorted(by_level)
    quota = allocate(args.n, levels)
    print(f"allocation (even across level, remainder alphabetical): {quota}")

    drawn = _draw_by_level(quota, by_level, args.seed)
    drawn.sort(key=lambda d: (d["court_level"], d["case_id"]))
    assert len({d["case_id"] for d in drawn}) == len(drawn) == args.n

    _write_templates_and_text(drawn, args.out_dir, args.text_dir)

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

    # Unlike --add (which always (re)writes its target file, a new filename
    # each time), a fresh draw only writes the flat data JSON when asked --
    # rerunning the base draw must never silently rewrite an existing
    # data/art6_domestic_gold<n>.json that other results already reference.
    if args.data_out is not None:
        _write_flat_data_json(manifest["documents"], text_by_case, args.data_out)


if __name__ == "__main__":
    main()
