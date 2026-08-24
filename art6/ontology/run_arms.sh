#!/usr/bin/env bash
#
# ============================================================================
# Single-model ARM sweep: one configuration axis varied per arm.
# ============================================================================
#
#   ./art6/ontology/run_arms.sh                 # every arm in ARMS
#   ./art6/ontology/run_arms.sh --dry-run       # preflight only, run nothing
#   LIMIT=1 ./art6/ontology/run_arms.sh         # smoke test, 1 case per arm
#   ARMS=nochunk_ttl_mv1 ./art6/ontology/run_arms.sh
#
# WHY THIS EXISTS, SEPARATELY FROM run_experiment.sh
# ---------------------------------------------------
# `run_experiment.sh` is a 4-model x 1-configuration driver: the only intended
# variable is the LLM, and it has no knobs for chunk size, max visits or
# assembly mode. The 2026-08-20 three-arm experiment was therefore run by an
# ad-hoc script that was never committed and no longer exists on disk, which
# makes that experiment unreproducible. This script is the committed
# replacement: ONE model, MANY arms, each varying exactly one axis.
#
# It also fixes three things that would silently corrupt an arm comparison:
#
#   1. run_experiment.sh routes extraction through run_data.sh, which `exec`s
#      `ontocast process` as a SUBPROCESS -- so response_repair/turtle_repair
#      never load. This script calls run_native.py / carry_forward.py, both of
#      which run in-process with the patches installed.
#   2. data/art6_domestic_test_set.jsonl carries a STALE embedded copy of the
#      facts prompt (1,783 chars against prompts/facts.txt's current 5,401).
#      The JSONL is rebuilt from the .json plus the live prompt every run.
#   3. Every arm gets its OWN Fuseki project, created fresh here. This is not
#      tidiness -- OntoCast's `_synchronize_ontologies` syncs a seed ontology
#      from disk only when its IRI is ABSENT from the triple store, matching on
#      IRI and never on version. An existing project that was populated under
#      echr.ttl 3.3.0 would therefore keep serving 3.3.0 forever. A fresh,
#      empty dataset is what guarantees the run actually sees 3.5.0.
#
# ARM AXES
# --------
# mode          native  -> run_native.py  (OntoCast fan-out + aggregator)
#               rolling -> carry_forward.py (sequential, graph carried forward)
#               "nochunk" is NOT a mode -- it is `native` with chunk sizes
#               larger than any document, giving one content unit per document.
# chunk_min/max CHUNK_MIN_SIZE / CHUNK_MAX_SIZE
# max_visits    MAX_VISITS (env for rolling; --max-visits flag for native)
# graph_format  LLM_GRAPH_FORMAT (turtle | jsonld)
#
# TIMING. Every phase is wall-clocked into <arm>/timings.json, and the LLM
# response cache is FORCED OFF (LLM_CACHE_ENABLED=false). Three of the nine
# rows in the 2026-08-20 cost table were unusable because they were partly
# cache-served; a cache hit replays the model's own bytes, so quality survives
# but no timing or call-count conclusion does.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ONTOCAST_REPO="${ONTOCAST_REPO:-$(cd "${REPO_ROOT}/../.." && pwd)/ontocast}"

# ============================================================================
# EDIT ME
# ============================================================================

