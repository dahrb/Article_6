# The code

`scripts/run_pipeline.sh OUT cases.jsonl` runs stages 1–3. Setup is in the
[root README](../README.md).

## The set model (`model.py`)

One document is a list of **sets** of proceedings and a flat list of
**events**. An event is the act that opened its set (`role: start`, at most one
per set) or a decision at one level (`role: decision`). Set membership and
`reviews` (the earlier decision of the same set that this one rules on) are the
links. The plain order inside a set is **derived** from the dates
(`ordered`, `derived_follows`): the start, then the decisions by date.

`model.py` holds the closed vocabularies, the annotation file format
(`from_nested` / `to_nested` / `dump_yaml`), `validate` and `check` (schema,
plus every quote found in the text), `is_review`, and the scorer's view of a
document (`nodes`). Run as a module, it checks annotation files:

```bash
uv run python -m art6.model annotation/test_simple --text-dir annotation/test_text
```

## Stage 1 (`stage1/`)

| module | does |
|---|---|
| `extract.py` | one call per document with `prompts/extract.txt` (temperature 0). The reply is parsed into the flat set-model form; an unknown closed-list value becomes the list's escape value; a set with no dated act is dropped; `reviews` / `enforces` pointing at a missing decision, or `reviews` crossing sets, are cleared. Writes `<case>.json` and the raw reply `<case>.raw.txt`; `--reparse` rebuilds from the raw replies. |
| `vote.py` | aligns each run to the first (the scorer's alignment); an event is kept when at least `--min-runs` runs have it, and one the first run lacks is added when that many other runs agree on it; fields and `reviews` by majority, the first run winning ties; parties and quotes are the first run's. |
| `to_payload.py` | the voted documents as stage-1 payloads (`<case>.compress.json`): every text field a span with offsets, each set a track, `follows` derived. |

## Stages 2–3 (`ontology/`)

**Bridge** (`bridge.py`, `render_bundles.py`, `prompt_spec.py`). The payload is
rendered as a digest, which becomes the `text` stage 2 reads; the raw judgment
is never shown to stage 2. Every span carries its `[start:end]` offsets. Each
record also carries the stage-2 instruction, `prompts/facts.txt`. `--raw` puts
the judgment itself in the record (the one-shot baseline).

**Stage 2** (`run_native.py`, `stage2_shards.py`, `stage2_coverage.py`,
`scripts/stage2.sh`). OntoCast builds the graph with Gemma at temperature 0.4:
one visit, the ontology and shapes snapshotted into the run directory, 3
shards, up to 3 attempts for documents that fail or come back with fewer than
80% of their events. `run_native.py` runs OntoCast in-process and refuses an
input whose instruction differs from `facts.txt`.

**Stage 3** (`reconcile.py`, `shacl_loop.py`, `graph_patch.py`). Stage 1
decides; stage 3 makes the graph say what stage 1 decided, and makes it valid.
It never re-reads the judgment for new facts.

| step | how |
|---|---|
| lossless rewrites (entailed types, split shared participations, …) | code |
| match each stage-1 event to its graph node(s) by quote, else by date and authority | code |
| transcribe fields, `follows` / `reviews` links, appeal and communication dates, and the sets (`echr:ProceedingSet`, membership, kind, pending, `enforcesDecision`) | code |
| mint stage-1 events the graph lacks; kept only if it adds no SHACL violation | 1 model call, `prompts/reconcile_mint.txt` |
| SHACL violation loop: graph and ontology only, no new evidence | model, at most 3 rounds, `prompts/shacl_loop*.txt` |
| transcribe again, so the loop cannot undo a stage-1 decision | code |

`graph_patch.apply_patch` applies every model patch operation by operation and
refuses adding a supporting quote, touching anything outside the document's
namespace, emptying an event's participations, or referencing a node that does
not exist. Each graph gets a `<stem>.reconcile.json` report, also the resume
marker.

Support: `provider.py` (OpenRouter request bodies, `ART6_EXTRA_BODY`),
`llm_cache.py`, `json_closers.py`, `threadsafe_sparql.py`, `validate_shapes.py`
(SHACL against `ontology/echr-shapes.ttl`), `validate_source_quotes.py` (each
supporting quote verbatim in the judgment).

## Evaluation (`evaluation/`)

| module | does |
|---|---|
| `align.py` | the one alignment rule: 1:1, a start pairs only with a start and a decision with a decision, dates must agree (same date at the coarser precision, or one character apart), most similar quotes win. Edges count only between aligned events. |
| `score.py` | a system against the gold: events, decisions, starts; level, outcome, direction, authority, matter, applicant's side, `appealed_by`, costs; `reviews` and derived `follows`; same-set F1; the Court's appendix columns (start, end, number of court levels) |
| `kg.py` | reads a final graph into the set model. Graphs without sets (the v2 pipeline, the JURIX configuration, the one-shot) are projected: a set is a connected component of their chains |
| `agreement.py` | any two sources against each other, no gold: structure F1, raw agreement and Cohen's kappa per field, bootstrap 95% intervals over documents. For annotator against annotator, and run against run |
| `reasonable_time.py`, `score_reasonable_time.py` | the Art. 6 § 1 period by a deterministic rule over the sets and HUDOC metadata (never The Law), scored against the Court's own period (`annotation/court_periods.yaml`) by MAE in months |

## Baselines (`baselines/`)

`openie.py`: Stanford OpenIE triples assembled into events by a fixed rule (a
dated sentence with a legal-act verb), written as set-model documents with and
without a naive date-order timeline, scored by `evaluation/score.py`.

## Corpus (`data/`)

HUDOC collection, metadata and text processing (`main.py` runs them in order),
and the seeded samplers behind every evaluation set: the 10-document set
(`build_ontocast_test_set.py`), its holdout, the evaluation sample and the
30-document annotation sample (`build_annotation_sample.py`).
