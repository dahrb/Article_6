#!/usr/bin/env bash
# run_simple.sh OUT_ROOT SOURCE_JSONL RUN...
# The simplified single-call extractor (art6/simple/extract.py), once per RUN
# (a STAGE1_MODEL name from env.sh, e.g. "gemma gemma qwen27"), sequentially so
# the OpenRouter usage delta in OUT_ROOT/cost.log is per run.
set -uo pipefail
OUT=$(readlink -m "$1"); SRC=$(readlink -f "$2"); shift 2
source "$(dirname "$0")/env.sh"
cd "$REPO"; mkdir -p "$OUT"
i=0
for name in "$@"; do
  i=$((i + 1)); d="$OUT/r${i}_$name"
  before=$(openrouter_usage)
  uv run python -m art6.simple.extract --input-jsonl "$SRC" --out-dir "$d" \
    --model "${STAGE1_MODEL[$name]}" --extra-body "${STAGE1_BODY[$name]}" > "$d.log" 2>&1
  after=$(openrouter_usage)
  echo "r${i}_$name $before $after $(python3 -c "print(round($after-$before,4))")" >> "$OUT/cost.log"
  tail -n +1 "$d.log" | tail -3
done
cat "$OUT/cost.log"
