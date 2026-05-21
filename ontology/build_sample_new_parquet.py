"""
Script to build the sampled metadata parquet used by initial OntoCast runs.

Last Updated:
19.05.26

Status:
Done

History:
v1_0 - extract notebook sampling and text-join logic into standalone script
"""

from pathlib import Path
import json
import re

import numpy as np
import polars as pl

SEED = 42
TARGET_PER_LEVEL = 50

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
TEXT_DIR = DATA_DIR / "processed_json"
OUTPUT_PATH = DATA_DIR / "sample_metadata.parquet"

itemid_pattern = re.compile(r'"itemid"\s*:\s*"([^"]+)"')
invalid_escape_re = re.compile(r"\\(?![\"\\/bfnrtu])")


def load_text_itemids(jsonl_path: Path) -> set[str]:
    itemids = set()
    with jsonl_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = itemid_pattern.search(line)
            if match:
                itemids.add(match.group(1))
    return itemids


def temporal_spread_sample(group_df: pl.DataFrame, target_n: int) -> pl.DataFrame:
    n = group_df.height
    if n <= target_n:
        return group_df

    sorted_df = group_df.sort("year")
    idx = np.linspace(0, n - 1, num=target_n, dtype=int)

    return (
        sorted_df.with_row_index("_row_idx")
        .filter(pl.col("_row_idx").is_in(idx.tolist()))
        .drop("_row_idx")
    )


