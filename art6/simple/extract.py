"""Single-call extractor for the simplified model.

    source scripts/env.sh   # OpenRouter key and BASE_URL (never printed)
    uv run python -m art6.simple.extract --input-jsonl SRC --out-dir OUT \\
        --model google/gemma-4-31b-it --extra-body "${STAGE1_BODY[gemma]}"

One model call per document with prompts/extract_simple.txt, temperature 0.
The reply is parsed, every quote is located in the source by string search
(normalised whitespace and quotes), and the result is written as
OUT/<case>.json in the simplified format, plus OUT/<case>.raw.txt. Events whose
quote cannot be found keep `quote_found: false` (they are still scored: the
scorer's quote similarity decides). A set with no dated act is dropped.
Documents already written are skipped; --reparse rebuilds them from the raw replies.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
from pathlib import Path

from openai import OpenAI

from art6.ontology.extraction import parse_json
from art6.simple.model import DIRECTIONS, KINDS, LEVELS, OUTCOMES

PROMPT = Path(__file__).with_name("prompts") / "extract_simple.txt"
_ISO = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def found(quote: str | None, text_norm: str) -> bool:
    return bool(quote) and _norm(quote) in text_norm


def _pick(v, allowed, default):
    v = str(v or "").strip().lower().replace(" ", "_").replace("-", "_")
    return v if v in allowed else default


def _date(v) -> str | None:
    v = str(v or "").strip()
    return v if _ISO.match(v) else None


def to_doc(case: str, reply: dict, text: str) -> dict:
    tn = _norm(text)
    sets, events = [], []
    for i, s in enumerate(reply.get("sets") or [], 1):
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or f"P{i}")
        sets.append(
            {
                "id": sid,
                "kind": _pick(s.get("kind"), KINDS, "other"),
                "label": str(s.get("label") or ""),
                "pending": bool(s.get("pending")),
            }
        )
        st = s.get("start")
        if isinstance(st, dict) and _date(st.get("date")):
            events.append(
                {
                    "id": f"{sid}.s",
                    "set": sid,
                    "role": "start",
                    "date": _date(st["date"]),
                    "body": st.get("body"),
                    "quote": st.get("quote"),
                    "quote_found": found(st.get("quote"), tn),
                }
            )
    known = {s["id"] for s in sets}
    for i, d in enumerate(reply.get("decisions") or [], 1):
        if not isinstance(d, dict) or not _date(d.get("date")):
            continue
        sid = str(d.get("set") or "")
        if sid not in known:
            sets.append(
                {"id": sid or "P?", "kind": "other", "label": "", "pending": False}
            )
            known.add(sets[-1]["id"])
            sid = sets[-1]["id"]
        events.append(
            {
                "id": f"d{i}",
                "set": sid,
                "role": "decision",
                "date": _date(d["date"]),
                "body": d.get("body"),
                "quote": d.get("quote"),
                "level": _pick(d.get("level"), LEVELS, "other"),
                "outcome": _pick(d.get("outcome"), OUTCOMES, "merits"),
                "direction": _pick(d.get("direction"), DIRECTIONS, "unknown"),
                "quote_found": found(d.get("quote"), tn),
            }
        )
    # a set with no dated act cannot be placed on a timeline: drop it
    used = {e["set"] for e in events}
    return {
        "case_id": case,
        "sets": [s for s in sets if s["id"] in used],
        "events": events,
    }


def reparse(src: Path, out_dir: Path) -> None:
    """Rebuild OUT/<case>.json from the saved raw replies, no model call."""
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        raw = out_dir / f"{rec['case_id']}.raw.txt"
        reply = parse_json(raw.read_text(encoding="utf-8")) if raw.exists() else None
        if isinstance(reply, dict):
            doc = to_doc(rec["case_id"], reply, rec["text"])
            (out_dir / f"{rec['case_id']}.json").write_text(
                json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8"
            )


def run_one(client, model, extra, rec, out_dir: Path, max_tokens: int) -> str:
    out = out_dir / f"{rec['case_id']}.json"
    if out.exists():
        return f"{rec['case_id']}: skipped (exists)"
    prompt = PROMPT.read_text(encoding="utf-8").replace("{text}", rec["text"])
    reply, raw = None, ""
    for _ in range(3):
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=max_tokens,
            seed=42,
            extra_body=extra,
        )
        raw = r.choices[0].message.content or ""
        reply = parse_json(raw)
        if isinstance(reply, dict):
            break
    (out_dir / f"{rec['case_id']}.raw.txt").write_text(raw, encoding="utf-8")
    if not isinstance(reply, dict):
        return f"{rec['case_id']}: FAILED to parse"
    doc = to_doc(rec["case_id"], reply, rec["text"])
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    n_found = sum(e["quote_found"] for e in doc["events"])
    return f"{rec['case_id']}: {len(doc['sets'])} sets, {len(doc['events'])} events, quotes found {n_found}/{len(doc['events'])}"


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--input-jsonl", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--model", required=True)
    ap.add_argument("--extra-body", default="{}")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument(
        "--reparse",
        action="store_true",
        help="rebuild outputs from saved raw replies; no model call",
    )
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)
    if a.reparse:
        reparse(a.input_jsonl, a.out_dir)
        return
    client = OpenAI(
        base_url=os.environ["BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
        timeout=600,
    )
    extra = json.loads(a.extra_body)
    recs = [
        json.loads(line)
        for line in a.input_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    with concurrent.futures.ThreadPoolExecutor(a.workers) as pool:
        for msg in pool.map(
            lambda r: run_one(client, a.model, extra, r, a.out_dir, a.max_tokens), recs
        ):
            print(msg, flush=True)


if __name__ == "__main__":
    main()
