#!/usr/bin/env bash
#
# ============================================================================
# Four-model extraction experiment over the full Art. 6 test set.
# ============================================================================
#
#   ./art6/ontology/run_experiment.sh                 # full run, all 4 models
#   ./art6/ontology/run_experiment.sh --dry-run       # print the plan, run nothing
#   LIMIT=1 ./art6/ontology/run_experiment.sh         # smoke test, 1 case each
#   MODELS=gemma4 ./art6/ontology/run_experiment.sh   # one model only
#
# For each model this runs TWO phases:
#   phase 1  `ontocast process` over the test set          -> <model>/raw/
#   phase 2  copy raw -> repaired/, then repair_facts.py   -> <model>/repaired/
#
# The repair pass always uses the SAME model that produced the facts, so a
# model is judged on its own extraction plus its own self-correction.
#
# BACKUPS. The raw/ directory is written once by ontocast and never touched
# again -- it is the pristine copy. repaired/ starts as a byte-for-byte copy of
# it, and repair_facts.py additionally snapshots each file into
# repaired/backup/ before overwriting. So every repaired file has two
# independent pre-repair copies to diff against.
#
# ---------------------------------------------------------------------------
# EXPERIMENT DESIGN
# ---------------------------------------------------------------------------
# Held constant across all four models (the only intended variable is the LLM):
#   ontology            ontology/echr.ttl @ 2.2.0
#   facts prompt        art6/ontology/prompts/facts.txt
#   chunking            CHUNK_SECTION_CLASSIFIER=off, MIN=5000, MAX=15000
#                       -- classifier off keeps the whole document; the section
#                       cascade was dropping title/PROCEDURE front matter.
#   graph format        LLM_GRAPH_FORMAT=$GRAPH_FORMAT (see GRAPH_FORMAT below)
#   ontology context    ONTOLOGY_CONTEXT_MODE=fixed_single_ontology (set in
#                       ontology.env) + ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr,
#                       which fixed mode REQUIRES. MAX_TRIPLES=4000 = no
#                       pruning (echr.ttl is 921 triples). Fixed mode removes
#                       the per-unit ontology-selection call, which halves
#                       calls and closes a silent failure: under selection,
#                       units intermittently got an EMPTY ontology context
#                       (gpt-5-mini 1/41, gemma 4/41, gpt-5.4-nano 23/41).
#   render mode         facts, MAX_VISITS=1
#   temperature         1.0
#
# KNOWN CONFOUND -- temperature. gpt-5 reasoning models reject every value
# except 1.0 ("Unsupported value: 'temperature' does not support 0.2 with this
# model"), so 1.0 is the only setting all four models share and is used
# throughout. ontology_vllm.env had 0.2 tuned for the local servers; that is
# deliberately overridden here for comparability, which may cost the open
# models some output determinism. If gemma/qwen produce malformed graphs, a
# re-run at 0.2 is the first thing to try -- and that result is itself a
# finding, not a bug.
#
# Measured context headroom (vLLM /tokenize, echr.ttl @ 921 triples):
#   gemma-4-31b  jsonld 48,137 / turtle 16,676 tok  of 98,304
#   Qwen-3-80B   jsonld 40,933 / turtle 15,886 tok  of 90,000
# Both formats fit; turtle leaves substantially more room for output.
#
# Each model gets its OWN Fuseki project (growgraph--art6_<key>--facts /
# --ontologies). This is required, not tidiness: doc_iri is a hash of the
# document, so every model would otherwise write into the same named graph in
# one shared dataset and read back each other's triples.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ============================================================================
# EDIT ME
# ============================================================================

# Which models to run, space separated. Keys are defined in MODEL_SPECS below.
MODELS="${MODELS:-gpt5mini gpt54nano gemma4 qwen3}"

# Records from the top of the test set. Empty means ALL (10 cases).
LIMIT="${LIMIT-}"

EXPERIMENT_DIR="${EXPERIMENT_DIR:-${REPO_ROOT}/results/experiment_$(date +%Y%m%d_%H%M%S)}"

