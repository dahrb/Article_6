"""
build_ontocast_test_set.py
--------------------------
Selects 10 English-language Art. 6 cases spanning every court level in the sample
(Grand Chamber, Chamber, Committee, and the admissibility/decision formations) and
covering violation, non-violation and inadmissibility outcomes, then writes the
summary + facts sections only to JSON for OntoCast extraction runs against
issues/ontologies/art6_domestic_extraction.ttl.

Only the narrative sections are exported: the Court's own reasoning (law, reasons)
is withheld so the extraction is scored on what the facts actually state about the
domestic proceedings.

Output:
  issues/ontocast_test_set/art6_domestic_test_set.json
      - a JSON array of {"case_id", "text"}, one entry per case

Usage:
  uv run python issues/build_ontocast_test_set.py
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent
PARQUET_PATH = SCRIPT_DIR / "sample_metadata.parquet"
OUT_DIR = SCRIPT_DIR / "ontocast_test_set"

# Keep texts in a band that chunks sanely at CHUNK_MIN_SIZE=10000 / MAX=30000
MIN_FACTS_CHARS = 2_500
MAX_FACTS_CHARS = 40_000

# (slot label, court_level, outcome predicate) — one row is taken per slot.
# outcome: "violation" | "nonviolation" | "any"
SLOTS: list[tuple[str, str, str]] = [
    ("Grand Chamber — Art. 6 violation",        "GRANDCHAMBER",     "violation"),
    ("Grand Chamber — no violation",            "GRANDCHAMBER",     "nonviolation"),
    ("Chamber — Art. 6 violation",              "CHAMBER",          "violation"),
    ("Chamber — no violation",                  "CHAMBER",          "nonviolation"),
    ("Committee — Art. 6 violation",            "COMMITTEE",        "violation"),
    ("Committee — second case",                 "COMMITTEE",        "any"),
    ("Admissibility decision (Chamber)",        "ADMISSIBILITY",    "any"),
    ("Admissibility decision (Chamber) — 2nd",  "ADMISSIBILITY",    "any"),
    ("Admissibility decision (Commission)",     "ADMISSIBILITYCOM", "any"),
    ("Grand Chamber admissibility decision",    "DECGRANDCHAMBER",  "any"),
]


def has_article_6(values: list[str] | None) -> bool:
    """True if any entry in a violation/nonviolation list refers to Article 6."""
    return any(str(v).strip().startswith("6") for v in (values or []))


def build_text(row: dict) -> str:
    """Summary + facts only, as one markdown document."""
    parts: list[str] = []
    for section in ("introduction", "procedure", "facts"):
        value = row.get(section)
        if value and str(value).strip():
            parts.append(str(value).strip())
    return "\n\n".join(parts)


def main() -> None:
    df = pl.read_parquet(PARQUET_PATH)

    pool = df.filter(
        (pl.col("languageisocode").cast(pl.Utf8).str.to_uppercase() == "ENG")
        & pl.col("facts").is_not_null()
        & (pl.col("facts").cast(pl.Utf8).str.len_chars() >= MIN_FACTS_CHARS)
        & (pl.col("facts").cast(pl.Utf8).str.len_chars() <= MAX_FACTS_CHARS)
    ).sort("itemid")

    rows = [pool.row(i, named=True) for i in range(pool.height)]

    selected: list[dict] = []
    used_ids: set[str] = set()
    used_states: set[str] = set()

    for label, level, outcome in SLOTS:
        candidates = [
            r for r in rows
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
            print(f"[warn] no candidate for slot: {label}")
            continue

        # Prefer an unseen respondent state, then the longest facts section.
        fresh = [r for r in candidates if r["respondent"] not in used_states]
        pick = max(fresh or candidates, key=lambda r: len(r["facts"]))

        used_ids.add(pick["itemid"])
        used_states.add(pick["respondent"])
        selected.append({"slot": label, "row": pick})

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest = [
        {"case_id": entry["row"]["itemid"], "text": build_text(entry["row"])}
        for entry in selected
    ]

    (OUT_DIR / "art6_domestic_test_set.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"{len(manifest)} cases -> {OUT_DIR.relative_to(REPO_ROOT)}\n")
    for entry, case in zip(selected, manifest):
        row = entry["row"]
        print(
            f"  {case['case_id']:<12} {row['court_level']:<17} {str(row['respondent']):<10} "
            f"{len(case['text']):>7,} chars  {entry['slot']}"
        )


if __name__ == "__main__":
    main()
