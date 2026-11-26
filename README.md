# Article 6 — domestic proceedings extraction

Extracting the **domestic procedural history** of a European Court of Human
Rights judgment as the Court itself counts it in a committee judgment's
appendix: each **set of proceedings**, the act that **started** it, and the
**decision** taken at each level, with the authority, the parties, the matter,
the outcome and whether it went for or against the applicant. The output is an
RDF graph against a fixed domain ontology.

| | what it is | where |
|---|---|---|
| **the set model** | the event model: vocabularies, file format, validation, derived order | `art6/model.py`, `annotation/annotation_guide.txt` |
| **the ontology** | `echr:` 4.1.0: classes, closed vocabularies and SHACL shapes | `ontology/` |
| **the pipeline** | stage-1 extraction × 4 models, a vote, OntoCast graph construction, reconciliation | `art6/stage1/`, `art6/ontology/`, `scripts/` |
| **the evaluation** | alignment, scoring, agreement, the reasonable-time rule | `art6/evaluation/` |
| **annotation** | the guide, the gold, and a local annotation app | `annotation/`, `annotation_tool/` |
| **corpus tooling** | HUDOC collection, metadata and sampling | `art6/data/`, `main.py` |

---

## The pipeline

```
judgment ──► stage 1 (×4 models) ──► vote ──► payload ──► stage 2 ──► stage 3 ──► graph
             one call per document     1b       1c        OntoCast    reconcile
             Gemma, Qwen 27B,                              Gemma      + SHACL loop
             DeepSeek, Luna
```

1. **Stage 1** (`art6/stage1/extract.py`, `prompts/extract.txt`): one model call per document returns the sets, start acts and decisions as JSON. Closed-list values are checked, and every quote is located in the judgment.
2. **Vote** (`art6/stage1/vote.py`): the runs are aligned to the first. An event is kept when at least `MIN_RUNS` (2) runs have it, and each field takes the majority value.
3. **Payload** (`art6/stage1/to_payload.py`): every text field becomes a span with its character offsets; `follows` is derived (the start act, then decisions by date).
4. **Stage 2** (`art6/ontology/bridge.py`, `render_bundles.py`, `run_native.py`): OntoCast builds the graph from a digest of the payload and never sees the judgment.
5. **Stage 3** (`art6/ontology/reconcile.py`): makes the graph say what stage 1 decided (fields, links, sets), mints events stage 2 lost, and repairs SHACL violations without reading the judgment.

Details of every module: [`art6/README.md`](art6/README.md).

## Setup

```bash
uv sync                                   # Python >= 3.12
```

- **API key:** `keys.env` in the repo root with `OPENROUTER=...`. The scripts read it themselves; it is gitignored and never printed.
- **Triple store:** an Apache Fuseki container named `ontocast-fuseki` on `localhost:3032`. It runs under Docker Desktop: after a reboot, run `systemctl --user start docker-desktop` and wait until the container is healthy.
- **OntoCast:** a checkout beside this repository (`$ONTOCAST_REPO`, default `../../ontocast`), installed with `uv sync --extra all`. It is imported in-process by `run_native.py`, not installed here.
- **OntoCast settings:** `ontology/ontology_vllm.env` (gitignored, holds credentials).

## Running it

Input is a JSONL of judgments, one `{"case_id": ..., "text": ...}` per line, Facts only.

```bash
scripts/run_pipeline.sh results/my_run cases.jsonl                    # all steps
RUNS="gemma" scripts/run_pipeline.sh results/one cases.jsonl          # one stage-1 run, no vote
STEPS="1" scripts/run_pipeline.sh results/my_run cases.jsonl          # stage 1 + vote only (no Fuseki)
STEPS="3" scripts/run_pipeline.sh results/my_run cases.jsonl          # rerun stage 3 only
```

Every step resumes. Outputs: `r<i>_<model>` (each stage-1 run), `vote`, `s1`
(payloads), `in/bundle.jsonl`, `g2` (stage-2 graphs), `g3` (final graphs), plus
`run.log` and `cost.log` (cumulative OpenRouter usage after each step).

`scripts/oneshot.sh` is the one-shot baseline (OntoCast alone on the raw
judgment); `art6/baselines/openie.py` is the non-LLM baseline.

## Scoring

```bash
# against the set-model gold (default annotation/test_simple, the 10-document set)
uv run python -m art6.evaluation.score vote=results/my_run/vote kg=results/my_run/g3

# agreement between any two sources: annotators, runs, systems (no gold needed)
uv run python -m art6.evaluation.agreement a=DIR b=DIR

# the reasonable-time period from the sets vs the Court's own
uv run python -m art6.evaluation.score_reasonable_time kg=results/my_run/g3

# check annotation files
uv run python -m art6.model annotation/test_simple --text-dir annotation/test_text
```

A source directory can hold `<case>.facts.ttl` graphs (any pipeline; graphs
without sets are projected onto the set model), `<case>.json` extractions or
votes, or `<case>.yaml` annotations.

## Annotating

```bash
uv run python annotation_tool/server.py --annotator <name>
```

A local web app that writes the guide's YAML: select a sentence, press S or D,
fill the fields. See [`annotation_tool/README.md`](annotation_tool/README.md).

---

## Versions

| | |
|---|---|
| **set model** | `annotation/annotation_guide.txt` v3.1 |
| **ontology** | `ontology/echr.ttl` 4.1.0-sets |
| **stage 1** | `google/gemma-4-31b-it`, `qwen/qwen3.8-27b`, `deepseek/deepseek-v4.1-flash-1017`, `openai/gpt-6-luna`, temperature 0, via OpenRouter (`scripts/env.sh`) |
| **stages 2–3** | `google/gemma-4-31b-it` via OpenRouter; stage 2 at temperature 0.4, stage 3 at 0 |
| **OntoCast** | the checkout in `$ONTOCAST_REPO` (v0.9.2 at the time of writing) |

Stage 2 runs at temperature 0.4, so full-pipeline runs vary; compare
configurations within a run, or report the spread over runs. Earlier
pipelines (the v2 agentic stage 1, the JURIX configuration) are in git history.

## Layout

```
art6/model.py        the set model
art6/stage1/         stage 1: extract, vote, to_payload; prompts/extract.txt
art6/ontology/       stages 2-3: bridge, OntoCast driver, reconcile, SHACL loop; prompts/
art6/evaluation/     align, score, kg (graph reader), agreement, reasonable_time
art6/baselines/      OpenIE baseline
art6/data/, main.py  corpus collection and sampling
ontology/            echr.ttl, echr-shapes.ttl
scripts/             run_pipeline.sh, stage2.sh, oneshot.sh, env.sh
annotation/          the guide, the set-model gold (test_simple), source texts
annotation_tool/     the annotation app
tests/
```

## Licence

Ontology: CC BY 4.0. Code: see `LICENSE`.
