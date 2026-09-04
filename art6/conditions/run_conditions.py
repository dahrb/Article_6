"""
run_conditions.py
-----------------
Extraction driver for the no-ontology condition of the JURIX study.

  O1 - schema-light: the same task and the same six target fields as O2,
       requested as flat JSON. No ontology, no closed vocabularies, no shapes.
       This is the honest baseline -- what a competent practitioner does
       without an ontology -- and the O2-vs-O1 contrast is the headline
       comparison, because it holds the task fixed and varies only
       formalisation.
WHY THIS IS NOT run_arms.sh

`run_arms.sh` drives the O2 pipeline: OntoCast fan-out or carry-forward,
ontology context assembly, IRI minting, a Fuseki project per arm, SHACL, and a
staged repair pass. O1 uses none of it. It is one LLM call per document with no
graph, no triple store and no chunking -- the study's run table puts every
primary run at whole-document, so there is nothing to assemble. Routing it
through the arm driver would mean threading a condition axis through several
hundred lines that then get skipped.

Keeping the condition prompt in art6/conditions/prompts/ is also a publication
requirement, not tidiness: docs/jurix_plan.md names publishing the O1 prompt
verbatim as the single most effective answer to the strawman objection, which
is the biggest threat to the contribution.

WHAT THIS DELIBERATELY DOES NOT DO

It does not validate, score or repair. O1's output is checked for parseability
and NOTHING else. Where it returns something unparseable, that is recorded as a
result and carried forward rather than hand-fixed.

Usage:
  uv run python -m art6.conditions.run_conditions \\
      --condition o1 \\
      --out-dir results/jurix/o1_gemma \\
      --model gemma-4-31b --base-url http://localhost:8000/v1
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import time
from pathlib import Path

from openai import LengthFinishReasonError, OpenAI

from art6.conditions.schema import NormalisedDocument
from art6.ontology.compress import call_with_recovery
from art6.ontology.repair_facts import model_call_kwargs
from art6.paths import REPO_ROOT, relative

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
DEFAULT_INPUT = REPO_ROOT / "data" / "art6_domestic_test_set.json"

CONDITIONS = {
    "o1": ("o1_schema_light.txt", "o1"),
}


def load_condition_prompt(condition: str) -> str:
    filename, _ = CONDITIONS[condition]
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8").strip()


def load_documents(input_path: Path, limit: int | None) -> list[dict]:
    """The test-set documents, as {case_id, text}, 1-based line numbers kept.

    Accepts the .json list or a .jsonl, and stamps each record with the
    1-based index it sat at. That index becomes the `L<N>` in the output
    filename, which is what lets a normalised O1 record be lined up against the
    O2 graph for the same document -- the whole study is within-document, so
    every condition must agree on which document is which.
    """
    if input_path.suffix == ".jsonl":
        records = [
            json.loads(line)
            for line in input_path.read_text().splitlines()
            if line.strip()
        ]
    else:
        records = json.loads(input_path.read_text())
    documents = []
    for index, record in enumerate(records, start=1):
        text = record.get("text") or record.get("content") or ""
        if not text:
            raise SystemExit(f"record {index} in {relative(input_path)} has no text")
        documents.append(
            {
                "line": index,
                "case_id": record.get("case_id", f"doc{index}"),
                "text": text,
            }
        )
    return documents[:limit] if limit else documents


def extract_one(
    client: OpenAI,
    model: str,
    document: dict,
    *,
    condition: str,
    temperature: float,
    max_tokens: int,
    token_param: str,
) -> tuple[str, dict]:
    """One condition prompt against one document. Returns (raw_output, timing).

    O1 is decoded under a JSON schema.

    Its schema is `NormalisedDocument` itself, which is not a shortcut: the
    condition is DEFINED as being asked for the study's target fields, so the
    shape it is asked for and the comparison form coincide by construction.
    Keeping the two in one place also stops them drifting: a field added to the
    prompt but not to the schema can never be emitted, because the decoding
    grammar forbids it.
    Forcing it removes a failure mode that is a property of the harness rather
    than of the condition -- a model that knows the answer and wraps it in prose
    is not evidence about schema-light extraction -- and it takes the JSON parse
    failure rate out of the results, where it would otherwise be reported as a
    finding about O1 rather than about output formatting.
    """
    started = time.perf_counter()
    # A cap is occasionally too small for a document that is short on input but
    # long on procedural history -- e.g. a single, ordinary-length judgment
    # (001-59859, 4.7k chars) still ran the structured JSON output past 16000
    # tokens on 2026-08-31. `.parse()` raises LengthFinishReasonError rather
    # than returning a truncated completion, so there is nothing to retry
    # against except a bigger cap. One doubling mirrors compress.py's own
    # truncation retry rather than looping indefinitely.
    for cap in (max_tokens, max_tokens * 2):
        # Sampling kwargs come from one place for every caller in the repository,
        # so that a hosted model is a --model change and nothing else: the
        # gpt-5/o-series families need `max_completion_tokens` and refuse an
        # explicit temperature. An explicit --token-param still wins, for an
        # endpoint that disagrees.
        sampling = model_call_kwargs(model, temperature=temperature, max_tokens=cap)
        if token_param != "auto":
            sampling.pop("max_tokens", None)
            sampling.pop("max_completion_tokens", None)
            sampling[token_param] = cap
        request = {
            "model": model,
            "messages": [
                {"role": "system", "content": load_condition_prompt(condition)},
                {"role": "user", "content": document["text"]},
            ],
            **sampling,
        }
        try:
            completion = call_with_recovery(
                client,
                label="o1",
                method="parse",
                response_format=NormalisedDocument,
                **request,
            )
            break
        except LengthFinishReasonError:
            if cap == max_tokens * 2:
                raise
    seconds = time.perf_counter() - started
    choice = completion.choices[0]
    usage = completion.usage
    return (choice.message.content or ""), {
        "line": document["line"],
        "case_id": document["case_id"],
        "seconds": round(seconds, 1),
        "finish_reason": choice.finish_reason,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "truncated": choice.finish_reason == "length",
    }


def parse_o1(raw: str) -> tuple[list | None, str | None]:
    """O1's proceedings list, or (None, reason).

    Kept as a guard rather than a parser now that O1 decodes under a schema:
    the grammar makes malformed output impossible, so anything this rejects is
    a harness fault -- a truncated generation, an empty completion -- and not a
    property of the condition. It stays because a silent empty list and a
    generation that died halfway must not read the same in the results.
    """
    text = raw.strip()
    if not text:
        return None, "empty response"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg} at line {exc.lineno}"
    if isinstance(parsed, dict):
        if isinstance(parsed.get("proceedings"), list):
            return parsed["proceedings"], None
        return None, "object without a 'proceedings' list"
    if not isinstance(parsed, list):
        return None, f"expected a JSON array, got {type(parsed).__name__}"
    return parsed, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[3])
    parser.add_argument("--condition", required=True, choices=sorted(CONDITIONS))
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--input-json", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--model", default="gemma-4-31b")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--temperature", type=float, default=0.4)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=16000,
        help=(
            "Completion cap. Higher than the O2 repair pass's because a whole "
            "chain of proceedings in one response is legitimately long, and "
            "truncating the baseline mid-chain would understate its recall -- "
            "the one measure the study most needs to be fair about."
        ),
    )
    parser.add_argument(
        "--token-param",
        default="auto",
        choices=["auto", "max_tokens", "max_completion_tokens"],
        help="Output-cap spelling. 'auto' picks it from the model name "
        "(gpt-5 and o-series reasoning models require max_completion_tokens).",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-run a document whose .o1.json already exists.",
    )
    args = parser.parse_args(argv)

    documents = load_documents(args.input_json, args.limit)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    _, suffix = CONDITIONS[args.condition]

    # API KEY FROM THE ENVIRONMENT BY DEFAULT, never from argv. A key passed as
    # --api-key is visible in `ps` output to every user on the machine for the
    # whole run; this project leaked a live OpenAI key that way on 2026-08-26.
    # `token-abc123` remains the fallback because a local vLLM ignores it.
    client = OpenAI(
        api_key=args.api_key or os.environ.get("OPENAI_API_KEY") or "token-abc123",
        base_url=args.base_url,
        timeout=args.timeout,
        max_retries=1,
    )

    print(f"condition {args.condition.upper()}: {len(documents)} document(s)")
    print(f"  model:  {args.model} @ {args.base_url or 'api.openai.com'}")
    print(f"  output: {relative(args.out_dir)}")

    started_at = datetime.datetime.now(datetime.UTC)
    run_start = time.perf_counter()
    timings: list[dict] = []
    failures = 0
    resumed = 0

    for document in documents:
        stem = f"input.L{document['line']}"
        # A document is "done" when its .o1.json report exists. Re-running the
        # same command therefore picks up where a dead endpoint left off,
        # rather than redoing all 250 documents -- the C0 baseline was the one
        # stage in this driver with no resume at all, measured 2026-08-31 on a
        # 250-document run that died mid-stage-1 with 239/250 C0 outputs
        # already on disk and no way to skip them on retry.
        if not args.overwrite and (args.out_dir / f"{stem}.{suffix}.json").exists():
            resumed += 1
            continue
        try:
            raw, timing = extract_one(
                client,
                args.model,
                document,
                condition=args.condition,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                token_param=args.token_param,
            )
        except Exception as exc:  # noqa: BLE001 - one document must not end the run
            failures += 1
            print(f"  FAILED {stem}: {type(exc).__name__}: {str(exc)[:200]}")
            timings.append(
                {
                    "line": document["line"],
                    "case_id": document["case_id"],
                    "error": type(exc).__name__,
                }
            )
            continue

        (args.out_dir / f"{stem}.{suffix}.txt").write_text(raw, encoding="utf-8")
        if args.condition == "o1":
            parsed, note = parse_o1(raw)
            timing["parsed"] = parsed is not None
            timing["parse_note"] = note
            if parsed is None:
                print(f"  {stem}: PARSE FAILED - {note}")
            else:
                (args.out_dir / f"{stem}.{suffix}.json").write_text(
                    json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                timing["proceedings"] = len(parsed)
        marker = "" if timing.get("parsed", True) else "  [unparseable]"
        if timing["truncated"]:
            marker += "  [TRUNCATED]"
        print(
            f"  {stem}: {timing['seconds']}s, "
            f"{timing.get('completion_tokens', '?')} completion tokens"
            f"{marker}"
        )
        timings.append(timing)

    run_seconds = time.perf_counter() - run_start
    parsed_ok = sum(1 for t in timings if t.get("parsed"))
    report = {
        "condition": args.condition,
        "model": args.model,
        "base_url": args.base_url,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "input": str(relative(args.input_json)),
        "documents": len(documents),
        "resumed": resumed,
        "failures": failures,
        "started_at": started_at.isoformat(),
        "seconds_total": round(run_seconds, 1),
        "prompt_sha_source": str(relative(PROMPTS_DIR / CONDITIONS[args.condition][0])),
        "prompt_verbatim": load_condition_prompt(args.condition),
        "documents_detail": timings,
    }
    if args.condition == "o1":
        report["parsed_ok"] = parsed_ok
        report["parse_failure_rate"] = round(1 - parsed_ok / max(len(documents), 1), 3)
    (args.out_dir / "run_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if resumed:
        print(f"\nresumed: {resumed} document(s) already present, skipped")
    print(f"\n{args.condition.upper()} complete in {run_seconds:.1f}s")
    if args.condition == "o1":
        print(f"  parsed cleanly: {parsed_ok}/{len(documents)}")
    if failures:
        print(f"  {failures} document(s) failed outright")
    truncated = [t for t in timings if t.get("truncated")]
    if truncated:
        print(f"  {len(truncated)} document(s) hit the {args.max_tokens}-token cap")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