# Temperature. 1.0 is the only value gpt-5 models accept -- see the note above.
TEMPERATURE="${TEMPERATURE:-1.0}"

# Serialization the ontology context and the model's own output use.
# turtle is ~2.9x more compact than jsonld for this ontology (measured via the
# vLLM /tokenize endpoint on echr.ttl @ 921 triples):
#   gemma-4-31b  jsonld 48,137 tok  vs  turtle 16,676 tok
#   Qwen-3-80B   jsonld 40,933 tok  vs  turtle 15,886 tok
# That is a large prefill saving per call. It is NOT known to be quality-neutral
# -- ontology_vllm.env notes jsonld was chosen only to match prior_results -- so
# a format change is a genuine experimental variable, not a free optimisation.
GRAPH_FORMAT="${GRAPH_FORMAT:-turtle}"

# Appended to each model's Fuseki project name. Runs that differ in any setting
# MUST NOT share a project: doc_iri is a hash of the document, so a re-run
# writes into the same named graph and would read back the previous run's
# triples during aggregation.
PROJECT_SUFFIX="${PROJECT_SUFFIX:-}"

# key|model_name|base_url|fuseki_project
# An empty base_url means the hosted OpenAI API.
MODEL_SPECS=(
    "gpt5mini|gpt-5-mini||art6_gpt5mini"
    "gpt54nano|gpt-5.4-nano||art6_gpt54nano"
    "gemma4|gemma-4-31b|http://localhost:8000/v1|art6_gemma4"
    "qwen3|Qwen-3-80B|http://localhost:8003/v1|art6_qwen3"
)

# Placeholder credential for the local vLLM servers, which do not check it.
VLLM_API_KEY="${VLLM_API_KEY:-token-abc123}"

# ============================================================================
# END EDIT ME
# ============================================================================

BASE_ENV_FILE="${BASE_ENV_FILE:-${REPO_ROOT}/ontology/ontology.env}"
KEYS_FILE="${KEYS_FILE:-${REPO_ROOT}/keys.env}"
RUN_DATA="${SCRIPT_DIR}/run_data.sh"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1 && shift

die() { printf 'error: %s\n' "$1" >&2; exit 1; }

[[ -f "${BASE_ENV_FILE}" ]] || die "base env not found: ${BASE_ENV_FILE}"
[[ -f "${KEYS_FILE}"     ]] || die "keys file not found: ${KEYS_FILE}"
[[ -x "${RUN_DATA}"      ]] || die "run_data.sh not found or not executable: ${RUN_DATA}"

spec_for() {
    local want="$1" spec
    for spec in "${MODEL_SPECS[@]}"; do
        [[ "${spec%%|*}" == "${want}" ]] && { printf '%s' "${spec}"; return 0; }
    done
    return 1
}

# --- preflight -------------------------------------------------------------
# Fail before burning an hour of GPU time, not during model four.

printf '=== preflight ===\n'
for key in ${MODELS}; do
    spec="$(spec_for "${key}")" || die "unknown model key: ${key}"
    IFS='|' read -r _ model_name base_url project <<< "${spec}"
    project="${project}${PROJECT_SUFFIX}"

    if [[ -n "${base_url}" ]]; then
        served=$(curl -s -m 10 "${base_url}/models" \
            | python3 -c 'import json,sys; print(" ".join(m["id"] for m in json.load(sys.stdin)["data"]))' 2>/dev/null || true)
        [[ -n "${served}" ]] || die "${key}: ${base_url} is not answering"
        [[ " ${served} " == *" ${model_name} "* ]] \
            || die "${key}: ${base_url} serves '${served}', not '${model_name}'"
        printf '  %-10s %-14s %s (reachable)\n' "${key}" "${model_name}" "${base_url}"
    else
        printf '  %-10s %-14s api.openai.com\n' "${key}" "${model_name}"
    fi

    for suffix in facts ontologies; do
        ds="growgraph--${project}--${suffix}"
        code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -u admin:test345 \
            -G --data-urlencode 'query=ASK{}' "http://localhost:3032/${ds}/sparql" </dev/null || true)
        [[ "${code}" == "200" ]] || die "${key}: Fuseki dataset ${ds} missing (HTTP ${code})"
    done
