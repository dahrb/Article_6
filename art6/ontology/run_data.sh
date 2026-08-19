#!/usr/bin/env bash
#
# ============================================================================
# Run OntoCast facts extraction over the Art. 6 test set.
# ============================================================================
#
#   ./art6/ontology/run_data.sh              # run with the settings below
#   ./art6/ontology/run_data.sh --dry-run    # build the input, print the command
#
# This drives `ontocast process`, which runs LOCALLY and does NOT need
# serve.sh running. It does need Fuseki up (docker start ontocast-fuseki).
#
# What it does:
#   1. reads INPUT_JSON (a JSON array of {"case_id", "text"} records)
#   2. writes a .jsonl next to it, one record per line, injecting the custom
#      prompts below into every record
#   3. runs `ontocast process` over that .jsonl
#
# Why JSONL: OntoCast turns each LINE of a .jsonl into its own document, and
# names outputs after the line number - `<stem>.L1.facts.ttl`, `<stem>.L1.run.json`,
# `<stem>.L2...`. A plain .json array would be processed as ONE document.
#
# Per record, OntoCast reads only these keys (ontocast/agent/convert_document.py):
#   text                                - the document text. Everything else in
#                                         the object, `case_id` included, is
#                                         ignored as content.
#   facts_user_instruction              - extra guidance for facts extraction
#   ontology_user_instruction           - guidance when extracting ontology terms
#                                         (unused while RENDER_MODE=facts)
#   ontology_selection_user_instruction - guidance when picking a catalog ontology
#   ontology_context_fixed_ontology_id  - pin one ontology, bypassing selection
#
# User instructions SUPPLEMENT the built-in extraction rules (two-namespace
# contract, class-vs-instance, typing). They do not replace them - see
# docs/user_guide/user_instructions.md in the ontocast checkout.
#
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ============================================================================
# EDIT ME
# ============================================================================
#
# Every value below is written as ${NAME:-default}, so you can either edit the
# default here, or override it for one run without touching the file:
#
#   LIMIT=5 ./art6/ontology/run_data.sh
#   LIMIT= OUTPUT_DIR=/tmp/all ./art6/ontology/run_data.sh     # empty = all
#
# ============================================================================

# Input: JSON array of {"case_id": ..., "text": ...} records.
INPUT_JSON="${INPUT_JSON:-${REPO_ROOT}/data/art6_domestic_test_set.json}"

# Where the .ttl and .run.json outputs land.
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/results/$(date +%Y%m%d)_run}"

# Dataset/collection namespace in Fuseki: {TENANT}--{PROJECT}--facts and
# --ontologies. These are CLI-only; OntoCast never reads them from the .env.
# The prior_results/ runs used growgraph / art6.
TENANT="${TENANT:-growgraph}"
PROJECT="${PROJECT:-art6}"

# How many records from the top of INPUT_JSON to process.
#   LIMIT=2   first two records (the two prior_results/ reference cases)
#   LIMIT=    empty means ALL of them - all 10 cases in the test set
# Records are taken in file order, so LIMIT=2 is always L1 and L2.
LIMIT="${LIMIT-2}"

# ---------------------------------------------------------------------------
# CUSTOM PROMPT
# ---------------------------------------------------------------------------
# Prompts live in plain text files under prompts/ so you can edit them freely
# without worrying about shell quoting. Edit the file to change the prompt;
# EMPTY THE FILE (or point the variable at "") to send no instruction at all.
#
# A prompt steers WHAT gets extracted. It supplements the built-in extraction
# rules rather than replacing them, and cannot change IRI or namespace policy.
#
# NOTE: any prompt changes the results. prior_results/ was produced with NO
# user instructions, so set all three to "" to reproduce that baseline.

PROMPT_DIR="${SCRIPT_DIR}/prompts"

# Guidance for facts extraction - the one that matters while RENDER_MODE=facts.
FACTS_PROMPT_FILE="${PROMPT_DIR}/facts.txt"

# Guidance when extracting ontology terms. Unused while RENDER_MODE=facts.
ONTOLOGY_PROMPT_FILE=""

# Guidance when picking a catalog ontology. Only consulted when
# ONTOLOGY_CONTEXT_MODE=selected_single_ontology.
ONTOLOGY_SELECTION_PROMPT_FILE=""

# ============================================================================
# END EDIT ME - machinery below
# ============================================================================

ONTOCAST_REPO="${ONTOCAST_REPO:-$(cd "${REPO_ROOT}/../.." && pwd)/ontocast}"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/ontology/ontology.env}"
KEYS_FILE="${KEYS_FILE:-${REPO_ROOT}/keys.env}"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1 && shift

die() { printf 'error: %s\n' "$1" >&2; exit 1; }

[[ -f "${INPUT_JSON}" ]] || die "input not found: ${INPUT_JSON}"
[[ -f "${KEYS_FILE}"  ]] || die "keys file not found: ${KEYS_FILE}"
[[ -f "${ENV_FILE}"   ]] || die "env file not found: ${ENV_FILE}"
[[ -d "${ONTOCAST_REPO}" ]] || die "ontocast checkout not found: ${ONTOCAST_REPO}"

# Read a prompt file into a variable. A path that is empty, or a file that is
# empty/whitespace-only, means "send no instruction for this stage".
read_prompt() {
    local path="$1"
    [[ -n "${path}" ]] || { printf ''; return 0; }
    [[ -f "${path}" ]] || die "prompt file not found: ${path}"
    cat "${path}"
}

