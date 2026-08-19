#!/usr/bin/env bash
#
# Start the OntoCast API server against this project's ontology.env.
#
#   ./art6/ontology/serve.sh                  # start the server
#   ./art6/ontology/serve.sh --tenant art6 --project domestic
#
# Any arguments are passed straight through to `ontocast serve`.
#
# The env files are SOURCED rather than passed to `uv run --env-file`, because
# ontology.env contains LLM_API_KEY=${OPENAI_API_KEY} and uv does not expand
# ${VAR} - it substitutes an empty string, which surfaces much later as a
# provider auth error. Sourcing keys.env first makes the expansion work.
#
# Override any of these from the caller if your layout differs:
#   ONTOCAST_REPO   checkout providing the `ontocast` CLI
#   ENV_FILE        OntoCast configuration
#   KEYS_FILE       secrets (OPENAI_API_KEY, ...)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

ONTOCAST_REPO="${ONTOCAST_REPO:-$(cd "${REPO_ROOT}/../.." && pwd)/ontocast}"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/ontology/ontology.env}"
KEYS_FILE="${KEYS_FILE:-${REPO_ROOT}/keys.env}"

die() { printf 'error: %s\n' "$1" >&2; exit 1; }

[[ -f "${KEYS_FILE}" ]] || die "keys file not found: ${KEYS_FILE}"
[[ -f "${ENV_FILE}"  ]] || die "env file not found: ${ENV_FILE}"
[[ -d "${ONTOCAST_REPO}" ]] || die "ontocast checkout not found: ${ONTOCAST_REPO}"

# Secrets first, so ${OPENAI_API_KEY} in the env file resolves.
set -a
# shellcheck disable=SC1090
source "${KEYS_FILE}"
[[ -n "${OPENAI_API_KEY:-}" ]] || die "OPENAI_API_KEY is not set in ${KEYS_FILE}"
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

[[ -n "${LLM_API_KEY:-}" ]] || die "LLM_API_KEY resolved empty - check ${ENV_FILE}"

PORT="${PORT:-8999}"
if ss -ltn 2>/dev/null | grep -q ":${PORT}\b"; then
    die "port ${PORT} is already in use - is a server already running?"
fi

# Fuseki is optional (OntoCast falls back to in-memory pyoxigraph), but a
# configured-yet-unreachable store fails at startup, so say so up front.
if [[ -n "${FUSEKI_URI:-}" ]]; then
    if curl -fsS -m 5 -o /dev/null "${FUSEKI_URI}/\$/ping" 2>/dev/null; then
        printf 'fuseki:   %s (reachable)\n' "${FUSEKI_URI}"
    else
        printf 'warning:  %s is configured but not answering - start it with\n' "${FUSEKI_URI}" >&2
        printf '          docker start ontocast-fuseki\n' >&2
    fi
fi

printf 'ontocast: %s\n' "${ONTOCAST_REPO}"
printf 'env:      %s\n' "${ENV_FILE}"
printf 'model:    %s (%s)\n' "${LLM_MODEL_NAME:-?}" "${LLM_PROVIDER:-?}"
printf 'mode:     render=%s ontology_context=%s\n' \
    "${RENDER_MODE:-?}" "${ONTOLOGY_CONTEXT_MODE:-?}"
printf 'serving:  http://127.0.0.1:%s/docs\n\n' "${PORT}"

# VIRTUAL_ENV is unset so uv resolves ontocast's own project environment. If it
# leaks through from an activated venv of this project, uv warns and ignores it
# anyway - but do NOT "fix" that by adding --active, which would run in this
# project's venv, where the ontocast CLI is not installed.
cd "${ONTOCAST_REPO}"
exec env -u VIRTUAL_ENV uv run ontocast serve "$@"
