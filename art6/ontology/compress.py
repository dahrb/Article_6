"""Stage 1: evidence selection.

Reads a judgment, returns verbatim spans grouped into event bundles, with
character offsets attached DETERMINISTICALLY here rather than asked of the model.

Asking an LLM for character offsets does not work -- they cannot count -- so the
model emits text only and this module locates each span in the source. That does
two jobs at once: it produces exact offsets, and it PROVES the span is verbatim.
A span that cannot be located is not silently trusted; it is dropped and counted.

The result is a stage-1 output that is verified by construction: every surviving
value is a substring of the source at a known offset. Stage 2 never sees the raw
judgment, so it cannot introduce content that did not pass through here.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import os
import re
import threading
import time
import unicodedata
import urllib.request
from pathlib import Path

from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT = Path(__file__).resolve().parent / "prompts" / "compress.txt"
PARTIES_PROMPT = Path(__file__).resolve().parent / "prompts" / "parties.txt"
APPLICANTS_PROMPT = Path(__file__).resolve().parent / "prompts" / "applicants.txt"

# How much of the document the applicants call reads. The description formula
# ("is a Ukrainian national who was born in 1979 and lives in Kiev") sits in the
# opening of the facts: across the pilot corpus its latest occurrence starts at
# character 6,202, so 8,000 covers every case with margin at about a fifth of the
# tokens of a full-document call.
APPLICANTS_HEAD_CHARS = 8000

# Fields whose values must be verbatim source text. Everything else (ids, order,
# follows) is structural and is left alone.
SPAN_SUFFIX = "_span"


def _norm(text: str) -> str:
    """Fold the differences that make a true quote look false.

    Extraction and the source disagree on curly vs straight quotes, non-breaking
    spaces and runs of whitespace far more often than they disagree on words.
    Folding those is what separates 'the model paraphrased' from 'the PDF had a
    different apostrophe'.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


class SpanIndex:
    """Locates a span in the source under the same folding, and maps the hit back
    to offsets in the ORIGINAL text.

    The folded text is searched, but offsets must refer to the original, so a
    position map is built alongside it.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        folded: list[str] = []
        self.positions: list[int] = []
        previous_space = False
        for index, char in enumerate(unicodedata.normalize("NFKC", source)):
            char = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"}.get(
                char, char
            )
            if char.isspace():
                if previous_space or not folded:
                    continue
                folded.append(" ")
                self.positions.append(index)
                previous_space = True
            else:
                folded.append(char)
                self.positions.append(index)
                previous_space = False
        while folded and folded[-1] == " ":
            folded.pop()
            self.positions.pop()
        self.folded = "".join(folded)
        self.lowered = self.folded.lower()

    def locate(self, span: str) -> tuple[int, int] | None:
        needle = _norm(span)
        if not needle:
            return None
        at = self.folded.find(needle)
        if at < 0:
            at = self.lowered.find(needle.lower())
        if at < 0:
            return None
        end = at + len(needle) - 1
        return self.positions[at], self.positions[end] + 1


def _located(index: SpanIndex, hit: tuple[int, int]) -> dict:
    """Record what the DOCUMENT says at these offsets, not what the model typed.

    Location is deliberately tolerant -- it folds whitespace and falls back to a
    case-insensitive search -- so a located span is not necessarily character-
    identical to the source. Storing the model's string then produces a record
    whose text and offsets disagree: measured on Ukraine v. Russia (re Crimea),
    1 span of 825 came back as "the sentence was later reduced" where the
    judgment reads "The sentence was later reduced". Slicing the source closes
    that gap, so `text == source[start:end]` holds for every span by
    construction rather than by luck.
    """
    start, end = hit
    return {"text": index.source[start:end], "start": start, "end": end}


def verify(payload: dict, source: str) -> tuple[dict, dict]:
    """Walk the payload, locate every *_span, attach offsets, drop what fails.

    Returns the verified payload and a tally. Dropping rather than keeping is
    deliberate: an unlocatable span is exactly the fabricated-anchor failure this
    pipeline exists to prevent, and it must not reach stage 2.
    """
    index = SpanIndex(source)
    stats = {"spans": 0, "located": 0, "dropped": 0, "dropped_values": []}

    def walk(node):
        if isinstance(node, dict):
            out = {}
            for key, value in node.items():
                # LISTS OF SPANS ("*_spans") are verified too. Checking only the
                # singular "*_span" keys left interim_spans -- free-text lists of
                # procedural acts -- entirely unchecked, which would have let
                # unverified text through to stage 2 and silently voided the
                # whole verified-by-construction guarantee.
                if key.endswith(SPAN_SUFFIX + "s") and isinstance(value, list):
                    kept = []
                    for item in value:
                        if not isinstance(item, str):
                            continue
                        stats["spans"] += 1
                        hit = index.locate(item)
                        if hit is None:
                            stats["dropped"] += 1
                            stats["dropped_values"].append(item[:80])
                            continue
                        stats["located"] += 1
                        kept.append(_located(index, hit))
                    if kept:
                        out[key] = kept
                elif key.endswith(SPAN_SUFFIX) and isinstance(value, str):
                    stats["spans"] += 1
                    hit = index.locate(value)
                    if hit is None:
                        stats["dropped"] += 1
                        stats["dropped_values"].append(value[:80])
                        continue
                    stats["located"] += 1
                    out[key] = _located(index, hit)
                else:
                    walked = walk(value)
                    if walked not in (None, [], {}):
                        out[key] = walked
            return out
        if isinstance(node, list):
            return [w for w in (walk(v) for v in node) if w not in (None, [], {})]
        return node

    return walk(payload), stats


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json(raw: str) -> dict | None:
    for candidate in (raw, *(m.group(1) for m in _FENCE.finditer(raw))):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        start = candidate.find("{")
        if start >= 0:
            try:
                return json.loads(candidate[start:])
            except json.JSONDecodeError:
                depth, in_string, escape = 0, False, False
                for position, char in enumerate(candidate[start:], start):
                    if escape:
                        escape = False
                        continue
                    if char == "\\":
                        escape = True
                        continue
                    if char == '"':
                        in_string = not in_string
                    elif not in_string:
                        depth += 1 if char == "{" else -1 if char == "}" else 0
                        if depth == 0:
                            try:
                                return json.loads(candidate[start : position + 1])
                            except json.JSONDecodeError:
                                break
    return None


# ---------------------------------------------------------------------------
# Admission control: concurrency bounded by TOKENS, not by request count
# ---------------------------------------------------------------------------


class TokenBudget:
    """Let requests run concurrently only while their token footprints fit.

    A worker count is the wrong unit for this pipeline. The three stage-1 calls
    have very different footprints -- the main call sends a whole judgment and
    asks for up to 16k back, the applicants call sends 8k characters and asks
    for 4k -- and the documents themselves span a 26-fold length range, from a
    3.8k-character Commission decision to a 98k Grand Chamber judgment. Four
    workers is therefore anywhere between a trivial load and four maximal
    sequences at once, depending entirely on which documents happen to align.

    What a server can actually hold is a number of TOKENS: every in-flight
    sequence occupies KV cache for its prompt plus everything it will generate,
    for as long as it runs. So each request reserves `prompt + max_output`
    before it starts and releases it when it finishes, and a request waits
    rather than starting when its footprint does not fit in what is left.

    vLLM will not error if this is exceeded -- it queues and preempts, and
    preemption means recomputing a prefill that was already paid for. The cost
    of getting this wrong is therefore silent slowdown and thrash rather than a
    crash, which is exactly the kind of failure that is easy to ship.

    A request larger than the whole budget runs alone rather than deadlocking,
    with a warning: that is a `--token-budget` set too low for the corpus, and
    the run should not stop for it.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._available = capacity
        self._lock = threading.Lock()
        self._freed = threading.Condition(self._lock)

    @contextlib.contextmanager
    def reserve(self, cost: int, *, label: str = ""):
        cost = max(1, int(cost))
        with self._lock:
            if cost > self.capacity:
                print(
                    f"    {label}: needs {cost:,} tokens, budget is "
                    f"{self.capacity:,} -- running it alone",
                    flush=True,
                )
                while self._available < self.capacity:
                    self._freed.wait()
                granted = self._available
            else:
                while self._available < cost:
                    self._freed.wait()
                granted = cost
            self._available -= granted
        try:
            yield
        finally:
            with self._lock:
                self._available += granted
                self._freed.notify_all()


