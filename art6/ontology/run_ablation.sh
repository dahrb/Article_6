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

SET_JSON=""; OUT_DIR=""; LIMIT="${LIMIT:-}"; FRESH=""; C4_ONLY=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --set) SET_JSON="$2"; shift 2;;
        --out) OUT_DIR="$2"; shift 2;;
        --limit) LIMIT="$2"; shift 2;;
        --fresh) FRESH=1; shift;;
        # Skip C0 (schema-light baseline) and the "raw" arm (C1/C2, judgment
        # -> graph with no compression) entirely. Those two exist only to
        # produce the ablation CONTRAST against C3/C4 -- a corpus build or a
        # reproducibility replicate cares only about the pipeline's actual
        # output (C4) and gets nothing from paying for C0-C2 as well.
        --c4-only) C4_ONLY=1; shift;;
        *) echo "unknown arg: $1" >&2; exit 2;;
    esac
done
[[ -n "${SET_JSON}" && -n "${OUT_DIR}" ]] || { echo "usage: $0 --set <json> --out <dir>" >&2; exit 2; }

MODEL="${MODEL:-gemma-4-31b}"
BASE_URL="${BASE_URL:-http://localhost:8003/v1}"
TEMPERATURE="${TEMPERATURE:-0.4}"          # stage 2/3; stage 1 is 0.0
# Stage 1 only can run on a different model/endpoint than stage 2/3 -- default
# to MODEL/BASE_URL so this is a no-op unless explicitly overridden. Used to
# compare a stage-1 backbone (e.g. a hosted model) against the stage-2/3 model
# without also changing what extracts and repairs the graph.
STAGE1_MODEL="${STAGE1_MODEL:-${MODEL}}"
STAGE1_BASE_URL="${STAGE1_BASE_URL:-${BASE_URL}}"
# Empty means "the same key as stages 2/3". Resolved at the stage-1 call, not
# here: the key itself is only settled further down (credentials block), and
# defaulting it this early sent stage 1 the local-server placeholder "EMPTY"
# against a hosted endpoint (2026-10-21, 401 on every document).
STAGE1_API_KEY="${STAGE1_API_KEY:-}"
# STAGE 1 is agentic_stage1.py (extract -> plan -> critic -> fix) unless
# STAGE1_IMPL=compress selects the single-call compress.py it replaced. STAGE 3
# is reconcile.py (diff the graph against stage 1, model only for missing
# events) unless REPAIR_IMPL=new_repair selects the old violation loop +
# document review. The old paths stay only so a comparison run can use them.
STAGE1_IMPL="${STAGE1_IMPL:-agentic}"
# Agent switches. Conditional agents (parallel tracks, targeted parties, the
# fixers, event recovery, the SHACL loop) run only when an earlier agent's
# flags or a deterministic check call for them; these turn whole agents off.
CRITIC_ROUNDS="${CRITIC_ROUNDS:-1}"        # stage-1 critic rounds; 0 = no critic
PARTIES="${PARTIES:-1}"                    # 0 = skip the parties pass
SHACL_ROUNDS="${SHACL_ROUNDS:-3}"          # 0 = no SHACL violation agent
FINAL_CRITIC="${FINAL_CRITIC:-1}"          # 0 = no final critic / graph fixer
REPAIR_IMPL="${REPAIR_IMPL:-reconcile}"
case "${STAGE1_IMPL}" in agentic|compress) ;; *) echo "STAGE1_IMPL must be agentic or compress" >&2; exit 2;; esac
case "${REPAIR_IMPL}" in reconcile|new_repair) ;; *) echo "REPAIR_IMPL must be reconcile or new_repair" >&2; exit 2;; esac
# Same extraction prompt for every stage-1 call unless overridden, so a prompt
# comparison across models/arms is guaranteed to run identical wording.
# Defaults: prompts/extract.txt (agentic) or prompts/compress.txt (compress).
COMPRESS_PROMPT_FILE="${COMPRESS_PROMPT_FILE:-}"
PROJECT_BASE="${PROJECT_BASE:-art6_abl_$(basename "${OUT_DIR}")}"
FUSEKI_CONTAINER="${FUSEKI_CONTAINER:-ontocast-fuseki}"
BASE_ENV_FILE="${BASE_ENV_FILE:-${REPO_ROOT}/ontology/ontology_vllm.env}"

