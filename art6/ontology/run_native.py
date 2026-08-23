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
    args, passthrough = ap.parse_known_args()

    if not args.no_response_repair:
        from art6.ontology.response_repair import enable as enable_response_repair

        enable_response_repair()

    if not args.no_turtle_repair:
        from art6.ontology.turtle_repair import enable as enable_turtle_repair

        enable_turtle_repair()

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
    if args.report:
        pathlib.Path(args.report).write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(f"report written to {args.report}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
