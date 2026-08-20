"""
carry_forward.py
----------------
Sequential, carry-forward chunked facts extraction.

The stock OntoCast pipeline processes a document's chunks as a *parallel*
fan-out (``make_render_facts_node`` gathers every unit at once), so chunk 3
cannot know what chunk 1 already extracted. Every chunk therefore mints its own
IRI for a court/party/proceeding it happens to re-mention, and the damage is
cleaned up afterwards by the embedding-based aggregator. That post-hoc merge is
what the 2026-08-18 run showed fusing distinct proceedings in L3/L10.

This script runs the same per-unit loop *sequentially* instead, threading the
graph accumulated so far into the next chunk's prompt, so the model is asked to
reuse an existing IRI rather than to invent a second one that later has to be
guessed-merged.

Two facts about OntoCast make this possible without touching that repo:

  1. ``ContentUnit.iri`` is a constant (``DEFAULT_IRI``), not a per-chunk hash --
     every chunk renders into the SAME namespace, so an IRI minted in chunk 1 is
     directly reusable in chunk 2. (The per-chunk namespace only appears later,
     via ``graph_absolute``, which is exactly where cross-chunk identity used to
     fragment.)
  2. ``render_facts`` already branches on ``len(content_unit.graph) == 0``. Seed
     that graph and the unit takes the ``render_facts_update`` path, which
     serialises the graph into a ``# SEMANTIC GRAPH OF FACTS`` chapter and asks
     for typed insert/delete operations against it -- precisely the shape we
     want, just fed from earlier *text* rather than from an earlier attempt at
     the same text.

The one gap is prompt semantics: that path's built-in wording assumes graph and
text describe the same content. ``UnitFactsState.facts_user_instruction`` is a
first-class per-run override that lands in the prompt above both chapters, so
the carry-forward framing goes there (see CARRY_FORWARD_INSTRUCTION).

Nothing in the ontocast checkout is modified; it is imported as a library. Like
chunk_probe.py, this must run where ontocast is importable:

    cd ../../ontocast
    env -u VIRTUAL_ENV uv run python \
        ../article_6/domestic_proceedings/art6/ontology/carry_forward.py \
        --input-path  <input.jsonl> \
        --output-dir  <dir> \
        --env-file    <ontology.env> \
        --chunk-min-size 8000 --chunk-max-size 15000

Output is one ``<stem>.L<n>.facts.ttl`` per input record, byte-comparable in
shape to what ``ontocast process`` writes, so repair_facts.py / validate_shapes.py
downstream need no changes.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import os
import pathlib
import re
import sys
import time

logger = logging.getLogger("carry_forward")

# ---------------------------------------------------------------------------
# The domain prompt, read live.
#
# ``expand_input_to_states`` picks ``facts_user_instruction`` out of each JSONL
# record, because that is how ``ontocast process`` receives it. run_data.sh
# regenerates the JSONL from prompts/facts.txt on every run, so that path is
# never stale -- but carry_forward.py is normally pointed at a JSONL somebody
# built earlier, and then the embedded prompt is a COPY frozen at build time.
# That is exactly how the 2026-08-19 cfcmp run shipped a 1,783-byte facts.txt
# that predated the 3.3.0 ontology: the file on disk had moved on, the JSONL
# had not.
#
# So read the file itself, every run, and let it win over whatever the record
# carries. There is no second copy to keep in sync and no way for a stale JSONL
# to silently steer an experiment.
# ---------------------------------------------------------------------------
FACTS_PROMPT_PATH = pathlib.Path(__file__).resolve().parent / "prompts" / "facts.txt"


def load_facts_prompt() -> str:
    """The current contents of prompts/facts.txt, resolved next to this file."""
    if not FACTS_PROMPT_PATH.is_file():
        raise SystemExit(f"facts prompt not found: {FACTS_PROMPT_PATH}")
    text = FACTS_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"facts prompt is empty: {FACTS_PROMPT_PATH}")
    return text


# ---------------------------------------------------------------------------
# The carry-forward framing.
#
# Lands in the "# USER INSTRUCTION" slot, which the template places BEFORE both
# the ontology/text chapters and the "# SEMANTIC GRAPH OF FACTS" chapter -- hence
# the forward references ("below"). Three things it has to do at once:
#   - re-frame the graph as coming from EARLIER text, not from this chunk;
#   - make IRI reuse the explicit, named goal (the whole point of the exercise);
#   - stop the model treating a non-empty graph as "already done" and
#     under-extracting the new text, which is the obvious failure mode of
#     reusing an update-shaped prompt for a fresh-content task.
# ---------------------------------------------------------------------------
CARRY_FORWARD_INSTRUCTION = """\
This document is being processed in sequential parts. The "SEMANTIC GRAPH OF \
FACTS" shown below was extracted from EARLIER PARTS of this same document. It \
was NOT extracted from the text shown below, which is new and has not been \
processed yet.

