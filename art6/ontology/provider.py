"""Extra request-body fields that every model call in the pipeline sends.

Set ART6_EXTRA_BODY to a JSON object and it is merged into every chat
completion request: stage 1, C0, stage 3, and -- through run_native's patch
of langchain's ChatOpenAI -- OntoCast's stage 2. It exists for routing that an
OpenAI-compatible gateway reads from the request body, chiefly OpenRouter's
provider pinning, which keeps a run on one provider and one quantisation:

    ART6_EXTRA_BODY='{"provider": {"order": ["Parasail"], "allow_fallbacks": false,
                      "quantizations": ["fp8"]}, "reasoning": {"enabled": false}}'

Unset (the default) sends nothing extra, so a local vLLM run is unchanged.
"""

from __future__ import annotations

import json
import os

ENV = "ART6_EXTRA_BODY"


def extra_body() -> dict:
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return {}
    value = json.loads(raw)  # an invalid value must fail loudly, not route silently
    if not isinstance(value, dict):
        raise TypeError(f"{ENV} must be a JSON object, got {type(value).__name__}")
    return value