def count_prompt_tokens(base_url: str, model: str, prompt: str, api_key: str) -> int:
    """Exact prompt length from the server's own tokenizer, or a safe estimate.

    vLLM exposes /tokenize, which is one cheap call and removes the guesswork.
    The fallback matters more than it looks: an underestimate lets too much
    run at once, so it errs high at 3 characters per token -- gemma averages
    nearer 4 on this corpus, and being wrong in the direction of caution costs
    a little throughput rather than a preemption storm.
    """
    root = base_url.rstrip("/")
    root = root.removesuffix("/v1")
    try:
        body = json.dumps({"model": model, "prompt": prompt}).encode()
        request = urllib.request.Request(
            f"{root}/tokenize",
            data=body,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
            },
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            count = json.loads(response.read()).get("count")
        if isinstance(count, int) and count > 0:
            return count
    except Exception:  # noqa: BLE001, S110 -- the estimate below is the point
        pass
    return len(prompt) // 3 + 1


def server_token_capacity(base_url: str, api_key: str, default: int = 98_304) -> int:
    """The server's max_model_len, which is the largest single sequence it can hold.

    Used as the default budget. It is deliberately conservative -- a server can
    usually hold several such sequences -- but it is the one number the server
    actually tells us, and the whole point of this class is to stop guessing.
    Raise it with --token-budget once a run's memory headroom is known.
    """
    try:
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read()).get("data", [])
        for entry in data:
            value = entry.get("max_model_len")
            if isinstance(value, int) and value > 0:
                return value
    except Exception:  # noqa: BLE001, S110
        pass
    return default


# ---------------------------------------------------------------------------
# Surviving a flaky endpoint
# ---------------------------------------------------------------------------

# How long to keep trying when the endpoint is unreachable rather than wrong.
# Long by design: a run over thousands of documents is measured in hours, and
# the failures that actually happen are an SSH port-forward dropping or a vLLM
# server being restarted -- minutes of unavailability, not permanent loss.
# Dying on those throws away everything computed so far; waiting costs nothing
# but wall-clock. Measured 2026-08-30: an unwrapped call lost a ten-document O1
# run in 5.9 seconds because the endpoint had gone down between runs.
TRANSIENT_MAX_WAIT_SECONDS = 1800
TRANSIENT_BACKOFF = (5, 15, 30, 60, 120, 300)


BUDGET: TokenBudget | None = None


def _reserve(
    prompt: str, max_out: int, label: str, base_url: str, model: str, key: str
):
    """Reserve this request's peak footprint, or nothing when running serially.

    Sequential runs need no admission control -- there is only ever one request
    in flight -- so the budget is left unset and this is a no-op. It is the
    parallel path that can put a 70k-token prompt and a 20k output budget on
    the wire beside three more of the same and exceed what the server can hold.
    """
    if BUDGET is None:
        return contextlib.nullcontext()
    cost = count_prompt_tokens(base_url, model, prompt, key) + max_out
    return BUDGET.reserve(cost, label=label)


def _is_transient(exc: Exception) -> bool:
    """True for "the endpoint is not there", false for "the request is wrong".

    A 400 means the prompt is too long or malformed and retrying it forever is
    a hang, not resilience. A connection error, a timeout, a 502 or a 503 means
    something upstream is restarting and the same request will succeed later.
    """
    name = type(exc).__name__
    if name in {
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
    }:
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status in {408, 409, 429, 500, 502, 503, 504}