Your task is to extract the facts in the NEW text, continuing the existing graph:

1. REUSE EXISTING IRIs. If the new text refers to an entity that already appears \
in the graph below -- the same court, authority, party, person or proceeding -- \
you MUST reuse that entity's existing IRI. Do not mint a second IRI for \
something already present. Entities are frequently referred to more loosely on \
later mention ("the court", "the applicant", "the Regional Court") than on \
first mention; match them to the existing entity anyway.
2. ADD the new facts. The new text contains material not yet in the graph. \
Extract it fully, with the same level of detail as the existing graph. Do not \
treat the existing graph as complete, and do not stop early because the graph \
already looks substantial.
3. EXTEND existing entities. If the new text adds information about an entity \
already in the graph (a decision date, an outcome, an appeal), attach those new \
triples to the EXISTING IRI.
4. DO NOT delete or rewrite existing triples unless the new text positively \
contradicts them. Facts from earlier parts stay, even where the new text does \
not mention them.
"""


def _load_env_file(path: pathlib.Path) -> int:
    """Apply a KEY=VALUE env file without adding a python-dotenv dependency.

    Two ordering rules, both of which matter for the existing ontology.env
    files and neither of which is the naive one:

      - WITHIN the file, the LAST assignment wins. These files are written to
        be ``source``d, and they rely on it: ontology.env sets the OpenAI model
        near the top and overrides it with the local gemma endpoint at the
        bottom. First-wins silently runs the wrong model against the wrong URL.
      - The PRE-EXISTING environment beats the file, so an explicit
        ``FOO=bar carry_forward.py ...`` still overrides, matching how
        run_data.sh treats its own defaults.

    Trailing ``# comment`` is stripped only when preceded by whitespace, so a
    value that legitimately contains ``#`` survives.
    """
    preexisting = set(os.environ)
    parsed: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = re.sub(r"\s+#.*$", "", value.strip()).strip().strip('"').strip("'")
        parsed[key] = value  # last assignment wins

    applied = 0
    for key, value in parsed.items():
        if key in preexisting:
            continue
        if "${" in value:
            # e.g. LLM_API_KEY=${OPENAI_API_KEY}: shell would expand it, we do
            # not. Skipping beats exporting a literal "${OPENAI_API_KEY}" as a
            # credential and failing deep inside a provider call.
            logger.warning(
                "env %s: skipping %s -- unexpanded shell reference %r",
                path.name,
                key,
                value,
            )
            continue
        os.environ[key] = value
        applied += 1
    return applied


def _fmt(n: float) -> str:
    return f"{n:,.1f}"


async def _run(args: argparse.Namespace) -> int:
    run_started = datetime.datetime.now(datetime.UTC)
    t_run = time.perf_counter()
    # Imported here, after the env file has been applied: Config() reads the
    # environment at construction, and several ontocast modules snapshot
    # settings at import time.
    from ontocast.agent.chunk_text import chunk_text
    from ontocast.agent.convert_document import convert_document
    from ontocast.api.process_helpers import (
        expand_input_to_states,
        facts_ttl_output_path,
        turtle_from_graph,
    )
    from ontocast.api.tenancy_resolution import (
        resolve_tenant_project,
        stores_use_tenancy_partitions,
    )
    from ontocast.config import Config
    from ontocast.onto.enum import Status
    from ontocast.onto.ontology_snapshot import OntologySnapshot
    from ontocast.onto.rdfgraph import RDFGraph
    from ontocast.onto.unit_states import UnitFactsState
    from ontocast.stategraph.atomic import facts_loop
    from ontocast.stategraph.context_resolver import (
        build_merged_document_ontology_context,
    )
    from ontocast.stategraph.unit_context import UnitLoopContext
    from ontocast.toolbox import ToolBox

    if not args.no_response_repair:
        from art6.ontology.response_repair import enable as enable_response_repair

        enable_response_repair()

    config = Config()
    config.validate_llm_config()

    # Chunk sizing is the whole experiment; make it explicit rather than
    # inheriting whatever the env happens to say.
    chunk_config = config.get_tool_config().chunk_config
    if args.chunk_min_size is not None:
        chunk_config.min_size = args.chunk_min_size
    if args.chunk_max_size is not None:
        chunk_config.max_size = args.chunk_max_size

    tools = await ToolBox.acreate(config)
    tenant, project = resolve_tenant_project(args.tenant, args.project)
    # Mirrors _bootstrap_tools: only Fuseki/vector-backed stores partition by
    # tenancy, and the vector store is left alone unless the ontology context
    # mode actually needs it.
    if stores_use_tenancy_partitions(tools):
        await tools.update_tenancy_with_vector_mode(
            tenant,
            project,
            initialize_vector_store=False,
            fail_on_vector_store_error=False,
        )
    await tools.initialize(fail_on_vector_store_error=False)

    # ToolBox may have snapshotted the chunker before the overrides landed;
    # pin the live tool too.
    if args.chunk_min_size is not None:
        tools.chunker.config.min_size = args.chunk_min_size
    if args.chunk_max_size is not None:
        tools.chunker.config.max_size = args.chunk_max_size

    logger.info(
        "chunking: min_size=%s max_size=%s section_classifier=%s",
        tools.chunker.config.min_size,
        tools.chunker.config.max_size,
        tools.chunker.config.section_classifier,
    )

    input_path = pathlib.Path(args.input_path).expanduser().resolve()
    output_dir = pathlib.Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    states = expand_input_to_states(
        input_path,
        config=config,
        head_chunks=None,
        ontology_context_mode_value=config.server.ontology_context_mode,
        tenant=tenant,
        project=project,
        max_visits=config.server.max_visits_per_node,
    )
    logger.info("expanded %s into %d record(s)", input_path.name, len(states))

    # Read once, applied per record below. Logged with its length so a run's own
    # log records which revision of the prompt it actually used.
    facts_prompt = load_facts_prompt()
    logger.info(
        "facts prompt: %d chars from %s (overrides any copy embedded in %s)",
        len(facts_prompt),
        FACTS_PROMPT_PATH,
        input_path.name,
    )

    selected = set(args.only) if args.only else None
    summary: list[dict] = []
    failures = 0

    for record_index, state in enumerate(states, start=1):
        if selected is not None and record_index not in selected:
            continue
        label = f"L{record_index}"
        t_record = time.perf_counter()

        convert_document(state, tools)
        # MUST come after convert_document: that agent copies the record's own
        # facts_user_instruction into the state, so an override applied any
        # earlier is silently discarded here.
        state.facts_user_instruction = facts_prompt
        if state.status == Status.FAILED or state.docling_doc is None:
            logger.error("%s: conversion failed: %s", label, state.failure_reason)
            failures += 1
            continue

        await chunk_text(state, tools)
        units = state.content_units
        if not units:
            logger.error("%s: chunker produced no content units", label)
            failures += 1
            continue

        # Ontology context is document-level; the stock fan-out builds it once
        # and shares it across units, so do the same rather than re-resolving
        # (and re-merging rdflib graphs) on every chunk.
        doc_context = UnitLoopContext.from_agent_state(state)
        merged_context = build_merged_document_ontology_context(doc_context)
        if merged_context is not None:
            state.facts_ontology_context = merged_context.snapshot.graph

        logger.info(
            "%s: %d chunk(s), sizes=%s",
            label,
            len(units),
            [len(u.text) for u in units],
        )

        # ---- the sequential carry-forward loop -----------------------------
        running = RDFGraph()
        chunk_rows: list[dict] = []
        record_failed = False
        last_unit = units[-1]

        for unit_index, unit in enumerate(units):
            carried = len(running)
            # Seeding the unit graph is what selects render_facts_update over
            # render_facts_fresh inside render_facts(); chunk 0 stays empty and
            # takes the fresh path exactly as the stock pipeline would.
            if carried:
                unit.graph = running.copy()

            facts_state = UnitFactsState(
                content_unit=unit,
                ontology_snapshot=OntologySnapshot.empty(
                    title="Pending context resolve",
                    description="Placeholder until resolve_unit_ontology_context runs.",
                ),
                ontology_patch_sources=[],
                # The carry-forward framing SUPPLEMENTS the domain prompt, it
                # does not stand in for it. Replacing it stripped every rule in
                # facts.txt -- evidence anchoring, no-precedent, one-court-per-
                # proceeding, one-label-per-entity -- from chunks 2..N, which is
                # what produced the cfcmp run's missing hasSupportingQuote layer
                # (4 quotes across L1/L2/L6/L10; 115 once this was fixed).
                facts_user_instruction=(
                    f"{state.facts_user_instruction}\n\n{CARRY_FORWARD_INSTRUCTION}"
                    if carried
                    else state.facts_user_instruction
                ),
                max_visits_per_node=state.max_visits,
                max_critic_visits_per_node=config.server.max_critic_visits_per_node,
                llm_graph_format=state.llm_graph_format,
                ontology_context_max_triples=config.server.ontology_context_max_triples,
            )

            t_chunk = time.perf_counter()
            result = await facts_loop(
                facts_state,
                tools,
                doc_context,
                pre_resolved_context=merged_context,
            )
            elapsed = time.perf_counter() - t_chunk

            produced = result.content_unit.graph
            ok = result.status == Status.SUCCESS
            if not ok:
                logger.error(
                    "%s chunk %d/%d FAILED (%s): %s",
                    label,
                    unit_index + 1,
                    len(units),
                    result.failure_stage,
                    result.failure_reason,
                )
                record_failed = True
            else:
                # The update path returns the seeded graph plus its edits, so
                # this is the running graph, not a delta to union in. Union
                # anyway: it is correct under both branches and cheap.
                running = running + produced if carried else produced

            chunk_rows.append(
                {
                    "chunk": unit_index + 1,
                    "chars": len(unit.text),
                    "carried_in": carried,
                    "triples_out": len(running),
                    "delta": len(running) - carried,
                    "seconds": round(elapsed, 1),
                    "status": str(result.status),
                }
            )
            logger.info(
                "%s chunk %d/%d: %d chars, carried %d -> %d triples (+%d) in %ss",
                label,
                unit_index + 1,
                len(units),
                len(unit.text),
                carried,
                len(running),
                len(running) - carried,
                _fmt(elapsed),
            )
            # NOTE: deliberately NOT appended to state.facts_units here. Under
            # carry-forward, unit N's graph already contains units 1..N-1, so
            # handing every unit to the aggregator feeds it the same entities
            # once per surviving chunk. That inflates its clustering input and
            # invites exactly the speculative cross-chunk merges this design
            # exists to avoid. The terminal unit alone carries the whole
            # document; see below.
            last_unit = result.content_unit

        # ---- aggregate + serialise ----------------------------------------
        # Carry-forward should make cross-chunk collisions rare, not impossible
        # (two chunks can name the same body differently enough that the model
        # does not connect them), so the stock aggregator stays on as a safety
        # net -- and it is also what attaches document metadata and remaps into
        # the document namespace, which the output shape depends on.
        # The terminal unit IS the document: carry-forward accumulated every
        # chunk into it. Give the aggregator that single unit, so it still does
        # the jobs only it does -- document-metadata attachment, remap into the
        # document namespace, provenance -- without re-deciding identity for
        # entities the sequential pass already resolved by IRI.
        last_unit.graph = running
        state.facts_units = [last_unit]

        if args.no_aggregate:
            state.aggregated_facts = running
        else:
            agg = tools.aggregator.postprocess_facts_units(
                units=state.facts_units,
                ontology_graph=state.facts_ontology_context,
                doc_iri=state.doc_iri,
                document_metadata=dict(state.document_metadata),
                doc_namespace=state.doc_namespace,
            )
            state.aggregated_facts = agg.graph

        out_path = None
        if state.aggregated_facts is not None and len(state.aggregated_facts):
            out_path = facts_ttl_output_path(
                input_path, line_number=record_index, output_dir=output_dir
            )
            out_path.write_text(
                turtle_from_graph(state.aggregated_facts, strip_provenance=True),
                encoding="utf-8",
            )

        record_seconds = time.perf_counter() - t_record
        if record_failed or out_path is None:
            failures += 1
        summary.append(
            {
                "record": label,
                "chunks": len(units),
                "chars": sum(len(u.text) for u in units),
                "final_triples": len(state.aggregated_facts or ()),
                "seconds": round(record_seconds, 1),
                "output": out_path.name if out_path else None,
                "chunk_detail": chunk_rows,
            }
        )
        logger.info(
            "%s done: %d chunk(s) -> %d triples in %ss -> %s",
            label,
            len(units),
            len(state.aggregated_facts or ()),
            _fmt(record_seconds),
            out_path.name if out_path else "(no output)",
        )

    print("\n" + "=" * 78)
    print("CARRY-FORWARD SUMMARY")
    print("=" * 78)
    print(f"{'rec':<5}{'chunks':>7}{'chars':>9}{'triples':>9}{'sec':>8}  output")
    for row in summary:
        print(
            f"{row['record']:<5}{row['chunks']:>7}{row['chars']:>9,}"
            f"{row['final_triples']:>9}{row['seconds']:>8}  {row['output'] or '-'}"
        )
    for row in summary:
        if row["chunks"] < 2:
            continue
        print(f"\n  {row['record']} per-chunk:")
        for c in row["chunk_detail"]:
            print(
                f"    chunk {c['chunk']}: {c['chars']:>7,} chars  "
                f"carried_in={c['carried_in']:>4}  out={c['triples_out']:>4}  "
                f"(+{c['delta']:>3})  {c['seconds']:>6}s  {c['status']}"
            )

    run_seconds = time.perf_counter() - t_run
    run_finished = datetime.datetime.now(datetime.UTC)
    print(
        f"\ntotal wall time: {_fmt(run_seconds)}  "
        f"({run_started.isoformat(timespec='seconds')} -> "
        f"{run_finished.isoformat(timespec='seconds')})"
    )

    if args.report:
        report_out = {
            "started": run_started.isoformat(timespec="seconds"),
            "finished": run_finished.isoformat(timespec="seconds"),
            "total_seconds": round(run_seconds, 1),
            "chunk_min_size": args.chunk_min_size,
            "chunk_max_size": args.chunk_max_size,
            "response_repair_enabled": not args.no_response_repair,
            "records": summary,
        }
        pathlib.Path(args.report).write_text(
            json.dumps(report_out, indent=2), encoding="utf-8"
        )
        print(f"\nreport written to {args.report}")

    print(f"\n{len(summary)} record(s) processed, {failures} failure(s)")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Sequential carry-forward chunked facts extraction (OntoCast as a library)."
    )
    ap.add_argument(
        "--input-path", required=True, help="Input .jsonl (or single file)."
    )
    ap.add_argument("--output-dir", required=True, help="Where facts TTLs are written.")
    ap.add_argument("--env-file", help="KEY=VALUE env file to apply before Config().")
    ap.add_argument("--tenant")
    ap.add_argument("--project")
    ap.add_argument("--chunk-min-size", type=int, help="Override CHUNK_MIN_SIZE.")
    ap.add_argument("--chunk-max-size", type=int, help="Override CHUNK_MAX_SIZE.")
    ap.add_argument(
        "--only",
        type=int,
        nargs="+",
        metavar="N",
        help="Process only these 1-based record numbers (smoke tests).",
    )
    ap.add_argument(
        "--no-aggregate",
        action="store_true",
        help="Skip the post-hoc aggregator; serialise the carried graph as-is.",
    )
    ap.add_argument(
        "--no-response-repair",
        action="store_true",
        help=(
            "Do not repair malformed facts-render JSON (mismatched bracket "
            "closers). Without repair these replies are dropped entirely -- "
            "see art6/ontology/response_repair.py -- so this is for A/B only."
        ),
    )
    ap.add_argument("--report", help="Write the run summary as JSON to this path.")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    if args.env_file:
        env_path = pathlib.Path(args.env_file).expanduser().resolve()
        if not env_path.is_file():
            print(f"env file not found: {env_path}", file=sys.stderr)
            return 2
        applied = _load_env_file(env_path)
        logger.info("applied %d setting(s) from %s", applied, env_path.name)

    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
