"""Package saved payloads, with source hashes, without invoking any model.

uv run python -m art6.explorer.prepare_demo
"""

import argparse
import hashlib
import json
from pathlib import Path

from art6.explorer.data import DEMO_PATH

RUNS = {
    "vote9_v9_luna": ("Nine-run vote · best event score", "vote9_v9_luna"),
    "v9_luna": ("Original run · includes minor events", "v9_luna/s1"),
}


def package(root: Path) -> dict:
    datasets = {}
    expected = None
    for key, (label, directory) in RUNS.items():
        cases = []
        for path in sorted((root / directory).glob("*.compress.json")):
            content = path.read_bytes()
            doc = json.loads(content)
            payload = doc["payload"]
            ids = [event["id"] for event in payload["events"]]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate event IDs: {path}")
            cases.append(
                {
                    "id": path.name.split(".")[0],
                    "payload": payload,
                    "source": f"results/exp_stage1/full/{directory}/{path.name}",
                    "sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        ids = {case["id"] for case in cases}
        if len(ids) != 10 or (expected is not None and ids != expected):
            raise ValueError(f"Expected the same ten validation cases in {directory}")
        expected = ids
        datasets[key] = {"label": label, "cases": cases}
    return {
        "schema_version": 2,
        "default": "vote9_v9_luna",
        "datasets": datasets,
        "documents": source_documents(root, expected),
    }


def source_documents(root: Path, expected: set[str]) -> dict:
    source_path = root / "v9_luna" / "source.jsonl"
    documents = {}
    for line in source_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        case_id, text = record["case_id"], record["text"]
        if case_id in documents:
            raise ValueError(f"Duplicate source case: {case_id}")
        documents[case_id] = {
            "text": text,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source": "results/exp_stage1/full/v9_luna/source.jsonl",
            "label": "Saved extraction input",
        }
    if set(documents) != expected:
        raise ValueError("Source documents must match the ten validation cases")
    return documents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-root", type=Path, default=Path("results/exp_stage1/full")
    )
    parser.add_argument("--output", type=Path, default=DEMO_PATH)
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="Add source text while preserving the packaged result snapshot",
    )
    args = parser.parse_args()
    if args.source_only:
        result = json.loads(args.output.read_text(encoding="utf-8"))
        expected = {
            case["id"]
            for dataset in result["datasets"].values()
            for case in dataset["cases"]
        }
        result["documents"] = source_documents(args.results_root, expected)
        result["schema_version"] = 2
    else:
        result = package(args.results_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Packaged two runs of ten cases in {args.output}")


if __name__ == "__main__":
    main()
