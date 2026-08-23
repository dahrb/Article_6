# Extraction cost report

Estimated LLM cost of running OntoCast facts extraction over the Art. 6 corpora,
derived from the two reference runs in `prior_results/`.

Prepared 2026-08-16. Prices from OpenAI's pricing page on that date — recheck
before committing to a corpus-scale run.

## Method

Two things are measured rather than assumed.

**Token accounting.** Taken from the `budget` block of `prior_results/*.run.json`.
Per `ontocast/onto/token_usage.py:24-45`, `reasoning_tokens` are counted *inside*
`output_tokens`, and `cache_read_input_tokens` are counted *inside* `input_tokens`
but billed at the cached rate. So:

```
billable fresh input = input_tokens - cache_read_input_tokens
billable output      = output_tokens          (reasoning included)
```

**Corpus size.** A full pass over both corpus JSONL files, summing the same three
sections the test set exports (`introduction` + `procedure` + `facts`) — not an
extrapolation from case counts.

| corpus | records | chars | mean/case |
|---|---:|---:|---:|
| Judgments | 15,305 | 137,235,911 | 8,967 |
| Decisions | 33,019 | 194,351,711 | 5,886 |
| **Both** | **48,324** | **331,587,622** | 6,862 |

Cost is scaled by character volume, since input tokens track text length.

## The two reference runs

Both were gpt-5-mini at temperature 1.0, `RENDER_MODE=facts`,
`ONTOLOGY_CONTEXT_MODE=selected_single_ontology`, ontology snapshot 1,417 triples.

| | case | chars | units | calls | fresh in | cached in | output | reasoning | facts triples |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| L1 | Stanev v. Bulgaria | 23,996 | 4 | 8 | 125,264 | 98,944 | 23,204 | 15,744 | 744 |
| L2 | Beer and Regan v. Germany | 34,551 | 9 | 19 | 41,327 | 412,928 | 58,074 | 35,904 | 1,855 |

Structural facts that drive everything below:

- **75%** of input tokens were served from the provider's prompt cache at a 90% discount
- **64%** of output tokens were reasoning tokens
- **75%** of total cost was output (on gpt-5-mini's rates)

## Model comparison

Rates per 1M tokens (standard tier). Same measured token counts, repriced.

| model | input | cached in | output | 2 cases | per 1k chars |
|---|---:|---:|---:|---:|---:|
| gpt-5-nano | $0.05 | $0.005 | $0.40 | $0.0434 | $0.000741 |
| gpt-5.6-luna | $0.20 | $0.02 | $1.20 | $0.1411 | $0.002410 |
| gpt-5-mini | $0.25 | $0.025 | $2.00 | $0.2170 | $0.003706 |

## Corpus estimates

| model | Judgments | Decisions | **Both** | Both, at batch rates |
|---|---:|---:|---:|---:|
| gpt-5-nano | $102 | $144 | **$246** | $123 |
| gpt-5.6-luna | $331 | $468 | **$799** | $400 |
| gpt-5-mini | $509 | $720 | **$1,229** | $615 |

Relative to gpt-5-mini: luna **−35%**, nano **−80%**. Luna beats its headline
input discount (0.20 vs 0.25 is only −20%) because output dominates the bill and
its output rate is 40% lower.

Decisions cost more in total than judgments despite being cheaper per case —
2.2× as many documents, at ⅔ the average length.

## Sensitivity

**Prompt caching is the largest swing.** Repricing the same work with *no* cache
discount raises gpt-5-mini from $1,229 to **$1,881** (+53%). The 75% hit rate
depends on running in sustained batches; a run spread thinly over days caches
far worse. This is worth real money.

**gpt-5.6-luna cache writes** ($0.25/M, above its $0.20 fresh input rate). Both
reference runs recorded `cache_creation_input_tokens: 0`, so there is no measured
write volume. Bounding it:

| assumption | Judgments | Decisions | Both |
|---|---:|---:|---:|
| no write charge (as measured) | $331 | $468 | $799 |
| half of fresh input written | $340 | $482 | $823 |
| all fresh input written (ceiling) | $350 | $496 | $846 |

At most **+5.9%** — fresh input is a small slice next to output. A single real run
on luna will populate `cache_creation_input_tokens` and collapse this range.

**Batch tier is not a config switch.** `LLMConfig` exposes no `service_tier`, and
`ontocast/tool/llm_batch.py` is a manual cache pre-warming workflow requiring the
prompts up front. Budget against standard-tier figures; treat the batch column as
a ceiling on savings.

## Caveats

- **n = 2**, both long Grand Chamber English judgments. Committee decisions and
  admissibility shells are simpler per character, so the decisions figure is more
  likely high than low.
- Both corpora include French cases; the test set was English-only.
- Excludes failed runs, retries beyond the `llm_repair_visits` these two cases
  used, and any re-runs (OntoCast's disk cache makes exact re-runs free).
- A `facts_user_instruction` adds tokens to every prompt — small next to the
  1,417-triple ontology context, but it applies across ~100k calls.
- gpt-5-mini may be delisted in favour of the GPT-5.4/5.5 families; confirm rates
  against the billing dashboard.

## On gpt-5-nano

80% cheaper, but unproven for this workload. Each call must emit a valid JSON-LD
graph obeying the two-namespace contract, class-vs-instance separation and typing
rules. The margin is already thin at gpt-5-mini: L2 finished with
`facts_validation_errors: 2`, `facts_findings_residual: 1` and one LLM repair
render. With 64% of output being reasoning tokens, the task leans on exactly the
capability nano has least of.

Probe it for ~$0.04 before deciding:

```bash
LLM_MODEL_NAME=gpt-5-nano LIMIT=2 OUTPUT_DIR=$PWD/results/nano_probe \
  ./art6/ontology/run_data.sh
```

Compare against the baseline — `facts_triples` (744 / 1,855),
`facts_validation_errors`, `facts_findings_residual`,
`facts_llm_repair_renders_failed` (was 0), `facts_rejected_merges`.

If nano proves context-limited rather than reasoning-limited,
`LLM_GRAPH_FORMAT=turtle` roughly halves prompt tokens per triple (50.7 vs 102.6
chars/triple by OntoCast's own measurement) with no change to extraction
semantics. It invalidates the LLM cache, so switch before a run, not during.

A plausible split if quality holds only on the shorter documents: nano for
decisions, luna for judgments — about **$475** on standard rates.
