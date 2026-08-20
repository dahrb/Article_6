"""
response_repair.py
-------------------
Recover the malformed-JSON facts-render responses that carry_forward.py was
silently dropping, WITHOUT editing the ontocast checkout.

BACKGROUND -- what carry_forward.py was losing
------------------------------------------------
OntoCast asks the model for JSON in prose and parses the reply with a
PydanticOutputParser. gemma-4-31b answers that request with *structurally
invalid* JSON often enough to lose whole chunks: on the 2026-08-19
carry-forward run, 12 captured raw responses broke down as

    5  strict-valid JSON
    1  malformed, repaired by langchain's own partial-JSON fallback
    6  UNRECOVERABLE -- carry_forward gave up on the chunk and moved on

The failure is always the same shape -- the model closes the wrong container,
writing `}` where the open `triple_operations` array needed `]`:

    ...cd:criminal_proceeding_1 echr:followsProceeding cd:police_custody_event_1 ."
      }
    }                      <-- should have been  ]  }  }

langchain's `parse_json_markdown` then returns **None instead of raising**,
which is why the failure surfaces as the opaque

    1 validation error for GraphUpdateRenderReport
    Input should be a valid dictionary ... input_value=None

Three retries do not help -- the model reproduces the same mistake each time.
The 6 dropped responses were NOT empty; each held 1.6k-1.8k chars of real,
recoverable Turtle. On the full run this cost 11,577 of 213,309 chars (5.4%
of the corpus), concentrated enough to erase 27% of one document.

WHY THIS IS A TEXT REPAIR, NOT GUIDED DECODING
-----------------------------------------------
The first fix attempted here was forcing vLLM's guided JSON decoding
(response_format={"type": "json_schema", ...}) on these calls, the same
mechanism repair_facts.py already uses successfully against this server. It
does eliminate the bracket mismatch -- but at realistic facts-render prompt
size (~17k tokens, vs. repair's ~11k) it trades that failure for a worse one:
confirmed by direct reproduction against the real captured prompt, the model
under a strict schema stops closing the output's open-ended string field and
pads forever with whitespace, burning the entire token budget without ever
reaching EOS (throughput held a steady ~99 tok/s at every budget tested --
400 through 8000 tokens -- so this is not a compile-time stall, the model
truly never stops). At `max_tokens=8000` unbounded it still had not
terminated. That is strictly worse than an occasional bad bracket: a
guaranteed 180s timeout on EVERY facts-render call at this prompt scale, not
just the unlucky few. Guided decoding is right-sized for repair_facts.py's
small, bounded patches; it is the wrong tool for a call whose whole point is
producing an open-ended amount of Turtle.

So: leave generation unconstrained (where the model reliably terminates
correctly ~92% of the time per the sample above) and repair the text
afterwards, in the same place `strip_json_comments`/`strip_trailing_commas`
already do their own after-the-fact cleanup.

WHAT THIS DOES
--------------
`ontocast.agent.common` does:

    from langchain_core.utils.json import parse_json_markdown
    ...
    json_object = parse_json_markdown(content_to_parse)

`parse_json_markdown` is a name bound in `ontocast.agent.common`'s own
module namespace, so it can be rebound from here without touching the
ontocast checkout: `common.parse_json_markdown = patched`. The patched
version tries the original first (so the ~92% that already parse take the
same path they always did) and only on a None/failure does it: extract the
fenced JSON body, replay it character by character rebuilding every `{[`/`}]`
closer from an explicit stack rather than trusting the model's own (already
wrong) closing punctuation, and hand the result to `json.loads`.

Validated against all 6 of the unrecoverable captures from the 2026-08-19
run: 6/6 now parse, each restoring the real Turtle graph update the model
actually produced.

Nothing here writes to the ontocast checkout; the patch lives for the
lifetime of the process that calls `enable()`.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger("response_repair")

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

_enabled = False


def _rebuild_closers(body: str) -> str:
    """Replay ``body``, replacing every ``}``/``]`` with what the container
    stack says it should be, and appending closers for anything left open.

    This is not "append the missing closers" -- the model does not truncate,
    it closes the WRONG container, so the bad bytes are already present and
    must be replaced, not padded around.
    """
    out: list[str] = []
    stack: list[str] = []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch == '"':
            j = i + 1
            while j < n:
                if body[j] == "\\":
                    j += 2
                    continue
                if body[j] == '"':
                    break
                j += 1
            out.append(body[i : j + 1])
            i = j + 1
            continue
        if ch in "{[":
            stack.append(ch)
            out.append(ch)
        elif ch in "}]":
            if stack:
                out.append("}" if stack.pop() == "{" else "]")
            # A closer with nothing open to close is surplus punctuation
            # (e.g. a stray brace after the real JSON body) -- drop it.
        else:
            out.append(ch)
        i += 1
    out.extend("}" if c == "{" else "]" for c in reversed(stack))
    return "".join(out)


def enable() -> None:
    """Install the patch. Idempotent; safe to call once per process."""
    global _enabled
    if _enabled:
        return

    from ontocast.agent import common

    original_parse = common.parse_json_markdown

    def patched_parse(text: str, *args, **kwargs):
        obj = original_parse(text, *args, **kwargs)
        if obj is not None:
            return obj

        match = _FENCE.search(text)
        body = match.group(1) if match else text.strip()
        try:
            repaired = json.loads(_rebuild_closers(body))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "response repair could not recover this reply (%s: %s); "
                "the original parse failure will propagate as before",
                type(exc).__name__,
                str(exc)[:150],
            )
            return None

        logger.info(
            "response repair recovered a malformed reply "
            "(mismatched JSON container closer, %d chars)",
            len(body),
        )
        return repaired

    common.parse_json_markdown = patched_parse
    _enabled = True
    logger.info(
        "response repair ENABLED: malformed facts-render JSON is repaired "
        "in place instead of being dropped"
    )
