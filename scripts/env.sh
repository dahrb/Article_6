# Sourced by the pipeline scripts. No secrets here: the OpenRouter key is read
# from keys.env (OPENROUTER=...) at run time and never printed.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ONTOCAST_REPO="${ONTOCAST_REPO:-$(cd "$REPO/../.." && pwd)/ontocast}"
_key="$(grep -E '^OPENROUTER=' "$REPO/keys.env" | cut -d= -f2- | tr -d "\"'")"
export OPENAI_API_KEY="$_key" VLLM_API_KEY="$_key" LLM_API_KEY="$_key"
unset _key
export BASE_URL=https://openrouter.ai/api/v1

# Stages 2 and 3: Gemma 4 31B, bf16, on Crusoe only (the evaluated setting).
export MODEL=google/gemma-4-31b-it
export ART6_EXTRA_BODY='{"provider":{"order":["Crusoe"],"allow_fallbacks":false,"quantizations":["bf16"]},"reasoning":{"enabled":false}}'
export LLM_CACHE_ENABLED=false LLM_MAX_RETRIES=10

# Stage-1 models: name -> OpenRouter id, and the request body each needs.
declare -A STAGE1_MODEL STAGE1_BODY
STAGE1_MODEL[luna]=openai/gpt-6-luna
STAGE1_BODY[luna]='{"usage":{"include":true}}'
STAGE1_MODEL[deepseek]=deepseek/deepseek-v4.1-flash-1017
STAGE1_BODY[deepseek]='{"reasoning":{"enabled":false},"usage":{"include":true}}'
# Open-weight models that fit one H100 each, pinned to providers that serve the
# full context our extract prompt needs (~17k-token prompt + judgment + output).
#   qwen27  Qwen3.8-27B dense, fp8/bf16
#   qwen9   Qwen3.5-9B, bf16 (no Qwen 7B/8B is served on OpenRouter)
#   gemma   Gemma 4 31B, bf16/fp8 (Crusoe alone throttles; Novita rejects our length)
# Qwen3-32B: its only full-context endpoint (SiliconFlow) was rate-limited
# upstream for a day (2026-11-10/11), so it is not used.
STAGE1_MODEL[qwen27]=qwen/qwen3.8-27b
STAGE1_BODY[qwen27]='{"provider":{"order":["Chutes","Near AI","DeepInfra"],"allow_fallbacks":false,"quantizations":["fp8","bf16"]},"reasoning":{"enabled":false},"usage":{"include":true}}'
STAGE1_MODEL[qwen9]=qwen/qwen3.5-9b
STAGE1_BODY[qwen9]='{"provider":{"order":["DeepInfra","Parasail"],"allow_fallbacks":false,"quantizations":["bf16"]},"reasoning":{"enabled":false},"usage":{"include":true}}'
STAGE1_MODEL[gemma]=google/gemma-4-31b-it
STAGE1_BODY[gemma]='{"provider":{"order":["Crusoe","Parasail","DeepInfra"],"allow_fallbacks":false,"quantizations":["bf16","fp8"]},"reasoning":{"enabled":false},"usage":{"include":true}}'

# Fuseki (Docker Desktop container ontocast-fuseki on localhost:3032).
FUSEKI_CONTAINER="${FUSEKI_CONTAINER:-ontocast-fuseki}"
FUSEKI_USERPASS="${FUSEKI_USERPASS:-admin:test345}"

# OpenRouter usage so far, in dollars (for per-stage cost logs).
openrouter_usage() {
  uv run --project "$REPO" python - <<'PY'
import json, os, urllib.request
req = urllib.request.Request("https://openrouter.ai/api/v1/credits",
                             headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]})
print(json.load(urllib.request.urlopen(req, timeout=30))["data"]["total_usage"])
PY
}

# graphs_by_case BUNDLE_JSONL RAW_DIR OUT_DIR
# OntoCast names its graphs <bundle>.L<line>.facts.ttl; copy each to
# OUT_DIR/<case_id>.facts.ttl and print MISSING <case_id> for any it lacks.
graphs_by_case() {
  python3 - "$@" <<'PY'
import json, shutil, sys
from pathlib import Path
bundle, raw, out = (Path(a) for a in sys.argv[1:4])
out.mkdir(parents=True, exist_ok=True)
for n, line in enumerate(bundle.read_text(encoding="utf-8").splitlines(), 1):
    if not line.strip():
        continue
    case = json.loads(line)["case_id"]
    f = raw / f"{bundle.stem}.L{n}.facts.ttl"
    if f.is_file():
        shutil.copy(f, out / f"{case}.facts.ttl")
    else:
        print("MISSING", case)
PY
}
