#!/usr/bin/env bash
# run_pipeline.sh OUT_DIR SOURCE_JSONL
#
# The whole pipeline on a JSONL of judgments ({"case_id", "text"} per line):
#   1  stage 1, once per entry in RUNS (in parallel)      -> OUT/s1_<i>_<model>
#   1b vote across those runs (skipped for a single run)  -> OUT/s1
#   2  bridge + OntoCast with Gemma                       -> OUT/in/bundle.jsonl, OUT/g2
#   3  reconcile: transcription, minting, SHACL loop      -> OUT/rep, OUT/g3
# Every step resumes: stage 1 skips documents already written, stage 3 skips
# graphs already reconciled, so a rerun after a stall finishes the job.
#
# Environment:
#   RUNS   stage-1 runs, first is the vote's base (default "luna luna deepseek deepseek")
#   STEPS  which steps to run (default "1 2 3"; the vote runs with step 1)
#   MIN_RUNS  runs that must have an event for the vote to keep it (default 2)
# Costs and timings: docs/cost_and_setup.md (gitignored). Needs keys.env and the
# Fuseki container (see README).
set -uo pipefail
OUT=$(readlink -m "$1"); SRC=$(readlink -f "$2")
source "$(dirname "$0")/env.sh"
read -ra RUNS <<< "${RUNS:-luna luna deepseek deepseek}"; STEPS=" ${STEPS:-1 2 3} "; MIN_RUNS=${MIN_RUNS:-2}
for name in "${RUNS[@]}"; do
  [[ -n "${STAGE1_MODEL[$name]:-}" ]] || { echo "unknown run '$name' (known: ${!STAGE1_MODEL[*]})" >&2; exit 2; }
done
mkdir -p "$OUT"; cd "$REPO"
log() { echo "$(date +%T) $*" | tee -a "$OUT/run.log"; }
cost() { echo "$1 $(openrouter_usage) $(date +%T)" >> "$OUT/cost.log"; }
cost start

if [[ "$STEPS" == *" 1 "* ]]; then
  dirs=()
  for i in "${!RUNS[@]}"; do
    name=${RUNS[$i]}; d="$OUT/s1_$((i + 1))_$name"; dirs+=("$d")
    ART6_EXTRA_BODY="${STAGE1_BODY[$name]}" uv run python -m art6.ontology.agentic_stage1 \
      --input-jsonl "$SRC" --out-dir "$d" --model "${STAGE1_MODEL[$name]}" --base-url "$BASE_URL" \
      --temperature 0.0 --critic-rounds 1 --workers 5 --token-budget 3000000 \
      --context-window 98304 --max-tokens 32000 > "$d.log" 2>&1 &
  done
  wait
  for d in "${dirs[@]}"; do log "stage 1 $(basename "$d"): $(ls "$d"/*.compress.json 2>/dev/null | wc -l) document(s)"; done
  if (( ${#dirs[@]} > 1 )); then
    uv run python -m art6.ontology.vote --out "$OUT/s1" --min-runs "$MIN_RUNS" "${dirs[@]}" | tee -a "$OUT/run.log"
  else
    rm -rf "$OUT/s1"; cp -r "${dirs[0]}" "$OUT/s1"
  fi
  cost stage1
fi

if [[ "$STEPS" == *" 2 "* ]]; then
  mkdir -p "$OUT/ontology_seed" "$OUT/shapes"
  cp ontology/echr.ttl "$OUT/ontology_seed/"; cp ontology/echr-shapes.ttl "$OUT/shapes/"
  uv run python -m art6.ontology.bridge "$OUT/s1" "$SRC" "$OUT/in/bundle.jsonl" | tee -a "$OUT/run.log"
  bash "$(dirname "$0")/stage2.sh" "$OUT/in/bundle.jsonl" "$OUT/g2" "art6_$(basename "$OUT")" \
    "$OUT/ontology_seed" "$OUT/shapes" > "$OUT/s2.log" 2>&1
  log "$(tail -1 "$OUT/s2.log")"
  rm -rf "$OUT/rep"; cp -r "$OUT/g2/_work/raw" "$OUT/rep"
  cost stage2
fi

if [[ "$STEPS" == *" 3 "* ]]; then
  uv run python -m art6.ontology.reconcile --facts-dir "$OUT/rep" --input-jsonl "$OUT/in/bundle.jsonl" \
    --stage1-dir "$OUT/s1" --original-jsonl "$SRC" --max-rounds 3 --workers 2 \
    --model "$MODEL" --base-url "$BASE_URL" --temperature 0.0 > "$OUT/s3.log" 2>&1
  log "stage 3 exit $?"
  graphs_by_case "$OUT/in/bundle.jsonl" "$OUT/rep" "$OUT/g3" | tee -a "$OUT/run.log"
  log "stage 3: $(ls "$OUT"/g3/*.facts.ttl 2>/dev/null | wc -l) graph(s) in $OUT/g3"
  cost stage3
fi
