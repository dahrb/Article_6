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