# CACHE ON BY DEFAULT, and EXPORTED AT TOP LEVEL so all three stages inherit
# it -- stage 1 and stage 3 run in their own subshells and would not see an
# export made inside stage 2's. One variable, read by OntoCast for stage 2 and
# by art6.ontology.llm_cache for stages 1 and 3.
#
# A cache hit replays the model's own bytes, so graph quality is unaffected and
# a re-run reproduces a result exactly. What it destroys is any measurement OF
# THE MODEL, and there are exactly two:
#
#   * §5.2 run variance -- the same config run twice, counting byte-identical
#     documents. Against a warm cache the second run never calls the model, so
#     it reports zero variance by construction rather than by finding.
#   * timing and cost -- a hit costs milliseconds. Three of the nine rows in
#     the 2026-08-20 cost table were unusable for exactly this reason
#     (run_arms.sh header).
#
# Both of those runs must set LLM_CACHE_ENABLED=false. The manifest records
# which mode ran so neither can be mistaken for the other after the fact.
export LLM_CACHE_ENABLED="${LLM_CACHE_ENABLED:-true}"

die() { echo "ABORT: $*" >&2; exit 1; }
now() { date +%s; }

mkdir -p "${OUT_DIR}"
OUT_DIR="$(cd "${OUT_DIR}" && pwd)"
LOG="${OUT_DIR}/run.log"
exec > >(tee -a "${LOG}") 2>&1
echo "=== ablation: $(basename "${SET_JSON}") -> ${OUT_DIR#${REPO_ROOT}/} @ $(date -Is) ==="

# ---- credentials -----------------------------------------------------------
# From the environment, never from a generated file, and never on a command line
# where `ps` shows them to every user on the machine. An already-exported value
# wins, so a hosted run needs only OPENAI_API_KEY set in the calling shell.
[[ -f "${BASE_ENV_FILE}" ]] || die "base env not found: ${BASE_ENV_FILE}"
set -a; eval "$(grep -E '^(LLM_API_KEY|FUSEKI_AUTH)=' "${BASE_ENV_FILE}")"; set +a
export LLM_API_KEY="${OPENAI_API_KEY:-${VLLM_API_KEY:-${LLM_API_KEY:-EMPTY}}}"
export OPENAI_API_KEY="${LLM_API_KEY}" VLLM_API_KEY="${LLM_API_KEY}"
FUSEKI_AUTH_B64="$(printf '%s' "${FUSEKI_AUTH:-admin:test345}" | tr '/' ':' | base64)"

# Reasoning models (gpt-5, o-series) reject any temperature but their default;
# the Python stages drop the parameter themselves, and this is the same rule for
# OntoCast, which is configured through the environment rather than by us.
STAGE2_TEMPERATURE="${TEMPERATURE}"
case "${MODEL,,}" in gpt-5*|o1*|o3*|o4*) STAGE2_TEMPERATURE=1.0;; esac

# ---- preflight -------------------------------------------------------------
curl -sf -m 10 -o /dev/null -H "Authorization: Bearer ${LLM_API_KEY}" \
    "${BASE_URL}/models" || die "${BASE_URL} unreachable or rejecting the key"
docker exec "${FUSEKI_CONTAINER}" true 2>/dev/null || die "fuseki container ${FUSEKI_CONTAINER} not running"
[[ -f "${SET_JSON}" ]] || die "set not found: ${SET_JSON}"
[[ -d "${ONTOCAST_REPO}" ]] || die "ontocast repo not found: ${ONTOCAST_REPO}"
echo "preflight ok: ${MODEL} @ ${BASE_URL}"

fuseki_ds() {   # create a dataset, idempotent
    docker exec "${FUSEKI_CONTAINER}" wget -qO- \
        --header="Authorization: Basic ${FUSEKI_AUTH_B64}" \
        --post-data "dbName=$1&dbType=tdb2" \
        http://localhost:3030/\$/datasets >/dev/null 2>&1 || true
}

