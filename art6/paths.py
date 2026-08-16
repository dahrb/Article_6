"""Canonical locations for everything the pipeline reads and writes.

Paths are anchored on the repo root derived from this file, never on the working
directory, so a stage resolves to the same place wherever it is launched from.
"""

from __future__ import annotations

from pathlib import Path

# art6/paths.py -> art6/ -> repo root
REPO_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = REPO_ROOT / "data"
MAPPINGS_DIR = DATA_DIR / "mappings"
ADDITIONAL_DATA_DIR = DATA_DIR / "additional_data"

# stage 1 - collection
RAW_METADATA_JSON = DATA_DIR / "hudoc_art6_raw_metadata.json"
APPNO_MAPPING_CSV = MAPPINGS_DIR / "echr_appno_mapping.csv"
JUDGMENT_TEXT_DIR = DATA_DIR / "judgment_text"
DECISION_TEXT_DIR = DATA_DIR / "decision_text"
JUDGMENTS_METADATA_JSON = DATA_DIR / "art_6_judgments_metadata.json"
DECISIONS_METADATA_JSON = DATA_DIR / "art_6_decisions_metadata.json"

# stage 2 - metadata processing
JUDGMENTS_METADATA_PROCESSED_JSON = DATA_DIR / "art_6_judgments_metadata_processed.json"
DECISIONS_METADATA_PROCESSED_JSON = DATA_DIR / "art_6_decisions_metadata_processed.json"
JUDGES_CSV = ADDITIONAL_DATA_DIR / "judges.csv"
JUDGES_PROCESSED_JSON = ADDITIONAL_DATA_DIR / "judges_processed.json"
UNMATCHED_JUDGES_TXT = ADDITIONAL_DATA_DIR / "unmatched_judges.txt"
KEY_LABELS_JSON = MAPPINGS_DIR / "key_labels.json"
LAW_SYSTEM_MAPPING_JSON = MAPPINGS_DIR / "law_system_mapping.json"

# stage 3 - text processing
PROCESSED_JSON_DIR = DATA_DIR / "processed_json"
JUDGMENTS_CORPUS_JSONL = PROCESSED_JSON_DIR / "echr_corpus.jsonl"
DECISIONS_CORPUS_JSONL = PROCESSED_JSON_DIR / "echr_decisions_corpus.jsonl"

# stage 4 - extraction test set
SAMPLE_METADATA_PARQUET = DATA_DIR / "sample_metadata.parquet"
ONTOCAST_TEST_SET_JSON = DATA_DIR / "art6_domestic_test_set.json"


def relative(path: Path) -> Path:
    """Path relative to the repo root when possible, for readable logging."""
    try:
        return path.relative_to(REPO_ROOT)
    except ValueError:
        return path
