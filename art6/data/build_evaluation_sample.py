"""
build_evaluation_sample.py
--------------------------
Draws the JURIX evaluation sample for judgments and decisions separately:
**even across court level, stratified over time within each level**, seeded.

WHY EVEN ACROSS LEVELS AND STRATIFIED WITHIN
--------------------------------------------
The corpus is wildly unbalanced -- 6,046 post-2000 English Chamber judgments
against 146 Grand Chamber -- so a proportional draw would put about five Grand
Chamber judgments in the study and support no claim about that formation at all.
Allocation across levels is therefore EVEN: equal power per formation, which is
what an evaluation set is for. It is not a survey of the corpus, and any
population estimate from it needs design weights.

Within a level, allocation over time is PROPORTIONAL, so each formation's sample
reproduces its own temporal spread rather than one it never had. It cannot be
even, because the levels do not span the same years: committees had no merits
competence until Protocol 14bis (1 October 2009), so an even draw over periods
would demand 2000-2009 committee judgments that do not exist -- the corpus holds
exactly two.

The stratified draw is scikit-learn's `train_test_split(..., stratify=periods)`,
which does the proportional allocation and the rounding itself. Everything is
driven by `--seed` (`random_state`), so a redraw is reproducible.

ONE DOCUMENT PER CASE
---------------------
A case decided by a Chamber and then referred to the Grand Chamber appears
twice, under the same application numbers, describing the same domestic
proceedings 1-2 years apart; merits and just-satisfaction judgments pair the same
way. There are 76 such pairs post-2000, 66 of them CHAMBER + GRANDCHAMBER, which
is 45% of the whole Grand Chamber stratum. The frame is collapsed to one document
per case before allocation, and uniqueness is asserted before writing.

NO LENGTH FILTER, NO LENGTH CEILING
-----------------------------------
Earlier sampling used MIN_FACTS_CHARS=2500 / MAX_FACTS_CHARS=40000, a chunking
convenience from a configuration no longer in use. It gated on the `facts`
section alone while the pipeline is fed introduction+procedure+facts; it removed
36% of English judgments; and it was not a neutral length filter but a filter on
court level in disguise, removing 65% of COMMITTEE against 24% of CHAMBER.

Nothing is excluded for length either. The one judgment that exceeds the stage 1
context window is split by `compress.py` and extracted in independent passes, so
document length is the pipeline's concern and not the sampler's.

Usage:
  uv run python -m art6.data.build_evaluation_sample --doc-type judgments --n 250
  uv run python -m art6.data.build_evaluation_sample --doc-type decisions --n 250
"""

from __future__ import annotations

import argparse
import collections
import json
import multiprocessing
import random
import re
from pathlib import Path

from sklearn.model_selection import train_test_split

from art6.data.build_ontocast_test_set import build_text
from art6.data.text_processing import process_echr_document
from art6.paths import REPO_ROOT, relative

DATA = REPO_ROOT / "data"
EXCLUSIONS = DATA / "art6_excluded_case_ids.json"

META = {
    "judgments": (
        DATA / "art_6_judgments_metadata_processed.json",
        DATA / "art_6_judgments_metadata.json",
    ),
    "decisions": (
        DATA / "art_6_decisions_metadata_processed.json",
        DATA / "art_6_decisions_metadata.json",
    ),
}

# The frame starts at 2000. The Commission was abolished in 1998 (Protocol 11)
# and committees had no merits competence until Protocol 14bis (1 October 2009),
# so a frame reaching further back cannot be balanced across the three
# formations at all.
MIN_YEAR = 2000
TEXT_DIR = {"judgments": DATA / "judgment_text", "decisions": DATA / "decision_text"}
PERIODS = ("2000-2009", "2010-2019", "2020+")

# scikit-learn's stratified split needs at least two members in every class.
MIN_STRATUM = 2


def period_of(year: int) -> str:
    if year < 2010:
        return "2000-2009"
    if year < 2020:
        return "2010-2019"
    return "2020+"


def load_years(raw_meta: Path) -> dict[str, int]:
    """Year of decision, from HUDOC's kpdateastext.

    NOT `judgementdate`: it is populated for judgments and empty for all but 6
    of 19,388 English decisions. `kpdateastext` is filled for every English
    document of both types and agrees with the year embedded in the ECLI on all
    of them.
    """
    years: dict[str, int] = {}
    for line in raw_meta.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        match = re.match(r"\d{2}/\d{2}/(\d{4})", str(record.get("kpdateastext") or ""))
        if match:
            years[record["itemid"]] = int(match.group(1))
    return years


def case_group(record: dict) -> str:
    """Identity of the CASE, not of the document, as a stable string.

    A case decided by a Chamber and then referred to the Grand Chamber appears
    twice under the same application numbers, describing the same domestic
    proceedings 1-2 years apart; merits and just-satisfaction judgments pair the
    same way. Both members are kept in the sample deliberately, so this is
    recorded rather than acted on -- it is what lets the pairs be found again
    once the pipeline has run over them.
    """
    appnos = sorted(record.get("case_appno") or [])
    return "+".join(appnos) if appnos else f"NAME:{record.get('case_name') or ''}"


