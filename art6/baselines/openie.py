"""Off-the-shelf, non-LLM baseline: Stanford OpenIE triples -> procedural events.

    uv run --with stanza==1.10.1 python -m art6.baselines.openie \\
        --input-jsonl results/best_vote4/source.jsonl --out-dir results/openie_test10

Needs Java and CoreNLP (`stanza.install_corenlp(dir=~/stanza_corenlp)`, or set
CORENLP_HOME). Nothing here calls an LLM.

OpenIE produces (subject, relation, object) triples per sentence and has no
notion of a court event, a date or a chain. The assembly step below turns
triples into events by a fixed rule and writes set-model documents, so the
baseline is scored by exactly the same scorer as the pipeline
(art6/evaluation/score.py):

  1  CoreNLP splits the Facts into sentences and extracts OpenIE triples.
  2  A sentence yields ONE event when it has (a) a triple whose relation
     contains a legal-act verb (LEGAL_VERBS) and (b) a date (parse_date: the
     first day-month-year, else month-year, else year).
  3  The event is a START act when its legal verbs are all filing verbs,
     otherwise a DECISION.
  4  authority = the subject of the first legal triple; quote = the sentence
     (OpenIE's provenance unit).

Two arms are written:
  none   every event its own set: OpenIE itself asserts no order
  chain  every event in one set, so the derived order is a naive timeline
         (date order, text order breaking ties) -- the obvious heuristic
         anyone would add on top

What it cannot produce -- level, outcome, direction, matter, separate sets of
proceedings -- is reported as not applicable, not scored as zero.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

MONTHS = {
    m: i
    for i, m in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        1,
    )
}
_MONTH_RE = "|".join(MONTHS)
_DAY_MONTH_YEAR = re.compile(
    rf"\b(\d{{1,2}})\s+({_MONTH_RE})\s+(\d{{4}})\b", re.IGNORECASE
)
_MONTH_YEAR = re.compile(rf"\b({_MONTH_RE})\s+(\d{{4}})\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


def parse_date(text: str) -> tuple[int, int, int] | None:
    """(year, month, day) with 0 for an unstated part, or None.

    Only the forms English ECHR judgments use. Anything else is left
    unparsed, which every caller treats as "order unknown", never as a guess.
    """
    if not text:
        return None
    if m := _DAY_MONTH_YEAR.search(text):
        return int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1))
    if m := _MONTH_YEAR.search(text):
        return int(m.group(2)), MONTHS[m.group(1).lower()], 0
    if m := _YEAR.search(text):
        return int(m.group(1)), 0, 0
    return None


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


def as_doc(case: str, events: list[dict], one_set: bool) -> dict:
    """The events as a flat set-model document (art6/model.py)."""
    sets, out = [], []
    for i, e in enumerate(events, 1):
        sid = "S1" if one_set else f"S{i}"
        if not one_set or i == 1:
            sets.append(
                {
                    "id": sid,
                    "kind": "Unknown",
                    "label": "",
                    "pending": False,
                    "enforces": None,
                }
            )
        start = "start_date_iso" in e
        ev = {
            "id": f"{sid}.s" if start and not one_set else f"E{i}",
            "set": sid,
            "role": "start" if start else "decision",
            "date": e.get("start_date_iso") or e.get("decision_date_iso"),
            "body": e["authority_span"]["text"],
            "authority_kind": "Unknown",
            "parties": [],
            "matter": [],
            "quote": e["what_happened_span"]["text"],
        }
        if not start:
            ev.update(
                level="Unknown", outcome="Unknown", direction="Unknown", reviews=None
            )
        out.append(ev)
    if one_set:  # a set has at most one start act: the earliest; later ones are dropped
        starts = sorted(
            (e for e in out if e["role"] == "start"),
            key=lambda e: e["date"].ljust(10, "9"),
        )
        out = [
            e
            for e in out
            if e["role"] == "decision" or e is (starts[0] if starts else None)
        ]
    return {"case_id": case, "sets": sets, "events": out}


def write(out_dir: Path, doc: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{doc['case_id']}.json").write_text(
        json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8"
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
            write(a.out_dir / "none", as_doc(r["case_id"], events, one_set=False))
            write(a.out_dir / "chain", as_doc(r["case_id"], events, one_set=True))
            n = sum(len(s.openieTriple) for s in doc.sentence)
            print(
                f"{r['case_id']}: {len(doc.sentence)} sentences, {n} triples, {len(events)} events"
            )


if __name__ == "__main__":
    main()
