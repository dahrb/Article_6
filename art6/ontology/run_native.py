"""
run_native.py
--------------
Run OntoCast's own extraction pipeline (parallel fan-out + aggregator, or the
no-chunking single-unit case -- both go through the same `ontocast process`
command, distinguished only by CHUNK_MIN_SIZE/MAX_SIZE) IN-PROCESS rather than
as a subprocess, so `response_repair.enable()` can be installed first.

WHY THIS EXISTS
---------------
`run_data.sh` shells out to `ontocast process` as a separate subprocess (`exec
... uv run ontocast process ...`), which means a monkeypatch applied from this
repo can never reach it -- the patch and the target process are different
Python interpreters. `carry_forward.py` doesn't have this problem because it
imports and calls ontocast's own agent functions directly, in the same
process, which is also exactly why `response_repair.enable()` could be wired
into it: rebinding `ontocast.agent.common.parse_json_markdown` only works
inside the process that made the binding.

`response_repair.py` fixes a real defect (malformed-JSON facts-render replies
silently dropping a whole chunk's worth of extracted text -- see that module's
docstring for the measured rate). Without a way to install the same patch
ahead of the native pipeline, any experiment comparing "native chunking" or
"nochunk" against "rolling forward" would be comparing a patched pipeline
against two unpatched ones -- not the chunking strategies themselves.

WHAT THIS DOES
--------------
`ontocast process` is a click command (`ontocast.cli.server.process`, exposed
as the `ontocast` console script via `ontocast.cli.server.cli`). This script
imports that click group directly, calls `response_repair.enable()` first,
then invokes the group in-process with `standalone_mode=False` so a
non-zero exit raises instead of calling `sys.exit` out from under this
wrapper. Every `ontocast process` CLI flag is passed straight through
unchanged -- this is not a reimplementation, just an earlier hook point.

Usage (from the ontocast checkout, exactly like run_data.sh's own `exec`,
with PYTHONPATH pointed back at this repo so `art6.ontology.response_repair`
is importable):

    cd /path/to/ontocast && env -u VIRTUAL_ENV \\
        PYTHONPATH=/path/to/article_6/domestic_proceedings \\
        uv run python -m art6.ontology.run_native \\
            --input-path input.jsonl --tenant growgraph --project art6_x \\
            --output-dir out/ --max-visits 1

ALSO WRITES a report.json next to --output-dir, matching the shape
carry_forward.py's own report already has (started/finished/total_seconds),
plus a tally of how many facts-render replies needed response_repair's
bracket fix and how many failed even after retries -- the same failure/
recovery signal carry_forward.py already surfaces, now available for the
native and nochunk arms too so all three chunking strategies are comparable
on this axis, not just rolling-forward.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import re
import sys
import time


def _fmt(seconds: float) -> str:
    return f"{seconds:,.1f}s" if seconds < 120 else f"{seconds / 60:,.1f}m"


# One content unit can time out or render malformed, and the pipeline
# aggregates the survivors and reports the record as DONE -- with an output
# file, a validation report and a run manifest, all of them clean.
#
# The 2026-08-21 evaluation is what this exists for. `gpt5mini_native_mv1`'s L1
# is 519 triples, fully quote-anchored, SHACL-conformant, and missing the
# entire Article 6 core of the case, because the one chunk carrying section D
# timed out and was dropped. Nothing in the pipeline noticed, and nothing in
# the output says so. Worse, in the same experiment two graphs that PASSED
# every shape were the best and the worst extraction in the run: shape
# conformance measures whether what you extracted is well-formed, never
# whether you extracted it, so it goes UP when the extractor fails hardest.
#
# These two signatures are the only place the loss is currently recorded, and
# only in a log nobody reads. Attribution is by position: ontocast processes
# records sequentially, so every loss line belongs to the next document whose
# run manifest is written. The manifest is the right boundary marker rather
# than the facts graph, because a document that loses ALL its units still gets
# a manifest but never gets a .facts.ttl -- which is exactly the case that
# most needs to be counted.
_UNIT_LOSS_RE = re.compile(
    r"Parallel facts map failed without usable output for (\d+)/(\d+) unit\(s\)"
)
_MANIFEST_RE = re.compile(r"Dumped run manifest to \S*?([^/\s]+)\.run\.json")


def units_lost_by_document(log_text: str) -> dict[str, dict[str, int]]:
    """Per-document unit loss, keyed by output stem (e.g. ``input.L4``)."""
    per_doc: dict[str, dict[str, int]] = {}
    pending_lost = pending_total = 0
    for line in log_text.splitlines():
        if match := _UNIT_LOSS_RE.search(line):
            pending_lost += int(match.group(1))
            # The denominator is the document's unit count, not a running sum:
            # a document can log this line more than once (native retries a
            # unit under MAX_VISITS>1) and each line reports the same total.
            pending_total = max(pending_total, int(match.group(2)))
            continue
        if match := _MANIFEST_RE.search(line):
            per_doc[match.group(1)] = {
                "units_total": pending_total,
                "units_lost": pending_lost,
            }
            pending_lost = pending_total = 0
    return per_doc


def _live_facts_prompt() -> str | None:
    """The current prompts/facts.txt, or None if it is not where we expect."""
    path = pathlib.Path(__file__).resolve().parent / "prompts" / "facts.txt"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _stale_prompt_records(input_path: str) -> list[str]:
    """Input records whose embedded facts prompt is not the live one.

    This check exists because its absence cost a 13-minute extraction run on
    2026-08-27 and, worse, produced a result that looked entirely valid. The
    facts prompt is embedded per record as `facts_user_instruction` when the
    JSONL is built; nothing reads prompts/facts.txt at extraction time. So a
    prompt edit followed by a re-run over an existing JSONL silently runs the
    OLD prompt. The measured symptom was a metric that stayed at exactly 0/47
    rather than moving partially -- which reads as "the model ignored the
    rule" and is in fact "the model was never shown the rule".

    Returns the case_ids that differ. Never raises: a check that cannot run
    must not be able to stop a run that would otherwise work.
    """
    live = _live_facts_prompt()
    if live is None:
        return []
    path = pathlib.Path(input_path)
    if not path.is_file() or path.suffix != ".jsonl":
        return []
    stale: list[str] = []
    try:
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            record = json.loads(line)
            embedded = record.get("facts_user_instruction")
            if embedded is not None and embedded.strip() != live:
                stale.append(str(record.get("case_id", f"line {i + 1}")))
    except (OSError, ValueError):
        return []
    return stale


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run `ontocast process` in-process with response_repair installed."
    )
    ap.add_argument("--input-path", required=True)
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--max-visits", type=int, default=None)
    ap.add_argument("--report", help="Write a run summary as JSON to this path.")
    ap.add_argument(
        "--no-response-repair",
        action="store_true",
        help="Skip the malformed-JSON fix, for A/B only -- see response_repair.py.",
    )
    ap.add_argument(
        "--no-turtle-repair",
        action="store_true",
        help="Skip the premature-period-before-property fix, for A/B only -- see turtle_repair.py.",
    )
    ap.add_argument(
        "--no-envelope-repair",
        action="store_true",
        help="Skip re-wrapping a bare JSON-LD facts response, for A/B only -- see envelope_repair.py.",
    )
    ap.add_argument(
        "--allow-unit-loss",
        action="store_true",
        help=(
            "Exit 0 even when documents lost content units. By default a run "
            "that dropped a unit exits 2, because the output of such a run "
            "looks clean at every other checkpoint -- see units_lost_by_document."
        ),
    )
    ap.add_argument(
        "--allow-stale-prompt",
        action="store_true",
        help=(
            "Run even when the input JSONL's embedded facts_user_instruction "
            "differs from the live prompts/facts.txt. Needed only to replay an "
            "old snapshot deliberately."
        ),
    )
    args, passthrough = ap.parse_known_args()

    stale = _stale_prompt_records(args.input_path)
    if stale and not args.allow_stale_prompt:
        live = len(_live_facts_prompt() or "")
        print(
            f"ABORT: {len(stale)} input record(s) carry a facts_user_instruction "
            f"that differs from the live art6/ontology/prompts/facts.txt "
            f"({live:,} chars).\n"
            "  The prompt is NOT read at extraction time -- it is baked into each\n"
            "  JSONL record when the input is built, so editing facts.txt and\n"
            "  reusing an existing JSONL runs the OLD prompt and produces a\n"
            "  perfectly clean-looking result that ignores the edit entirely.\n"
            "  Rebuild the input, or pass --allow-stale-prompt to replay the\n"
            "  embedded one on purpose.",
            file=sys.stderr,
        )
        return 2

    if not args.no_response_repair:
        from art6.ontology.response_repair import enable as enable_response_repair

        enable_response_repair()

    if not args.no_turtle_repair:
        from art6.ontology.turtle_repair import enable as enable_turtle_repair

        enable_turtle_repair()

    if not args.no_envelope_repair:
        from art6.ontology.envelope_repair import enable as enable_envelope_repair

        enable_envelope_repair()

    from ontocast.cli.server import cli

    argv = [
        "process",
        "--input-path",
        args.input_path,
        "--tenant",
        args.tenant,
        "--project",
        args.project,
        "--output-dir",
        args.output_dir,
    ]
    if args.max_visits is not None:
        argv += ["--max-visits", str(args.max_visits)]
    argv += passthrough

    # Capture ontocast's own logging so the failure/recovery tally below can
    # be computed from it, the same way carry_forward.py's driver.log already
    # lets us grep for these signatures -- and so a human re-reading this run
    # later has the same evidence trail without re-running anything.
    log_path = pathlib.Path(args.output_dir) / "extract.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    root_logger.setLevel(logging.INFO)

    started = datetime.datetime.now(datetime.UTC)
    t0 = time.perf_counter()
    t0_wall = time.time()
    exit_code = 0
    try:
        cli(argv, standalone_mode=False)
    except SystemExit as exc:
        exit_code = exc.code or 0
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("run_native").error("process failed: %s", exc)
        exit_code = 1
    finally:
        total_seconds = time.perf_counter() - t0
        finished = datetime.datetime.now(datetime.UTC)
        file_handler.flush()
        root_logger.removeHandler(file_handler)
        file_handler.close()

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    # Signatures matched to the two known failure classes and the recovery
    # response_repair reports on success -- see that module's docstring for
    # where these numbers come from.
    parse_failures = len(
        re.findall(r"Failed to parse LLM response after \d+ attempts", log_text)
    )
    repaired = len(re.findall(r"response repair recovered a malformed reply", log_text))
    turtle_repaired = len(re.findall(r"turtle repair: rewrote a premature", log_text))
    chunk_failures = len(re.findall(r"render failed at attempt", log_text))

    out_dir = pathlib.Path(args.output_dir)
    facts_files = sorted(out_dir.glob("*.facts.ttl"))

    # Per-document unit accounting. The log gives the loss; the per-document
    # run manifest gives the authoritative unit count (retrieval_metrics.
    # facts_anchor_units), which is the only source that is right for a
    # document that lost every unit and therefore never logged a denominator
    # worth trusting.
    loss_by_doc = units_lost_by_document(log_text)
    documents = []
    for manifest_path in sorted(out_dir.glob("*.run.json")):
        stem = manifest_path.name.removesuffix(".run.json")
        loss = loss_by_doc.get(stem, {"units_total": 0, "units_lost": 0})
        units_total = loss["units_total"]
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            units_total = (
                manifest.get("retrieval_metrics", {}).get("facts_anchor_units")
                or units_total
            )
        except (OSError, ValueError):
            manifest = {}
        units_lost = loss["units_lost"]
        documents.append(
            {
                "document": stem,
                "units_total": units_total,
                "units_lost": units_lost,
                "units_lost_fraction": (
                    round(units_lost / units_total, 3) if units_total else 0.0
                ),
                "facts_triples": manifest.get("facts_triples"),
                "output_written": (out_dir / f"{stem}.facts.ttl").exists(),
                # The whole point: a record is NOT done just because a file
                # was written. Anything that lost a unit is incomplete, and
                # anything that produced no file at all is a total loss.
                "complete": units_lost == 0
                and (out_dir / f"{stem}.facts.ttl").exists(),
            }
        )
    incomplete = [d for d in documents if not d["complete"]]
    # Best-effort per-document timing: ontocast's own CLI does not expose a
    # per-record hook the way carry_forward.py's own loop does, so this reads
    # each output file's own mtime relative to run start. That is coarse
    # (native fan-out can process units for the SAME document out of strict
    # order, and parallel documents interleave) but gives a real completion
    # timestamp per document, which a raw wall-clock total cannot.
    per_file = [
        {
            "output": f.name,
            "seconds_from_start": round(f.stat().st_mtime - t0_wall, 1),
        }
        for f in facts_files
    ]

    report = {
        "started": started.isoformat(timespec="seconds"),
        "finished": finished.isoformat(timespec="seconds"),
        "total_seconds": round(total_seconds, 1),
        "exit_code": exit_code,
        "response_repair_enabled": not args.no_response_repair,
        "response_repair_recoveries": repaired,
        "turtle_repair_enabled": not args.no_turtle_repair,
        "turtle_repair_recoveries": turtle_repaired,
        "parse_failures_unrecovered": parse_failures,
        "chunk_render_failures": chunk_failures,
        "documents": documents,
        "documents_total": len(documents),
        "documents_incomplete": len(incomplete),
        "units_lost_total": sum(d["units_lost"] for d in documents),
        "output_files": per_file,
    }
    print(
        f"\ntotal wall time: {_fmt(total_seconds)}  "
        f"({started.isoformat(timespec='seconds')} -> {finished.isoformat(timespec='seconds')})"
    )
    print(
        f"response_repair: {repaired} recovered, turtle_repair: {turtle_repaired} "
        f"recovered, {parse_failures} unrecovered parse failure(s), "
        f"{chunk_failures} chunk render failure(s) logged"
    )
    if incomplete:
        print(
            f"\nINCOMPLETE: {len(incomplete)}/{len(documents)} document(s) lost "
            f"content ({report['units_lost_total']} unit(s) total). These files "
            f"exist and look clean; they are missing text:"
        )
        for d in incomplete:
            state = "no output" if not d["output_written"] else "partial"
            print(
                f"    {d['document']:<16} {d['units_lost']}/{d['units_total']} "
                f"unit(s) lost ({d['units_lost_fraction']:.0%})  [{state}]"
            )
    else:
        print(f"complete: all {len(documents)} document(s) kept every unit")
    if args.report:
        pathlib.Path(args.report).write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(f"report written to {args.report}")

    if incomplete and not args.allow_unit_loss and exit_code == 0:
        # Fail the RUN, not just the record, so a driver script cannot log a
        # clean extraction phase over documents that silently lost text. The
        # experiment driver already treats a non-zero extraction as a flag
        # rather than an abort ("WARNING: ... continuing to next model"), so
        # this surfaces the problem without costing the remaining runs.
        exit_code = 2

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
