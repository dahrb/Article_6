"""Bridge: stage-1 payloads -> the JSONL OntoCast (stage 2) reads.

Each record is the judgment's case id, the RENDERED stage-1 digest as `text`
(render_bundles.render), and the stage-2 instruction as
`facts_user_instruction` (prompt_spec.facts_instruction). Stage 2 never sees
the raw judgment.

    uv run python -m art6.ontology.bridge STAGE1_DIR SOURCE_JSONL OUT_JSONL
    uv run python -m art6.ontology.bridge --raw SOURCE_JSONL OUT_JSONL

`--raw` is the one-shot baseline: the judgment itself as `text`, with the same
instruction, so OntoCast alone extracts from the source.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from art6.ontology.prompt_spec import facts_instruction
from art6.ontology.render_bundles import render


def build(stage1_dir: Path | None, source_jsonl: Path, out_jsonl: Path) -> int:
    """Write one record per judgment; stage1_dir None writes the raw judgment."""
    instruction = facts_instruction()
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(out_jsonl, "w", encoding="utf-8") as fh:
        for line in source_jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            text = record["text"]
            if stage1_dir is not None:
                payload = json.loads(
                    (stage1_dir / f"{record['case_id']}.compress.json").read_text(
                        encoding="utf-8"
                    )
                )["payload"]
                text = render(payload, text, "bundle")
            fh.write(
                json.dumps(
                    {
                        "case_id": record["case_id"],
                        "text": text,
                        "facts_user_instruction": instruction,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--raw", action="store_true", help="no stage 1: the judgment itself"
    )
    ap.add_argument(
        "paths",
        nargs="+",
        type=Path,
        metavar="PATH",
        help="STAGE1_DIR SOURCE_JSONL OUT_JSONL, or with --raw SOURCE_JSONL OUT_JSONL",
    )
    args = ap.parse_args()
    if len(args.paths) != (2 if args.raw else 3):
        ap.error(
            "expected STAGE1_DIR SOURCE_JSONL OUT_JSONL, or --raw SOURCE_JSONL OUT_JSONL"
        )
    stage1_dir = None if args.raw else args.paths[0]
    n = build(stage1_dir, *args.paths[-2:])
    print(f"bridge: {n} record(s) -> {args.paths[-1]}")


if __name__ == "__main__":
    main()