# key|mode|chunk_min|chunk_max|max_visits|graph_format
#
# STATUS: the 2026-08-23 defaults are PENDING RE-DECISION on the JURIX config
# pilot (docs/jurix_plan.md, "The O2 configuration is settled by a pilot").
# They were chosen on connectivity, conformance and cost, with NO recall
# measure involved -- and recall is the axis that actually varies. On the same
# ten documents, counted 2026-08-24:
#
#            events  persons  quote-verbatim  dup rate
#   nochunk ttl          76       15              94%      1.3%
#   nochunk jsonld       58       10             100%      3.4%
#   rolling 3k/6k       159       42              96%      4.4%
#
# turtle found 31% more events and 50% more persons than jsonld at nochunk,
# and the chunk size deprioritised as expensive found 2x the events of either.
# Precision is near-saturated everywhere (94-100% quote fidelity, 1-6%
# duplicates) while recall spans 58->159, so the earlier ranking optimised the
# flat axis. Do not treat the notes below as settled until the pilot re-runs.
#
# WHAT THE 2026-08-23 SWEEP DID SETTLE, and still stands:
#   max_visits  1. mv2 cost +54% wall clock at nochunk and ended at the same
#               conformance with one MORE violation after repair. Nothing to
#               recover: both arms lost zero units. NOT a pilot axis.
#
# WHAT IT DID NOT SETTLE, now crossed in the pilot:
#   format      jsonld was preferred on graph connectivity (1 proceeding
#               without a court against turtle's 19) and conformance. But
#               jsonld costs 2.5x turtle's tokens for the same graph (57,374
#               vs 22,897, unpruned ontology), and in `rolling` mode the
#               carried graph is re-injected every prompt -- so format decides
#               how much context is left for document text. That interaction
#               cannot exist at nochunk, where the graph is never fed back,
#               which is why format is crossed with assembly rather than fixed.
#   chunk       8000/16000 was 41% cheaper to extract, 61% cheaper to repair
#               and halved SHACL violations against 3000/6000 -- all precision
#               and cost measures. 3000/6000 found 2x the events. Both stay in.
#
# THE EIGHT PILOT ARMS: 4 assembly modes x 2 graph formats, max_visits fixed
# at 1. Selection is on RECALL at the raw/ checkpoint, with precision at
# repaired/ as a constraint (duplicate rate < 10%, quote-verbatim within 3
# points of the best arm) -- never as the objective. Ranking these by
# precision selects all the whole-document arms and excludes every chunked
# one, i.e. it excludes exactly the arms with 2-3x the recall.
ARM_SPECS=(
    "pilot_nochunk_ttl|native|20000|50000|1|turtle"
    "pilot_nochunk_jsonld|native|20000|50000|1|jsonld"
    "pilot_fanout_8k16k_ttl|native|8000|16000|1|turtle"
    "pilot_fanout_8k16k_jsonld|native|8000|16000|1|jsonld"
    "pilot_rolling_8k16k_ttl|rolling|8000|16000|1|turtle"
    "pilot_rolling_8k16k_jsonld|rolling|8000|16000|1|jsonld"
    "pilot_rolling_3k6k_ttl|rolling|3000|6000|1|turtle"
    "pilot_rolling_3k6k_jsonld|rolling|3000|6000|1|jsonld"

    # The 2026-08-23 sweep, kept verbatim so it stays re-runnable.
    "nochunk_jsonld_mv1|native|20000|50000|1|jsonld"
    "rolling_8k16k_jsonld_mv1|rolling|8000|16000|1|jsonld"
    "nochunk_ttl_mv1|native|20000|50000|1|turtle"
    "nochunk_ttl_mv2|native|20000|50000|2|turtle"
    "rolling_3k6k_mv1|rolling|3000|6000|1|turtle"
    "rolling_8k16k_mv1|rolling|8000|16000|1|turtle"
)

# Default is the eight-arm config pilot. Until it runs there is no settled
# configuration to default to -- naming one would re-assert the very choice
# the pilot exists to re-decide. ARMS="..." names any subset.
ARMS="${ARMS:-pilot_nochunk_ttl pilot_nochunk_jsonld pilot_fanout_8k16k_ttl pilot_fanout_8k16k_jsonld pilot_rolling_8k16k_ttl pilot_rolling_8k16k_jsonld pilot_rolling_3k6k_ttl pilot_rolling_3k6k_jsonld}"