done
printf '  fuseki datasets: ok\n'
printf '  output: %s\n' "${EXPERIMENT_DIR}"
printf '  limit:  %s\n' "${LIMIT:-all 10 cases}"
printf '  format: %s\n' "${GRAPH_FORMAT}"
printf '  fuseki project suffix: %s\n\n' "${PROJECT_SUFFIX:-<none>}"

if (( DRY_RUN )); then
    printf 'dry run - nothing executed\n'
    exit 0
fi

mkdir -p "${EXPERIMENT_DIR}"

# Record exactly what this experiment held constant, alongside its outputs.
cp "${REPO_ROOT}/ontology/echr.ttl" "${EXPERIMENT_DIR}/echr.ttl.snapshot"
cp "${SCRIPT_DIR}/prompts/facts.txt" "${EXPERIMENT_DIR}/facts.prompt.snapshot"

python3 - <<PY > "${EXPERIMENT_DIR}/manifest.json"
import json, subprocess, datetime
print(json.dumps({
    "started": datetime.datetime.now().isoformat(timespec="seconds"),
    "models": "${MODELS}".split(),
    "limit": "${LIMIT}" or "all",
    "temperature": float("${TEMPERATURE}"),
    "temperature_note": (
        "1.0 is the only value gpt-5 reasoning models accept; applied to all "
        "four models for comparability. ontology_vllm.env had 0.2 tuned for "
        "the local servers."
    ),
    "held_constant": {
        "ontology": "ontology/echr.ttl (snapshot alongside)",
        "facts_prompt": "art6/ontology/prompts/facts.txt (snapshot alongside)",
        "chunk_section_classifier": "off",
        "chunk_min_size": 5000,
        "chunk_max_size": 15000,
        "llm_graph_format": "${GRAPH_FORMAT}",
        "ontology_context_mode": "selected_single_ontology",
        "render_mode": "facts",
        "max_visits": 1,
    },
    "git_head": subprocess.run(["git","rev-parse","HEAD"],capture_output=True,text=True,cwd="${REPO_ROOT}").stdout.strip(),
    "git_dirty": bool(subprocess.run(["git","status","--porcelain"],capture_output=True,text=True,cwd="${REPO_ROOT}").stdout.strip()),
}, indent=2))
PY

# --- run -------------------------------------------------------------------