FACTS_PROMPT="$(read_prompt "${FACTS_PROMPT_FILE}")"
ONTOLOGY_PROMPT="$(read_prompt "${ONTOLOGY_PROMPT_FILE}")"
ONTOLOGY_SELECTION_PROMPT="$(read_prompt "${ONTOLOGY_SELECTION_PROMPT_FILE}")"

# Secrets first so ${OPENAI_API_KEY} inside the env file expands. uv's
# --env-file would NOT expand it - see the note in ontology.env.
set -a
# shellcheck disable=SC1090
source "${KEYS_FILE}"
[[ -n "${OPENAI_API_KEY:-}" ]] || die "OPENAI_API_KEY is not set in ${KEYS_FILE}"
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a
[[ -n "${LLM_API_KEY:-}" ]] || die "LLM_API_KEY resolved empty - check ${ENV_FILE}"

# Fuseki must be reachable AND hold the tenant/project datasets. OntoCast tries
# to create them itself, but the container's shiro.ini refuses admin calls from
# outside, so a missing dataset fails the run.
if [[ -n "${FUSEKI_URI:-}" ]]; then
    curl -fsS -m 5 -o /dev/null "${FUSEKI_URI}/\$/ping" 2>/dev/null \
        || die "${FUSEKI_URI} is not answering - start it: docker start ontocast-fuseki"
    # FUSEKI_AUTH is user/password, but curl -u wants user:password.
    fuseki_userpass="${FUSEKI_AUTH/\//:}"
    for suffix in facts ontologies; do
        ds="${TENANT}--${PROJECT}--${suffix}"
        code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -u "${fuseki_userpass}" \
            -G --data-urlencode 'query=ASK{}' "${FUSEKI_URI}/${ds}/sparql" </dev/null || true)
        if [[ "${code}" != "200" ]]; then
            printf 'error: Fuseki dataset %s missing (HTTP %s). Create it with:\n' "${ds}" "${code}" >&2
            printf '  AUTH=$(printf %s | base64)\n' "'${fuseki_userpass}'" >&2
            printf '  docker exec ontocast-fuseki wget -q -O - --header="Authorization: Basic $AUTH" \\\n' >&2
            printf '    --post-data "dbName=%s&dbType=tdb2" '"'"'http://localhost:3030/$/datasets'"'"'\n' "${ds}" >&2
            exit 1
        fi
    done
fi

# Build the JSONL, injecting the prompts into every record. Done in Python so
# the text field keeps its exact JSON escaping.
INPUT_JSONL="${INPUT_JSON%.json}.jsonl"
FACTS_PROMPT="${FACTS_PROMPT}" \
ONTOLOGY_PROMPT="${ONTOLOGY_PROMPT}" \
ONTOLOGY_SELECTION_PROMPT="${ONTOLOGY_SELECTION_PROMPT}" \
LIMIT="${LIMIT}" \
INPUT_JSON="${INPUT_JSON}" \
INPUT_JSONL="${INPUT_JSONL}" \
python3 - <<'PY'
import json, os

src, dst = os.environ["INPUT_JSON"], os.environ["INPUT_JSONL"]
limit = os.environ.get("LIMIT", "").strip()
prompts = {
    "facts_user_instruction": os.environ.get("FACTS_PROMPT", "").strip(),
    "ontology_user_instruction": os.environ.get("ONTOLOGY_PROMPT", "").strip(),
    "ontology_selection_user_instruction": os.environ.get(
        "ONTOLOGY_SELECTION_PROMPT", ""
    ).strip(),
}
prompts = {key: value for key, value in prompts.items() if value}

records = json.load(open(src, encoding="utf-8"))
if limit:
    records = records[: int(limit)]

with open(dst, "w", encoding="utf-8") as handle:
    for record in records:
        handle.write(json.dumps({**record, **prompts}, ensure_ascii=False) + "\n")

print(f"input:    {dst}")
print(f"records:  {len(records)}")
for i, record in enumerate(records, start=1):
    print(f"  L{i}: {record.get('case_id', '?'):<12} {len(record.get('text', '')):>7,} chars")
print("prompts:  " + (", ".join(prompts) if prompts else "none (baseline behaviour)"))
PY

mkdir -p "${OUTPUT_DIR}"

printf '\noutput:   %s\n' "${OUTPUT_DIR}"
printf 'tenant:   %s / %s\n' "${TENANT}" "${PROJECT}"
printf 'model:    %s (%s, temp %s)\n' "${LLM_MODEL_NAME:-?}" "${LLM_PROVIDER:-?}" "${LLM_TEMPERATURE:-?}"
printf 'mode:     render=%s ontology_context=%s max_visits=%s\n\n' \
    "${RENDER_MODE:-?}" "${ONTOLOGY_CONTEXT_MODE:-?}" "${MAX_VISITS:-1}"

if (( DRY_RUN )); then
    printf 'dry run - would execute:\n\n'
    printf '  cd %s && env -u VIRTUAL_ENV uv run ontocast process \\\n' "${ONTOCAST_REPO}"
    printf '    --input-path %s \\\n' "${INPUT_JSONL}"
    printf '    --tenant %s --project %s \\\n' "${TENANT}" "${PROJECT}"
    printf '    --output-dir %s %s\n' "${OUTPUT_DIR}" "$*"
    exit 0
fi

cd "${ONTOCAST_REPO}"
exec env -u VIRTUAL_ENV uv run ontocast process \
    --input-path "${INPUT_JSONL}" \
    --tenant "${TENANT}" \
    --project "${PROJECT}" \
    --output-dir "${OUTPUT_DIR}" \
    "$@"