# The single model every arm uses. Arms vary the pipeline, never the LLM.
MODEL_NAME="${MODEL_NAME:-gemma-4-31b}"
BASE_URL="${BASE_URL:-http://localhost:8000/v1}"
PROJECT_BASE="${PROJECT_BASE:-art6_gemma4}"

# 0.4, not 1.0: at 1.0 gemma-4-31b showed wide response variance on an
# IDENTICAL repair prompt (2026-08-20, 5 draws -- a real 9-op answer, a real
# 1-op answer, an empty decline, and a runaway generation that never closed).
TEMPERATURE="${TEMPERATURE:-0.4}"

# Repair passes PER STAGE. repair_facts.py runs FOUR staged calls (authorities /
# proceedings / persons / quotes), so --passes N costs up to 4N calls per
# document, not N. run_experiment.sh's default of 4 would mean a ceiling of 16
# calls/document; 2 keeps the worst case at 8. The loop stops early on a clean
# stage or a no-op pass, so this is a ceiling rather than a fixed cost.
REPAIR_PASSES="${REPAIR_PASSES:-2}"

# Arm that additionally gets a `repaired_legacy/` tree built by the repair
# implementation at git HEAD, for a like-for-like comparison against the staged
# repair from identical raw/ input. Empty disables the comparison entirely.
LEGACY_REPAIR_ARM="${LEGACY_REPAIR_ARM-nochunk_ttl_mv1}"

# Records from the top of the test set. Empty means ALL (10 cases).
LIMIT="${LIMIT-}"

EXPERIMENT_DIR="${EXPERIMENT_DIR:-${REPO_ROOT}/results/experiment_arms_$(date +%Y%m%d_%H%M%S)}"

# Appended to every arm's Fuseki project name. MUST differ between runs that
# should not see each other's triples: doc_iri is a hash of the document, so a
# re-run writes into the SAME named graph and the aggregation step reads back
# the previous run's output. Set it for smoke tests (PROJECT_SUFFIX=_smoke) so
# they cannot contaminate the real sweep.
PROJECT_SUFFIX="${PROJECT_SUFFIX:-}"

VLLM_API_KEY="${VLLM_API_KEY:-token-abc123}"
ONTOLOGY_TTL="${ONTOLOGY_TTL:-${REPO_ROOT}/ontology/echr.ttl}"
BASE_ENV_FILE="${BASE_ENV_FILE:-${REPO_ROOT}/ontology/ontology_vllm.env}"

FUSEKI_URI="${FUSEKI_URI:-http://localhost:3032}"
FUSEKI_USERPASS="${FUSEKI_USERPASS:-admin:test345}"
FUSEKI_CONTAINER="${FUSEKI_CONTAINER:-ontocast-fuseki}"

# ============================================================================
# END EDIT ME
# ============================================================================

TEST_SET_JSON="${REPO_ROOT}/data/art6_domestic_test_set.json"
FACTS_PROMPT_FILE="${SCRIPT_DIR}/prompts/facts.txt"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1 && shift

die() { printf 'error: %s\n' "$1" >&2; exit 1; }
now() { date +%s.%N; }
since() { python3 -c "print(round(float('$2') - float('$1'), 1))"; }

spec_for() {
    local want="$1" spec
    for spec in "${ARM_SPECS[@]}"; do
        [[ "${spec%%|*}" == "${want}" ]] && { printf '%s' "${spec}"; return 0; }
    done
    return 1
}

fuseki_ds_ok() {
    local ds="$1" code
    code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -u "${FUSEKI_USERPASS}" \
        -G --data-urlencode 'query=ASK{}' "${FUSEKI_URI}/${ds}/sparql" </dev/null || true)
    [[ "${code}" == "200" ]]
}

# Fuseki's admin API refuses non-localhost calls (the container's shiro.ini),
# and "localhost" means inside the container -- so dataset creation has to go
# through docker exec, not through the published port.
fuseki_create_ds() {
    local ds="$1" auth
    auth=$(printf '%s' "${FUSEKI_USERPASS}" | base64)
    docker exec "${FUSEKI_CONTAINER}" wget -q -O - \
        --header="Authorization: Basic ${auth}" \
        --post-data "dbName=${ds}&dbType=tdb2" \
        'http://localhost:3030/$/datasets' >/dev/null 2>&1 || true
}

