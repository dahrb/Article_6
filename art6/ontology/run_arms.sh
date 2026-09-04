#!/usr/bin/env bash
#
# ============================================================================
# Single-model ARM sweep: one configuration axis varied per arm.
# ============================================================================
#
#   ./art6/ontology/run_arms.sh                 # every arm in ARMS
#   ./art6/ontology/run_arms.sh --dry-run       # preflight only, run nothing
#   LIMIT=1 ./art6/ontology/run_arms.sh         # smoke test, 1 case per arm
#   ARMS=o2_large_jsonld ./art6/ontology/run_arms.sh
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
#      `ontocast process` as a SUBPROCESS, so it produces no run report. This
#      script calls run_native.py / carry_forward.py, which run in-process and
#      record per-document unit loss.
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
# PHASE 1 PILOT (docs/jurix_plan.md §3). Ten arms, gemma only, max_visits 1:
# five assembly configurations fully crossed with two serialisations.
#
#   o2_large / o2_med / o2_low   the three configurations the MAIN STUDY runs,
#                                all fan-out (`native`) so they differ only in
#                                chunk size. o2_large is whole-document.
#   x turtle / jsonld            the serialisation ablation, crossed with all
#                                three sizes because the format effect is not
#                                assumed constant across them.
#   o2_cf_med / o2_cf_low        the carry-forward ablation, each matched to
#                                the fan-out arm at the SAME chunk size and the
#                                same format. That pairing is the whole point:
#                                without it, rolling's recall confounds "smaller
#                                units help" with "seeing the prior graph
#                                helps", and those imply different fixes.
#                                Crossed with format like every other arm. The
#                                format effect is expected to be LARGEST here:
#                                in carry-forward the accumulated graph is
#                                re-injected into every subsequent prompt, so
#                                serialisation decides how much context is left
#                                for document text -- an interaction that cannot
#                                exist at whole-document, where the graph is
#                                never fed back.
#
# Selection is on RECALL at the raw/ checkpoint, with precision at repaired/ as
# a CONSTRAINT (duplicate rate < 10%, quote-verbatim within 3 points of the
# best arm) -- never as the objective. Ranking these by precision selects every
# whole-document arm and excludes every chunked one, i.e. it excludes exactly
# the arms with 2-3x the recall.
#
# PHASE 1 PILOT RESOLVED THE SERIALISATION AXIS (docs/phase1_pilot_report.md,
# 2026-08-24): jsonld beat turtle on body coverage in 4/5 matched pairs and on
# quote fidelity in 5/5, with recall a wash. turtle arms are REMOVED, not just
# deprioritised -- there is no plan to re-run them. jsonld is the only format
# from here on.
ARM_SPECS=(
    "o2_large_jsonld|native|20000|50000|1|jsonld"
    "o2_med_jsonld|native|8000|16000|1|jsonld"
    "o2_low_jsonld|native|3000|6000|1|jsonld"
    "o2_cf_med_jsonld|rolling|8000|16000|1|jsonld"
    "o2_cf_low_jsonld|rolling|3000|6000|1|jsonld"
)

# The five jsonld-only configurations from the phase 1 pilot. ARMS="..."
# names any subset.
ARMS="${ARMS:-o2_large_jsonld o2_med_jsonld o2_low_jsonld o2_cf_med_jsonld o2_cf_low_jsonld}"

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
LEGACY_REPAIR_ARM="${LEGACY_REPAIR_ARM-o2_large_jsonld}"

# Records from the top of the test set. Empty means ALL (10 cases).
LIMIT="${LIMIT-}"

EXPERIMENT_DIR="${EXPERIMENT_DIR:-${REPO_ROOT}/results/experiment_arms_$(date +%Y%m%d_%H%M%S)}"
# Absolutised immediately, because extraction runs with cwd set to
# ONTOCAST_REPO and every path handed to run_native.py / carry_forward.py
# resolves THERE. A relative EXPERIMENT_DIR sends --input-path into the
# ontocast checkout, where the file does not exist -- and the failure is
# silent: OntoCast finds no documents, prints "complete: all 0 document(s)
# kept every unit", and the arm ends in one second with an empty raw/.
# Observed 2026-08-24 with EXPERIMENT_DIR=results/jurix_phase1, which wrote
# three arm directories into ~/Projects/ontocast before it was caught. The
# built-in default is absolute, which is why an override was needed to expose
# this.
mkdir -p "${EXPERIMENT_DIR}"
EXPERIMENT_DIR="$(cd "${EXPERIMENT_DIR}" && pwd)"