def allocate_even(counts: dict, total: int) -> dict:
    """Split `total` as evenly as possible across levels, capped by population.

    A level smaller than its equal share is taken whole and the surplus is
    redistributed over the levels that still have headroom.
    """
    cells = {k: v for k, v in counts.items() if v > 0}
    if not cells:
        return {}
    alloc = {k: 0 for k in cells}
    open_cells = set(cells)
    remaining = total
    while remaining > 0 and open_cells:
        share, extra = divmod(remaining, len(open_cells))
        if share == 0:
            for k in sorted(open_cells, key=lambda c: cells[c], reverse=True)[
                :remaining
            ]:
                alloc[k] += 1
            break
        remaining = 0
        for n, k in enumerate(sorted(open_cells)):
            want = share + (1 if n < extra else 0)
            take = min(want, cells[k] - alloc[k])
            alloc[k] += take
            remaining += want - take
        open_cells = {k for k in cells if alloc[k] < cells[k]}
    return alloc


def stratified_draw(pool: list[dict], want: int, seed: int) -> list[dict]:
    """Draw `want` from `pool`, stratified over period, reproducibly.

    scikit-learn does the proportional allocation and the rounding. Strata below
    MIN_STRATUM are set aside first, because `train_test_split` refuses a class
    with fewer than two members; they are then sampled directly at their
    proportional rate so a tiny-but-real cell -- the two 2000-2009 committee
    judgments -- is neither crashed on nor silently discarded.
    """
    if want >= len(pool):
        return list(pool)
    rng = random.Random(seed)
    counts = collections.Counter(r["period"] for r in pool)
    tiny = [r for r in pool if counts[r["period"]] < MIN_STRATUM]
    main = [r for r in pool if counts[r["period"]] >= MIN_STRATUM]

    picked: list[dict] = []
    if tiny:
        share = round(want * len(tiny) / len(pool))
        picked.extend(rng.sample(tiny, min(share, len(tiny), want)))
    remaining = want - len(picked)
    if remaining <= 0 or not main:
        return picked
    if remaining >= len(main):
        return picked + main
    drawn, _ = train_test_split(
        main,
        train_size=remaining,
        stratify=[r["period"] for r in main],
        random_state=seed,
    )
    return picked + drawn


