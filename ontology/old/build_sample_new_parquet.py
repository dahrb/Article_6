"""
Build a full judgments parquet (no decisions, no language filter).

Behavioral note (important):
- Every judgments row is included from data/art_6_judgments_metadata_processed.json.
- No English-only filter is applied.
- For rows where full_text is longer than 40,000 characters, full_text is replaced
    with the extracted "facts" section. The replacement status is recorded in
    full_text_fallback_applied.
"""

from pathlib import Path
import json
import re

import polars as pl

MAX_FULL_TEXT_CHARS = 40_000

# this script lives in ontology/old/, so the repo root is two levels up
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
TEXT_DIR = DATA_DIR / "processed_json"
OUTPUT_PATH = DATA_DIR / "judgments_metadata_full.parquet"

invalid_escape_re = re.compile(r"\\(?![\"\\/bfnrtu])")

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

def _facts_fallback_expr(df: pl.DataFrame) -> pl.Expr:
    candidates = ["facts", "the_facts", "statement_of_facts"]
    available = [c for c in candidates if c in df.columns]
    if not available:
        return pl.lit(None, dtype=pl.Utf8)
    return pl.coalesce([pl.col(c).cast(pl.Utf8, strict=False) for c in available])

def build_judgments_dataframe() -> pl.DataFrame:
    """Build the full judgments metadata dataframe with text and fallback handling."""
    judgments_metadata = pl.read_ndjson(
        DATA_DIR / "art_6_judgments_metadata_processed.json",
        infer_schema_length=None,
    ).with_columns(
        pl.lit("judgments").alias("source"),
        pl.col("itemid").cast(pl.Utf8),
    )

    required_cols = {"itemid", "source"}
    missing_cols = required_cols - set(judgments_metadata.columns)
    if missing_cols:
        raise ValueError(f"judgments_metadata is missing required columns: {sorted(missing_cols)}")

    judgment_ids = set(judgments_metadata["itemid"].cast(pl.Utf8).to_list())

    text_rows = []
    text_rows.extend(
        load_text_rows(
            TEXT_DIR / "echr_corpus.jsonl",
            "judgments",
            judgment_ids,
        )
    )

    if text_rows:
        text_lookup = (
            pl.from_dicts(text_rows)
            .with_columns(
                pl.col("itemid").cast(pl.Utf8),
                pl.col("source").cast(pl.Utf8),
            )
            .unique(subset=["itemid", "source"], keep="first")
        )
    else:
        text_lookup = pl.DataFrame(
            {
                "itemid": [],
                "source": [],
                "full_text": [],
                "text_sections": [],
            },
            schema={
                "itemid": pl.Utf8,
                "source": pl.Utf8,
                "full_text": pl.Utf8,
                "text_sections": pl.Null,
            },
        )

    judgments_metadata = (
        judgments_metadata
        .with_columns(pl.col("itemid").cast(pl.Utf8))
        .join(text_lookup, on=["itemid", "source"], how="left")
    )

    facts_expr = _facts_fallback_expr(judgments_metadata)
    fallback_applied_expr = (
        pl.col("full_text").cast(pl.Utf8, strict=False).str.len_chars() > MAX_FULL_TEXT_CHARS
    )

    judgments_metadata = judgments_metadata.with_columns(
        fallback_applied_expr.alias("full_text_fallback_applied"),
        pl.when(fallback_applied_expr)
        .then(facts_expr)
        .otherwise(pl.col("full_text").cast(pl.Utf8, strict=False))
        .alias("full_text"),
    )

    judgments_metadata = judgments_metadata.drop(["text_sections"], strict=False)
    return judgments_metadata

def main() -> None:
    # Build and persist the full judgments parquet used by OntoCast runner scripts.
    judgments_metadata = build_judgments_dataframe()
    judgments_metadata.write_parquet(OUTPUT_PATH)

    print(f"Wrote: {OUTPUT_PATH}")
    print(f"Rows: {judgments_metadata.height}")
    print(f"Columns: {len(judgments_metadata.columns)}")
    fallback_count = (
        judgments_metadata
        .select(pl.col("full_text_fallback_applied").sum().alias("fallback_count"))
        .item()
    )
    print(f"Rows with full_text replaced by facts (>40k chars): {fallback_count}")

if __name__ == "__main__":
    main()