for key in ${MODELS}; do
    spec="$(spec_for "${key}")"
    IFS='|' read -r _ model_name base_url project <<< "${spec}"
    project="${project}${PROJECT_SUFFIX}"

    model_dir="${EXPERIMENT_DIR}/${key}"
    raw_dir="${model_dir}/raw"
    repaired_dir="${model_dir}/repaired"
    mkdir -p "${raw_dir}"

    printf '\n############################################################\n'
    printf '# %s  (%s)\n' "${key}" "${model_name}"
    printf '############################################################\n'

    # Per-model env: the shared base, then overrides appended. Later
    # assignments win when the file is sourced, so these override the base
    # without editing it. Kept next to the outputs as a record of the run.
    model_env="${model_dir}/ontology.env"
    {
        cat "${BASE_ENV_FILE}"
        printf '\n# ---- run_experiment.sh overrides for %s ----\n' "${key}"
        printf 'LLM_MODEL_NAME=%s\n' "${model_name}"
        printf 'LLM_TEMPERATURE=%s\n' "${TEMPERATURE}"
        printf 'CHUNK_SECTION_CLASSIFIER=off\n'
        printf 'LLM_GRAPH_FORMAT=%s\n' "${GRAPH_FORMAT}"
        # Pin the ontology instead of having each model pick it from the
        # catalog. selected_single_ontology spends an LLM call per unit on a
        # choice with exactly one candidate, and a model that fluffs that call
        # extracts against an EMPTY context: in the smoke test gpt-5.4-nano
        # came back with ontology_snapshot_triples=0 while the other three got
        # 929. Pinning makes the ontology context identical for every model,
        # which is the whole point of the comparison.
        printf 'ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr\n'
        if [[ -n "${base_url}" ]]; then
            printf 'LLM_BASE_URL=%s\n' "${base_url}"
            printf 'LLM_API_KEY=%s\n' "${VLLM_API_KEY}"
            # Local servers queue rather than scale; keep the fan-out modest.
            printf 'LLM_MAX_INFLIGHT=4\n'
        fi
    } > "${model_env}"

    # Give each model its own copy of the input. run_data.sh derives the
    # .jsonl path from the .json path, so sharing one input would have every
    # model rewriting the same data/art6_domestic_test_set.jsonl -- harmless
    # while runs are sequential, a torn read as soon as they are not. The copy
    # doubles as a record of exactly what this model was fed.
    model_input="${model_dir}/input.json"
    cp "${REPO_ROOT}/data/art6_domestic_test_set.json" "${model_input}"

    printf '\n--- phase 1: ontocast extraction -> %s ---\n' "${raw_dir#${REPO_ROOT}/}"
    set +e
    ENV_FILE="${model_env}" \
    INPUT_JSON="${model_input}" \
    OUTPUT_DIR="${raw_dir}" \
    TENANT=growgraph \
    PROJECT="${project}" \
    LIMIT="${LIMIT}" \
        "${RUN_DATA}" 2>&1 | tee "${model_dir}/extract.log"
    extract_rc=${PIPESTATUS[0]}
    set -e
    if (( extract_rc != 0 )); then
        printf 'WARNING: %s extraction exited %d - continuing to next model\n' "${key}" "${extract_rc}"
        printf '%s\n' "${extract_rc}" > "${model_dir}/extract.failed"
        continue
    fi

    produced=$(find "${raw_dir}" -name '*.facts.ttl' | wc -l)
    printf '\nphase 1 done: %s facts file(s)\n' "${produced}"
    if (( produced == 0 )); then
        printf 'WARNING: %s produced no facts files - skipping repair\n' "${key}"
        continue
    fi

    printf '\n--- phase 2: repair pass (same model) -> %s ---\n' "${repaired_dir#${REPO_ROOT}/}"
    rm -rf "${repaired_dir}"
    cp -r "${raw_dir}" "${repaired_dir}"

    repair_args=(--facts-dir "${repaired_dir}" --model "${model_name}" --temperature "${TEMPERATURE}")
    if [[ -n "${base_url}" ]]; then
        repair_args+=(--base-url "${base_url}" --api-key "${VLLM_API_KEY}")
    fi

    set +e
    (cd "${REPO_ROOT}" && uv run python -m art6.ontology.repair_facts "${repair_args[@]}") \
        2>&1 | tee "${model_dir}/repair.log"
    repair_rc=${PIPESTATUS[0]}
    set -e
    if (( repair_rc != 0 )); then
        printf 'WARNING: %s repair exited %d\n' "${key}" "${repair_rc}"
        printf '%s\n' "${repair_rc}" > "${model_dir}/repair.failed"
    fi
    printf '\nphase 2 done for %s\n' "${key}"
done

printf '\n============================================================\n'
printf 'experiment complete: %s\n' "${EXPERIMENT_DIR}"
printf '============================================================\n'
for key in ${MODELS}; do
    d="${EXPERIMENT_DIR}/${key}"
    [[ -d "${d}" ]] || continue
    raw=$(find "${d}/raw" -name '*.facts.ttl' 2>/dev/null | wc -l)
    rep=$(find "${d}/repaired" -maxdepth 1 -name '*.facts.ttl' 2>/dev/null | wc -l)
    flags=""
    [[ -f "${d}/extract.failed" ]] && flags="${flags} EXTRACT_FAILED"
    [[ -f "${d}/repair.failed"  ]] && flags="${flags} REPAIR_FAILED"
    printf '  %-10s raw=%-3s repaired=%-3s%s\n' "${key}" "${raw}" "${rep}" "${flags}"
done
