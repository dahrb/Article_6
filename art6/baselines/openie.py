"""Off-the-shelf, non-LLM baseline: Stanford OpenIE triples -> procedural events.

    uv run --with stanza==1.10.1 python -m art6.baselines.openie \\
        --input-jsonl results/best_vote4/source.jsonl --out-dir results/openie_test10

Needs Java and CoreNLP (`stanza.install_corenlp(dir=~/stanza_corenlp)`, or set
CORENLP_HOME). Nothing here calls an LLM.

OpenIE produces (subject, relation, object) triples per sentence and has no
notion of a court event, a date or a chain. The assembly step below turns
triples into events by a fixed rule, so the baseline can be scored by exactly
the same strict scorer as the pipeline (diagnostics/score_payloads):

  1  CoreNLP splits the Facts into sentences and extracts OpenIE triples.
  2  A sentence yields ONE event when it has (a) a triple whose relation
     contains a legal-act verb (LEGAL_VERBS) and (b) a date (parse_date: the
     first day-month-year, else month-year, else year).
  3  The event is a FILING (start date) when its legal verbs are all filing
     verbs, otherwise a DECISION (decision date).
  4  authority = the subject of the first legal triple; quote = the sentence
     (OpenIE's provenance unit).

Two arms are written:
  s1_none   events only, no links: OpenIE itself asserts no order
  s1_chain  plus a naive timeline: each event `follows` the previous one in
            date order (text order breaks ties) -- the obvious heuristic
            anyone would add on top

What it cannot produce -- outcome, level, matter, part_of, chains of separate
proceedings -- is reported as not applicable, not scored as zero.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from itertools import pairwise
from pathlib import Path

from art6.ontology.agentic_stage1 import parse_date

FILING = r"lodg|filed|files|file\b|brought|bring|institut|appeal|appli|complain|petition|requested|sued|challeng|referr?ed|submitted"
DECISION = (
    r"dismiss|upheld|uphold|quash|reject|grant|order|convict|sentenc|acquit|decid|"
    r"ruled|rule\b|deliver|declar|annul|allow|refus|discontinu|remit|award|found|"
    r"terminat|suspend|stay|adopt|issued|approv|set aside|overturn|endorsed|confirm|"
    r"held|discontinued|closed|charged|arrested|detained|indicted|released|struck"
)
FILING_RE = re.compile(FILING, re.IGNORECASE)
DECISION_RE = re.compile(DECISION, re.IGNORECASE)
LEGAL_VERBS = re.compile(f"{FILING}|{DECISION}", re.IGNORECASE)


def iso(d: tuple[int, int, int]) -> str:
    return (
        f"{d[0]:04d}"
        + (f"-{d[1]:02d}" if d[1] else "")
        + (f"-{d[2]:02d}" if d[2] else "")
    )


def sentence_text(sent, text: str) -> tuple[str, int, int]:
    lo = sent.token[0].beginChar
    hi = sent.token[-1].endChar
    return text[lo:hi], lo, hi


def events_from(doc, text: str) -> list[dict]:
    out = []
    for sent in doc.sentence:
        legal = [t for t in sent.openieTriple if LEGAL_VERBS.search(t.relation)]
        if not legal:
            continue
        s, lo, hi = sentence_text(sent, text)
        d = parse_date(s)
        if d is None:
            continue
        filing = all(
            FILING_RE.search(t.relation) and not DECISION_RE.search(t.relation)
            for t in legal
        )
        e = {
            "id": f"e{len(out) + 1}",
            "what_happened_span": {"text": s, "start": lo, "end": hi},
            "authority_span": {"text": legal[0].subject},
            "openie_triples": [[t.subject, t.relation, t.object] for t in legal],
        }
        e["start_date_iso" if filing else "decision_date_iso"] = iso(d)
        out.append(e)
    return out


def chained(events: list[dict]) -> list[dict]:
    def key(ie):
        i, e = ie
        d = e.get("decision_date_iso") or e.get("start_date_iso")
        return (d.ljust(10, "9"), i)  # year-only sorts to the end of its year

    order = [e for _, e in sorted(enumerate(events), key=key)]
    out = [dict(e) for e in events]
    by = {e["id"]: e for e in out}
    for prev, cur in pairwise(order):
        by[cur["id"]]["follows"] = prev["id"]
    return out


def write(out_dir: Path, case: str, events: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{case}.compress.json").write_text(
        json.dumps({"payload": {"events": events}}, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--input-jsonl", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--port", type=int, default=9011)
    a = ap.parse_args()

    from stanza.server import CoreNLPClient

    os.environ.setdefault("CORENLP_HOME", str(Path.home() / "stanza_corenlp"))
    records = [
        json.loads(line)
        for line in a.input_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    with CoreNLPClient(
        annotators=[
            "tokenize",
            "ssplit",
            "pos",
            "lemma",
            "depparse",
            "natlog",
            "openie",
        ],
        properties={"openie.resolve_coref": False, "openie.triple.strict": True},
        endpoint=f"http://localhost:{a.port}",
        memory="6G",
        timeout=600000,
        be_quiet=True,
    ) as client:
        for r in records:
            doc = client.annotate(r["text"])
            events = events_from(doc, r["text"])
            write(a.out_dir / "s1_none", r["case_id"], events)
            write(a.out_dir / "s1_chain", r["case_id"], chained(events))
            n = sum(len(s.openieTriple) for s in doc.sentence)
            print(
                f"{r['case_id']}: {len(doc.sentence)} sentences, {n} triples, {len(events)} events"
            )


if __name__ == "__main__":
    main()