# --- preflight -------------------------------------------------------------

printf '=== preflight ===\n'

[[ -f "${BASE_ENV_FILE}"     ]] || die "base env not found: ${BASE_ENV_FILE}"
[[ -f "${ONTOLOGY_TTL}"      ]] || die "ontology not found: ${ONTOLOGY_TTL}"
[[ -f "${TEST_SET_JSON}"     ]] || die "test set not found: ${TEST_SET_JSON}"
[[ -f "${FACTS_PROMPT_FILE}" ]] || die "facts prompt not found: ${FACTS_PROMPT_FILE}"
[[ -d "${ONTOCAST_REPO}"     ]] || die "ontocast checkout not found: ${ONTOCAST_REPO}"
[[ -f "${SCRIPT_DIR}/run_native.py"    ]] || die "run_native.py missing"
[[ -f "${SCRIPT_DIR}/carry_forward.py" ]] || die "carry_forward.py missing"

served=$(curl -s -m 10 "${BASE_URL}/models" \
    | python3 -c 'import json,sys; print(" ".join(m["id"] for m in json.load(sys.stdin)["data"]))' 2>/dev/null || true)
[[ -n "${served}" ]] || die "${BASE_URL} is not answering"
[[ " ${served} " == *" ${MODEL_NAME} "* ]] \
    || die "${BASE_URL} serves '${served}', not '${MODEL_NAME}'"
printf '  model:    %s @ %s (reachable)\n' "${MODEL_NAME}" "${BASE_URL}"

curl -fsS -m 5 -o /dev/null "${FUSEKI_URI}/\$/ping" 2>/dev/null \
    || die "${FUSEKI_URI} is not answering - start it: docker start ${FUSEKI_CONTAINER}"

for arm in ${ARMS}; do
    spec="$(spec_for "${arm}")" || die "unknown arm key: ${arm}"
    project="${PROJECT_BASE}_${arm}${PROJECT_SUFFIX}"
    for suffix in facts ontologies; do
        ds="growgraph--${project}--${suffix}"
        if fuseki_ds_ok "${ds}"; then
            printf '  fuseki:   %s (exists)\n' "${ds}"
        else
            if (( DRY_RUN )); then
                printf '  fuseki:   %s (WOULD CREATE)\n' "${ds}"
            else
                fuseki_create_ds "${ds}"
                fuseki_ds_ok "${ds}" || die "could not create Fuseki dataset ${ds}"
                printf '  fuseki:   %s (created)\n' "${ds}"
            fi
        fi
    done
done

ontology_version=$(grep -m1 'owl:versionInfo' "${ONTOLOGY_TTL}" | sed 's/.*"\(.*\)".*/\1/')
printf '  ontology: %s @ %s\n' "${ONTOLOGY_TTL#${REPO_ROOT}/}" "${ontology_version}"
printf '  output:   %s\n' "${EXPERIMENT_DIR}"
printf '  limit:    %s\n' "${LIMIT:-all 10 cases}"
printf '  arms:     %s\n' "${ARMS}"
printf '  repair:   --passes %s (per stage, 3 stages)\n' "${REPAIR_PASSES}"
printf '  legacy repair A/B on: %s\n\n' "${LEGACY_REPAIR_ARM:-<none>}"

if (( DRY_RUN )); then
    printf 'dry run - nothing executed\n'
    exit 0
fi

mkdir -p "${EXPERIMENT_DIR}"
cp "${ONTOLOGY_TTL}" "${EXPERIMENT_DIR}/echr.ttl.snapshot"
cp "${FACTS_PROMPT_FILE}" "${EXPERIMENT_DIR}/facts.prompt.snapshot"
cp "${SCRIPT_DIR}/prompts/repair_system_prompt.txt" "${EXPERIMENT_DIR}/repair_system_prompt.snapshot"

