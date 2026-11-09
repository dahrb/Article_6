"""Bridge: stage-1 payloads -> the JSONL OntoCast (stage 2) reads.

Each record is the judgment's case id, the RENDERED stage-1 digest as `text`
(render_bundles.render), and the stage-2 instruction as
`facts_user_instruction` (prompt_spec.facts_instruction). Stage 2 never sees
the raw judgment.

    uv run python -m art6.ontology.bridge STAGE1_DIR SOURCE_JSONL OUT_JSONL
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from art6.ontology.prompt_spec import facts_instruction
from art6.ontology.render_bundles import render


def build(stage1_dir: Path, source_jsonl: Path, out_jsonl: Path) -> int:
    instruction = facts_instruction()
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(out_jsonl, "w", encoding="utf-8") as fh:
        for line in source_jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            payload = json.loads(
                (stage1_dir / f"{record['case_id']}.compress.json").read_text(
                    encoding="utf-8"
                )
            )["payload"]
            fh.write(
                json.dumps(
                    {
                        "case_id": record["case_id"],
                        "text": render(payload, record["text"], "bundle"),
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
    ap.add_argument("stage1_dir", type=Path)
    ap.add_argument("source_jsonl", type=Path, help="the judgments: case_id, text")
    ap.add_argument("out_jsonl", type=Path)
    args = ap.parse_args()
    n = build(args.stage1_dir, args.source_jsonl, args.out_jsonl)
    print(f"bridge: {n} record(s) -> {args.out_jsonl}")


if __name__ == "__main__":
    main()
