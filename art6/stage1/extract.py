"""Single-call stage 1 for the set model (annotation_guide.txt v3.1).

    source scripts/env.sh   # OpenRouter key and BASE_URL (never printed)
    uv run python -m art6.stage1.extract --input-jsonl SRC --out-dir OUT \\
        --model google/gemma-4-31b-it --extra-body "${STAGE1_BODY[gemma]}"

One model call per document with prompts/extract.txt, temperature 0.
The reply is parsed into the flat set-model form (art6/model.py),
closed-list values are checked (an unknown value becomes the list's escape
value), every quote is located in the source by string search, and the result
is written as OUT/<case>.json plus the raw reply OUT/<case>.raw.txt.
A set with no dated act is dropped; `reviews` and `enforces` pointing at a
missing decision, or `reviews` crossing sets, are cleared.
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

from art6.model import (
    APPEALED_BY,
    AUTHORITY_KINDS,
    COSTS,
    DIRECTIONS,
    KINDS,
    LEVELS,
    MATTERS,
    OUTCOMES,
    SIDES,
)

PROMPT = Path(__file__).with_name("prompts") / "extract.txt"
_ISO = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json(raw: str) -> dict | None:
    for candidate in (raw, *(m.group(1) for m in _FENCE.finditer(raw))):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        start = candidate.find("{")
        if start >= 0:
            try:
                return json.loads(candidate[start:])
            except json.JSONDecodeError:
                depth, in_string, escape = 0, False, False
                for position, char in enumerate(candidate[start:], start):
                    if escape:
                        escape = False
                        continue
                    if char == "\\":
                        escape = True
                        continue
                    if char == '"':
                        in_string = not in_string
                    elif not in_string:
                        depth += 1 if char == "{" else -1 if char == "}" else 0
                        if depth == 0:
                            try:
                                return json.loads(candidate[start : position + 1])
                            except json.JSONDecodeError:
                                break
    return None


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def found(quote: str | None, text_norm: str) -> bool:
    return bool(quote) and _norm(str(quote)) in text_norm


def pick(v, allowed, default):
    key = re.sub(r"[^a-z]", "", str(v or "").lower())
    for a in allowed:
        if key == a.lower():
            return a
    return default


def iso(v) -> str | None:
    v = str(v or "").strip()
    return v if _ISO.match(v) else None


def _text(v) -> str | None:
    return str(v).strip() if isinstance(v, (str, int)) and str(v).strip() else None


def _parties(v) -> list[dict]:
    out = []
    for p in v if isinstance(v, list) else []:
        if isinstance(p, dict) and _text(p.get("name")):
            out.append(
                {
                    "name": _text(p["name"]),
                    "side": pick(p.get("side"), SIDES, "Unknown"),
                }
            )
    return out


def _matter(v) -> list[str]:
    vals = v if isinstance(v, list) else [v] if v else []
    out = []
    for m in vals:
        x = pick(m, MATTERS, None)
        if x and x not in out:
            out.append(x)
    return out


def _event_common(e: dict, tn: str) -> dict:
    return {
        "body": _text(e.get("body")),
        "authority_kind": pick(e.get("authority_kind"), AUTHORITY_KINDS, "Unknown"),
        "parties": _parties(e.get("parties")),
        "matter": _matter(e.get("matter")),
        "quote": _text(e.get("quote")),
        "quote_found": found(e.get("quote"), tn),
    }


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
                "kind": pick(s.get("kind"), KINDS, "Unknown"),
                "label": _text(s.get("label")) or "",
                "pending": s.get("pending") is True,
                "enforces": _text(s.get("enforces")),
            }
        )
        st = s.get("start")
        if isinstance(st, dict) and iso(st.get("date")):
            events.append(
                {
                    "id": f"{sid}.s",
                    "set": sid,
                    "role": "start",
                    "date": iso(st["date"]),
                    **_event_common(st, tn),
                }
            )
    known = {s["id"] for s in sets}
    for i, d in enumerate(reply.get("decisions") or [], 1):
        if not isinstance(d, dict) or not iso(d.get("date")):
            continue
        sid = str(d.get("set") or "")
        if sid not in known:
            sid = sid or "P?"
            sets.append(
                {
                    "id": sid,
                    "kind": "Unknown",
                    "label": "",
                    "pending": False,
                    "enforces": None,
                }
            )
            known.add(sid)
        events.append(
            {
                "id": str(d.get("id") or f"D{i}"),
                "set": sid,
                "role": "decision",
                "date": iso(d["date"]),
                **_event_common(d, tn),
                "level": pick(d.get("level"), LEVELS, "Unknown"),
                "outcome": pick(d.get("outcome"), OUTCOMES, "Unknown"),
                "direction": pick(d.get("direction"), DIRECTIONS, "Unknown"),
                "reviews": _text(d.get("reviews")),
                "appealed_by": pick(d.get("appealed_by"), APPEALED_BY, None),
                "appeal_date": iso(d.get("appeal_date")),
                "communication_date": iso(d.get("communication_date")),
                "costs": pick(d.get("costs"), COSTS, None),
            }
        )
    # ids must be unique: a model that repeats "D3" keeps the first
    seen, uniq = set(), []
    for e in events:
        if e["id"] not in seen:
            seen.add(e["id"])
            uniq.append(e)
    events = uniq
    set_of = {e["id"]: e["set"] for e in events if e["role"] == "decision"}
    for e in events:
        if e["role"] != "decision":
            continue
        if set_of.get(e.get("reviews")) != e["set"] or e.get("reviews") == e["id"]:
            e["reviews"] = None
        if not e["reviews"]:
            e["appealed_by"] = e["appeal_date"] = None
        elif e["appealed_by"] is None:
            e["appealed_by"] = "Unknown"
    used = {e["set"] for e in events}
    sets = [
        s for s in sets if s["id"] in used
    ]  # a set with no dated act cannot be placed on a timeline
    for s in sets:
        if s["enforces"] not in set_of:
            s["enforces"] = None
    doc = {"case_id": case, "sets": sets, "events": events}
    case_info = reply.get("case") if isinstance(reply.get("case"), dict) else {}
    doc["case"] = {
        k: _text(case_info.get(k))
        for k in (
            "case_name",
            "application_number",
            "respondent_state",
            "judgment_date",
        )
    }
    doc["applicants"] = [
        {
            k: _text(a.get(k))
            for k in (
                "name",
                "birth_year",
                "nationality",
                "residence",
                "gender",
                "quote",
            )
        }
        for a in reply.get("applicants") or []
        if isinstance(a, dict) and _text(a.get("name"))
    ]
    doc["persons"] = [
        {"name": _text(p.get("name")), "role": _text(p.get("role"))}
        for p in reply.get("persons") or []
        if isinstance(p, dict) and _text(p.get("name"))
    ]
    return doc


def _write(out_dir: Path, case: str, reply: dict, text: str) -> dict:
    doc = to_doc(case, reply, text)
    (out_dir / f"{case}.json").write_text(
        json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return doc


def reparse(src: Path, out_dir: Path) -> None:
    """Rebuild OUT/<case>.json from the saved raw replies, no model call."""
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        raw = out_dir / f"{rec['case_id']}.raw.txt"
        reply = parse_json(raw.read_text(encoding="utf-8")) if raw.exists() else None
        if isinstance(reply, dict):
            _write(out_dir, rec["case_id"], reply, rec["text"])


def run_one(client, model, extra, rec, out_dir: Path, max_tokens: int) -> str:
    if (out_dir / f"{rec['case_id']}.json").exists():
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
    doc = _write(out_dir, rec["case_id"], reply, rec["text"])
    n_found = sum(e["quote_found"] for e in doc["events"])
    return (
        f"{rec['case_id']}: {len(doc['sets'])} sets, {len(doc['events'])} events, "
        f"quotes found {n_found}/{len(doc['events'])}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--input-jsonl", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--model", required=True)
    ap.add_argument("--extra-body", default="{}")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--max-tokens", type=int, default=24000)
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
