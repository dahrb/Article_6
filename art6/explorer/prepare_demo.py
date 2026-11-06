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
    return {"schema_version": 1, "default": "vote9_v9_luna", "datasets": datasets}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-root", type=Path, default=Path("results/exp_stage1/full")
    )
    parser.add_argument("--output", type=Path, default=DEMO_PATH)
    args = parser.parse_args()
    result = package(args.results_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Packaged two runs of ten cases in {args.output}")


if __name__ == "__main__":
    main()