# Rebuild the JSONL from the .json plus the LIVE facts prompt. The checked-in
# data/art6_domestic_test_set.jsonl embeds a stale prompt and must never be
# used directly -- see the header note.
INPUT_JSONL="${EXPERIMENT_DIR}/input.jsonl"
LIMIT="${LIMIT}" \
INPUT_JSON="${TEST_SET_JSON}" \
INPUT_JSONL="${INPUT_JSONL}" \
FACTS_PROMPT_FILE="${FACTS_PROMPT_FILE}" \
python3 - <<'PY'
import json, os, pathlib

src = os.environ["INPUT_JSON"]
dst = os.environ["INPUT_JSONL"]
limit = os.environ.get("LIMIT", "").strip()
prompt = pathlib.Path(os.environ["FACTS_PROMPT_FILE"]).read_text(encoding="utf-8").strip()

records = json.load(open(src, encoding="utf-8"))
if limit:
    records = records[: int(limit)]

with open(dst, "w", encoding="utf-8") as handle:
    for record in records:
        handle.write(
            json.dumps({**record, "facts_user_instruction": prompt}, ensure_ascii=False)
            + "\n"
        )

print(f"input:   {dst}")
print(f"records: {len(records)}")
print(f"prompt:  {len(prompt):,} chars from {os.environ['FACTS_PROMPT_FILE']}")
for i, record in enumerate(records, start=1):
    print(f"  L{i}: {record.get('case_id', '?'):<12} {len(record.get('text', '')):>7,} chars")
PY

python3 - <<PY > "${EXPERIMENT_DIR}/manifest.json"
import json, subprocess, datetime
specs = {}
for spec in """$(printf '%s\n' "${ARM_SPECS[@]}")""".strip().splitlines():
    key, mode, cmin, cmax, mv, fmt = spec.strip().split("|")
    specs[key] = {
        "mode": mode, "chunk_min_size": int(cmin), "chunk_max_size": int(cmax),
        "max_visits": int(mv), "llm_graph_format": fmt,
    }
print(json.dumps({
    "started": datetime.datetime.now().isoformat(timespec="seconds"),
    "arms_run": "${ARMS}".split(),
    "arm_specs": specs,
    "limit": "${LIMIT}" or "all",
    "model": "${MODEL_NAME}",
    "base_url": "${BASE_URL}",
    "temperature": float("${TEMPERATURE}"),
    "repair_passes_per_stage": int("${REPAIR_PASSES}"),
    "legacy_repair_arm": "${LEGACY_REPAIR_ARM}" or None,
    "held_constant": {
        "ontology": "${ONTOLOGY_TTL} (snapshot alongside)",
        "ontology_version": "${ontology_version}",
        "facts_prompt": "art6/ontology/prompts/facts.txt (snapshot alongside)",
        "chunk_section_classifier": "off",
        "ontology_context_mode": "fixed_single_ontology",
        "ontology_context_fixed_ontology_id": "echr",
        "render_mode": "facts",
        "llm_cache_enabled": False,
        "response_repair": True,
        "turtle_repair": True,
    },
    "git_head": subprocess.run(["git","rev-parse","HEAD"],capture_output=True,text=True,cwd="${REPO_ROOT}").stdout.strip(),
    "git_dirty": bool(subprocess.run(["git","status","--porcelain"],capture_output=True,text=True,cwd="${REPO_ROOT}").stdout.strip()),
}, indent=2))
PY

# --- run -------------------------------------------------------------------

