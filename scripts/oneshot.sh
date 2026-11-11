#!/usr/bin/env bash
# oneshot.sh OUT_DIR SOURCE_JSONL MODEL_NAME
#
# Baseline: OntoCast alone on the raw judgments, no stage 1 and no stage 3.
# The same ontology, shapes, stage-2 instruction (prompts/facts.txt) and OntoCast
# settings as the pipeline's stage 2; only the input (judgment, not digest) and
# the model differ. MODEL_NAME is a key of STAGE1_MODEL in env.sh (luna, deepseek)
# or "gemma" for the pipeline's own stage-2 model.
#   -> OUT/in/bundle.jsonl, OUT/g (graphs, <case_id>.facts.ttl), run.log, cost.log
set -uo pipefail
OUT=$(readlink -m "$1"); SRC=$(readlink -f "$2"); name="$3"
source "$(dirname "$0")/env.sh"
if [[ "$name" != gemma ]]; then
  [[ -n "${STAGE1_MODEL[$name]:-}" ]] || { echo "unknown model '$name' (known: gemma ${!STAGE1_MODEL[*]})" >&2; exit 2; }
  export STAGE2_MODEL="${STAGE1_MODEL[$name]}" STAGE2_BODY="${STAGE1_BODY[$name]}"
fi
mkdir -p "$OUT/ontology_seed" "$OUT/shapes"; cd "$REPO"
log() { echo "$(date +%T) $*" | tee -a "$OUT/run.log"; }
echo "start $(openrouter_usage) $(date +%T)" >> "$OUT/cost.log"
cp ontology/echr.ttl "$OUT/ontology_seed/"; cp ontology/echr-shapes.ttl "$OUT/shapes/"
uv run python -m art6.ontology.bridge --raw "$SRC" "$OUT/in/bundle.jsonl" | tee -a "$OUT/run.log"
log "OntoCast $(git -C "$ONTOCAST_REPO" describe --tags), model ${STAGE2_MODEL:-$MODEL}"
bash "$(dirname "$0")/stage2.sh" "$OUT/in/bundle.jsonl" "$OUT/g" "art6_$(basename "$OUT")" \
  "$OUT/ontology_seed" "$OUT/shapes" > "$OUT/s2.log" 2>&1
log "$(tail -1 "$OUT/s2.log")"
echo "oneshot $(openrouter_usage) $(date +%T)" >> "$OUT/cost.log"