# Appended to every arm's Fuseki project name. MUST differ between runs that
# should not see each other's triples: doc_iri is a hash of the document, so a
# re-run writes into the SAME named graph and the aggregation step reads back
# the previous run's output. Set it for smoke tests (PROJECT_SUFFIX=_smoke) so
# they cannot contaminate the real sweep.
PROJECT_SUFFIX="${PROJECT_SUFFIX:-}"

# Credential for the model endpoint. The local vLLM servers do not check it, so
# the placeholder is fine there; a hosted endpoint (api.openai.com) needs a real
# key. Prefer sourcing it rather than pasting it on the command line:
#   set -a; source keys.env; set +a; API_KEY="${OPENAI_API_KEY}" ./run_arms.sh
# keys.env is gitignored. VLLM_API_KEY is kept as the older spelling.
VLLM_API_KEY="${VLLM_API_KEY:-token-abc123}"
API_KEY="${API_KEY:-${VLLM_API_KEY}}"
ONTOLOGY_TTL="${ONTOLOGY_TTL:-${REPO_ROOT}/ontology/echr.ttl}"
# SHACL shapes handed to OntoCast's OWN validation gate (FACTS_SHAPES_DIR).
# Empty disables the gate and restores the pre-2026-08-25 behaviour, where
# every validation.json reported shacl_evaluated:null -- which reads as
# "clean" and actually means "never checked".
SHAPES_TTL="${SHAPES_TTL:-${REPO_ROOT}/ontology/echr-shapes.ttl}"
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

# OntoCast's seed scanner loads EVERY *.ttl in ONTOCAST_ONTOLOGY_DIRECTORY as a
# candidate ontology. A shapes file has no owl:Ontology declaration, so it syncs
# under a null IRI ("Cannot add ontology without valid IRI"), and the next fetch
# fails with "Fetched 1 of 2 ontology graphs; 1 failed. The catalog is
# incomplete" -- which discards the WHOLE assembled ontology context, silently.
# This script therefore stages the ontology and the shapes into two separate
# single-file directories rather than pointing either at ontology/.
#
# The damage also persists: those null-IRI graphs are content-addressed and stay
# in the Fuseki ontologies dataset across runs, because the fetch step lists
# every named graph in the dataset rather than only the current seed set. So a
# project polluted once stays broken until the graphs are deleted by URI, which
# is what this does.
#
# Deletion goes through python3, not curl: the graph URIs contain '#', which
# curl treats as a fragment delimiter and strips, so the DELETE lands on the
# wrong URI and 404s while looking like it worked.
fuseki_purge_foreign_ontology_graphs() {
    local ds="$1"
    FUSEKI_URI="${FUSEKI_URI}" FUSEKI_USERPASS="${FUSEKI_USERPASS}" DS="${ds}" \
    python3 - <<'PYPURGE'
import base64, json, os, urllib.error, urllib.parse, urllib.request

base = os.environ["FUSEKI_URI"].rstrip("/")
dataset = os.environ["DS"]
auth = base64.b64encode(os.environ["FUSEKI_USERPASS"].encode()).decode()
headers = {"Authorization": f"Basic {auth}"}


def request(url, method="GET", extra_headers=None):
    req = urllib.request.Request(
        url, method=method, headers={**headers, **(extra_headers or {})}
    )
    return urllib.request.urlopen(req, timeout=15)


query = urllib.parse.urlencode(
    {"query": "SELECT DISTINCT ?g WHERE { GRAPH ?g { ?s ?p ?o } }"}
)
try:
    with request(
        f"{base}/{dataset}/sparql?{query}",
        extra_headers={"Accept": "application/sparql-results+json"},
    ) as response:
        rows = json.load(response)["results"]["bindings"]
except Exception as error:  # dataset may be brand new and empty
    print(f"  fuseki:   {dataset} (could not list graphs: {error})")
    raise SystemExit(0)

# Keep only graphs belonging to the echr catalog; anything else in an
# ONTOLOGIES dataset got there by the seed-scanner accident above.
stale = [r["g"]["value"] for r in rows if "/echr#" not in r["g"]["value"]]
for graph in stale:
    target = f"{base}/{dataset}/data?{urllib.parse.urlencode({'graph': graph})}"
    try:
        with request(target, method="DELETE") as response:
            print(f"  fuseki:   purged stale ontology graph {graph} ({response.status})")
    except urllib.error.HTTPError as error:
        print(f"  fuseki:   FAILED to purge {graph}: {error.code}")
if not stale:
    print(f"  fuseki:   {dataset} (no stale ontology graphs)")
PYPURGE
}