def call_with_recovery(client: OpenAI, *, label: str, **kwargs):
    """One chat completion, waiting out an endpoint that has gone away.

    Returns the response, or raises the last exception once
    TRANSIENT_MAX_WAIT_SECONDS of retrying has not helped -- at which point the
    endpoint is down rather than restarting, and the caller should record the
    document as failed and carry on to the next one.
    """
    waited = 0.0
    index = 0
    while True:
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            if not _is_transient(exc) or waited >= TRANSIENT_MAX_WAIT_SECONDS:
                raise
            delay = TRANSIENT_BACKOFF[min(index, len(TRANSIENT_BACKOFF) - 1)]
            index += 1
            waited += delay
            print(
                f"    {label}: {type(exc).__name__}, endpoint unreachable -- "
                f"retrying in {delay}s (waited {waited:.0f}s of "
                f"{TRANSIENT_MAX_WAIT_SECONDS}s)",
                flush=True,
            )
            time.sleep(delay)


def compress(
    client: OpenAI,
    model: str,
    text: str,
    *,
    temperature: float,
    max_tokens: int,
    retries: int = 2,
    verify_against: str | None = None,
) -> tuple[dict | None, dict, str]:
    """One extraction pass over `text`.

    `verify_against` is the document spans are located in, defaulting to `text`
    itself. The split path (compress_split) passes the WHOLE document while
    sending only a half to the model, so that offsets come back relative to the
    full source rather than to the half -- otherwise every span from the second
    half would be silently mis-anchored.
    """
    source = text if verify_against is None else verify_against
    prompt = (
        PROMPT.read_text(encoding="utf-8") + "\n\nJUDGMENT\n<<<DOC\n" + text + "\nDOC\n"
    )
    last = ""
    truncated = False
    for attempt in range(1, retries + 2):
        # The output-cap kwarg and the temperature a model will accept differ
        # by family: gpt-5 reasoning models reject `max_tokens` in favour of
        # `max_completion_tokens`, and reject any temperature but the default.
        # Reusing repair_facts._token_limit_kwargs keeps the one spelling rule
        # in one place rather than drifting between the two callers.
        from art6.ontology.repair_facts import _token_limit_kwargs

        kwargs = dict(_token_limit_kwargs(model, max_tokens))
        if not model.lower().lstrip().startswith(("gpt-5", "o1", "o3", "o4")):
            kwargs["temperature"] = temperature
        kwargs["seed"] = MODEL_SEED
        with _reserve(
            prompt,
            max_tokens,
            "main",
            str(client.base_url),
            model,
            client.api_key or "",
        ):
            response = call_with_recovery(
                client,
                label="main",
                model=model,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
        last = response.choices[0].message.content or ""
        truncated = response.choices[0].finish_reason == "length"
        payload = parse_json(last)
        if payload is not None and payload.get("events"):
            verified, stats = verify(payload, source)
            stats["attempt"] = attempt
            return verified, stats, last
    # Distinguish "the model wrote nonsense" from "the model was cut off". Only
    # the second is fixable by giving it more room or less document, and the
    # split path keys off exactly that distinction.
    reason = "output truncated at the token cap" if truncated else "no usable JSON"
    return None, {"attempt": retries + 1, "error": reason, "truncated": truncated}, last


# Halving 424,646 chars (the corpus maximum) clears the window in one split, so
# the cap is a guard against pathological input, not a working depth.
# Sampling seed sent with every completion. It does NOT make generation
# reproducible: at temperature 0 decoding is greedy, so there is no sampling RNG
# for a seed to control, and the residual run-to-run variance is numerical --
# batch composition and prefix-cache state change reduction order in the GPU
# kernels. Measured 2026-08-30 on a 57k-token prompt: four calls, three
# byte-identical (two unseeded, one seeded) and a fourth with the SAME seed
# differing. Sent anyway so the request is fully specified and so any future
# run at temperature > 0 is reproducible without a code change.
MODEL_SEED = 42

MAX_SPLIT_DEPTH = 4

# What a split part is allowed to WRITE. A document only reaches the split path
# because it is long, and a long ECHR judgment is long in events as well as in
# words -- half of Ukraine v. Russia (re Crimea) alone holds 73. Parts are sized
# so the prompt and this budget both fit the window.
SPLIT_OUTPUT_TOKENS = 32_000
SPLIT_OUTPUT_MARGIN = 1_000


def _halve(text: str) -> tuple[str, str]:
    """Split as near the midpoint as a sane boundary allows.

    Separators are tried coarsest-first, but a separator is only used if its
    occurrence NEAREST THE MIDPOINT is actually near it. That proviso is the
    whole point: the largest judgment in the corpus has 24 blank-line breaks and
    all of them fall in its first 29k of 425k characters, so preferring
    paragraph breaks unconditionally cut 29k/396k, recursed on a tail barely
    smaller than the input, and exhausted the depth cap without ever fitting.
    Long ECHR narratives separate paragraphs with a single newline.
    """
    mid = len(text) // 2
    tolerance = len(text) // 4
    for separator in ("\n\n", "\n", ". ", " "):
        positions = [m.start() for m in re.finditer(re.escape(separator), text)]
        if not positions:
            continue
        best = min(positions, key=lambda position: abs(position - mid))
        if abs(best - mid) <= tolerance:
            cut = best + (len(separator) if separator == ". " else 0)
            return text[:cut], text[cut:]
    return text[:mid], text[mid:]


def _merge_split_payloads(parts: list[dict]) -> dict:
    """Concatenate the parts' events, keeping ids unique and `follows` intact.

    `follows` holds another event's id ("e1") and is scoped to the call that
    produced it, so two halves both emit an "e1". Ids are prefixed per part and
    every follows pointer is rewritten with the same map; a chain that crosses
    the split simply has no link, because neither half could see the other side.
    """
    merged: dict = {}
    events: list = []
    for n, part in enumerate(parts, start=1):
        if not isinstance(part, dict):
            continue
        for key, value in part.items():
            if key != "events" and key not in merged:
                merged[key] = value
        remap = {}
        part_events = part.get("events") or []
        for event in part_events:
            if isinstance(event, dict) and event.get("id"):
                remap[event["id"]] = f"p{n}{event['id']}"
        for event in part_events:
            if not isinstance(event, dict):
                continue
            event = dict(event)
            if event.get("id") in remap:
                event["id"] = remap[event["id"]]
            follows = event.get("follows")
            if follows:
                if follows in remap:
                    event["follows"] = remap[follows]
                else:
                    # Points across the split. Drop the pointer and its span
                    # rather than leave a dangling id for stage 2 to resolve.
                    event.pop("follows", None)
                    event.pop("follows_span", None)
            events.append(event)
    merged["events"] = events
    return merged


def compress_split(
    client: OpenAI,
    model: str,
    text: str,
    *,
    temperature: float,
    max_tokens: int,
    retries: int = 2,
    capacity: int,
    verify_against: str | None = None,
    depth: int = 0,
) -> tuple[dict | None, dict, str]:
    """compress(), but halve the document when it will not fit the window.

    Two of 9,270 English judgments approach the model window. Truncating one
    would lose proceedings from the end of the chain without saying so, and
    dropping it would leave the pipeline unable to process its own corpus, so an
    oversize document is halved and each half extracted independently. Spans are
    verified against the WHOLE document, so verified-by-construction holds
    exactly as on the single-pass path.

    SIZE THE PARTS FOR THE ANSWER, NOT JUST THE QUESTION
    ----------------------------------------------------
    The first version of this split sized parts to fit the prompt and left the
    output budget at the caller's 16,000. Measured on Ukraine v. Russia (re
    Crimea): the 212,326-character second half is 57,270 prompt tokens, leaving
    41,034 of headroom, and its answer ran past 16,000 tokens and was cut off
    mid-event at id e74 -- 73 events in one half, because an inter-state case
    enumerates individual incidents. So parts are now sized so that the prompt
    AND a generous output budget both fit, and a part that still comes back
    truncated is split again rather than accepted.
    """
    source = text if verify_against is None else verify_against
    cost = _prompt_cost(client, model, text)
    if cost + max_tokens <= capacity:
        return compress(
            client,
            model,
            text,
            temperature=temperature,
            max_tokens=max_tokens,
            retries=retries,
            verify_against=source,
        )
    payloads, totals, raws = [], {"spans": 0, "located": 0, "dropped": 0}, []
    failed = _extract_parts(
        client,
        model,
        text,
        temperature=temperature,
        retries=retries,
        capacity=capacity,
        source=source,
        depth=depth,
        payloads=payloads,
        totals=totals,
        raws=raws,
    )
    totals["split_parts"] = len(payloads)
    totals["split_parts_failed"] = failed
    raw = "\n".join(raws)
    if failed or not payloads:
        # A half-extracted judgment is WORSE than a missing one: it looks like a
        # complete extraction and would be scored as one. Fail the document and
        # let the caller record it, rather than returning a partial that reports
        # a healthy verbatim rate over the parts that happened to work.
        totals["error"] = (
            f"{failed} split part(s) produced no usable JSON --"
            " refusing to emit a partially extracted document"
        )
        return None, totals, raw
    return _merge_split_payloads(payloads), totals, raw


def _prompt_cost(client: OpenAI, model: str, text: str) -> int:
    prompt = (
        PROMPT.read_text(encoding="utf-8") + "\n\nJUDGMENT\n<<<DOC\n" + text + "\nDOC\n"
    )
    return count_prompt_tokens(
        str(client.base_url), model, prompt, client.api_key or ""
    )


def _extract_parts(
    client: OpenAI,
    model: str,
    text: str,
    *,
    temperature: float,
    retries: int,
    capacity: int,
    source: str,
    depth: int,
    payloads: list,
    totals: dict,
    raws: list,
) -> int:
    """Recursively halve `text` until each part fits, extracting as it goes.

    Appends one payload per part, in document order, and returns how many parts
    failed. Parts get SPLIT_OUTPUT_TOKENS rather than the caller's budget: a
    document only reaches this path because it is unusually long, and length
    here means many events to emit, not merely many to read.
    """
    cost = _prompt_cost(client, model, text)
    room = capacity - cost
    fits = room >= SPLIT_OUTPUT_TOKENS
    if fits or depth >= MAX_SPLIT_DEPTH:
        budget = min(SPLIT_OUTPUT_TOKENS, max(room - SPLIT_OUTPUT_MARGIN, 1_000))
        payload, stats, raw = compress(
            client,
            model,
            text,
            temperature=temperature,
            max_tokens=budget,
            retries=retries,
            verify_against=source,
        )
        raws.append(raw)
        for key in ("spans", "located", "dropped"):
            totals[key] = totals.get(key, 0) + stats.get(key, 0)
        totals.setdefault("dropped_values", []).extend(stats.get("dropped_values", []))
        if payload is None:
            if stats.get("truncated") and depth < MAX_SPLIT_DEPTH:
                # It read the part fine and ran out of room to answer. Fewer
                # events per part is the fix, so halve it and try again rather
                # than record a failure the split path can still avoid.
                print(
                    f"    part truncated at {budget:,} output tokens;"
                    f" splitting {len(text):,} chars further",
                    flush=True,
                )
                head, tail = _halve(text)
                if head.strip() and tail.strip():
                    return sum(
                        _extract_parts(
                            client,
                            model,
                            half,
                            temperature=temperature,
                            retries=retries,
                            capacity=capacity,
                            source=source,
                            depth=depth + 1,
                            payloads=payloads,
                            totals=totals,
                            raws=raws,
                        )
                        for half in (head, tail)
                    )
            print(
                f"    SPLIT PART FAILED ({stats.get('error')}):"
                f" {len(text):,} chars, {cost:,} prompt tokens,"
                f" {budget:,} output budget",
                flush=True,
            )
            return 1
        payloads.append(payload)
        return 0
    head, tail = _halve(text)
    if not head.strip() or not tail.strip():
        depth = MAX_SPLIT_DEPTH
    print(
        f"    {len(text):,} chars is {cost:,} prompt tokens against a"
        f" {capacity:,} window; splitting into {len(head):,} + {len(tail):,}",
        flush=True,
    )
    failed = 0
    for half in (head, tail):
        failed += _extract_parts(
            client,
            model,
            half,
            temperature=temperature,
            retries=retries,
            capacity=capacity,
            source=source,
            depth=depth + 1,
            payloads=payloads,
            totals=totals,
            raws=raws,
        )
    return failed


def compress_applicants(
    client: OpenAI,
    model: str,
    text: str,
    *,
    temperature: float,
    max_tokens: int = 4000,
) -> tuple[list, str]:
    """The applicants' own description, asked for in a SEPARATE call.

    WHY THIS IS NOT PART OF THE MAIN PROMPT
    ---------------------------------------
    It was, and it cost events. Measured 2026-08-28 on gemma-4-31b at
    temperature 0, arms interleaved within each round so that vLLM batching
    could not align with the comparison:

        L3   without applicants 21/21/21 events   with 19/19/19
        L4   without applicants 13/13/13 events   with 10/10/10
        L1   unaffected

    The cause is not prompt length: a length-matched placebo of already-stated
    rules cost nothing (L3 21/21). It is not the extra output section either --
    a structurally identical dummy section asking for documents mentioned, which
    the model actually filled with 2-5 entries, cost nothing and if anything
    raised the count (L3 22/22, L4 14/15). Nor is it attention drawn to the
    document's opening: the guidance prose WITHOUT the schema fields cost
    nothing (L3 21/21/21). Only asking the model to emit these particular fields
    alongside the events did it, and no single mechanism I tested accounts for
    that. So this is a workaround for a measured effect, not a fix for an
    understood one.

    A second call makes the question moot. The main call runs the unchanged
    prompt and so extracts exactly what it did before, and the applicants come
    from a small, cheap call over the opening alone -- which is also more
    accurate: birth-year recall went from 5/9 available values in the merged
    prompt to 9/9 here, with no fabrications either way.
    """
    prompt = (
        APPLICANTS_PROMPT.read_text(encoding="utf-8")
        + "\n\nDOCUMENT OPENING\n<<<DOC\n"
        + text[:APPLICANTS_HEAD_CHARS]
        + "\nDOC\n"
    )
    from art6.ontology.repair_facts import _token_limit_kwargs

    kwargs = dict(_token_limit_kwargs(model, max_tokens))
    if not model.lower().lstrip().startswith(("gpt-5", "o1", "o3", "o4")):
        kwargs["temperature"] = temperature
        kwargs["seed"] = MODEL_SEED
    with _reserve(
        prompt,
        max_tokens,
        "applicants",
        str(client.base_url),
        model,
        client.api_key or "",
    ):
        response = call_with_recovery(
            client,
            label="applicants",
            model=model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
    raw = response.choices[0].message.content or ""
    payload = parse_json(raw)
    if not isinstance(payload, dict):
        return [], raw
    applicants = payload.get("applicants")
    return (applicants if isinstance(applicants, list) else []), raw


def compress_parties(
    client: OpenAI,
    model: str,
    text: str,
    events: list,
    *,
    temperature: float,
    max_tokens: int = 6000,
) -> tuple[dict, str]:
    """Ask ONLY "who was on each side", in a second call, given the event list.

    OPT-IN via --parties-pass. The main prompt keeps its own `parties` field and
    is unchanged, so turning this off restores the previous behaviour exactly.

    WHY A SECOND CALL
    -----------------
    The main prompt asks for twenty-odd fields per event and the opposing party
    competes with all of them. There is precedent for that mattering here: see
    compress_applicants, where moving two fields out of the main prompt and into
    a call of their own stopped a measured loss of events and raised birth-year
    recall from 5/9 to 9/9. Attention, not capability, was the binding
    constraint there.

    It looks like the binding constraint for parties too. Measured 2026-08-30 on
    a held-out ten, extraction records both sides on 32% of events, while the
    schema-light O1 baseline -- one call, nine flat fields, no ontology --
    records two parties on 52%. O1 is not better at this; 20 of its party
    entries are the deciding body of the very entry they sit on, which the
    ontology conditions make impossible. But a much simpler prompt naming the
    same thing gets closer to it, which points at competition for attention
    rather than a limit on what the model can read.

    The second call is given the events the first call already found, so it
    cannot add, remove or renumber proceedings -- it can only answer the party
    question about each one. Its spans are verified against the FULL source like
    every other span, and its parties go through the deciding-body filter.

    Returns ({event_id: [party dicts]}, raw response).
    """
    listing = []
    for event in events:
        bits = [f"[{event.get('id')}]"]
        for key, label in (
            ("decision_date_span", "date"),
            ("authority_span", "decided by"),
            ("what_happened_span", "what happened"),
        ):
            value = event.get(key) or {}
            value = value.get("text") if isinstance(value, dict) else value
            if value:
                bits.append(f"{label}: {value}")
        listing.append(" | ".join(bits))

    prompt = (
        PARTIES_PROMPT.read_text(encoding="utf-8")
        + "\n\nTHE PROCEEDINGS\n"
        + "\n".join(listing)
        + "\n\nTHE JUDGMENT\n<<<DOC\n"
        + text
        + "\nDOC\n"
    )
    from art6.ontology.repair_facts import _token_limit_kwargs

    kwargs = dict(_token_limit_kwargs(model, max_tokens))
    if not model.lower().lstrip().startswith(("gpt-5", "o1", "o3", "o4")):
        kwargs["temperature"] = temperature
        kwargs["seed"] = MODEL_SEED
    with _reserve(
        prompt, max_tokens, "parties", str(client.base_url), model, client.api_key or ""
    ):
        response = call_with_recovery(
            client,
            label="parties",
            model=model,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
    raw = response.choices[0].message.content or ""
    payload = parse_json(raw)
    if not isinstance(payload, dict):
        return {}, raw
    out: dict = {}
    for row in payload.get("parties_by_event") or []:
        if isinstance(row, dict) and row.get("id"):
            parties = row.get("parties")
            out[str(row["id"])] = parties if isinstance(parties, list) else []
    return out, raw


def merge_parties(payload: dict, by_event: dict) -> tuple[int, int]:
    """Add second-pass parties the main pass did not already have.

    ADDITIVE ONLY. The main pass's parties are never removed or overwritten,
    so the second call can only improve coverage, never cost it -- which keeps
    the failure mode one-directional and makes the flag safe to leave on.

    Matching is on normalised name text, so the second pass naming "the
    applicant" where the first said "the applicant" adds nothing, while naming
    "the Regional Government" adds a party.

    Returns (parties added, events that gained one).
    """
    added = 0
    events_touched = 0
    for event in payload.get("events") or []:
        proposed = by_event.get(str(event.get("id"))) or []
        if not proposed:
            continue
        existing = event.get("parties") or []
        seen = {
            _normalise_name((p.get("name_span") or {}).get("text"))
            for p in existing
            if isinstance(p, dict)
        }
        gained = False
        for party in proposed:
            if not isinstance(party, dict):
                continue
            name = party.get("name_span")
            name = name.get("text") if isinstance(name, dict) else name
            key = _normalise_name(name)
            if not key or key in seen:
                continue
            seen.add(key)
            existing.append(party)
            added += 1
            gained = True
        if gained:
            events_touched += 1
        event["parties"] = existing
    return added, events_touched


# Words describing something DONE in an investigation rather than a request
# answered. Deliberately narrow: each is an act with no respondent and no
# ruling, and none of the five ProceedingOutcome values is true of it.
_INVESTIGATIVE_ACTS = re.compile(
    r"\b(seiz|search|arrest|detain|remand|inspect|question|interrogat|"
    r"exhum|autops|forensic|expert examination|opened an investigation|"
    r"investigation was opened|charged|indict)",
    re.IGNORECASE,
)


# A decision verb anywhere in the span means something WAS answered, whatever
# else the wording contains. Added after a false positive: "the Stavanger was
# freed from arrest" matched the act list on the word "arrest", but a
# prosecutor freeing a ship FROM arrest has granted a request, which the
# prompt's own rule makes `merits decided`. The act list asks "does this look
# like an investigative step"; this asks "was it nevertheless a ruling", and
# the second question wins.
_DECISION_VERBS = re.compile(
    r"\b(freed|releas|grant|allow|refus|reject|dismiss|uphold|upheld|quash|"
    r"annul|overturn|order(?:ed|s)?|award|convict|acquit|sentenc|declar|"
    r"rul(?:ed|ing)|decid|terminat|discontinu)",
    re.IGNORECASE,
)


def drop_actless_outcomes(payload: dict) -> tuple[int, list[str]]:
    """Remove `outcome` from investigative events that decided nothing.

    `outcome` is one of five ProceedingOutcome values and every one of them
    presupposes that something was ASKED and ANSWERED. A gun seizure, a search
    warrant, an arrest, a crime-scene inspection and the opening of an
    investigation answer nothing: `merits decided` is false because no merits
    were reached, and `other` is false because there was no result at all.

    Measured 2026-08-28 on L9 (a Georgian criminal case): stage 1 labelled a
    seizure, a warrant and an arrest `merits decided`. Every one of those
    triples was well-formed, in-vocabulary, evidenced and SHACL-conformant, and
    every one was false -- which is exactly why this cannot be left to the
    validation layer. The shapes check that a value is IN the vocabulary; no
    shape can check that the vocabulary applies.

    The bias matters more than the count. It is systematic and one-directional,
    so a distribution computed over the released corpus would skew toward
    `merits decided` with nothing downstream able to detect it. An omitted
    outcome is a gap anyone can see; a wrong one is a gap nobody can.

    Deliberately conservative -- all three conditions must hold:

      * `instance_level` is investigative. Other rungs decide things by
        definition, and an administrative body granting a request HAS ruled on
        its substance (see the prompt's note on orders).
      * there is no `outcome_span`. If the document states a result in words,
        the model found one and it is kept. This preserves the correct
        `inadmissible` on "the request was rejected by the investigator",
        which is a genuine answer to a genuine request.
      * the event's own `what_happened_span` names an act from the list above.

    Returns (number dropped, the event ids).
    """
    dropped: list[str] = []
    for event in payload.get("events") or []:
        if not event.get("outcome"):
            continue
        level = (event.get("instance_level") or "").strip().lower()
        if level != "investigative":
            continue
        if event.get("outcome_span"):
            continue
        what = ((event.get("what_happened_span") or {}).get("text")) or ""
        if not _INVESTIGATIVE_ACTS.search(what):
            continue
        if _DECISION_VERBS.search(what):
            continue
        event.pop("outcome", None)
        dropped.append(event.get("id", "?"))
    return len(dropped), dropped


def drop_deciding_body_parties(payload: dict) -> tuple[int, list[str]]:
    """Remove any party that IS the body deciding that same entry.

    The deciding body is never a party to the step it decided -- echr.ttl says
    so, echr:DecidingBodyNotAPartyShape enforces it, and repair_facts.apply_patch
    refuses to add one. Stage 1 was the only place the rule was left to the
    prompt, and the prompt cannot hold it.

    Measured 2026-08-28 across four ten-document stage-1 runs on gemma-4-31b.
    Asking for the opposing party at all raises deciding-body-as-party from 7 to
    18 regardless of how the request is worded: a version phrased as a quota
    ("list both") and a version phrased as permission ("add it where the document
    makes it plain, one party is a correct answer") produced 18 each. Three
    explicit sentences forbidding it moved the number not at all. A prohibition
    competes with an instruction in the same prompt, and loses.

    So the prompt asks for recall and this enforces the constraint, which is the
    division that has worked everywhere else in this pipeline. The filter costs
    nothing real: on the same measurement it removed 18 party entries and left
    two-sided coverage at 54% of events, against 28% without the carry-forward
    instruction -- so the instruction's gain survives and only its contamination
    is taken out.

    Matching is on normalised text and is deliberately generous about
    containment ("the Fund" against "the Savings Deposit Insurance Fund"), since
    a party that is a substring of the deciding body's name is that body under
    a shorter description, not a different entity.

    Returns (number dropped, the dropped names).
    """
    dropped: list[str] = []
    for event in payload.get("events") or []:
        authority = _normalise_name((event.get("authority_span") or {}).get("text"))
        if not authority:
            continue
        kept = []
        for party in event.get("parties") or []:
            name = _normalise_name((party.get("name_span") or {}).get("text"))
            if name and (name == authority or name in authority or authority in name):
                dropped.append((party.get("name_span") or {}).get("text", ""))
                continue
            kept.append(party)
        event["parties"] = kept
    return len(dropped), dropped


def _normalise_name(value: str | None) -> str:
    """Normalised form for comparing two party names.

    Leading articles are stripped because the second pass and the main pass
    routinely name the same body differently -- measured 2026-08-30, "Regional
    Government" and "the Regional Government" landed on the same event as two
    parties. An article is never what distinguishes two litigants.
    """
    value = re.sub(r"\s+", " ", (value or "")).strip().lower()
    return re.sub(r"^(the|a|an)\s+", "", value)


def doc_id_for(index: int, line: str) -> str:
    """Name an output after the DOCUMENT, not after its line number.

    Positional ids (input.L1 ... input.L250) make the mapping back to HUDOC
    depend on input line order, which resume markers, partial reruns and any
    reordering of the input silently invalidate -- and a 250-document run is
    exactly where that goes unnoticed. The evaluation samples carry `case_id`,
    so use it when present and fall back to the positional name otherwise, which
    keeps older input files working unchanged.
    """
    try:
        record = json.loads(line)
    except (ValueError, TypeError):
        return f"input.L{index}"
    for field in ("case_id", "itemid"):
        value = str(record.get(field) or "").strip()
        if value:
            return value
    return f"input.L{index}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-jsonl", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--model", default="gemma-4-31b")
    ap.add_argument("--base-url", default="http://localhost:8001/v1")
    ap.add_argument("--api-key", default=os.environ.get("VLLM_API_KEY", "EMPTY"))
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Documents to extract concurrently. 1 (the default) is the "
        "sequential path, which is bit-reproducible: three separate runs of "
        "the same input produced byte-identical payloads. Concurrency changes "
        "vLLM batch composition and that guarantee is not claimed above 1.",
    )
    ap.add_argument(
        "--token-budget",
        type=int,
        default=0,
        help="Ceiling on prompt+output tokens in flight at once. 0 asks the "
        "server for its max_model_len. Only consulted when --workers > 1.",
    )
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Redo documents whose .compress.json already exists. Off by "
        "default so an interrupted run resumes where it stopped.",
    )
    ap.add_argument(
        "--parties-pass",
        action="store_true",
        help="Second call asking only who was on each side, merged additively "
        "into the main pass's parties. Off by default -- see compress_parties.",
    )
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument(
        "--only", default="", help="comma-separated doc ids, e.g. input.L1,input.L6"
    )
    args = ap.parse_args()

    client = OpenAI(base_url=args.base_url, api_key=args.api_key, timeout=900.0)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    wanted = {w.strip() for w in args.only.split(",") if w.strip()}

    lines = [
        l
        for l in args.input_jsonl.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    global BUDGET
    # Capacity is needed on every path now, not just the parallel one: it is
    # what decides whether a document has to be split.
    capacity = args.token_budget or server_token_capacity(
        args.base_url, args.api_key or ""
    )
    if args.workers > 1:
        BUDGET = TokenBudget(capacity)
        print(
            f"stage 1: {args.model} @ {args.base_url} "
            f"(temperature {args.temperature}, {args.workers} workers, "
            f"token budget {capacity:,})"
        )
    else:
        print(
            f"stage 1: {args.model} @ {args.base_url} (temperature {args.temperature})"
        )
    totals = {"spans": 0, "located": 0, "dropped": 0}
    failures: list[str] = []
    resumed = 0
    lock = threading.Lock()

    def _run_doc(i: int, line: str, doc_id: str) -> None:
        """Extract one document. Safe to call from several threads at once.

        Everything here is per-document except `totals` and the printing, so
        those take the lock and nothing else needs to. The document's own
        SpanIndex, payload and stats are local, which is what makes the
        parallel path a scheduling change rather than a behavioural one.
        """
        text = json.loads(line).get("text", "")
        t0 = time.perf_counter()
        payload, stats, raw = compress_split(
            client,
            args.model,
            text,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            capacity=capacity,
        )
        if payload is None:
            with lock:
                failures.append(doc_id)
            print(f"  {doc_id}: FAILED ({stats.get('error')})")
            (args.out_dir / f"{doc_id}.raw.txt").write_text(raw, encoding="utf-8")
            return
        # The applicants call is separate (see compress_applicants) but its spans
        # are held to exactly the same standard: verified against the FULL source,
        # not the 8,000-character head it was asked about, so the offsets that
        # travel to stage 2 refer to the document as a whole like every other span.
        applicants, applicants_raw = compress_applicants(
            client, args.model, text, temperature=args.temperature
        )
        if applicants:
            verified_applicants, applicant_stats = verify(
                {"applicants": applicants}, text
            )
            for key in ("spans", "located", "dropped"):
                stats[key] = stats.get(key, 0) + applicant_stats[key]
            stats["dropped_values"] += applicant_stats["dropped_values"]
            payload["applicants"] = verified_applicants.get("applicants", [])
        elif applicants_raw:
            (args.out_dir / f"{doc_id}.applicants.raw.txt").write_text(
                applicants_raw, encoding="utf-8"
            )
        if args.parties_pass:
            by_event, parties_raw = compress_parties(
                client,
                args.model,
                text,
                payload.get("events") or [],
                temperature=args.temperature,
            )
            # An empty string is not a span. The second pass emits
            # "role_span": "" where the document states no role, and feeding
            # those to verify() counted 79 phantom drops on one ten-document
            # run -- a span-fidelity figure that looked like a fabrication
            # problem and was punctuation.
            for _parties in by_event.values():
                for _party in _parties:
                    if isinstance(_party, dict):
                        for _k in [k for k, v in _party.items() if not str(v).strip()]:
                            _party.pop(_k)
            if by_event:
                verified, party_stats = verify({"e": by_event}, text)
                for key in ("spans", "located", "dropped"):
                    stats[key] = stats.get(key, 0) + party_stats[key]
                stats["dropped_values"] += party_stats["dropped_values"]
                p_added, p_events = merge_parties(payload, verified.get("e", {}))
            else:
                p_added = p_events = 0
                if parties_raw:
                    (args.out_dir / f"{doc_id}.parties.raw.txt").write_text(
                        parties_raw, encoding="utf-8"
                    )
            stats["parties_pass_added"] = p_added
            stats["parties_pass_events"] = p_events
            with lock:
                totals["p_added"] = totals.get("p_added", 0) + p_added
        else:
            p_added = p_events = 0
        actless, actless_ids = drop_actless_outcomes(payload)
        stats["actless_outcomes_dropped"] = actless
        stats["actless_outcomes_dropped_ids"] = actless_ids
        body_parties, body_names = drop_deciding_body_parties(payload)
        stats["deciding_body_parties_dropped"] = body_parties
        stats["deciding_body_parties_dropped_values"] = body_names
        with lock:
            for k in ("spans", "located", "dropped"):
                totals[k] += stats.get(k, 0)
            totals["body_parties"] = totals.get("body_parties", 0) + body_parties
            totals["actless"] = totals.get("actless", 0) + actless
        rate = stats["located"] / stats["spans"] * 100 if stats["spans"] else 0.0
        print(
            f"  {doc_id}: {len(payload.get('events', [])):3} events, "
            f"{len(payload.get('persons', [])):2} persons, "
            f"{len(payload.get('applicants', [])):2} applicants, "
            f"{stats['located']:4}/{stats['spans']:4} spans verbatim ({rate:5.1f}%), "
            f"{stats['dropped']:3} dropped, "
            f"{body_parties:2} body-as-party, {actless:2} actless-outcome removed"
            + (f", +{p_added:2} parties (2nd pass) " if args.parties_pass else " ")
            + f"[{time.perf_counter() - t0:.0f}s]"
        )
        (args.out_dir / f"{doc_id}.compress.json").write_text(
            json.dumps(
                {"doc": doc_id, "stats": stats, "payload": payload},
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _guarded(i: int, line: str) -> None:
        doc_id = doc_id_for(i, line)
        # ONE DOCUMENT MUST NOT END THE RUN. call_with_recovery waits out a
        # restarting endpoint and re-raises only when it is genuinely gone;
        # at that point this document is recorded as failed and the others
        # keep going, so a run that loses its server for good still keeps
        # everything it had already produced and still exits non-zero.
        try:
            _run_doc(i, line, doc_id)
        except Exception as exc:  # noqa: BLE001 -- recorded, not swallowed
            with lock:
                failures.append(doc_id)
            print(f"  {doc_id}: FAILED ({type(exc).__name__}: {exc})", flush=True)

    todo = []
    for i, line in enumerate(lines, 1):
        doc_id = doc_id_for(i, line)
        if wanted and doc_id not in wanted:
            continue
        # RESUME. A long run will be interrupted -- a dropped port-forward,
        # a restarted server, a killed shell -- and redoing completed work to
        # reach the point of failure is both slow and a source of drift. An
        # existing output is a finished document.
        if (args.out_dir / f"{doc_id}.compress.json").exists() and not args.overwrite:
            resumed += 1
            continue
        todo.append((i, line))

    if args.workers > 1:
        with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
            list(pool.map(lambda a: _guarded(*a), todo))
    else:
        for i, line in todo:
            _guarded(i, line)
    if resumed:
        print(f"\nresumed: {resumed} document(s) already present, skipped")
    if totals["spans"]:
        print(
            f"\ntotal: {totals['located']}/{totals['spans']} spans verbatim "
            f"({totals['located'] / totals['spans'] * 100:.1f}%), {totals['dropped']} dropped, "
            f"{totals.get('body_parties', 0)} deciding-body parties, "
            f"{totals.get('actless', 0)} actless outcomes removed"
        )
    # A NON-ZERO EXIT WHEN ANYTHING WAS LOST. A stage that reports a tidy
    # summary after failing on some of its input is the failure mode that cost
    # a whole v15 run: run_native printed "complete: all 0 document(s) kept
    # every unit" and exited 0 after Fuseki refused every write. Wall time and
    # span rates look normal when the denominator is the documents that
    # worked, so the count has to be checked against the input, not itself.
    expected = len(wanted) if wanted else len(lines)
    produced = len(list(args.out_dir.glob("*.compress.json")))
    if failures:
        print(f"FAILED on {len(failures)} document(s): {', '.join(failures)}")
    if produced < expected:
        print(f"INCOMPLETE: {produced} output(s) for {expected} input(s)")
    if failures or produced < expected:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