for arm in ${ARMS}; do
    spec="$(spec_for "${arm}")"
    IFS='|' read -r _ mode chunk_min chunk_max max_visits graph_format <<< "${spec}"
    project="${PROJECT_BASE}_${arm}${PROJECT_SUFFIX}"

    arm_dir="${EXPERIMENT_DIR}/${arm}"
    raw_dir="${arm_dir}/raw"
    repaired_dir="${arm_dir}/repaired"
    mkdir -p "${raw_dir}"

    printf '\n############################################################\n'
    printf '# %s  (%s, chunk %s/%s, max_visits %s, %s)\n' \
        "${arm}" "${mode}" "${chunk_min}" "${chunk_max}" "${max_visits}" "${graph_format}"
    printf '############################################################\n'

    # Per-arm env: shared base, then overrides appended. Later assignments win
    # when sourced, and carry_forward.py's own _load_env_file also takes the
    # LAST assignment -- so appending is enough, the base is never edited.
    arm_env="${arm_dir}/arm.env"
    {
        cat "${BASE_ENV_FILE}"
        printf '\n# ---- run_arms.sh overrides for arm %s ----\n' "${arm}"
        printf 'LLM_MODEL_NAME=%s\n' "${MODEL_NAME}"
        printf 'LLM_TEMPERATURE=%s\n' "${TEMPERATURE}"
        printf 'LLM_BASE_URL=%s\n' "${BASE_URL}"
        # A LITERAL key, never ${OPENAI_API_KEY}: carry_forward.py's
        # _load_env_file skips any value containing '${' with a warning, so an
        # interpolated key silently becomes no key at all.
        printf 'LLM_API_KEY=%s\n' "${VLLM_API_KEY}"
        printf 'LLM_MAX_INFLIGHT=4\n'
        # Forced off so wall clock and call counts mean something. See header.
        printf 'LLM_CACHE_ENABLED=false\n'
        printf 'LLM_GRAPH_FORMAT=%s\n' "${graph_format}"
        printf 'CHUNK_SECTION_CLASSIFIER=off\n'
        printf 'CHUNK_MIN_SIZE=%s\n' "${chunk_min}"
        printf 'CHUNK_MAX_SIZE=%s\n' "${chunk_max}"
        printf 'MAX_VISITS=%s\n' "${max_visits}"
        printf 'ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr\n'
    } > "${arm_env}"

    # ---- phase 1: extraction ----
    printf '\n--- phase 1: extraction (%s) -> %s ---\n' "${mode}" "${raw_dir#${REPO_ROOT}/}"
    t_extract_start=$(now)
    set +e
    if [[ "${mode}" == "native" ]]; then
        # run_native.py has no --env-file; the env must be in the environment.
        # Sourced inside a subshell so one arm's settings cannot leak into the
        # next. --allow-unit-loss so a lost unit is RECORDED in the report and
        # the arm still reaches repair, instead of exiting 2 and skipping it.
        (
            set -a; source "${arm_env}"; set +a
            cd "${ONTOCAST_REPO}"
            env -u VIRTUAL_ENV PYTHONPATH="${REPO_ROOT}" \
                uv run python -m art6.ontology.run_native \
                    --input-path "${INPUT_JSONL}" \
                    --tenant growgraph \
                    --project "${project}" \
                    --output-dir "${raw_dir}" \
                    --max-visits "${max_visits}" \
                    --report "${arm_dir}/extract_report.json" \
                    --allow-unit-loss
        ) 2>&1 | tee "${arm_dir}/extract.log"
    else
        (
            cd "${ONTOCAST_REPO}"
            env -u VIRTUAL_ENV PYTHONPATH="${REPO_ROOT}" \
                uv run python "${SCRIPT_DIR}/carry_forward.py" \
                    --input-path "${INPUT_JSONL}" \
                    --output-dir "${raw_dir}" \
                    --env-file "${arm_env}" \
                    --tenant growgraph \
                    --project "${project}" \
                    --chunk-min-size "${chunk_min}" \
                    --chunk-max-size "${chunk_max}" \
                    --report "${arm_dir}/extract_report.json"
        ) 2>&1 | tee "${arm_dir}/extract.log"
    fi
    extract_rc=${PIPESTATUS[0]}
    set -e
    t_extract_end=$(now)
    extract_seconds=$(since "${t_extract_start}" "${t_extract_end}")

    if (( extract_rc != 0 )); then
        printf 'WARNING: %s extraction exited %d\n' "${arm}" "${extract_rc}"
        printf '%s\n' "${extract_rc}" > "${arm_dir}/extract.failed"
    fi

    produced=$(find "${raw_dir}" -maxdepth 1 -name '*.facts.ttl' | wc -l)
    printf '\nphase 1 done: %s facts file(s) in %ss\n' "${produced}" "${extract_seconds}"
    if (( produced == 0 )); then
        printf 'WARNING: %s produced no facts files - skipping repair\n' "${arm}"
        continue
    fi

    # ---- phase 2: staged repair ----
    printf '\n--- phase 2: staged repair -> %s ---\n' "${repaired_dir#${REPO_ROOT}/}"
    rm -rf "${repaired_dir}"
    cp -r "${raw_dir}" "${repaired_dir}"

    t_repair_start=$(now)
    set +e
    # PYTHONUNBUFFERED: stdout through `tee` is a pipe, so Python block-buffers
    # it and the log stays EMPTY until the process exits. On a multi-hour
    # unattended sweep that is the difference between watchable progress and a
    # run you cannot tell apart from a hang.
    (cd "${REPO_ROOT}" && PYTHONUNBUFFERED=1 uv run python -m art6.ontology.repair_facts \
        --facts-dir "${repaired_dir}" \
        --model "${MODEL_NAME}" \
        --base-url "${BASE_URL}" \
        --api-key "${VLLM_API_KEY}" \
        --temperature "${TEMPERATURE}" \
        --input-jsonl "${INPUT_JSONL}" \
        --passes "${REPAIR_PASSES}") 2>&1 | tee "${arm_dir}/repair.log"
    repair_rc=${PIPESTATUS[0]}
    set -e
    t_repair_end=$(now)
    repair_seconds=$(since "${t_repair_start}" "${t_repair_end}")
    if (( repair_rc != 0 )); then
        printf 'WARNING: %s repair exited %d\n' "${arm}" "${repair_rc}"
        printf '%s\n' "${repair_rc}" > "${arm_dir}/repair.failed"
    fi
    printf '\nphase 2 done in %ss\n' "${repair_seconds}"

    # ---- phase 2b: legacy repair, same raw/ input, for the A/B ----
    legacy_seconds="None"
    if [[ "${arm}" == "${LEGACY_REPAIR_ARM}" ]]; then
        legacy_dir="${arm_dir}/repaired_legacy"
        legacy_tree="${arm_dir}/.legacy_worktree"
        printf '\n--- phase 2b: LEGACY repair (git HEAD) -> %s ---\n' "${legacy_dir#${REPO_ROOT}/}"
        rm -rf "${legacy_dir}" "${legacy_tree}"
        cp -r "${raw_dir}" "${legacy_dir}"
        # A worktree at HEAD, rather than stashing: the working tree is never
        # touched, and HEAD's repair_facts.py is self-contained (it still
        # carries its inline SYSTEM_PROMPT, predating the prompt extraction).
        git -C "${REPO_ROOT}" worktree add --detach "${legacy_tree}" HEAD >/dev/null 2>&1 \
            || printf 'WARNING: could not create legacy worktree\n'
        if [[ -d "${legacy_tree}" ]]; then
            t_legacy_start=$(now)
            set +e
            (cd "${legacy_tree}" && PYTHONUNBUFFERED=1 uv run python -m art6.ontology.repair_facts \
                --facts-dir "${legacy_dir}" \
                --model "${MODEL_NAME}" \
                --base-url "${BASE_URL}" \
                --api-key "${VLLM_API_KEY}" \
                --temperature "${TEMPERATURE}" \
                --passes "${REPAIR_PASSES}") 2>&1 | tee "${arm_dir}/repair_legacy.log"
            set -e
            t_legacy_end=$(now)
            legacy_seconds=$(since "${t_legacy_start}" "${t_legacy_end}")
            git -C "${REPO_ROOT}" worktree remove --force "${legacy_tree}" >/dev/null 2>&1 || true
            printf '\nphase 2b done in %ss\n' "${legacy_seconds}"
        fi
    fi

    # ---- phase 3: static SHACL gate ----
    printf '\n--- phase 3: SHACL gate -> %s ---\n' "${repaired_dir#${REPO_ROOT}/}"
    t_validate_start=$(now)
    set +e
    (cd "${REPO_ROOT}" && uv run python -m art6.ontology.validate_shapes \
        --facts-dir "${repaired_dir}") 2>&1 | tee "${arm_dir}/validate.log"
    validate_rc=${PIPESTATUS[0]}
    set -e
    t_validate_end=$(now)
    validate_seconds=$(since "${t_validate_start}" "${t_validate_end}")
    if (( validate_rc != 0 )); then
        printf '%s\n' "${validate_rc}" > "${arm_dir}/validate.failed"
    fi
    printf '\nphase 3 done in %ss\n' "${validate_seconds}"

    # ---- timings ----
    python3 - <<PY > "${arm_dir}/timings.json"