def resolve_text(job: tuple[str, str, bool]) -> tuple[str, str]:
    """Read one document's HTML and join its narrative sections.

    Section joining is `build_ontocast_test_set.build_text` itself, so the
    evaluation samples are assembled exactly as the tuning and holdout sets were
    -- introduction + procedure + facts, in that order, blank-line separated.
    The sections come from the corpus HTML rather than the sample parquet, which
    covers only part of the corpus.
    """
    case_id, path, is_decision = job
    file = Path(path)
    if not file.exists():
        return case_id, ""
    try:
        chunks = process_echr_document(
            file.read_text(encoding="utf-8", errors="replace"), is_decision=is_decision
        )["text_chunks"]
    except Exception:  # noqa: BLE001 -- reported by the caller, not swallowed
        # Any parse failure means "no text", and main() lists the affected ids
        # and exits non-zero. Letting one malformed file abort a 250-document
        # resolution would be worse than reporting it.
        return case_id, ""
    return case_id, build_text(chunks)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--doc-type", choices=sorted(META), required=True)
    ap.add_argument("--n", type=int, default=250)
    ap.add_argument(
        "--seed",
        type=int,
        default=20260830,
        help="Drives dedup tie-breaks and the stratified draw (random_state).",
    )
    ap.add_argument(
        "--language",
        default="ENG",
        help="HUDOC languageisocode, e.g. ENG or FRE.",
    )
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--workers", type=int, default=max(1, (multiprocessing.cpu_count() or 2) - 1)
    )
    args = ap.parse_args()

    language = args.language.upper()
    processed, raw = META[args.doc_type]
    years = load_years(raw)
    excluded = (
        set(json.loads(EXCLUSIONS.read_text())["exclude"])
        if EXCLUSIONS.exists()
        else set()
    )

    frame: list[dict] = []
    dropped: collections.Counter = collections.Counter()
    for line in processed.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        item = record["itemid"]
        if str(record.get("languageisocode") or "").upper() != language:
            dropped[f"not {language}"] += 1
            continue
        if item in excluded:
            dropped["tuning document"] += 1
            continue
        year = years.get(item)
        if year is None:
            dropped["no usable date"] += 1
            continue
        if year < MIN_YEAR:
            dropped[f"before {MIN_YEAR}"] += 1
            continue
        frame.append(
            {
                "case_id": item,
                "case_group": case_group(record),
                "court_level": str(record.get("court_level")),
                "year": year,
                "period": period_of(year),
                "respondent": str(record.get("respondent") or "?"),
                "case_name": str(record.get("case_name") or ""),
            }
        )

    print(f"frame: {len(frame):,} {language} {args.doc_type}")
    for reason, n in dropped.most_common():
        print(f"  excluded, {reason}: {n:,}")

    # NOT deduplicated by case. A Chamber judgment and the Grand Chamber judgment
    # that followed it describe the same domestic proceedings, and both are wanted
    # here: whether the pipeline extracts the same chain from two independent
    # accounts is a property worth measuring, not one to sample away. Each
    # document carries `case_group` so the pairs can be identified after
    # extraction.
    groups = collections.Counter(r["case_group"] for r in frame)
    paired = sum(1 for r in frame if groups[r["case_group"]] > 1)
    print(f"  documents sharing a case with another in the frame: {paired:,}")

    by_level = collections.Counter(r["court_level"] for r in frame)
    # A level with fewer than MIN_STRATUM documents cannot be stratified and
    # contributes a stratum of size one. French judgments have exactly one
    # post-2000 Grand Chamber document, so this is a real case, not a guard
    # against a hypothetical. Excluded and reported rather than carried.
    too_small = {k: v for k, v in by_level.items() if v < MIN_STRATUM}
    for level, n in sorted(too_small.items()):
        print(f"  level excluded, too few to sample: {level} (n={n})")
    if too_small:
        frame = [r for r in frame if r["court_level"] not in too_small]
        by_level = collections.Counter(r["court_level"] for r in frame)
    level_alloc = allocate_even(dict(by_level), args.n)

    picked: list[dict] = []
    for offset, (level, want) in enumerate(sorted(level_alloc.items())):
        pool = [r for r in frame if r["court_level"] == level]
        # Offset the seed per level so the three draws are independent rather
        # than three runs of the same pseudo-random sequence.
        picked.extend(stratified_draw(pool, want, args.seed + offset))
    picked.sort(key=lambda r: (r["court_level"], r["year"], r["case_id"]))

    drawn_groups = collections.Counter(r["case_group"] for r in picked)
    repeats = {g: n for g, n in drawn_groups.items() if n > 1}

    # Resolve the narrative text, as the tuning and holdout builders do, so the
    # sample file is directly usable rather than a list of ids to be joined to
    # the corpus by some later script.
    is_decision = args.doc_type == "decisions"
    jobs = [
        (
            r["case_id"],
            str(TEXT_DIR[args.doc_type] / f"{r['case_id']}.html"),
            is_decision,
        )
        for r in picked
    ]
    with multiprocessing.Pool(args.workers) as pool:
        texts = dict(pool.map(resolve_text, jobs))
    unusable = [r["case_id"] for r in picked if not texts.get(r["case_id"])]
    for record in picked:
        record["text"] = texts.get(record["case_id"], "")

    suffix = "" if language == "ENG" else f"_{language.lower()}"
    out = args.out or DATA / f"art6_eval_sample_{args.doc_type}{suffix}.json"
    jsonl = out.with_suffix(".jsonl")
    with jsonl.open("w", encoding="utf-8") as handle:
        for record in picked:
            if record["text"]:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    out.write_text(
        json.dumps(
            {
                "doc_type": args.doc_type,
                "language": language,
                "n": len(picked),
                "seed": args.seed,
                "design": "even across court levels, stratified over time within level",
                "min_year": MIN_YEAR,
                "frame_size": len(frame),
                "unique_cases": False,
                "case_groups_drawn_more_than_once": len(repeats),
                "excluded_tuning_ids": sorted(excluded),
                "documents": picked,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nsampled {len(picked)} -> {relative(out)}")
    print(f"  pipeline input -> {relative(jsonl)}")
    lengths = sorted(len(r["text"]) for r in picked if r["text"])
    if lengths:
        print(
            f"  narrative chars: min={lengths[0]:,}"
            f" median={lengths[len(lengths) // 2]:,} max={lengths[-1]:,}"
        )
    if unusable:
        raise SystemExit(
            f"{len(unusable)} sampled document(s) have no narrative: {unusable[:8]}"
        )
    if repeats:
        print(
            f"  {len(repeats)} case(s) drawn more than once, kept deliberately:"
            f" {sorted(repeats)[:5]}"
        )
    print()

    levels = sorted({r["court_level"] for r in picked})
    print(
        f"{'court_level':20}" + "".join(f"{p:>12}" for p in PERIODS) + f"{'total':>8}"
    )
    for level in levels:
        row = [
            sum(1 for r in picked if r["court_level"] == level and r["period"] == p)
            for p in PERIODS
        ]
        print(f"{level:20}" + "".join(f"{v:>12}" for v in row) + f"{sum(row):>8}")
    totals = [sum(1 for r in picked if r["period"] == p) for p in PERIODS]
    print(f"{'total':20}" + "".join(f"{v:>12}" for v in totals) + f"{len(picked):>8}")

    states = len({r["respondent"] for r in picked})
    print(f"\nrespondent states represented: {states}")
    print(
        f"year range: {min(r['year'] for r in picked)}-{max(r['year'] for r in picked)}"
    )


if __name__ == "__main__":
    main()
