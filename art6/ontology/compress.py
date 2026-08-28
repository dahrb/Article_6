"""Stage 1: evidence selection.

Reads a judgment, returns verbatim spans grouped into event bundles, with
character offsets attached DETERMINISTICALLY here rather than asked of the model.

Asking an LLM for character offsets does not work -- they cannot count -- so the
model emits text only and this module locates each span in the source. That does
two jobs at once: it produces exact offsets, and it PROVES the span is verbatim.
A span that cannot be located is not silently trusted; it is dropped and counted.

The result is a stage-1 output that is verified by construction: every surviving
value is a substring of the source at a known offset. Stage 2 never sees the raw
judgment, so it cannot introduce content that did not pass through here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import unicodedata
from pathlib import Path

from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT = Path(__file__).resolve().parent / "prompts" / "compress.txt"
APPLICANTS_PROMPT = Path(__file__).resolve().parent / "prompts" / "applicants.txt"

# How much of the document the applicants call reads. The description formula
# ("is a Ukrainian national who was born in 1979 and lives in Kiev") sits in the
# opening of the facts: across the pilot corpus its latest occurrence starts at
# character 6,202, so 8,000 covers every case with margin at about a fifth of the
# tokens of a full-document call.
APPLICANTS_HEAD_CHARS = 8000

# Fields whose values must be verbatim source text. Everything else (ids, order,
# follows) is structural and is left alone.
SPAN_SUFFIX = "_span"


def _norm(text: str) -> str:
    """Fold the differences that make a true quote look false.

    Extraction and the source disagree on curly vs straight quotes, non-breaking
    spaces and runs of whitespace far more often than they disagree on words.
    Folding those is what separates 'the model paraphrased' from 'the PDF had a
    different apostrophe'.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


