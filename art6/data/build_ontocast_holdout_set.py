"""
build_ontocast_holdout_set.py
-----------------------------
Selects a further N English-language Art. 6 cases by the SAME slot design as
`build_ontocast_test_set.py` -- one per slot by default, so the holdout mirrors
the pilot's stratification exactly -- excluding every case already in the
10-document pilot set. `--per-slot 2` doubles it to 20 if a larger holdout is
wanted later.

WHY A SEPARATE SET, AND WHY IT MUST BE RECORDED
-----------------------------------------------
The pilot 10 were used to finesse prompts and settle configuration, so any
number measured on them is selection-contaminated -- the prompts were written
while looking at those documents. These 20 have never been seen by a prompt
author, which is what makes them a usable read on the pipeline as configured.

They are therefore ALSO contaminated the moment they are used that way. The
written manifest exists so the 240-document evaluation sample can exclude all
30 (10 pilot + 20 holdout) and keep selection and evaluation on disjoint sets.
Freeze the exclusion list before drawing the evaluation sample; do not draw it
first and subtract afterwards.

Output:
  data/art6_domestic_holdout_20.json      [{"case_id", "text"}, ...]
  data/art6_excluded_case_ids.json        the union of pilot + holdout ids,
                                          with provenance, for the sampler

Usage:
  uv run python -m art6.data.build_ontocast_holdout_set
"""

from __future__ import annotations

import argparse
import datetime
import json

import polars as pl

from art6.data.build_ontocast_test_set import (
    MAX_FACTS_CHARS,
    MIN_FACTS_CHARS,
    SLOTS,
    build_text,
    has_article_6,
)
from art6.paths import ONTOCAST_TEST_SET_JSON, SAMPLE_METADATA_PARQUET, relative

HOLDOUT_JSON = ONTOCAST_TEST_SET_JSON.parent / "art6_domestic_holdout.json"
EXCLUSIONS_JSON = ONTOCAST_TEST_SET_JSON.parent / "art6_excluded_case_ids.json"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-slot", type=int, default=1)
    per_slot = ap.parse_args().per_slot

    pilot = json.loads(ONTOCAST_TEST_SET_JSON.read_text(encoding="utf-8"))
    pilot_ids = {entry["case_id"] for entry in pilot}

    df = pl.read_parquet(SAMPLE_METADATA_PARQUET)
    pool = df.filter(
        (pl.col("languageisocode").cast(pl.Utf8).str.to_uppercase() == "ENG")
        & pl.col("facts").is_not_null()
        & (pl.col("facts").cast(pl.Utf8).str.len_chars() >= MIN_FACTS_CHARS)
        & (pl.col("facts").cast(pl.Utf8).str.len_chars() <= MAX_FACTS_CHARS)
    ).sort("itemid")
    rows = [pool.row(i, named=True) for i in range(pool.height)]

    # Seed the used-state set from the pilot so the holdout spreads across
    # respondent states the pilot did not already cover, exactly as the pilot
    # spread across states within itself.
    used_ids = set(pilot_ids)
    used_states = {r["respondent"] for r in rows if r["itemid"] in pilot_ids}

    selected: list[dict] = []
    for label, level, outcome in SLOTS:
        for nth in range(per_slot):
            candidates = [
                r
                for r in rows
                if r["itemid"] not in used_ids
                and r["court_level"] == level
                and (
                    outcome == "any"
                    or (outcome == "violation" and has_article_6(r["violation"]))
                    or (
                        outcome == "nonviolation"
                        and has_article_6(r["nonviolation"])
                        and not has_article_6(r["violation"])
                    )
                )
            ]
            if not candidates:
                print(f"[warn] no candidate for slot: {label} (#{nth + 1})")
                continue
            fresh = [r for r in candidates if r["respondent"] not in used_states]
            pick = max(fresh or candidates, key=lambda r: len(r["facts"]))
            used_ids.add(pick["itemid"])
            used_states.add(pick["respondent"])
            slot = label if per_slot == 1 else f"{label} (#{nth + 1})"
            selected.append({"slot": slot, "row": pick})

    manifest = [
        {"case_id": e["row"]["itemid"], "text": build_text(e["row"])} for e in selected
    ]
    HOLDOUT_JSON.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    EXCLUSIONS_JSON.write_text(
        json.dumps(
            {
                "written": datetime.datetime.now(datetime.UTC).date().isoformat(),
                "why": (
                    "Pilot and holdout documents were used to develop prompts and "
                    "pipeline configuration. Exclude all of them from the "
                    "evaluation sample so selection and evaluation stay disjoint."
                ),
                "pilot_10": sorted(pilot_ids),
                "holdout": [c["case_id"] for c in manifest],
                "exclude": sorted(pilot_ids | {c["case_id"] for c in manifest}),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"{len(manifest)} holdout cases -> {relative(HOLDOUT_JSON)}")
    print(
        f"exclusion list ({len(pilot_ids) + len(manifest)} ids) -> {relative(EXCLUSIONS_JSON)}\n"
    )
    for entry, case in zip(selected, manifest):
        row = entry["row"]
        print(
            f"  {case['case_id']:<12} {row['court_level']:<17} {row['respondent']!s:<12} "
            f"{len(case['text']):>7,} chars  {entry['slot']}"
        )


if __name__ == "__main__":
    main()