def _safe_json_load(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None

    try:
        return json.loads(line)
    except json.JSONDecodeError:
        fixed = invalid_escape_re.sub(r"\\\\", line)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            return None


def _normalize_label(label: str) -> str:
    label = label.strip().lower()
    if label.startswith("the "):
        label = label[4:]
    label = re.sub(r"[^a-z0-9]+", "_", label)
    label = label.strip("_")
    return label

def _extract_json_labeled_sections(raw_text_chunks) -> dict[str, str]:
    labeled: dict[str, list[str]] = {}

    if isinstance(raw_text_chunks, dict):
        for key, value in raw_text_chunks.items():
            label = _normalize_label(str(key))
            if not label:
                continue
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            if text and text.strip():
                labeled.setdefault(label, []).append(text.strip())

    elif isinstance(raw_text_chunks, list) and all(isinstance(x, dict) for x in raw_text_chunks):
        for chunk in raw_text_chunks:
            raw_label = chunk.get("label") or chunk.get("section") or chunk.get("title")
            if raw_label is None:
                continue
            label = _normalize_label(str(raw_label))
            if not label:
                continue
            text = chunk.get("text") or chunk.get("content") or ""
            if isinstance(text, str) and text.strip():
                labeled.setdefault(label, []).append(text.strip())

    return {k: "\n\n".join(v) for k, v in labeled.items() if v}

def _derive_sections_for_text_sections(full_text: str | None, raw_text_chunks):
    if isinstance(raw_text_chunks, (dict, list)) and raw_text_chunks:
        return raw_text_chunks
    if isinstance(full_text, str) and full_text.strip():
        sections = [part.strip() for part in full_text.split("\n\n") if part.strip()]
        return sections if sections else None
    return None

def load_text_rows(file_path: Path, source_label: str, keep_ids: set[str]) -> list[dict]:
    rows = []
    with file_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            obj = _safe_json_load(line)
            if not obj:
                continue

            itemid = obj.get("itemid")
            if itemid is None:
                continue

            itemid_str = str(itemid)
            if itemid_str not in keep_ids:
                continue

            full_text = obj.get("full_text")
            raw_text_chunks = obj.get("text_chunks")
            text_sections = _derive_sections_for_text_sections(full_text, raw_text_chunks)
            labeled_sections = _extract_json_labeled_sections(raw_text_chunks)

            row = {
                "itemid": itemid_str,
                "source": source_label,
                "full_text": full_text,
                "text_sections": text_sections,
            }
            row.update(labeled_sections)
            rows.append(row)

    return rows

def build_sample_dataframe() -> pl.DataFrame:
    """Build the sampled metadata dataframe with full text and labeled sections."""
    np.random.seed(SEED)

    judgment_text_itemids = sorted(load_text_itemids(TEXT_DIR / "echr_corpus.jsonl"))
    decision_text_itemids = sorted(load_text_itemids(TEXT_DIR / "echr_decisions_corpus.jsonl"))

    judgments_metadata_raw = pl.read_ndjson(
        DATA_DIR / "art_6_judgments_metadata_processed.json",
        infer_schema_length=None,
    ).with_columns(
        pl.lit("judgments").alias("source"),
        pl.col("itemid").cast(pl.Utf8),
    )

    decisions_metadata_raw = pl.read_ndjson(
        DATA_DIR / "art_6_decisions_metadata_processed.json",
        infer_schema_length=None,
    ).with_columns(
        pl.lit("decisions").alias("source"),
        pl.col("itemid").cast(pl.Utf8),
    )

    judgments_metadata = judgments_metadata_raw.filter(pl.col("itemid").is_in(judgment_text_itemids))
    decisions_metadata = decisions_metadata_raw.filter(pl.col("itemid").is_in(decision_text_itemids))

    metadata = pl.concat([judgments_metadata, decisions_metadata], how="diagonal_relaxed")

    metadata = metadata.with_columns(
        pl.coalesce(
            [
                pl.col("judgementdate").cast(pl.Utf8).str.slice(0, 4).cast(pl.Int32, strict=False),
                pl.col("ecli").cast(pl.Utf8).str.extract(r":(\d{4}):", 1).cast(pl.Int32, strict=False),
            ]
        ).alias("year")
    )

    metadata_for_sampling = metadata.filter(pl.col("court_level").is_not_null() & pl.col("year").is_not_null())

    sampled_groups = []
    for level in sorted(metadata_for_sampling["court_level"].unique().to_list()):
        level_df = metadata_for_sampling.filter(pl.col("court_level") == level)
        sampled_groups.append(temporal_spread_sample(level_df, TARGET_PER_LEVEL))

    sampled_metadata = pl.concat(sampled_groups, how="diagonal_relaxed")

    required_cols = {"itemid", "source"}
    missing_cols = required_cols - set(sampled_metadata.columns)
    if missing_cols:
        raise ValueError(f"sampled_metadata is missing required columns: {sorted(missing_cols)}")

    sample_ids_by_source = {
        "judgments": set(
            sampled_metadata
            .filter(pl.col("source") == "judgments")["itemid"]
            .cast(pl.Utf8)
            .to_list()
        ),
        "decisions": set(
            sampled_metadata
            .filter(pl.col("source") == "decisions")["itemid"]
            .cast(pl.Utf8)
            .to_list()
        ),
    }

    text_rows = []
    text_rows.extend(
        load_text_rows(
            TEXT_DIR / "echr_corpus.jsonl",
            "judgments",
            sample_ids_by_source["judgments"],
        )
    )
    text_rows.extend(
        load_text_rows(
            TEXT_DIR / "echr_decisions_corpus.jsonl",
            "decisions",
            sample_ids_by_source["decisions"],
        )
    )

    text_lookup = (
        pl.from_dicts(text_rows)
        .with_columns(
            pl.col("itemid").cast(pl.Utf8),
            pl.col("source").cast(pl.Utf8),
        )
        .unique(subset=["itemid", "source"], keep="first")
    )

    base_cols = [c for c in sampled_metadata.columns if c in metadata_for_sampling.columns]
    sampled_metadata = sampled_metadata.select(base_cols)

    sampled_metadata = (
        sampled_metadata
        .with_columns(pl.col("itemid").cast(pl.Utf8))
        .join(text_lookup, on=["itemid", "source"], how="left")
    )

    sampled_metadata = sampled_metadata.drop(["text_sections"], strict=False)
    return sampled_metadata

def main() -> None:
    # Build and persist the new sample parquet used by OntoCast runner scripts.
    sampled_metadata = build_sample_dataframe()
    sampled_metadata.write_parquet(OUTPUT_PATH)

    print(f"Wrote: {OUTPUT_PATH}")
    print(f"Rows: {sampled_metadata.height}")
    print(f"Columns: {len(sampled_metadata.columns)}")

if __name__ == "__main__":
    main()
