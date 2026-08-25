#!/usr/bin/env bash
# Run new_repair.py across arms, into repaired_v2/ so the old repaired/ output
# stays intact for comparison.
#
# Each arm's repaired_v2/ is reset from raw/ before the run, so a rerun always
# starts from the extraction output rather than compounding a previous repair.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

RESULTS="${RESULTS:-results/jurix_phase1}"
INPUT_JSONL="${INPUT_JSONL:-${RESULTS}/input.jsonl}"
BASE_URL="${BASE_URL:-http://localhost:8000/v1}"
MODEL="${MODEL:-gemma-4-31b}"
TEMPERATURE="${TEMPERATURE:-0.0}"
ARMS=("$@")
if [[ ${#ARMS[@]} -eq 0 ]]; then
    echo "usage: $0 <arm> [arm...]" >&2
    exit 2
fi

# PREFLIGHT. A sweep that starts against a dead endpoint resets every arm's
# repaired_v2/ from raw/ and then fails each document, which looks identical to
# a run that legitimately found nothing to do.
if ! curl -sf -m 10 -o /dev/null "${BASE_URL}/models"; then
    echo "ABORT: ${BASE_URL} is not reachable - not touching any arm" >&2
    exit 1
fi

for arm in "${ARMS[@]}"; do
    src="${RESULTS}/${arm}/raw"
    dst="${RESULTS}/${arm}/repaired_v2"
    if [[ ! -d "${src}" ]]; then
        echo "skip ${arm}: no ${src}" >&2
        continue
    fi
    echo "===== ${arm} ====="
    rm -rf "${dst}"
    mkdir -p "${dst}"
    cp "${src}"/*.facts.ttl "${dst}/"
    start=$(date +%s)
    python -u -m art6.ontology.new_repair \
        --facts-dir "${dst}" \
        --input-jsonl "${INPUT_JSONL}" \
        --model "${MODEL}" \
        --base-url "${BASE_URL}" \
        --temperature "${TEMPERATURE}" \
        2>&1 | tee "${RESULTS}/${arm}/new_repair.log"
    rc=${PIPESTATUS[0]}
    echo "${arm} done in $(( $(date +%s) - start ))s (rc=${rc})"
done