import json
print(json.dumps({
    "arm": "${arm}",
    "mode": "${mode}",
    "chunk_min_size": ${chunk_min},
    "chunk_max_size": ${chunk_max},
    "max_visits": ${max_visits},
    "llm_graph_format": "${graph_format}",
    "documents": ${produced},
    "seconds": {
        "extract": ${extract_seconds},
        "repair_staged": ${repair_seconds},
        "repair_legacy": ${legacy_seconds},
        "validate": ${validate_seconds},
    },
    "seconds_per_document": {
        "extract": round(${extract_seconds} / ${produced}, 1),
        "repair_staged": round(${repair_seconds} / ${produced}, 1),
    },
    "exit_codes": {
        "extract": ${extract_rc},
        "repair": ${repair_rc},
        "validate": ${validate_rc},
    },
    "cache_enabled": False,
}, indent=2))
PY
    printf '\ntimings -> %s\n' "${arm_dir#${REPO_ROOT}/}/timings.json"
done

# --- summary ---------------------------------------------------------------

printf '\n============================================================\n'
printf 'arm sweep complete: %s\n' "${EXPERIMENT_DIR}"
printf '============================================================\n'
printf '%-22s %-5s %-5s %-9s %-9s %s\n' arm raw rep extract_s repair_s flags
for arm in ${ARMS}; do
    d="${EXPERIMENT_DIR}/${arm}"
    [[ -d "${d}" ]] || continue
    raw=$(find "${d}/raw" -maxdepth 1 -name '*.facts.ttl' 2>/dev/null | wc -l)
    rep=$(find "${d}/repaired" -maxdepth 1 -name '*.facts.ttl' 2>/dev/null | wc -l)
    es=$(python3 -c "import json;print(json.load(open('${d}/timings.json'))['seconds']['extract'])" 2>/dev/null || echo '?')
    rs=$(python3 -c "import json;print(json.load(open('${d}/timings.json'))['seconds']['repair_staged'])" 2>/dev/null || echo '?')
    flags=""
    [[ -f "${d}/extract.failed"  ]] && flags="${flags} EXTRACT_FAILED"
    [[ -f "${d}/repair.failed"   ]] && flags="${flags} REPAIR_FAILED"
    [[ -f "${d}/validate.failed" ]] && flags="${flags} VALIDATE_FAILED"
    printf '%-22s %-5s %-5s %-9s %-9s%s\n' "${arm}" "${raw}" "${rep}" "${es}" "${rs}" "${flags}"
done
