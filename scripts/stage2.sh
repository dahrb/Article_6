#!/usr/bin/env bash
# stage2.sh BUNDLE_JSONL OUT_DIR PROJECT SEED_DIR SHAPES_DIR
# OntoCast (stage 2) over the bridge's bundle, 3 shards, up to 3 attempts for
# documents that failed. Graphs are copied to OUT_DIR/<case_id>.facts.ttl; OntoCast's
# own outputs (bundle.L<n>.*) stay in OUT_DIR/_work/raw, which stage 3 starts from.
set -uo pipefail
source "$(dirname "$0")/env.sh"
IN=$(readlink -f "$1"); OUT=$(readlink -m "$2"); P="$3"; SEED=$(readlink -f "$4"); SH=$(readlink -f "$5")
B64=$(printf '%s' "$FUSEKI_USERPASS" | base64)
for ds in facts ontologies shapes; do
  docker exec "$FUSEKI_CONTAINER" wget -qO- --header="Authorization: Basic $B64" \
    --post-data "dbName=growgraph--$P--$ds&dbType=tdb2" 'http://localhost:3030/$/datasets' >/dev/null 2>&1 || true
done
mkdir -p "$OUT"; W="$OUT/_work"; rm -rf "$W"
(
  set -a; source <(grep -vE '^(LLM_API_KEY|FUSEKI_AUTH)=' "$REPO/ontology/ontology_vllm.env"); set +a
  source "$(dirname "$0")/env.sh"
  export LLM_MODEL_NAME="$MODEL" LLM_BASE_URL="$BASE_URL" LLM_TEMPERATURE=0.4
  export LLM_GRAPH_FORMAT=jsonld LLM_OUTPUT_LAYOUT=free LLM_MAX_INFLIGHT=4 CHUNK_SECTION_CLASSIFIER=off \
         CHUNK_MIN_SIZE=40000 CHUNK_MAX_SIZE=45000 ONTOLOGY_CONTEXT_FIXED_ONTOLOGY_ID=echr \
         ONTOCAST_ONTOLOGY_DIRECTORY="$SEED" FACTS_SHAPES_DIR="$SH" FACTS_SHACL_INFERENCE=rdfs \
         FACTS_SHACL_ADVANCED=true FACTS_SHACL_AUTOFIX=prune FACTS_SHACL_AUTOFIX_PASSES=1
  cd "$ONTOCAST_REPO"
  T="$REPO/art6/ontology/stage2_shards.py"
  for attempt in 0 1 2; do
    todo=$(python3 "$T" missing --input "$IN" --raw-dir "$W/raw"); [[ -z "$todo" ]] && break
    root="$W/a$attempt"
    mapfile -t sds < <(python3 "$T" plan --input "$IN" --raw-dir "$W/raw" --shards 3 --root "$root")
    for sd in "${sds[@]}"; do
      env -u VIRTUAL_ENV PYTHONPATH="$REPO" uv run python -m art6.ontology.run_native --input-path "$sd/$(basename "$IN")" \
        --tenant growgraph --project "$P" --output-dir "$sd/raw" --max-visits 1 --report "$sd/rep.json" --allow-unit-loss \
        > "$sd/out.log" 2>&1 &
    done; wait
    python3 "$T" merge --input "$IN" --raw-dir "$W/raw" --root "$root" >/dev/null
  done
)
n=0; while IFS= read -r line; do n=$((n+1)); [[ -z "${line// }" ]] && continue
  cid=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["case_id"])' "$line")
  f="$W/raw/$(basename "$IN" .jsonl).L$n.facts.ttl"; [[ -f "$f" ]] && cp "$f" "$OUT/$cid.facts.ttl" || echo "MISSING $cid"
done < "$IN"
echo "stage 2: $(ls "$OUT"/*.facts.ttl 2>/dev/null | wc -l) graph(s) in $OUT"
