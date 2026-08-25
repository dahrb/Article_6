#!/usr/bin/env bash
# Re-run the staged repair pass over every arm of an existing experiment dir,
# from raw/ each time, then re-run the SHACL gate. Extraction is NOT repeated.
#
# Written for the 2026-08-24 applier fix (lexical-form fallback on literal
# removes): the shipped repaired/ graphs were produced by an applier whose
# `remove` ops silently no-opped on any datatype/language mismatch, so every
# arm needs its repair stage redone against the same raw/ extraction.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXPERIMENT_DIR="${EXPERIMENT_DIR:-${REPO_ROOT}/results/jurix_phase1}"
MODEL_NAME="${MODEL_NAME:-gemma-4-31b}"
BASE_URL="${BASE_URL:-http://localhost:8000/v1}"
API_KEY="${API_KEY:-${VLLM_API_KEY:-EMPTY}}"
# Repair runs at temperature 0. Sampling diversity buys nothing for a
# correction task and costs reproducibility: the plan already records four
# identical repair runs producing 0/2/5/2 truncation failures, and on the
# 2026-08-24 arm-1 re-run the participation-splitting fix appeared on L6 and
# not on L10 purely by sampling, which makes any single-run before/after
# comparison unreadable. Extraction keeps its own temperature; this is the
# repair pass only.
TEMPERATURE="${TEMPERATURE:-0}"
REPAIR_PASSES="${REPAIR_PASSES:-2}"
INPUT_JSONL="${INPUT_JSONL:-${EXPERIMENT_DIR}/input.jsonl}"

printf 'experiment : %s\nmodel      : %s @ %s\npasses     : %s\n\n' \
  "${EXPERIMENT_DIR}" "${MODEL_NAME}" "${BASE_URL}" "${REPAIR_PASSES}"

for arm_dir in "${EXPERIMENT_DIR}"/o2_*/; do
    arm="$(basename "${arm_dir}")"
    raw_dir="${arm_dir}raw"
    repaired_dir="${arm_dir}repaired"
    [ -d "${raw_dir}" ] || { printf 'SKIP %s (no raw/)\n' "${arm}"; continue; }

    printf '\n=============== %s ===============\n' "${arm}"

    # PREFLIGHT. The repaired/ dir is wiped and rebuilt from raw/ below, so an
    # unreachable endpoint does not merely fail an arm -- it replaces a repaired
    # arm with an unrepaired copy and then does the same to every arm after it.
    # Measured 2026-08-24: the vLLM server died mid-sweep and the driver
    # cheerfully carried on, clobbering arm 1 and heading for the other nine.
    # Abort the whole sweep rather than destroy work we cannot regenerate
    # without the very server that just went away.
    if ! curl -sf -m 10 "${BASE_URL%/v1}/v1/models" >/dev/null 2>&1; then
        printf 'ABORT: %s is unreachable - stopping before %s is wiped\n' \
            "${BASE_URL}" "${arm}" >&2
        exit 1
    fi

    t0=$(date +%s)
    rm -rf "${repaired_dir}"
    cp -r "${raw_dir}" "${repaired_dir}"

    (cd "${REPO_ROOT}" && PYTHONUNBUFFERED=1 uv run python -m art6.ontology.repair_facts \
        --facts-dir "${repaired_dir}" \
        --model "${MODEL_NAME}" \
        --base-url "${BASE_URL}" \
        --api-key "${API_KEY}" \
        --temperature "${TEMPERATURE}" \
        --input-jsonl "${INPUT_JSONL}" \
        --passes "${REPAIR_PASSES}") 2>&1 | tee "${arm_dir}repair.log"
    rc=${PIPESTATUS[0]}
    (( rc != 0 )) && printf '%s\n' "${rc}" > "${arm_dir}repair.failed"

    (cd "${REPO_ROOT}" && uv run python -m art6.ontology.validate_shapes \
        --facts-dir "${repaired_dir}") 2>&1 | tee "${arm_dir}validate.log"

    printf '\n%s done in %ss (repair rc=%s)\n' "${arm}" "$(( $(date +%s) - t0 ))" "${rc}"
done

printf '\n\n===== SHACL gate summary, all arms =====\n'
for arm_dir in "${EXPERIMENT_DIR}"/o2_*/; do
    printf '%-22s %s\n' "$(basename "${arm_dir}")" \
      "$(grep -E '^\s*TOTAL' "${arm_dir}validate.log" 2>/dev/null | tail -1)"
done
