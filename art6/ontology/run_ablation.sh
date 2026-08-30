#!/usr/bin/env bash
#
# C0-C4 ablation for the JURIX study, on one document set.
#
#   ./art6/ontology/run_ablation.sh --set data/art6_domestic_test_set.json \
#                                   --out results/ablation_test
#
#   C0  O1 schema-light JSON, one call, no ontology
#   C1  OntoCast alone: raw judgment -> graph
#   C2  C1 + repair
#   C3  compressed -> graph
#   C4  compressed -> graph + repair   (the full pipeline)
#
# C1/C2 and C3/C4 are the raw/ and repaired/ checkpoints of the SAME extraction,
# so stage 2 runs twice, not four times, and the repair contrast is measured
# from identical input on each side.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ONTOCAST_REPO="${ONTOCAST_REPO:-$(cd "${REPO_ROOT}/../.." && pwd)/ontocast}"

SET_JSON=""; OUT_DIR=""; LIMIT="${LIMIT:-}"; FRESH=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --set) SET_JSON="$2"; shift 2;;
        --out) OUT_DIR="$2"; shift 2;;
        --limit) LIMIT="$2"; shift 2;;
        --fresh) FRESH=1; shift;;
        *) echo "unknown arg: $1" >&2; exit 2;;
    esac
done
[[ -n "${SET_JSON}" && -n "${OUT_DIR}" ]] || { echo "usage: $0 --set <json> --out <dir>" >&2; exit 2; }

MODEL="${MODEL:-gemma-4-31b}"
BASE_URL="${BASE_URL:-http://localhost:8003/v1}"
TEMPERATURE="${TEMPERATURE:-0.4}"          # stage 2/3; stage 1 is 0.0
PROJECT_BASE="${PROJECT_BASE:-art6_abl_$(basename "${OUT_DIR}")}"
FUSEKI_CONTAINER="${FUSEKI_CONTAINER:-ontocast-fuseki}"
FUSEKI_AUTH_B64="$(printf '%s' "${FUSEKI_AUTH:-admin:test345}" | tr '/' ':' | base64)"
BASE_ENV_FILE="${BASE_ENV_FILE:-${REPO_ROOT}/ontology/ontology_vllm.env}"

die() { echo "ABORT: $*" >&2; exit 1; }
now() { date +%s; }

mkdir -p "${OUT_DIR}"
OUT_DIR="$(cd "${OUT_DIR}" && pwd)"
LOG="${OUT_DIR}/run.log"
exec > >(tee -a "${LOG}") 2>&1
echo "=== ablation: $(basename "${SET_JSON}") -> ${OUT_DIR#${REPO_ROOT}/} @ $(date -Is) ==="

# ---- preflight -------------------------------------------------------------
curl -sf -m 10 -o /dev/null "${BASE_URL}/models" || die "${BASE_URL} unreachable"
docker exec "${FUSEKI_CONTAINER}" true 2>/dev/null || die "fuseki container ${FUSEKI_CONTAINER} not running"
[[ -f "${SET_JSON}" ]] || die "set not found: ${SET_JSON}"
[[ -d "${ONTOCAST_REPO}" ]] || die "ontocast repo not found: ${ONTOCAST_REPO}"
[[ -f "${BASE_ENV_FILE}" ]] || die "base env not found: ${BASE_ENV_FILE}"
echo "preflight ok: ${MODEL} @ ${BASE_URL}"

fuseki_ds() {   # create a dataset, idempotent
    docker exec "${FUSEKI_CONTAINER}" wget -qO- \
        --header="Authorization: Basic ${FUSEKI_AUTH_B64}" \
        --post-data "dbName=$1&dbType=tdb2" \
        http://localhost:3030/\$/datasets >/dev/null 2>&1 || true
}

# Credentials come from the environment, never from a generated file.
set -a; eval "$(grep -E '^(LLM_API_KEY|FUSEKI_AUTH)=' "${BASE_ENV_FILE}")"; set +a
export LLM_API_KEY="${VLLM_API_KEY:-${LLM_API_KEY:-EMPTY}}"

