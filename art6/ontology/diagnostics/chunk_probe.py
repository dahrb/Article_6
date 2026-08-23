"""
### DELETE IN FUTURE
chunk_probe.py
--------------
Offline probe of OntoCast's chunking and section classification on the Art. 6
test set. Answers "how would this document be split, and what would each piece
be labelled?" without spending a single LLM token.

Only the deterministic classifier tiers are exercised ('off', 'heading',
'heuristic'); 'llm' needs a ToolBox and would cost money, which defeats the
point of a probe.

Each chunk is reported with its size, its section label and the tier that
assigned it, plus the span of numbered paragraphs (**N.** / N.) it covers.
The paragraph span is what makes a bad boundary visible: a chunk running
"§5-35" next to one running "§1-24" is cutting through a narrated chain.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from ontocast.config import ChunkConfig
from ontocast.onto.docling_helpers import plain_text_to_docling_doc
from ontocast.tool.chunk.chunker import ChunkerTool
from ontocast.tool.chunk.prepare import PrepareOptions, prepare_content_units

PARA = re.compile(r"(?m)^\*\*(\d+)\.\*\*")


def para_span(text: str):
    nums = [int(m.group(1)) for m in PARA.finditer(text)]
    return (min(nums), max(nums), len(nums)) if nums else None


async def run(
    records,
    *,
    classifier,
    schema_id,
    min_size,
    max_size,
    exclude=None,
    target=None,
    detect="headings",
):
    cfg = ChunkConfig(
        min_size=min_size,
        max_size=max_size,
        section_classifier=classifier,
        section_schema_detect=detect,
    )
    chunker = ChunkerTool(chunk_config=cfg)
    out = []
    for i, rec in enumerate(records, 1):
        doc = plain_text_to_docling_doc(rec["text"], f"L{i}")
        opts = PrepareOptions(
            section_schema_id=schema_id,
            exclude_sections=exclude,
            target_sections=target,
        )
        try:
            chunks = await prepare_content_units(doc, chunker, cfg, opts, None)
        except Exception as exc:  # noqa: BLE001 - a probe reports failures, never raises
            out.append((i, rec["case_id"], f"ERROR {type(exc).__name__}: {exc}", []))
            continue
        rows = [
            (
                len(c.text),
                c.section_label,
                str(c.section_label_source),
                round(c.section_label_confidence, 2),
                para_span(c.text),
            )
            for c in chunks
        ]
        out.append((i, rec["case_id"], None, rows))
    return out


def report(title, res):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    tot = 0
    for i, cid, err, rows in res:
        if err:
            print(f"L{i:<3} {cid:<12} {err}")
            continue
        tot += len(rows)
        chars = sum(r[0] for r in rows)
        print(f"L{i:<3} {cid:<12} {len(rows)} chunks, {chars:,} chars kept")
        for j, (n, lab, src, conf, ps) in enumerate(rows):
            span = f"§{ps[0]}-{ps[1]} ({ps[2]})" if ps else "no §"
            print(
                f"      [{j}] {n:>6} chars  label={lab!s:<22} src={src.split('.')[-1]:<20} conf={conf:<5} {span}"
            )
    print(f"  TOTAL CHUNKS: {tot}")


async def main():
    records = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    mode = sys.argv[2] if len(sys.argv) > 2 else "all"

    if mode in ("all", "baseline"):
        report(
            "A. BASELINE — classifier=off, min=5000 max=15000 (the 2026-08-18 runs)",
            await run(
                records, classifier="off", schema_id=None, min_size=5000, max_size=15000
            ),
        )

    if mode in ("all", "nochunk"):
        report(
            "B. NO CHUNKING — classifier=off, min=40000 max=45000",
            await run(
                records,
                classifier="off",
                schema_id=None,
                min_size=40000,
                max_size=45000,
            ),
        )

    if mode in ("all", "heading"):
        report(
            "C. classifier=heading, legal schema, min=5000 max=15000",
            await run(
                records,
                classifier="heading",
                schema_id="legal",
                min_size=5000,
                max_size=15000,
            ),
        )
        report(
            "D. classifier=heuristic, auto-detect schema, min=5000 max=15000",
            await run(
                records,
                classifier="heuristic",
                schema_id=None,
                min_size=5000,
                max_size=15000,
            ),
        )
        report(
            "E. classifier=heuristic, auto-detect, NO SIZE CHUNKING (min=40000)",
            await run(
                records,
                classifier="heuristic",
                schema_id=None,
                min_size=40000,
                max_size=45000,
            ),
        )


asyncio.run(main())