# --- preflight -------------------------------------------------------------

printf '=== preflight ===\n'

[[ -f "${BASE_ENV_FILE}"     ]] || die "base env not found: ${BASE_ENV_FILE}"
[[ -f "${ONTOLOGY_TTL}"      ]] || die "ontology not found: ${ONTOLOGY_TTL}"
if [[ -n "${SHAPES_TTL}" ]]; then
    [[ -f "${SHAPES_TTL}" ]] || die "shapes not found: ${SHAPES_TTL}"
    # pyshacl lives in the ONTOCAST venv, not ours: the gate runs inside
    # ontocast. Without the extra it logs a warning and reports nothing, which
    # is the same silent "clean" this change exists to remove.
    (cd "${ONTOCAST_REPO}" && env -u VIRTUAL_ENV uv run python -c 'import pyshacl' 2>/dev/null) \
        || die "ontocast venv lacks pyshacl - run: (cd ${ONTOCAST_REPO} && uv sync --extra shacl)"
fi
[[ -f "${TEST_SET_JSON}"     ]] || die "test set not found: ${TEST_SET_JSON}"
[[ -f "${FACTS_PROMPT_FILE}" ]] || die "facts prompt not found: ${FACTS_PROMPT_FILE}"
[[ -d "${ONTOCAST_REPO}"     ]] || die "ontocast checkout not found: ${ONTOCAST_REPO}"
[[ -f "${SCRIPT_DIR}/run_native.py"    ]] || die "run_native.py missing"
[[ -f "${SCRIPT_DIR}/carry_forward.py" ]] || die "carry_forward.py missing"

# Authorization is sent unconditionally: vLLM ignores it, a hosted endpoint
# requires it. Without the header this check 401s against api.openai.com and
# reports it as "not answering", which is a confusing way to say "no key".
served=$(curl -s -m 15 -H "Authorization: Bearer ${API_KEY}" "${BASE_URL}/models" \
    | python3 -c 'import json,sys; print(" ".join(m["id"] for m in json.load(sys.stdin)["data"]))' 2>/dev/null || true)
[[ -n "${served}" ]] || die "${BASE_URL} is not answering (or the API key was rejected)"
# Only the first few ids are echoed: a hosted endpoint lists ~100 models and
# dumping all of them buries the actual error.
[[ " ${served} " == *" ${MODEL_NAME} "* ]] \
    || die "${BASE_URL} does not serve '${MODEL_NAME}' (it serves $(printf '%s' "${served}" | cut -d' ' -f1-6) ...)"
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
        if [[ "${suffix}" == "ontologies" && -n "${SHAPES_TTL}" ]]; then
            fuseki_purge_foreign_ontology_graphs "${ds}"
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

# Two SINGLE-FILE staging directories, because OntoCast globs *.ttl in each.
# Pointing ONTOCAST_ONTOLOGY_DIRECTORY at ontology/ silently destroys the whole
# ontology context (see fuseki_purge_foreign_ontology_graphs above). Staged
# under EXPERIMENT_DIR so the run records exactly which ontology and which
# shapes it used.
ONTOLOGY_SEED_DIR="${EXPERIMENT_DIR}/ontology_seed"
SHAPES_DIR="${EXPERIMENT_DIR}/shapes"
rm -rf "${ONTOLOGY_SEED_DIR}" "${SHAPES_DIR}"
mkdir -p "${ONTOLOGY_SEED_DIR}"
cp "${ONTOLOGY_TTL}" "${ONTOLOGY_SEED_DIR}/"
if [[ -n "${SHAPES_TTL}" ]]; then
    mkdir -p "${SHAPES_DIR}"
    cp "${SHAPES_TTL}" "${SHAPES_DIR}/"
    printf '  shapes:   %s -> OntoCast facts gate\n' "${SHAPES_TTL#${REPO_ROOT}/}"
else
    printf '  shapes:   <disabled> (OntoCast gate reports no SHACL)\n'
