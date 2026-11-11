# The pipeline

`scripts/run_pipeline.sh OUT cases.jsonl` runs every step below. Setup is in
the [root README](../../README.md).

## Stage 1 — agentic extraction (`agentic_stage1.py`, `extraction.py`)

A fixed order of agents, each with one job, routed by their own yes/no flags.
Everything a model returns is checked in code before it is kept.

| agent | runs when | prompt |
|---|---|---|
| event detection | always; flag `domestic_events_found` | `extract.txt` |
| linker: tracks, and each event's predecessor from computed candidates | events were found | `plan.txt` |
| person identifier: applicants, then a chain-aware parties pass | events were found | `applicants.txt`, `parties.txt` |
| critic, with deterministic hints; flags `needs_fix`, `parallel_tracks`, `parties_incomplete` | events were found (`--critic-rounds`, default 1) | `critic.txt` |
| parallel track agent, then the re-linker inside the new tracks | `parallel_tracks = yes` | `tracks.txt`, `link.txt` |
| parties pass on the events the critic named | `parties_incomplete = yes` | `parties.txt` |
| fixer: validated operations on the draft | `needs_fix = yes` | `fix.txt` |

Then `finalize` (code, no model) types every link:
- the same body, rung, procedure and track gives `part_of`;
- a `during` link gives `occurs_during`;
- anything else gives `follows`.

Every link targets the earliest act of its proceeding. Along the way it:
- splits a filing that was folded into its decision;
- folds an undated filing into its decision;
- reroutes a decision's link past steps that decided nothing (hearings, questionings) to the act above them.

`extract.txt` is the annotation guide with stage 1's field rules on top.
Its rules on granularity (one date, two acts; what counts as a filing) follow
the gold conventions. Every dated occurrence is either **procedural** (an official
or state-recognised body takes part: courts, prosecutors, police, ministries,
prisons, professional and disciplinary bodies; hearings and progress enquiries
included) and goes in `events`, or **factual** (the incident, an injury, a
contract, a dismissal) and goes in `circumstances`, which stage 2 writes as
`echr:FactualCircumstance`. Only procedural events form chains and are scored. The critic and fixer
are held to its FIELD RULES section.

**Spans are proof.** The model returns text only; `extraction.py` locates each
span in the judgment. A span that cannot be found is dropped and counted.
Long judgments are split at a paragraph boundary and extracted in passes.

**Output:** per document, `<case>.compress.json` (the payload; the name is
historical) and `<case>.agentic.json` (every agent's raw answer, flags, what
was refused and why, and the draft before `finalize` as `prefinal`).

## Stage 1b — vote (`vote.py`)

4 runs (2 Luna + 2 DeepSeek) aligned to the first with the scorer's aligner.
- An event is kept if at least 2 runs have it.
- Each kept event's predecessor is the one most runs give; a tie keeps the base's.
- Voting is on the procedural events. Factual circumstances are pooled across
  the runs: the base run's, plus any other run's whose words overlap neither one
  already kept nor a kept event. Pointers to dropped events are removed.

## Bridge (`bridge.py`, `render_bundles.py`, `prompt_spec.py`)

The voted payload is rendered as a digest, which becomes the `text` stage 2
reads; the raw judgment is never shown to stage 2. Every span carries its
`[start:end]` offsets. Each record also carries the stage-2 instruction from
`prompts/facts.txt`.

## Stage 2 — OntoCast (`run_native.py`, `stage2_shards.py`, `scripts/stage2.sh`)

OntoCast builds the graph with Gemma at temperature 0.4. Settings:
- `render_mode=facts`, one visit;
- the ontology and shapes are snapshotted into the run directory;
- 3 shards, with up to 3 attempts for documents that fail.

`run_native.py` runs OntoCast in-process and refuses an input whose baked
instruction differs from `facts.txt`.

## Stage 3 — reconcile (`reconcile.py`, `shacl_loop.py`, `graph_patch.py`)

Stage 1 decides; stage 3 makes the graph say what stage 1 decided, and makes
it valid. It never re-reads the judgment for new facts.

| step | how |
|---|---|
| lossless rewrites (entailed types, split shared participations, …) | code |
| match each stage-1 event to its graph node(s) by quote, else by date and authority | code |
| transcribe: fill or replace fields, add, retype or remove chain links so the graph matches stage 1 | code |
| mint stage-1 events the graph lacks; kept only if it adds no SHACL violation | 1 model call, `reconcile_mint.txt` |
| SHACL violation loop: SHACL errors, disjoint types, duplicates; graph and ontology only, no new evidence | model, at most 3 rounds, `shacl_loop.txt`, `shacl_loop_system.txt` |
| transcribe again, so the loop cannot undo a stage-1 decision | code |

`graph_patch.apply_patch` applies every model patch operation by operation, and
refuses:
- adding a supporting quote;
- touching anything outside the document's own namespace;
- emptying an event's participations;
- referencing a node that does not exist.

Each graph gets a `<stem>.reconcile.json` report, which is also the resume marker.

## Diagnostics (`diagnostics/`)

| module | does |
|---|---|
| `score_payloads` | stage-1 payloads vs gold: procedural events, `follows`, `part_of` |
| `score_graphs` | final graphs vs gold: event tiers, typed edges, fields, matter, transitive `follows`, chains |
| `miss_audit` | why each gold event is missed; what each extra event is |
| `score_graph_metrics` | the strict aligner and edge scorer everything above uses |
| `score_against_gold`, `field_agreement` | event tiers and per-field agreement |
| `validate_source_quotes` | does each supporting quote occur verbatim in the judgment |
| `score_reasonable_time` | the reasonable-time period inferred from the chain (`reasonable_time.py`) vs the Court's own (`annotation/court_periods.yaml`): MAE in months of start, end and length |

Scoring is strict:
- A filing aligns only with a filing, and a decision only with a decision.
- An edge counts only when both of its ends align to gold events.

## Other modules

| module | does |
|---|---|
| `provider.py` | OpenRouter request bodies (`ART6_EXTRA_BODY`) |
| `llm_cache.py`, `json_closers.py`, `threadsafe_sparql.py` | response cache, JSON repair, rdflib parse lock |
| `validate_shapes.py` | SHACL validation against `ontology/echr-shapes.ttl` (also a CLI) |
| `reasonable_time.py` | deterministic rule: the Art. 6 § 1 period (start, end, pending) from the chain and HUDOC metadata, never from The Law |
