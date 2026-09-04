# Historical experiment arm configurations

Extracted from the per-arm `arm.env` files before `results/` was wiped on 2026-08-30. Those files were never in git (`*.env` is ignored, and they
carried `LLM_API_KEY` and `FUSEKI_AUTH`), so this was the only record of how each
arm was configured. Credential values are deliberately not reproduced here —
they are read from `keys.env` at run time and belong in no other file.

Only the per-arm override block is kept. Everything above it in `arm.env` was a
verbatim copy of the base config (`ontology/ontology_vllm.env`), identical across
arms.

18 arms.

---

## `experiment_arms_20260825_133905/o2_low_jsonld`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=jsonld
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=3000
CHUNK_MAX_SIZE=6000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
ONTOCAST_ONTOLOGY_DIRECTORY=/home/dahrb/Projects/article_6/domestic_proceedings/results/experiment_arms_20260825_133905/ontology_seed
FACTS_SHAPES_DIR=/home/dahrb/Projects/article_6/domestic_proceedings/results/experiment_arms_20260825_133905/shapes
FACTS_SHACL_INFERENCE=rdfs
FACTS_SHACL_ADVANCED=true
FACTS_SHACL_AUTOFIX=prune
FACTS_SHACL_AUTOFIX_PASSES=1
```

## `jurix_phase1/o2_cf_low_jsonld`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=jsonld
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=3000
CHUNK_MAX_SIZE=6000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `jurix_phase1/o2_cf_low_ttl`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=turtle
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=3000
CHUNK_MAX_SIZE=6000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `jurix_phase1/o2_cf_med_jsonld`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=jsonld
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=8000
CHUNK_MAX_SIZE=16000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `jurix_phase1/o2_cf_med_ttl`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=turtle
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=8000
CHUNK_MAX_SIZE=16000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `jurix_phase1/o2_large_jsonld`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=jsonld
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=20000
CHUNK_MAX_SIZE=50000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `jurix_phase1/o2_large_ttl`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=turtle
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=20000
CHUNK_MAX_SIZE=50000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `jurix_phase1/o2_low_jsonld`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=jsonld
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=3000
CHUNK_MAX_SIZE=6000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `jurix_phase1/o2_low_ttl`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=turtle
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=3000
CHUNK_MAX_SIZE=6000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `jurix_phase1/o2_med_jsonld`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=jsonld
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=8000
CHUNK_MAX_SIZE=16000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `jurix_phase1/o2_med_ttl`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=turtle
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=8000
CHUNK_MAX_SIZE=16000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `jurix_phase1_gpt5mini/o2_large_jsonld`

```
LLM_MODEL_NAME=gpt-5-mini
LLM_TEMPERATURE=1.0
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=jsonld
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=20000
CHUNK_MAX_SIZE=50000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `jurix_phase1_gpt5mini/o2_med_jsonld`

```
LLM_MODEL_NAME=gpt-5-mini
LLM_TEMPERATURE=1.0
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=jsonld
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=8000
CHUNK_MAX_SIZE=16000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `old/experiment_arms_20260823_223700/nochunk_jsonld_mv1`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=jsonld
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=20000
CHUNK_MAX_SIZE=50000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `old/experiment_arms_20260823_223700/nochunk_ttl_mv1`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=turtle
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=20000
CHUNK_MAX_SIZE=50000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `old/experiment_arms_20260823_223700/nochunk_ttl_mv2`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=turtle
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=20000
CHUNK_MAX_SIZE=50000
MAX_VISITS=2
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `old/experiment_arms_20260823_223700/rolling_3k6k_mv1`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=turtle
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=3000
CHUNK_MAX_SIZE=6000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```

## `old/experiment_arms_20260823_223700/rolling_8k16k_mv1`

```
LLM_MODEL_NAME=gemma-4-31b
LLM_TEMPERATURE=0.4
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=<from keys.env, not recorded>
LLM_MAX_INFLIGHT=4
LLM_CACHE_ENABLED=false
LLM_GRAPH_FORMAT=turtle
CHUNK_SECTION_CLASSIFIER=off
CHUNK_MIN_SIZE=8000
CHUNK_MAX_SIZE=16000
MAX_VISITS=1
ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
```