# ---- ontology + shapes, staged so the run records what it used -------------
SEED_DIR="${OUT_DIR}/ontology_seed"; SHAPES_DIR="${OUT_DIR}/shapes"
rm -rf "${SEED_DIR}" "${SHAPES_DIR}"; mkdir -p "${SEED_DIR}" "${SHAPES_DIR}"
cp "${REPO_ROOT}/ontology/echr.ttl" "${SEED_DIR}/"
cp "${REPO_ROOT}/ontology/echr-shapes.ttl" "${SHAPES_DIR}/"
cp "${SCRIPT_DIR}/prompts/facts.txt" "${OUT_DIR}/facts.prompt.snapshot"
cp "${SCRIPT_DIR}/prompts/compress.txt" "${OUT_DIR}/compress.prompt.snapshot"

# ---- input: rebuild the JSONL with the LIVE facts prompt -------------------
INPUT_JSONL="${OUT_DIR}/input.jsonl"
LIMIT="${LIMIT}" INPUT_JSON="${SET_JSON}" INPUT_JSONL="${INPUT_JSONL}" \
FACTS_PROMPT_FILE="${SCRIPT_DIR}/prompts/facts.txt" python3 - <<'PY'
import json, os, pathlib
records = json.load(open(os.environ["INPUT_JSON"], encoding="utf-8"))
limit = os.environ.get("LIMIT", "").strip()
if limit:
    records = records[: int(limit)]
prompt = pathlib.Path(os.environ["FACTS_PROMPT_FILE"]).read_text(encoding="utf-8").strip()
with open(os.environ["INPUT_JSONL"], "w", encoding="utf-8") as fh:
    for r in records:
        fh.write(json.dumps({**r, "facts_user_instruction": prompt}, ensure_ascii=False) + "\n")
print(f"records: {len(records)}   facts prompt: {len(prompt):,} chars")
for i, r in enumerate(records, 1):
    print(f"  L{i}: {r.get('case_id','?'):<12} {len(r.get('text','')):>7,} chars")
PY

phase() { echo; echo "--- $* ---"; }

# RESUME. A long run dies for reasons that have nothing to do with the work --
# a dropped port-forward, a restarted vLLM server. Each phase drops a marker
# when it completes, and a rerun with the same --out skips what is already
# done. Within a phase, compress.py and new_repair.py resume per DOCUMENT on
# their own markers; stage 2 input is filtered to documents with no .facts.ttl
# yet, so it resumes per document too. --fresh forces everything to rerun.
DONE_DIR="${OUT_DIR}/.done"; mkdir -p "${DONE_DIR}"
done_phase()  { [[ -z "${FRESH}" && -f "${DONE_DIR}/$1" ]]; }
mark_phase()  { touch "${DONE_DIR}/$1"; }

# ---- C0: O1 schema-light, no ontology --------------------------------------
if done_phase C0; then
    echo; echo "--- C0  (already done, skipping) ---"
else
phase "C0  O1 schema-light"
t0=$(now)
(cd "${REPO_ROOT}" && uv run python -m art6.conditions.run_conditions \
    --condition o1 --input-json "${SET_JSON}" --out-dir "${OUT_DIR}/C0" \
    --model "${MODEL}" --base-url "${BASE_URL}" --api-key "${LLM_API_KEY}" \
    ${LIMIT:+--limit "${LIMIT}"}) || echo "C0 returned $?"
echo "C0 done in $(( $(now) - t0 ))s"
mark_phase C0
fi

# ---- stage 1: compression (C3/C4 input) ------------------------------------
if done_phase stage1; then
    echo; echo "--- stage 1  (already done, skipping) ---"
else
phase "stage 1  compression -> evidence bundles"
t0=$(now)
(cd "${REPO_ROOT}" && uv run python -m art6.ontology.compress \
    --input-jsonl "${INPUT_JSONL}" --out-dir "${OUT_DIR}/stage1" \
    --model "${MODEL}" --base-url "${BASE_URL}" --temperature 0.0 \
    --parties-pass)
(cd "${REPO_ROOT}" && uv run python -m art6.ontology.render_bundles \
    --compress-dir "${OUT_DIR}/stage1" --source-jsonl "${INPUT_JSONL}" \
    --out-jsonl "${OUT_DIR}/bundles.jsonl")
echo "stage 1 done in $(( $(now) - t0 ))s"
mark_phase stage1
fi

