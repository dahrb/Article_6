# Article 6 — domestic proceedings extraction

Extracting the **domestic procedural history** of a European Court of Human
Rights case — the chain of national proceedings, the authorities that decided
them, the parties to each, and their outcomes — from the judgment text, as an
RDF graph against a fixed domain ontology.

| | what it is | where |
|---|---|---|
| **the ontology** | `echr:` — classes, closed vocabularies and SHACL shapes for domestic procedural history | `ontology/` |
| **the pipeline** | agentic stage-1 extraction, a vote across runs, OntoCast graph construction, and SHACL repair | `art6/ontology/`, `scripts/` |
| **the evaluation** | the annotation standard, gold annotations and deterministic scorers | `annotation/`, `art6/ontology/diagnostics/` |
| **the corpus tooling** | HUDOC collection, metadata and sampling | `art6/data/`, `main.py` |

---

## The pipeline

```
judgment ──► stage 1 (×4 runs) ──► vote ──► bridge ──► stage 2 ──► stage 3 ──► graph
             agentic extraction     1b      digest     OntoCast     reconcile
             Luna ×2, DeepSeek ×2                      Gemma        + SHACL loop
```

1. **Stage 1** (`agentic_stage1.py`): event detection, linking, a person identifier and a critic, which routes to a parallel-track agent, a parties pass and a fixer. Every span is located verbatim in the judgment or dropped.
2. **Vote** (`vote.py`): several stage-1 runs aligned; an event is kept if at least 2 runs have it, and each event's predecessor is chosen by majority.
3. **Bridge** (`bridge.py`): renders the voted events as the digest stage 2 reads. Stage 2 never sees the raw judgment.
4. **Stage 2**: OntoCast builds the RDF graph from the digest (`run_native.py`).
5. **Stage 3** (`reconcile.py`): copies stage-1 decisions back where stage 2 lost them, recreates missing events, and repairs SHACL violations without reading the judgment.

Details, prompts and every module: [`art6/ontology/README.md`](art6/ontology/README.md).

---

## Setup

```bash
uv sync                                   # Python >= 3.12
```

- **API key:** `keys.env` in the repo root with `OPENROUTER=...`. The scripts read it themselves; it is gitignored and never printed.
- **Triple store:** an Apache Fuseki container named `ontocast-fuseki` on `localhost:3032`. It runs under Docker Desktop: after a reboot, run `systemctl --user start docker-desktop` and wait until the container is healthy.
- **OntoCast:** a checkout beside this repository (`$ONTOCAST_REPO`, default `../../ontocast`), installed with `uv sync --extra all`. It is imported in-process by `run_native.py`, not installed here.
- **OntoCast settings:** `ontology/ontology_vllm.env` (gitignored, holds credentials).

## Running it

Input is a JSONL of judgments, one `{"case_id": ..., "text": ...}` per line.

```bash
scripts/run_pipeline.sh results/my_run cases.jsonl          # all steps
RUNS="deepseek" scripts/run_pipeline.sh results/cheap cases.jsonl   # one stage-1 run, no vote
STEPS="3" scripts/run_pipeline.sh results/my_run cases.jsonl        # rerun stage 3 only
```

Every step resumes, so rerunning after a stall (stage 3 runs on Crusoe, which
throttles) finishes the job. Outputs: `s1_*` (each stage-1 run), `s1` (voted),
`in/bundle.jsonl`, `g2` (stage-2 graphs), `g3` (final graphs), plus `run.log`
and `cost.log` (OpenRouter usage after each stage).

## Scoring

```bash
# stage-1 payloads, major events only
uv run python -m art6.ontology.diagnostics.score_payloads voted=results/my_run/s1

# final graphs: drop minor events, then score
uv run python -m art6.ontology.diagnostics.major_only results/my_run/g3 results/my_run/s1 results/my_run/g3_major
uv run python -m art6.ontology.diagnostics.score_graphs final=results/my_run/g3_major

# why gold events are missed, and what the extra events are
uv run python -m art6.ontology.diagnostics.miss_audit --source cases.jsonl --extras results/my_run/s1
```

Gold defaults to `annotation/test` (the 10-document validation set); pass
`--gold-dir annotation/gold` for the 30-document test set.

---

## Versions

| | |
|---|---|
| **stage 1** | `openai/gpt-6-luna` and `deepseek/deepseek-v4.1-flash-1017` (reasoning off), temperature 0, via OpenRouter |
| **stages 2–3** | `google/gemma-4-31b-it`, bf16 on Crusoe, via OpenRouter; stage 2 at temperature 0.4, stage 3 at 0 |
| **OntoCast** | results so far use **v0.6.9**; the checkout is now v0.9.0 |
| **ontology** | `ontology/echr.ttl` 3.8.0-timeline |

Stage 2 runs at temperature 0.4, so full-pipeline runs vary; compare
configurations within a run, or report the spread over runs.

## Layout

```
ontology/            echr.ttl, echr-shapes.ttl
art6/ontology/       the pipeline: stage 1, vote, bridge, stage-2 driver, stage 3
  prompts/           one prompt per agent
  diagnostics/       scoring, all deterministic
art6/data/, main.py  corpus collection and sampling
scripts/             run_pipeline.sh, stage2.sh, env.sh
annotation/          annotation standard, gold (30) and validation (10) annotations
tests/
```

## Licence

Ontology: CC BY 4.0. Code: see `LICENSE`.