# ---- ontology + shapes, staged so the run records what it used -------------
SEED_DIR="${OUT_DIR}/ontology_seed"; SHAPES_DIR="${OUT_DIR}/shapes"
rm -rf "${SEED_DIR}" "${SHAPES_DIR}"; mkdir -p "${SEED_DIR}" "${SHAPES_DIR}"
cp "${REPO_ROOT}/ontology/echr.ttl" "${SEED_DIR}/"
cp "${REPO_ROOT}/ontology/echr-shapes.ttl" "${SHAPES_DIR}/"
# The stage-2 instruction is facts.txt PLUS the shared definitions of what the
# graph holds (art6/ontology/prompt_spec.py), so the snapshot is the composed
# text actually sent, not facts.txt alone.
(cd "${REPO_ROOT}" && uv run python -c "from art6.ontology.prompt_spec import facts_instruction; print(facts_instruction())") \
    > "${OUT_DIR}/facts.prompt.snapshot"
cp "${SCRIPT_DIR}/prompts/compress.txt" "${OUT_DIR}/compress.prompt.snapshot"
for p in extract plan link tracks critic fix parties applicants reconcile_mint final_critic final_fix; do
    cp "${SCRIPT_DIR}/prompts/${p}.txt" "${OUT_DIR}/${p}.prompt.snapshot"
done
[[ -z "${COMPRESS_PROMPT_FILE}" ]] || cp "${COMPRESS_PROMPT_FILE}" "${OUT_DIR}/stage1_override.prompt.snapshot"
ONTOCAST_VERSION="$(git -C "${ONTOCAST_REPO}" describe --tags --always 2>/dev/null || echo unknown)"
echo "ontocast: ${ONTOCAST_VERSION}   stage 1: ${STAGE1_IMPL}   stage 3: ${REPAIR_IMPL}"

# ---- input: rebuild the JSONL with the LIVE facts prompt -------------------
INPUT_JSONL="${OUT_DIR}/input.jsonl"
LIMIT="${LIMIT}" INPUT_JSON="${SET_JSON}" INPUT_JSONL="${INPUT_JSONL}" \
FACTS_PROMPT_FILE="${OUT_DIR}/facts.prompt.snapshot" python3 - <<'PY'
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
if [[ -n "${C4_ONLY}" ]]; then
    echo; echo "--- C0  (skipped -- --c4-only) ---"
elif done_phase C0; then
    echo; echo "--- C0  (already done, skipping) ---"
else
phase "C0  O1 schema-light"
t0=$(now)
(cd "${REPO_ROOT}" && uv run python -m art6.conditions.run_conditions \
    --condition o1 --input-json "${SET_JSON}" --out-dir "${OUT_DIR}/C0" \
    --model "${MODEL}" --base-url "${BASE_URL}" \
    ${LIMIT:+--limit "${LIMIT}"}) || echo "C0 returned $?"