# ---- stage 2 + 3, once per input form --------------------------------------
# $1 label, $2 input jsonl, $3 arm dir
run_arm() {
    local label="$1" input="$2" dir="$3"
    local project="${PROJECT_BASE}_${label}"
    mkdir -p "${dir}"
    fuseki_ds "growgraph--${project}--facts"
    fuseki_ds "growgraph--${project}--ontologies"

    # RESUME IS PER PHASE HERE, NOT PER DOCUMENT. run_native names its outputs
    # from the INPUT FILE stem plus line position (input.jsonl -> input.L1 ...),
    # so filtering the input to the undone documents renames every output and
    # renumbers the survivors -- L8 of ten becomes L1 of three and collides with
    # an existing file. Stage 2 is therefore all-or-nothing per arm. Stage 1 and
    # stage 3 resume per document on their own markers, and those are the long
    # phases; a repeated stage 2 costs ~75s per document.
    if done_phase "stage2_${label}"; then
        echo; echo "--- ${label} stage 2  (already done, skipping) ---"
    else
    phase "${label}  stage 2  extraction -> ${dir#${REPO_ROOT}/}/raw"
    local t=$(now)
    (
        set -a; source <(grep -vE '^(LLM_API_KEY|FUSEKI_AUTH)=' "${BASE_ENV_FILE}"); set +a
        export LLM_MODEL_NAME="${MODEL}" LLM_BASE_URL="${BASE_URL}" LLM_TEMPERATURE="${TEMPERATURE}"
        export LLM_GRAPH_FORMAT=jsonld LLM_CACHE_ENABLED=false LLM_MAX_INFLIGHT=4
        export CHUNK_SECTION_CLASSIFIER=off CHUNK_MIN_SIZE=200000 CHUNK_MAX_SIZE=400000
        export ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
        export ONTOCAST_ONTOLOGY_DIRECTORY="${SEED_DIR}"
        export FACTS_SHAPES_DIR="${SHAPES_DIR}" FACTS_SHACL_INFERENCE=rdfs \
               FACTS_SHACL_ADVANCED=true FACTS_SHACL_AUTOFIX=prune FACTS_SHACL_AUTOFIX_PASSES=1
        cd "${ONTOCAST_REPO}"
        env -u VIRTUAL_ENV PYTHONPATH="${REPO_ROOT}" \
            uv run python -m art6.ontology.run_native \
                --input-path "${input}" --tenant growgraph --project "${project}" \
                --output-dir "${dir}/raw" --max-visits 1 \
                --report "${dir}/extract_report.json" --allow-unit-loss
    ) || echo "${label} stage 2 returned $?"
    echo "${label} stage 2 done in $(( $(now) - t ))s"
    mark_phase "stage2_${label}"
    fi

    phase "${label}  stage 3  repair -> ${dir#${REPO_ROOT}/}/repaired"
    t=$(now)
    # Seed repaired/ from raw/ only on a first (or --fresh) run. Re-copying on a
    # resume would discard every document already repaired and restart stage 3
    # from scratch, which is what resume exists to prevent.
    if [[ -n "${FRESH}" || ! -d "${dir}/repaired" ]]; then
        rm -rf "${dir}/repaired"; cp -r "${dir}/raw" "${dir}/repaired"
    else
        cp -rn "${dir}/raw/." "${dir}/repaired/" 2>/dev/null || true
    fi
    (cd "${REPO_ROOT}" && PYTHONUNBUFFERED=1 uv run python -m art6.ontology.new_repair \
        --facts-dir "${dir}/repaired" --input-jsonl "${input}" \
        --model "${MODEL}" --base-url "${BASE_URL}" --api-key "${LLM_API_KEY}" \
        --temperature 0.0) || echo "${label} stage 3 returned $?"
    echo "${label} stage 3 done in $(( $(now) - t ))s"
}

run_arm "raw"  "${INPUT_JSONL}"          "${OUT_DIR}/C1_C2"
run_arm "comp" "${OUT_DIR}/bundles.jsonl" "${OUT_DIR}/C3_C4"

phase "done"
cat > "${OUT_DIR}/manifest.json" <<JSON
{
  "set": "${SET_JSON}",
  "finished": "$(date -Is)",
  "model": "${MODEL}",
  "base_url": "${BASE_URL}",
  "temperature_stage2_3": ${TEMPERATURE},
  "temperature_stage1": 0.0,
  "conditions": {
    "C0": "C0/",
    "C1": "C1_C2/raw/",
    "C2": "C1_C2/repaired/",
    "C3": "C3_C4/raw/",
    "C4": "C3_C4/repaired/"
  }
}
JSON
echo "manifest -> ${OUT_DIR#${REPO_ROOT}/}/manifest.json"
