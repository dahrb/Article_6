#!/usr/bin/env bash
# run_pipeline.sh OUT_DIR SOURCE_JSONL
#
# The pipeline on a JSONL of judgments ({"case_id", "text"} per line; Facts only):
#   1  stage 1: one extraction call per document (art6/stage1/extract.py,
#      prompts/extract.txt), once per entry in RUNS, in sequence so the
#      OpenRouter usage per run is in OUT/cost.log            -> OUT/r<i>_<name>
#   1b vote across the runs (first is the base)              -> OUT/vote
#   1c the voted documents as stage-1 payloads                 -> OUT/s1
#   2  bridge + OntoCast with Gemma                          -> OUT/in/bundle.jsonl, OUT/g2
#   3  reconcile: transcription, minting, SHACL loop         -> OUT/rep, OUT/g3
# Every step resumes: stage 1 skips documents already written, stage 3 skips
# graphs already reconciled, so a rerun after a stall finishes the job.
# Score:  uv run python -m art6.evaluation.score vote=OUT/vote kg=OUT/g3
#
# Environment:
#   RUNS      stage-1 runs, names from env.sh (default "gemma qwen27 deepseek luna")
#   STEPS     which steps to run (default "1 2 3"; the vote runs with step 1)
#   MIN_RUNS  runs that must have an event for the vote to keep it (default 2)
# Needs keys.env and, for step 2, the Fuseki container (see README).
set -uo pipefail
OUT=$(readlink -m "$1"); SRC=$(readlink -f "$2")
source "$(dirname "$0")/env.sh"
read -ra RUNS <<< "${RUNS:-gemma qwen27 deepseek luna}"; STEPS=" ${STEPS:-1 2 3} "; MIN_RUNS=${MIN_RUNS:-2}
for name in "${RUNS[@]}"; do
  [[ -n "${STAGE1_MODEL[$name]:-}" ]] || { echo "unknown run '$name' (known: ${!STAGE1_MODEL[*]})" >&2; exit 2; }
done
mkdir -p "$OUT"; cd "$REPO"
# Inputs are the Facts only: a Law section would hand the models the Court's reasoning.
law=$(python3 -c 'import json,re,sys
for l in open(sys.argv[1], encoding="utf-8"):
    if l.strip() and re.search(r"(?m)^#*\s*(?:AS TO )?THE LAW\s*$", json.loads(l)["text"]):
        print(json.loads(l)["case_id"])' "$SRC")
[[ -z "$law" ]] || { echo "input has a Law section (re-cut the Facts): $law" >&2; exit 2; }
log() { echo "$(date +%T) $*" | tee -a "$OUT/run.log"; }
cost() { echo "$1 $(openrouter_usage) $(date +%T)" >> "$OUT/cost.log"; }
cost start

if [[ "$STEPS" == *" 1 "* ]]; then
  dirs=()
  for i in "${!RUNS[@]}"; do
    name=${RUNS[$i]}; d="$OUT/r$((i + 1))_$name"; dirs+=("$d")
    uv run python -m art6.stage1.extract --input-jsonl "$SRC" --out-dir "$d" \
      --model "${STAGE1_MODEL[$name]}" --extra-body "${STAGE1_BODY[$name]}" > "$d.log" 2>&1
    log "stage 1 $(basename "$d"): $(ls "$d"/*.json 2>/dev/null | wc -l) document(s)"
    cost "r$((i + 1))_$name"
  done
  if (( ${#dirs[@]} > 1 )); then
    uv run python -m art6.stage1.vote --out "$OUT/vote" --min-runs "$MIN_RUNS" "${dirs[@]}" | tee -a "$OUT/run.log"
  else
    rm -rf "$OUT/vote"; cp -r "${dirs[0]}" "$OUT/vote"
  fi
  uv run python -m art6.stage1.to_payload "$OUT/vote" "$SRC" "$OUT/s1" | tee -a "$OUT/run.log"
fi

if [[ "$STEPS" == *" 2 "* ]]; then
  mkdir -p "$OUT/ontology_seed" "$OUT/shapes"
  cp ontology/echr.ttl "$OUT/ontology_seed/"; cp ontology/echr-shapes.ttl "$OUT/shapes/"
  uv run python -m art6.ontology.bridge "$OUT/s1" "$SRC" "$OUT/in/bundle.jsonl" | tee -a "$OUT/run.log"
  bash "$(dirname "$0")/stage2.sh" "$OUT/in/bundle.jsonl" "$OUT/g2" "art6_$(basename "$OUT")" \
    "$OUT/ontology_seed" "$OUT/shapes" > "$OUT/s2.log" 2>&1
  log "$(tail -1 "$OUT/s2.log")"
  grep -h '^THINNED' "$OUT/s2.log" | while read -r line; do log "$line"; done
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
