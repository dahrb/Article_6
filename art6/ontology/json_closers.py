"""
json_closers.py
----------------
Rebuild the container closers of a JSON reply whose brackets are mismatched.

WHY THIS SURVIVED ITS ORIGINAL HOME
------------------------------------
This function was the working half of `response_repair.py`, a monkeypatch that
recovered malformed facts-render replies inside OntoCast. That patch is gone:
ontocast v0.6.2 replaced `agent.common.parse_json_markdown` with its own
`parse_json_object`, whose `repair_bracket_kinds` performs the same stack-based
closer rewrite in the FIRST parse attempt rather than as a downstream fallback,
with real error context fed back into retries. Measured across ~800 documents,
the patch recovered 9 replies on ontocast v0.6.1 and 0 on v0.6.2.

But the defect it addressed is a property of the MODEL, not of OntoCast, and
`repair_facts.py` calls that model directly for stage-3 repair patches without
going through ontocast at all -- so nothing upstream covers those calls. The
recovery logic therefore outlives the patch that introduced it.

THE FAILURE SHAPE
-----------------
gemma-4-31b closes the WRONG container rather than truncating: it writes `}`
where an open `triple_operations` array needed `]`.

    ...cd:criminal_proceeding_1 echr:followsProceeding cd:police_custody_event_1 ."
      }
    }                      <-- should have been  ]  }  }

Because the bad bytes are already present, the fix is to REPLACE every closer
with what the container stack demands, not to append missing ones. Validated
against all 6 unrecoverable captures from the 2026-08-19 carry-forward run:
6/6 parse, each restoring the real Turtle the model actually produced.
"""

from __future__ import annotations


def rebuild_closers(body: str) -> str:
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
