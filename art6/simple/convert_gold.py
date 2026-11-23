"""Write the simplified 10-set gold from annotation/test + the hand mapping.

    uv run python -m art6.simple.convert_gold

The original gold (annotation/test) is read, never written. Output:
annotation/test_simple/<case>.json, one per document in the mapping.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from art6.simple.model import convert_gold


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gold-dir", type=Path, default=Path("annotation/test"))
    ap.add_argument("--out-dir", type=Path, default=Path("annotation/test_simple"))
    a = ap.parse_args()
    mapping = yaml.safe_load((a.out_dir / "mapping.yaml").read_text(encoding="utf-8"))
    total_in = total_out = 0
    for case in sorted(mapping):
        doc = convert_gold(case, a.gold_dir, mapping)
        (a.out_dir / f"{case}.json").write_text(
            json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        n_in = len(
            yaml.safe_load((a.gold_dir / f"{case}.yaml").read_text())["proceedings"]
        )
        total_in += n_in
        total_out += len(doc["events"])
        print(
            f"{case}: {n_in} gold events -> {len(doc['events'])} "
            f"({sum(e['role'] == 'start' for e in doc['events'])} starts), {len(doc['sets'])} sets"
        )
    print(f"total: {total_in} -> {total_out}")


if __name__ == "__main__":
    main()
