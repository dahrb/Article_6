"""
submit_cases.py
---------------
Read cases from judgments_metadata_full.parquet, extract only the 'facts'
section text (skip cases without usable facts), and POST each to the OntoCast
server via multipart form at http://localhost:8999/process.
Cases are submitted PARALLEL_WORKERS at a time using a ThreadPoolExecutor.
Progress is written to PROGRESS_FILE so interrupted runs can resume.
Set N_CASES = None to run the full dataset.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import polars as pl
import requests

SERVER = "http://localhost:8999"
PARQUET = Path("C:/Postdoc/Article_6/data/judgments_metadata_full.parquet")
N_CASES = None          # None = full dataset
PARALLEL_WORKERS = int(os.getenv("ONTOCAST_PARALLEL_WORKERS", "8"))
REQUEST_TIMEOUT_S = int(os.getenv("ONTOCAST_REQUEST_TIMEOUT_S", "1800"))
MAX_RETRIES = int(os.getenv("ONTOCAST_MAX_RETRIES", "2"))
RETRY_BACKOFF_S = float(os.getenv("ONTOCAST_RETRY_BACKOFF_S", "20"))
PROGRESS_FILE = Path(os.getenv("ONTOCAST_PROGRESS_FILE", "C:/Postdoc/Article_6/results/facts_extract/submit_cases_progress.jsonl"))
RESUME = os.getenv("ONTOCAST_RESUME", "1").strip().lower() not in {"0", "false", "no"}

# Columns (in priority order) that contain the facts section.
FACTS_COLS = ("facts", "the_facts", "statement_of_facts")
# Minimum characters in the facts section before falling back to full_text.
# The UMAP chunker needs ≥ 3 sentences; ~600 chars guards against that failure.
MIN_FACTS_CHARS = 600


def _load_processed_case_keys(progress_file: Path) -> set[str]:
    if not progress_file.exists():
        return set()
    done: set[str] = set()
    with progress_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Only skip cases that completed successfully — failures will be retried
            if rec.get("status") != "ok":
                continue
            key = str(rec.get("case_key") or "").strip()
            if key:
                done.add(key)
    return done


def _append_progress(progress_file: Path, record: dict) -> None:
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    with progress_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _is_retryable_response(resp: requests.Response) -> bool:
    if resp.status_code < 500:
        return False
    text = (resp.text or "")[:2000]
    retry_markers = ("ReadTimeout", "ConnectTimeout", "Timeout", "temporar")
    return any(marker in text for marker in retry_markers)


def _build_facts_instruction(case_key: str, row: dict) -> str:
    """Mirrors run_facts_extract._build_facts_instruction exactly."""
    case_name = str(row.get("case_name") or row.get("ecli") or "")
    facts_iri_base = f"https://github.com/dahrb/Art_6/tree/main/facts/{case_key}#"
    return (
        f"Target Case ID: {case_key}. "
        f"Case name: {case_name}. "
        "NAMESPACE POLICY: Use doc: for case-specific individuals and reserve seed: for shared ontology terms only. "
        f"doc: base IRI is <{facts_iri_base}>."
        "PARTIES: Extract applicants/appellants and link them to the case using seed:Party-compatible relations. "
        "DOMESTIC PROCEEDINGS: Build a chronological chain of domestic events as seed:DomesticProceeding instances. "
        "Use stable IRIs such as doc:proc_YYYY_MM_DD and suffix variants (_a, _b) when needed. "
        "MANDATORY SHAPE: Each proceeding should include one seed:hasDecisionDate (xsd:date) and one seed:hasCourt when evidence exists. "
        "CHAINING: Use seed:followsProceeding only when chronology supports it. Never create self-links or cycles. "
        "TYPED LITERALS: Dates must be xsd:date and booleans must be xsd:boolean. "
        "PREDICATE POLICY: Use declared ontology predicates only; do not invent undeclared predicates. "
        "EXTRACTION SCOPE: Prioritize party details, domestic chronology, proceeding duration, legal issues, and per-issue outcomes. "
        "COMPLETENESS: Extract as much grounded, schema-compatible information as the text supports."
        "Only use underscores in iris or alphanumeric characters, nothing else."
    )


def wait_for_server(timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{SERVER}/health", timeout=5)
            if r.status_code == 200:
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(2)
    return False


def main():
    print("Checking server health...")
    if not wait_for_server(timeout=10):
        print(f"ERROR: OntoCast server not reachable at {SERVER}")
        print("Start it first with: cd C:\\Postdoc\\Article_6 && .venv\\Scripts\\ontocast.exe --env-file ontology\\ontology_local.env")
        sys.exit(1)
    print("Server ready.\n")
    print(
        "Run settings: "
        f"workers={PARALLEL_WORKERS}, timeout={REQUEST_TIMEOUT_S}s, "
        f"max_retries={MAX_RETRIES}, backoff={RETRY_BACKOFF_S}s, "
        f"resume={RESUME}, progress_file={PROGRESS_FILE}"
    )

    df = pl.read_parquet(PARQUET)
    print(f"Parquet loaded: {df.height} rows")

    # Build _send_text from facts-only content. Cases with missing/short facts
    # are skipped to avoid unexpectedly sending full_text.
    available_facts_cols = [c for c in FACTS_COLS if c in df.columns]
    if available_facts_cols:
        facts_expr = pl.coalesce([pl.col(c).cast(pl.Utf8) for c in available_facts_cols])
        text_expr = pl.when(
            facts_expr.is_not_null() & (facts_expr.str.len_chars() >= MIN_FACTS_CHARS)
        ).then(facts_expr).otherwise(None)
        print(f"Using facts columns: {available_facts_cols} (≥{MIN_FACTS_CHARS} chars; no full_text fallback)")
    else:
        text_expr = pl.lit(None, dtype=pl.Utf8)
        print("No facts section columns found — all cases will be skipped")

    cases = (
        df.with_columns(text_expr.alias("_send_text"))
        .filter(
            pl.col("_send_text").is_not_null()
            & (pl.col("_send_text").str.len_chars() > 0)
        )
        .sort(["itemid", "ecli", "judgementdate"])
    )

    if RESUME:
        done_keys = _load_processed_case_keys(PROGRESS_FILE)
        if done_keys:
            cases = cases.filter(~pl.col("itemid").cast(pl.Utf8).is_in(sorted(done_keys)))
            print(f"Resume enabled: skipping {len(done_keys)} already processed case IDs")

    if N_CASES is not None:
        cases = cases.head(N_CASES)
    print(f"Selected {cases.height} cases.\n")

    if cases.height == 0:
        print("No pending facts-only cases to process. Exiting.")
        return

    rows = [cases.row(i, named=True) for i in range(cases.height)]
    total = len(rows)
    completed = 0

    def _print_and_record(prefix, idx, case_key, row, resp, elapsed, err, attempts):
        nonlocal completed
        if err:
            print(f"{prefix} -> EXCEPTION: {err}")
            _append_progress(PROGRESS_FILE, {"case_key": case_key, "status": "exception", "elapsed_s": round(elapsed, 3), "attempts": attempts, "error": err})
        elif resp is not None and resp.status_code == 200:
            body = resp.json()
            meta = body.get("metadata", {})
            budget = meta.get("budget", {})
            facts = meta.get("facts_triples") or meta.get("triple_count") or budget.get("facts_triples_generated") or "?"
            print(f"{prefix} -> OK  facts={facts}  name={str(row.get('case_name',''))[:60]}")
            _append_progress(PROGRESS_FILE, {"case_key": case_key, "status": "ok", "elapsed_s": round(elapsed, 3), "attempts": attempts, "facts": facts})
        else:
            status = resp.status_code if resp is not None else "?"
            text = resp.text[:200] if resp is not None else str(err)
            print(f"{prefix} -> ERROR {status}: {text}")
            _append_progress(PROGRESS_FILE, {"case_key": case_key, "status": "error", "elapsed_s": round(elapsed, 3), "attempts": attempts, "error": text})

    def submit(idx_row):
        idx, row = idx_row
        case_key = str(row.get("itemid") or row.get("ecli") or f"case_{idx+1}")
        send_text = str(row.get("_send_text") or "")
        # Strip ** bold markers so OntoCast's sentence splitter fires on paragraph
        # numbers like "4. The applicant..." — without this, **4.** defeats the
        # lookbehind (?<=[.!?]) and the text collapses to ≤5 "sentences" which
        # causes UMAP to fail with n_neighbors=2 > n_samples.
        send_text = send_text.replace("**", "")
        instruction = _build_facts_instruction(case_key, row)
        encoded_text = json.dumps(send_text).encode("utf-8")
        t0 = time.time()
        last_err = None

        for attempt in range(1, MAX_RETRIES + 2):
            try:
                resp = requests.post(
                    f"{SERVER}/process",
                    files={f"{case_key}.txt": (f"{case_key}.txt", encoded_text, "text/plain")},
                    data={
                        "facts_user_instruction": instruction,
                        "ontology_context_fixed_ontology_id": "seed",
                    },
                    timeout=REQUEST_TIMEOUT_S,
                )
                if _is_retryable_response(resp) and attempt <= MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_S * attempt)
                    continue
                return idx, case_key, row, resp, time.time() - t0, last_err, attempt
            except requests.exceptions.RequestException as exc:
                last_err = str(exc)
                if attempt <= MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_S * attempt)
                    continue
                return idx, case_key, row, None, time.time() - t0, last_err, attempt

        # Unreachable in normal flow; defensive return.
        return idx, case_key, row, None, time.time() - t0, last_err, MAX_RETRIES + 1

    with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as pool:
        # ── Warm-up ──────────────────────────────────────────────────────────
        # EntityClusterer._embedder (AGG step) uses lazy init with NO lock.
        # Submitting 8 parallel requests means 8 threads simultaneously call
        # SentenceTransformer() → PyTorch _fast_init race → meta tensor crash.
        # Fix: submit the first case alone BEFORE the parallel pool so the
        # model is loaded by a single thread; subsequent requests reuse it.
        if PARALLEL_WORKERS > 1 and rows:
            w_idx, w_key, w_row, w_resp, w_elapsed, w_err, w_attempts = submit((0, rows[0]))
            completed += 1
            prefix = f"[{completed}/{total}] {w_key}  ({w_elapsed:.0f}s, attempts={w_attempts}) [WARMUP]"
            _print_and_record(prefix, w_idx, w_key, w_row, w_resp, w_elapsed, w_err, w_attempts)
            rows = rows[1:]
            print(f"Warm-up done. Starting parallel submission for {len(rows)} remaining cases...\n")
        # ─────────────────────────────────────────────────────────────────────

        futures = {pool.submit(submit, (i + 1, row)): i for i, row in enumerate(rows)}
        for fut in as_completed(futures):
            idx, case_key, row, resp, elapsed, err, attempts = fut.result()
            completed += 1
            prefix = f"[{completed}/{total}] {case_key}  ({elapsed:.0f}s, attempts={attempts})"
            _print_and_record(prefix, idx, case_key, row, resp, elapsed, err, attempts)


if __name__ == "__main__":
    main()
