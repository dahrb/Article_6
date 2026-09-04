# Article 6 — domestic proceedings extraction

Extracting the **domestic procedural history** of a European Court of Human
Rights case — the chain of national proceedings, the authorities that decided
them, the parties to each, and their outcomes — from the judgment text, as an
RDF graph against a fixed domain ontology.

The repository holds three things that can be used independently:

| | what it is | where |
|---|---|---|
| **the ontology** | `echr:` — an extraction schema for domestic procedural history, with closed vocabularies and SHACL shapes | `ontology/` |
| **the pipeline** | evidence stage → ontology-guided extraction (OntoCast) → validation-driven repair, plus a no-ontology baseline | `art6/ontology/`, `art6/conditions/` |
| **the evaluation** | a five-condition ablation over 250 judgments, an annotation standard, and the protocol scoring it | `art6/ontology/README.md`, `annotation/` |

---

## Quick start

```bash
uv sync                                   # Python >= 3.12
```

Then, from a `.jsonl` of case text (see **Input format** below):

```bash
# 1. convert to the JSON array the runner takes
uv run python -c "
import json,sys
recs=[json.loads(l) for l in open('cases.jsonl') if l.strip()]
json.dump(recs, open('cases.json','w'), ensure_ascii=False)"

# 2. run all five conditions
./art6/ontology/run_ablation.sh --set cases.json --out results/my_run

# 3. score them, no model in the loop
uv run python -m art6.ontology.diagnostics.ablation_auto_eval \
    --run-dir results/my_run --source-json cases.json
```

A one-document smoke test takes a few minutes:

```bash
./art6/ontology/run_ablation.sh --set cases.json --out results/smoke --limit 1
```

`run_ablation.sh` preflights the LLM endpoint, the triple store, the document
set, the OntoCast checkout and the configuration file, and aborts before doing
any work if one is missing. Requirements and every flag are in
[`art6/ontology/README.md` §3](art6/ontology/README.md).

---

## Input format

One JSON object per line. Only `case_id` and `text` are required — `case_id`
must be unique, `text` is the judgment as plain text or markdown.

```jsonl
{"case_id": "001-58538", "text": "## SECOND SECTION\n\nCASE OF I.S. v. SLOVAKIA\n..."}
{"case_id": "001-59859", "text": "## FIRST SECTION\n\nCASE OF PEKDAŞ v. TURKEY\n..."}
```

The drawn evaluation samples carry more (`case_name`, `court_level`, `year`,
`period`, `respondent`, `case_group`); those fields are passed through untouched
and are used only for stratification and reporting, never by extraction.

**Nothing is filtered for length.** A judgment that exceeds the stage 1 context
window is split at the paragraph boundary nearest its midpoint, extracted in
independent passes, and concatenated for the graph stage, with spans located in
the whole document so offsets stay global.

The runner takes a **JSON array**, not JSONL, and writes its own
`<out>/input.jsonl` (adding the stage 2 instruction per record). The conversion
above is the whole difference.

---

## Versions this was run with

Reproducing the reported numbers needs these pinned. A run also copies its
prompts, ontology and shapes into its own output directory, so any result can be
regenerated from the run rather than from the repository's current state.

| | version |
|---|---|
| **OntoCast** | **v0.6.2** (commit `2504579`, 2026-08-29), `render_mode=facts`, invoked in-process by `art6/ontology/run_native.py` |
| **model** | `gemma-4-31b` — `RedHatAI/gemma-4-31B-it-FP8-dynamic`, served by vLLM with `--max-model-len 98304` |
| **ontology** | `ontology/echr.ttl` v3.5.0 |
| **temperature** | stage 1 `0.0` (fixed); stages 2 and 3 `0.4` |
| **Python** | >= 3.12, `uv sync` |

OntoCast is **not** installed as a dependency — it is a checkout beside this
repository (`$ONTOCAST_REPO`, default `../../ontocast`) and is invoked from
there. v0.6.2 matters specifically: it absorbed the JSON-recovery patches this
project previously carried locally, so earlier versions need those patches back.

> Extraction is **not deterministic at temperature 0**. Two identical stage 1
> runs disagree materially on which proceedings they find. Compare conditions
> within a run, never a condition in one run against a condition in another.

---

## Layout

```
ontology/          echr.ttl (v3.5.0), echr-shapes.ttl, OntoCast env files
art6/
  conditions/      the no-ontology baseline (C0) and the common renderer
  ontology/        the pipeline, the ablation runner, and its README
    diagnostics/   scoring, all of it deterministic
  data/            sampling: evaluation sample, annotation subsample
annotation/        the annotation standard, the scored triple set, gold templates
docs/              experiment plan, evaluation protocol, run reports
results/           run outputs; each carries its own manifest, prompts and shapes
data/              corpus, metadata, drawn samples
```

---

## Where to go next

| you want to | read |
|---|---|
| run the pipeline or reproduce the ablation | [`art6/ontology/README.md`](art6/ontology/README.md) |
| understand the experiment design | [`docs/JURIX_2.md`](docs/JURIX_2.md) |
| know how outputs are scored | [`docs/eval_phase_checklist.md`](docs/eval_phase_checklist.md) |
| annotate, or read the annotation standard | [`annotation/README.md`](annotation/README.md) |
| understand the extraction target | `ontology/echr.ttl` — every class and property carries `skos:definition` and, where the boundary is contestable, `skos:scopeNote` |

## Licence

Ontology: CC BY 4.0. Code: see `LICENSE`.