class SpanIndex:
    """Locates a span in the source under the same folding, and maps the hit back
    to offsets in the ORIGINAL text.

    The folded text is searched, but offsets must refer to the original, so a
    position map is built alongside it.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        folded: list[str] = []
        self.positions: list[int] = []
        previous_space = False
        for index, char in enumerate(unicodedata.normalize("NFKC", source)):
            char = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"}.get(
                char, char
            )
            if char.isspace():
                if previous_space or not folded:
                    continue
                folded.append(" ")
                self.positions.append(index)
                previous_space = True
            else:
                folded.append(char)
                self.positions.append(index)
                previous_space = False
        while folded and folded[-1] == " ":
            folded.pop()
            self.positions.pop()
        self.folded = "".join(folded)
        self.lowered = self.folded.lower()

    def locate(self, span: str) -> tuple[int, int] | None:
        needle = _norm(span)
        if not needle:
            return None
        at = self.folded.find(needle)
        if at < 0:
            at = self.lowered.find(needle.lower())
        if at < 0:
            return None
        end = at + len(needle) - 1
        return self.positions[at], self.positions[end] + 1


def verify(payload: dict, source: str) -> tuple[dict, dict]:
    """Walk the payload, locate every *_span, attach offsets, drop what fails.

    Returns the verified payload and a tally. Dropping rather than keeping is
    deliberate: an unlocatable span is exactly the fabricated-anchor failure this
    pipeline exists to prevent, and it must not reach stage 2.
    """
    index = SpanIndex(source)
    stats = {"spans": 0, "located": 0, "dropped": 0, "dropped_values": []}

    def walk(node):
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                # LISTS OF SPANS ("*_spans") are verified too. Checking only the
                # singular "*_span" keys left interim_spans -- free-text lists of
                # procedural acts -- entirely unchecked, which would have let
                # unverified text through to stage 2 and silently voided the
                # whole verified-by-construction guarantee.
                if key.endswith(SPAN_SUFFIX + "s") and isinstance(value, list):
                    kept = []
                    for item in value:
                        if not isinstance(item, str):
                            continue
                        stats["spans"] += 1
                        hit = index.locate(item)
                        if hit is None:
                            stats["dropped"] += 1
                            stats["dropped_values"].append(item[:80])
                            continue
                        stats["located"] += 1
                        kept.append({"text": item, "start": hit[0], "end": hit[1]})
                    if kept:
                        out[key] = kept
                elif key.endswith(SPAN_SUFFIX) and isinstance(value, str):
                    stats["spans"] += 1
                    hit = index.locate(value)
                    if hit is None:
                        stats["dropped"] += 1
                        stats["dropped_values"].append(value[:80])
                        continue
                    stats["located"] += 1
                    out[key] = {"text": value, "start": hit[0], "end": hit[1]}
                else:
                    walked = walk(value)
                    if walked not in (None, [], {}):
                        out[key] = walked
            return out
        if isinstance(node, list):
            return [w for w in (walk(v) for v in node) if w not in (None, [], {})]
        return node

    return walk(payload), stats


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


def compress(
    client: OpenAI,
    model: str,
    text: str,
    *,
    temperature: float,
    max_tokens: int,
    retries: int = 2,
) -> tuple[dict | None, dict, str]:
    prompt = (
        PROMPT.read_text(encoding="utf-8") + "\n\nJUDGMENT\n<<<DOC\n" + text + "\nDOC\n"
    )
    last = ""
    for attempt in range(1, retries + 2):
        # The output-cap kwarg and the temperature a model will accept differ
        # by family: gpt-5 reasoning models reject `max_tokens` in favour of
        # `max_completion_tokens`, and reject any temperature but the default.
        # Reusing repair_facts._token_limit_kwargs keeps the one spelling rule
        # in one place rather than drifting between the two callers.
        from art6.ontology.repair_facts import _token_limit_kwargs

        kwargs = dict(_token_limit_kwargs(model, max_tokens))
        if not model.lower().lstrip().startswith(("gpt-5", "o1", "o3", "o4")):
            kwargs["temperature"] = temperature
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        last = response.choices[0].message.content or ""
        payload = parse_json(last)
        if payload is not None and payload.get("events"):
            verified, stats = verify(payload, text)
            stats["attempt"] = attempt
            return verified, stats, last
    return None, {"attempt": retries + 1, "error": "no usable JSON"}, last


def compress_applicants(
    client: OpenAI,
    model: str,
    text: str,
    *,
    temperature: float,
    max_tokens: int = 4000,
) -> tuple[list, str]:
    """The applicants' own description, asked for in a SEPARATE call.

    WHY THIS IS NOT PART OF THE MAIN PROMPT
    ---------------------------------------
    It was, and it cost events. Measured 2026-08-28 on gemma-4-31b at
    temperature 0, arms interleaved within each round so that vLLM batching
    could not align with the comparison:

        L3   without applicants 21/21/21 events   with 19/19/19
        L4   without applicants 13/13/13 events   with 10/10/10
        L1   unaffected

    The cause is not prompt length: a length-matched placebo of already-stated
    rules cost nothing (L3 21/21). It is not the extra output section either --
    a structurally identical dummy section asking for documents mentioned, which
    the model actually filled with 2-5 entries, cost nothing and if anything
    raised the count (L3 22/22, L4 14/15). Nor is it attention drawn to the
    document's opening: the guidance prose WITHOUT the schema fields cost
    nothing (L3 21/21/21). Only asking the model to emit these particular fields
    alongside the events did it, and no single mechanism I tested accounts for
    that. So this is a workaround for a measured effect, not a fix for an
    understood one.

    A second call makes the question moot. The main call runs the unchanged
    prompt and so extracts exactly what it did before, and the applicants come
    from a small, cheap call over the opening alone -- which is also more
    accurate: birth-year recall went from 5/9 available values in the merged
    prompt to 9/9 here, with no fabrications either way.
    """
    prompt = (
        APPLICANTS_PROMPT.read_text(encoding="utf-8")
        + "\n\nDOCUMENT OPENING\n<<<DOC\n"
        + text[:APPLICANTS_HEAD_CHARS]
        + "\nDOC\n"
    )
    from art6.ontology.repair_facts import _token_limit_kwargs

    kwargs = dict(_token_limit_kwargs(model, max_tokens))
    if not model.lower().lstrip().startswith(("gpt-5", "o1", "o3", "o4")):
        kwargs["temperature"] = temperature
    response = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}], **kwargs
    )
    raw = response.choices[0].message.content or ""
    payload = parse_json(raw)
    if not isinstance(payload, dict):
        return [], raw
    applicants = payload.get("applicants")
    return (applicants if isinstance(applicants, list) else []), raw


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-jsonl", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--model", default="gemma-4-31b")
    ap.add_argument("--base-url", default="http://localhost:8001/v1")
    ap.add_argument("--api-key", default=os.environ.get("VLLM_API_KEY", "EMPTY"))
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument(
        "--only", default="", help="comma-separated doc ids, e.g. input.L1,input.L6"
    )
    args = ap.parse_args()

    client = OpenAI(base_url=args.base_url, api_key=args.api_key, timeout=900.0)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    wanted = {w.strip() for w in args.only.split(",") if w.strip()}

    lines = [
        l
        for l in args.input_jsonl.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    print(f"stage 1: {args.model} @ {args.base_url} (temperature {args.temperature})")
    totals = {"spans": 0, "located": 0, "dropped": 0}
    for i, line in enumerate(lines, 1):
        doc_id = f"input.L{i}"
        if wanted and doc_id not in wanted:
            continue
        text = json.loads(line).get("text", "")
        t0 = time.perf_counter()
        payload, stats, raw = compress(
            client,
            args.model,
            text,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
        if payload is None:
            print(f"  {doc_id}: FAILED ({stats.get('error')})")
            (args.out_dir / f"{doc_id}.raw.txt").write_text(raw, encoding="utf-8")
            continue
        # The applicants call is separate (see compress_applicants) but its spans
        # are held to exactly the same standard: verified against the FULL source,
        # not the 8,000-character head it was asked about, so the offsets that
        # travel to stage 2 refer to the document as a whole like every other span.
        applicants, applicants_raw = compress_applicants(
            client, args.model, text, temperature=args.temperature
        )
        if applicants:
            verified_applicants, applicant_stats = verify(
                {"applicants": applicants}, text
            )
            for key in ("spans", "located", "dropped"):
                stats[key] = stats.get(key, 0) + applicant_stats[key]
            stats["dropped_values"] += applicant_stats["dropped_values"]
            payload["applicants"] = verified_applicants.get("applicants", [])
        elif applicants_raw:
            (args.out_dir / f"{doc_id}.applicants.raw.txt").write_text(
                applicants_raw, encoding="utf-8"
            )
        for k in totals:
            totals[k] += stats.get(k, 0)
        rate = stats["located"] / stats["spans"] * 100 if stats["spans"] else 0.0
        print(
            f"  {doc_id}: {len(payload.get('events', [])):3} events, "
            f"{len(payload.get('persons', [])):2} persons, "
            f"{len(payload.get('applicants', [])):2} applicants, "
            f"{stats['located']:4}/{stats['spans']:4} spans verbatim ({rate:5.1f}%), "
            f"{stats['dropped']:3} dropped [{time.perf_counter() - t0:.0f}s]"
        )
        (args.out_dir / f"{doc_id}.compress.json").write_text(
            json.dumps(
                {"doc": doc_id, "stats": stats, "payload": payload},
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    if totals["spans"]:
        print(
            f"\ntotal: {totals['located']}/{totals['spans']} spans verbatim "
            f"({totals['located'] / totals['spans'] * 100:.1f}%), {totals['dropped']} dropped"
        )


if __name__ == "__main__":
    main()
