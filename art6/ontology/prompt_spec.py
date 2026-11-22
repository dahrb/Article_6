"""The instruction OntoCast (stage 2) builds the graph under.

prompts/facts.txt is the whole instruction: how to write the graph, the
definitions of what goes in it, and the mapping for the timeline ontology
(circumstances; the health, immigration and enforcement matters). Stage 3
quotes it wherever a prompt says "the instructions the graph was built under".

It used to be composed at run time from sections of the extraction prompt, so
that every ablation arm was held to the same definitions. With one pipeline
and the guide-based extraction prompt, which no longer has those sections, it
is frozen as the text the evaluated runs used (the instruction baked into
results/exp_timeline/input_timeline.jsonl), plus one fix made on 2026-11-09:
the matter mapping now names the timeline matters (health, immigration,
enforcement), which the evaluated runs left to stage 3's transcription.
"""

from __future__ import annotations

from pathlib import Path

FACTS = Path(__file__).resolve().parent / "prompts" / "facts.txt"


def facts_instruction() -> str:
    return FACTS.read_text(encoding="utf-8").strip()