fi
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
        "shacl_shapes": "${SHAPES_TTL}" or None,
        "shacl_gate": "ontocast facts gate (FACTS_SHAPES_DIR)" if "${SHAPES_TTL}" else "disabled",
        "shacl_autofix": "prune" if "${SHAPES_TTL}" else None,
        "facts_prompt": "art6/ontology/prompts/facts.txt (snapshot alongside)",
        "chunk_section_classifier": "off",
        "ontology_context_mode": "fixed_single_ontology",
        "ontology_context_fixed_ontology_id": "echr",
        "render_mode": "facts",
        "llm_cache_enabled": False,
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
    # CREDENTIALS ARE NEVER WRITTEN INTO A GENERATED FILE. They are read from
    # keys.env into this shell and inherited by both consumers below -- the
    # `source "${arm_env}"` path and `uv run --env-file`, which both add to the
    # process environment rather than replacing it. arm.env lives under
    # results/, a tracked directory, and previously held a literal LLM_API_KEY
    # and FUSEKI_AUTH; only *.env being gitignored kept them out of a public
    # repo. Exported here, stripped from the file below.
    set -a
    eval "$(grep -E '^(LLM_API_KEY|FUSEKI_AUTH)=' "${BASE_ENV_FILE}")"
    set +a
    LLM_API_KEY="${API_KEY:-${LLM_API_KEY:-}}"
    export LLM_API_KEY
    [[ -n "${LLM_API_KEY}" ]] || die "LLM_API_KEY resolved empty - source keys.env first"
    [[ -n "${FUSEKI_AUTH:-}" ]] || die "FUSEKI_AUTH resolved empty - check ${BASE_ENV_FILE}"

    {
        grep -vE '^(LLM_API_KEY|FUSEKI_AUTH|OPENAI_API_KEY|GEMINI_KEY|VLLM_API_KEY)=' \
            "${BASE_ENV_FILE}"
        printf '\n# ---- run_arms.sh overrides for arm %s ----\n' "${arm}"
        printf 'LLM_MODEL_NAME=%s\n' "${MODEL_NAME}"
        printf 'LLM_TEMPERATURE=%s\n' "${TEMPERATURE}"
        printf 'LLM_BASE_URL=%s\n' "${BASE_URL}"
        printf 'LLM_MAX_INFLIGHT=4\n'
        # Forced off so wall clock and call counts mean something. See header.
        printf 'LLM_CACHE_ENABLED=false\n'
        printf 'LLM_GRAPH_FORMAT=%s\n' "${graph_format}"
        printf 'CHUNK_SECTION_CLASSIFIER=off\n'
        printf 'CHUNK_MIN_SIZE=%s\n' "${chunk_min}"
        printf 'CHUNK_MAX_SIZE=%s\n' "${chunk_max}"
        printf 'MAX_VISITS=%s\n' "${max_visits}"
        printf 'ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr\n'
        # Overrides the base env's ontology/ path: that directory also holds
        # echr-shapes.ttl, which the seed scanner would load as a second,
        # null-IRI ontology and thereby discard the whole catalog.
        printf 'ONTOCAST_ONTOLOGY_DIRECTORY=%s\n' "${ONTOLOGY_SEED_DIR}"
        if [[ -n "${SHAPES_TTL}" ]]; then
            # OntoCast's own SHACL gate. autofix stays at its default 'prune',
            # which is LLM-free and deliberately narrow: it retypes literals
            # against sh:datatype, resolves a literal to a catalog IRI, and
            # drops nodes that violate sh:minCount while asserting nothing.
            # Measured 2026-08-25 on L1: 0 repairs applied against 19
            # violations, because every one was MaxCount (two labels, two
            # courts, two dates) or a Class violation on a node that is already
            # an IRI. Choosing which of two labels to discard is judgment, and
            # OntoCast declines it by design -- that is what new_repair.py is
            # for. The gate is enabled to REPORT, not to repair.
            printf 'FACTS_SHAPES_DIR=%s\n' "${SHAPES_DIR}"
            printf 'FACTS_SHACL_INFERENCE=rdfs\n'
            printf 'FACTS_SHACL_ADVANCED=true\n'
            printf 'FACTS_SHACL_AUTOFIX=prune\n'
            printf 'FACTS_SHACL_AUTOFIX_PASSES=1\n'
        fi
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
        --api-key "${API_KEY}" \
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
                --api-key "${API_KEY}" \
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