echo "C0 done in $(( $(now) - t0 ))s"
# Only mark the phase done if every input document actually produced output --
# a dead endpoint mid-run must not get masked as "complete" (this bit us once:
# a partial 239/250 run got marked done and silently skipped on resume).
c0_expect=$(wc -l < "${INPUT_JSONL}")
c0_got=$(ls "${OUT_DIR}/C0"/*.o1.json 2>/dev/null | wc -l)
if [[ "${c0_got}" -eq "${c0_expect}" ]]; then
    mark_phase C0
else
    echo "C0 incomplete: ${c0_got}/${c0_expect} -- not marking done, will resume next run"
fi
fi

# ---- stage 1: compression (C3/C4 input) ------------------------------------
if done_phase stage1; then
    echo; echo "--- stage 1  (already done, skipping) ---"
else
phase "stage 1  ${STAGE1_IMPL} -> evidence bundles"
t0=$(now)
# The key travels in the environment (VLLM_API_KEY, which both stage-1
# entry points read as their default), never on the command line, where `ps`
# would show it to every user on the machine.
(cd "${REPO_ROOT}" && VLLM_API_KEY="${STAGE1_API_KEY:-${VLLM_API_KEY}}" \
    uv run python -m "art6.ontology.$([[ "${STAGE1_IMPL}" == agentic ]] && echo agentic_stage1 || echo compress)" \
    --input-jsonl "${INPUT_JSONL}" --out-dir "${OUT_DIR}/stage1" \
    --model "${STAGE1_MODEL}" --base-url "${STAGE1_BASE_URL}" \
    --temperature 0.0 \
    ${SPLIT_MAX_TOKENS:+--split-max-tokens "${SPLIT_MAX_TOKENS}"} \
    ${COMPRESS_PROMPT_FILE:+--prompt-file "${COMPRESS_PROMPT_FILE}"} \
    $([[ "${STAGE1_IMPL}" == agentic ]] && echo --critic-rounds "${CRITIC_ROUNDS}") \
    $([[ "${STAGE1_IMPL}" == agentic && "${PARTIES}" == 0 ]] && echo --no-parties) \
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
    # ontocast >= v0.6.2 partitions SHACL shapes into their own dataset
    # (FusekiConfig.shapes_dataset, tenant_project_shapes_name) rather than
    # co-locating them in --ontologies -- a shapes document declares its own
    # owl:Ontology header, and catalog discovery would otherwise offer it to
    # the renderer as schema. Without this dataset pre-created, stage 2 fails
    # outright (403 auto-create from a non-localhost origin, then 405 on the
    # shapes SPARQL endpoint) on every document.
    fuseki_ds "growgraph--${project}--shapes"

    # RESUME IS PER PHASE HERE, NOT PER DOCUMENT. run_native names its outputs
    # from the INPUT FILE stem plus line position (input.jsonl -> input.L1 ...),
    # so filtering the input to the undone documents renames every output and
    # renumbers the survivors -- L8 of ten becomes L1 of three and collides with
    # an existing file. Stage 2 is therefore all-or-nothing per arm. Stage 1 and
    # stage 3 resume per document on their own markers, and those are the long
    # phases; a repeated stage 2 costs ~75s per document.
    local stage2_complete=1
    if done_phase "stage2_${label}"; then
        echo; echo "--- ${label} stage 2  (already done, skipping) ---"
    else
    stage2_complete=0
    phase "${label}  stage 2  extraction -> ${dir#${REPO_ROOT}/}/raw"
    local t=$(now)
    (
        set -a; source <(grep -vE '^(LLM_API_KEY|FUSEKI_AUTH)=' "${BASE_ENV_FILE}"); set +a
        export LLM_MODEL_NAME="${MODEL}" LLM_BASE_URL="${BASE_URL}" LLM_TEMPERATURE="${STAGE2_TEMPERATURE}"
        # LLM_OUTPUT_LAYOUT=free keeps the pre-0.6.7 response layout: 0.6.7
        # made `compact` (minified JSON) the default, which changes what the
        # renderer is asked to write. One change at a time.
        export LLM_GRAPH_FORMAT=jsonld LLM_OUTPUT_LAYOUT=free LLM_MAX_INFLIGHT=4
        # LLM_CACHE_ENABLED is exported at the top of this script so stages 1
        # and 3 inherit it too; OntoCast reads it here for stage 2.
        # CHUNKING APPLIES TO BOTH ARMS -- this block is inside run_arm(), so
        # "raw" and "comp" get identical settings and the compression contrast
        # stays a contrast about compression. Changing it on one arm only would
        # confound C1/C3 and C2/C4 with a stage-2 configuration difference.
        #
        # 10k/25k from 2026-09-06 -- the base env file's calibrated 20k/50k,
        # halved. CHUNK_MIN_SIZE is the lever that matters: it is the size at
        # which a chunk is closed, which is why setting it above any document
        # length (the old 200k) disabled chunking altogether. CHUNK_MAX_SIZE is
        # a ceiling and does not bind here, since the largest digest in the
        # 250-case corpus is 20,360 chars.
        #
        # Stage 2 was losing 26.6% of stage 1's events on the eight documents
        # whose digest exceeds 15k chars (3.9% corpus wide -- see
        # stage_boundary.json). NOT truncation: those graphs end on a complete
        # triple and produced 4-5k output tokens where other documents in the
        # same run produced up to 20k. It is coverage decay across a ~110k-token
        # prompt, so a bigger output cap would do nothing and a smaller unit is
        # the actual remedy.
        #
        # This is NOT the configuration the 2026-09-02 check found "clearly
        # worse" (docs/ontocast_v062_chunk_check.md). That arm ran 20k/50k WITH
        # CHUNK_SECTION_CLASSIFIER=heuristic, and its damage was attributed to
        # the classifier isolating markdown headings into standalone units that
        # rendered zero triples. The classifier stays off here. That makes this
        # an untested configuration rather than a known-bad one -- validate on
        # the 10-doc test/holdout sets before committing a full corpus to it.
        export CHUNK_SECTION_CLASSIFIER=off
        export CHUNK_MIN_SIZE="${CHUNK_MIN_SIZE:-10000}" \
               CHUNK_MAX_SIZE="${CHUNK_MAX_SIZE:-25000}"
        export ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr
        export ONTOCAST_ONTOLOGY_DIRECTORY="${SEED_DIR}"
        export FACTS_SHAPES_DIR="${SHAPES_DIR}" FACTS_SHACL_INFERENCE=rdfs \
               FACTS_SHACL_ADVANCED=true FACTS_SHACL_AUTOFIX=prune FACTS_SHACL_AUTOFIX_PASSES=1
        cd "${ONTOCAST_REPO}"
        env -u VIRTUAL_ENV PYTHONPATH="${REPO_ROOT}" \
            uv run python -m art6.ontology.run_native \
                --input-path "${input}" --tenant growgraph --project "${project}" \
                --output-dir "${dir}/raw" --max-visits "${MAX_VISITS:-1}" \
                --report "${dir}/extract_report.json" --allow-unit-loss
    ) || echo "${label} stage 2 returned $?"
    echo "${label} stage 2 done in $(( $(now) - t ))s"
    # Same guard as C0: only mark done if every input document actually got a
    # facts.ttl out of it -- a dead endpoint mid-arm must not be masked as
    # "complete" (stage 2 is all-or-nothing per arm, see note above, so a
    # false mark_phase here would strand the arm at whatever it reached).
    s2_expect=$(wc -l < "${input}")
    s2_got=$(ls "${dir}/raw"/*.facts.ttl 2>/dev/null | wc -l)
    if [[ "${s2_got}" -eq "${s2_expect}" ]]; then
        mark_phase "stage2_${label}"
        stage2_complete=1
    else
        echo "${label} stage 2 incomplete: ${s2_got}/${s2_expect} -- not marking done, will resume next run"
    fi
    fi

    # Repair on an incomplete stage 2 is worse than useless: it burns real time
    # retrying against whatever killed stage 2 in the first place (usually the
    # same dead endpoint), can mark documents "repaired" (a .newrepair.json
    # written even though every call inside it failed) that a later resume
    # would then skip, and lets the script reach "done" and write a manifest
    # for an arm that never actually finished. Measured 2026-08-31: stage 2
    # gave up at 25/250 when the endpoint died, and stage 3 immediately started
    # anyway, spending several minutes on APIConnectionError retries against
    # documents whose siblings didn't exist yet.
    if [[ "${stage2_complete}" -ne 1 ]]; then
        echo; echo "--- ${label} stage 3  (skipped -- stage 2 incomplete) ---"
        return
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
    if [[ "${REPAIR_IMPL}" == reconcile ]]; then
    # RECONCILE AGAINST STAGE 1, ON BOTH ARMS. Each graph is diffed against
    # the stage-1 payload for its case (joined on case_id through the arm's
    # own input file) and repaired only where they disagree; the model is
    # called only for stage-1 events the graph has no node for. On the raw
    # arm this imports stage 1's decisions into C2 -- read C1 vs C2 as "what
    # reconciling with the evidence stage adds", not as an evidence-free
    # repair. --original-jsonl is the judgment, for verifying minted quotes.
    (cd "${REPO_ROOT}" && PYTHONUNBUFFERED=1 uv run python -m art6.ontology.reconcile \
        --facts-dir "${dir}/repaired" --input-jsonl "${input}" \
        --stage1-dir "${OUT_DIR}/stage1" --original-jsonl "${INPUT_JSONL}" \
        --max-rounds "${SHACL_ROUNDS}" $([[ "${FINAL_CRITIC}" == 0 ]] && echo --no-final-critic) \
        --model "${MODEL}" --base-url "${BASE_URL}" \
        --temperature 0.0) || echo "${label} stage 3 returned $?"
    else
    # --original-jsonl is ALWAYS the raw judgment file, on both arms. Only
    # stage 2 differs between arms: "raw" extracts from the judgment, "comp"
    # from the digest. Review is the same stage in both and reads the same
    # judgment in both, or the compressed arm's review can never recover what
    # compression dropped -- which is the one thing a document-reading
    # backstop is for. --input-jsonl still names the arm's own file because
    # stage 2 named its outputs after it (bundles.jsonl -> bundles.L194);
    # new_repair joins the two on case_id.
    (cd "${REPO_ROOT}" && PYTHONUNBUFFERED=1 uv run python -m art6.ontology.new_repair \
        --facts-dir "${dir}/repaired" --input-jsonl "${input}" \
        --original-jsonl "${INPUT_JSONL}" \
        --model "${MODEL}" --base-url "${BASE_URL}" \
        --temperature 0.0) || echo "${label} stage 3 returned $?"
    fi
    echo "${label} stage 3 done in $(( $(now) - t ))s"
}

[[ -n "${C4_ONLY}" ]] || run_arm "raw"  "${INPUT_JSONL}"          "${OUT_DIR}/C1_C2"
run_arm "comp" "${OUT_DIR}/bundles.jsonl" "${OUT_DIR}/C3_C4"

# Stage 1 states an event inventory; this is the only place on the pipeline
# where recall has an exact denominator and needs neither gold nor a model
# call. NON-GATING on purpose -- it is an instrument, and a run that loses
# events should still finish and leave its output for inspection. Add
# --fail-under once a run has a baseline worth holding itself to.
phase "boundary check  stage 1 inventory -> stage 2 graphs"
(cd "${REPO_ROOT}" && uv run python -m art6.ontology.check_stage_boundary \
    --run-dir "${OUT_DIR}" --out "${OUT_DIR}/stage_boundary.json") \
    || echo "boundary check returned $? (non-gating)"

phase "done"
cat > "${OUT_DIR}/manifest.json" <<JSON
{
  "set": "${SET_JSON}",
  "finished": "$(date -Is)",
  "model": "${MODEL}",
  "stage1_model": "${STAGE1_MODEL}",
  "stage1_base_url": "${STAGE1_BASE_URL}",
  "compress_prompt_file": "${COMPRESS_PROMPT_FILE:-<default for stage 1 impl>}",
  "stage1_impl": "${STAGE1_IMPL}",
  "stage3_impl": "${REPAIR_IMPL}",
  "agents": {"critic_rounds": ${CRITIC_ROUNDS}, "parties": ${PARTIES}, "shacl_rounds": ${SHACL_ROUNDS}, "final_critic": ${FINAL_CRITIC}},
  "ontocast_version": "${ONTOCAST_VERSION}",
  "llm_output_layout": "free",
  "base_url": "${BASE_URL}",
  "temperature_stage2_3": ${STAGE2_TEMPERATURE},
  "temperature_stage1": 0.0,
  "llm_cache_enabled": ${LLM_CACHE_ENABLED:-true},
  "_llm_cache_note": "true means any of the three stages may have been served from cache: the graphs are still the model's own bytes, but timings and call counts from this run mean nothing and it cannot be used as a §5.2 variance repeat. Set LLM_CACHE_ENABLED=false for either of those measurements.",
  "chunk_min_size": ${CHUNK_MIN_SIZE:-10000},
  "chunk_max_size": ${CHUNK_MAX_SIZE:-25000},
  "chunk_section_classifier": "off",
  "c4_only": $([[ -n "${C4_ONLY}" ]] && echo true || echo false),
  "conditions": {
$([[ -n "${C4_ONLY}" ]] || cat <<'COND'
    "C0": "C0/",
    "C1": "C1_C2/raw/",
    "C2": "C1_C2/repaired/",
COND
)
    "C3": "C3_C4/raw/",
    "C4": "C3_C4/repaired/"
  }
}
JSON
echo "manifest -> ${OUT_DIR#${REPO_ROOT}/}/manifest.json"
