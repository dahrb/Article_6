"""
post_case_to_server.py
----------------------
Loads N English ECHR cases from the sample parquet, builds per-case facts/
ontology instructions, POSTs each sequentially to the running OntoCast server
at http://localhost:8999/process, and records per-case timing for evaluation.

What is saved (results/server_test/<run_id>/):
  <case_key>_facts.ttl   — aggregated facts Turtle returned by the server
                           (distinct from OntoCast's internal working files)
  run_summary.csv        — one row per case: timing, chunk counts, status

What is NOT saved here (OntoCast handles it internally):
  - Ontology artifacts / seed updates (written to ONTOCAST_WORKING_DIRECTORY)
  - Fuseki graph writes (handled by the server's triple store manager)

Last Updated : 2026-06-11
Version      : 1.1.1
Progress     : complete

Version History:
  - v1.0.0 (2026-06-08): Initial server run configuration.
  - v1.1.0 (2026-06-09): Added Gemini API integration and keys.env dynamic parser.
  - v1.1.1 (2026-06-11): Changed default N_CASES to 1 to run only the first case by default.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import polars as pl
import requests
from dotenv import load_dotenv

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT  = SCRIPT_DIR.parent

# ── Environment ───────────────────────────────────────────────────────────────
for _env in (REPO_ROOT / "keys.env", SCRIPT_DIR / "ontology.env"):
    if _env.exists():
        load_dotenv(_env, override=True)

# ── Config ────────────────────────────────────────────────────────────────────
SERVER_URL  = os.getenv("ONTOCAST_SERVER_URL", "http://localhost:8999")
N_CASES     = int(os.getenv("N_CASES", "1"))    # how many cases to run
START_INDEX = int(os.getenv("START_INDEX", "0")) # offset into the filtered parquet

# ── IRI helpers ───────────────────────────────────────────────────────────────
_DATA_BASE_IRI = "https://github.com/dahrb/Art_6/tree/main/ontology/metadata.ttl#"
_FACTS_BASE    = "https://github.com/dahrb/Art_6/tree/main/facts"

def _case_iri(case_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", case_id.strip()).strip("_").lower()
    return f"{_DATA_BASE_IRI}case_{slug or 'unknown'}"

def _facts_iri_base(case_id: str) -> str:
    return f"{_FACTS_BASE}/{case_id}#"

# ── Load cases from parquet ───────────────────────────────────────────────────
_parquet = SCRIPT_DIR / "sample_metadata.parquet"
if not _parquet.exists():
    sys.exit(f"Missing parquet: {_parquet}")

df  = pl.read_parquet(_parquet)
eng = (
    df.filter(
        pl.col("full_text").is_not_null()
        & (pl.col("full_text").cast(pl.Utf8).str.len_chars() > 0)
        & (pl.col("languageisocode").cast(pl.Utf8).str.to_uppercase() == "ENG")
    )
    .sort(["itemid", "ecli", "judgementdate"])
    .slice(START_INDEX, N_CASES)
)

cases = [eng.row(i, named=True) for i in range(eng.height)]
print(f"Server    : {SERVER_URL}")
print(f"Cases     : {len(cases)} (rows {START_INDEX}–{START_INDEX + len(cases) - 1})")
print()

# ── Check server health before starting ──────────────────────────────────────
try:
    health = requests.get(f"{SERVER_URL}/health", timeout=10)
    health.raise_for_status()
    print(f"Server health : {health.json().get('status', 'ok')}\n")
except Exception as exc:
    sys.exit(
        f"[ERROR] Cannot reach {SERVER_URL}/health — {exc}\n"
        "Start the server with: .\\start_server.ps1"
    )

# ── Instruction builders ──────────────────────────────────────────────────────
def build_facts_instruction(case_key: str) -> str:
    doc_ns = _facts_iri_base(case_key)
    return (
        f"Target Case ID: {case_key}. "
        "NAMESPACE POLICY: Use doc: for case-specific individuals and reserve seed: for shared ontology terms only. "
        f"doc: base IRI is <{doc_ns}>. "
        "PARTIES: Extract applicants/appellants and link them to the case using seed:Party-compatible relations. "
        "DOMESTIC PROCEEDINGS: Build a chronological chain of domestic events as seed:DomesticProceeding instances. "
        "Use stable IRIs such as doc:proc_YYYY_MM_DD and suffix variants (_a, _b) when needed. "
        "MANDATORY SHAPE: Each proceeding should include one seed:hasDecisionDate (xsd:date) and one seed:hasCourt when evidence exists. "
        "CHAINING: Use seed:followsProceeding only when chronology supports it. Never create self-links or cycles. "
        "TYPED LITERALS: Dates must be xsd:date and booleans must be xsd:boolean. "
        "PREDICATE POLICY: Use declared ontology predicates only; do not invent undeclared predicates. "
        "EXTRACTION SCOPE: Prioritize party details, domestic chronology, proceeding duration, legal issues, and per-issue outcomes. "
        "COMPLETENESS: Extract as much grounded, schema-compatible information as the text supports. "
        "Only use underscores in IRIs or alphanumeric characters, nothing else."
    )

ONTOLOGY_INSTRUCTION = (
    "1. NAMESPACE: Use ONLY the IRI base <https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl#> for all new entities. "
    "2. TYPES: Use proper owl:Class for entities (e.g., seed:Hearing). Do not use property IRIs as rdf:type. "
    "3. DATATYPES: Adhere strictly to XSD types (xsd:boolean, xsd:string, xsd:date) for all owl:DatatypeProperty ranges. "
    "4. DOMAIN SEMANTICS: seed:Judgment and seed:Decision are reserved exclusively for ECHR records not domestic proceedings. "
)

# ── Output directory ──────────────────────────────────────────────────────────
run_id  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
out_dir = REPO_ROOT / "results" / "server_test" / run_id
out_dir.mkdir(parents=True, exist_ok=True)
print(f"Output dir : {out_dir}\n")
print("=" * 70)

# ── Per-case processing ───────────────────────────────────────────────────────
summary_rows: list[dict] = []

for idx, row in enumerate(cases, start=1):
    case_key   = str(row.get("itemid") or row.get("ecli") or f"case_{idx}")
    input_text = str(row["full_text"])
    safe_key   = re.sub(r"[^A-Za-z0-9_.-]+", "_", case_key)

    print(f"[{idx}/{len(cases)}] {case_key}")
    print(f"  Text len : {len(input_text):,} chars")

    file_bytes        = json.dumps(input_text).encode("utf-8")
    facts_instruction = build_facts_instruction(case_key)

    t_start = perf_counter()
    try:
        resp = requests.post(
            f"{SERVER_URL}/process",
            files={
                f"{case_key}.txt": (f"{case_key}.txt", file_bytes, "text/plain"),
            },
            data={
                "facts_user_instruction":        facts_instruction,
                "ontology_user_instruction":     ONTOLOGY_INSTRUCTION,
                # Required: server is in fixed_single_ontology mode and reads
                # ontology_context_fixed_ontology_id from the request, not env.
                "ontology_context_fixed_ontology_id": "seed",
            },
        )
        
        elapsed = perf_counter() - t_start


    except (requests.exceptions.ConnectionError, OSError) as exc:
        elapsed = perf_counter() - t_start
        print(f"  [ERROR] Connection failed after {elapsed:.1f}s: {exc}")
        print("  Server may have crashed — check its terminal window.")
        summary_rows.append({
            "idx": idx, "case_key": case_key,
            "status": "connection_error", "http_code": None,
            "elapsed_s": round(elapsed, 2),
            "chunks_processed": None, "chunks_remaining": None,
            "facts_chars": 0, "error": str(exc),
        })
        continue

    elapsed = perf_counter() - t_start

    if resp.status_code != 200:
        print(f"  [ERROR] HTTP {resp.status_code} in {elapsed:.1f}s")
        print(f"  Body   : {resp.text[:400]}")
        summary_rows.append({
            "idx": idx, "case_key": case_key,
            "status": "http_error", "http_code": resp.status_code,
            "elapsed_s": round(elapsed, 2),
            "chunks_processed": None, "chunks_remaining": None,
            "facts_chars": 0, "error": resp.text[:300],
        })
        continue

    payload    = resp.json()
    srv_status = payload.get("status", "unknown")
    data       = payload.get("data", {})
    meta       = payload.get("metadata", {})
    facts_ttl  = data.get("facts", "")

    chunks_ok  = meta.get("chunks_processed", "?")
    chunks_rem = meta.get("chunks_remaining", "?")

    print(f"  Status   : {srv_status}  |  {elapsed:.1f}s")
    print(f"  Chunks   : {chunks_ok} processed, {chunks_rem} remaining")
    print(f"  Facts    : {len(facts_ttl):,} chars")

    # Save facts TTL for this case (aggregated response, useful for evaluation)
    if facts_ttl:
        ttl_path = out_dir / f"{safe_key}_facts.ttl"
        ttl_path.write_text(facts_ttl, encoding="utf-8")
        print(f"  Saved    : {ttl_path.name}")

    summary_rows.append({
        "idx": idx,
        "case_key": case_key,
        "status": srv_status,
        "http_code": resp.status_code,
        "elapsed_s": round(elapsed, 2),
        "chunks_processed": chunks_ok,
        "chunks_remaining": chunks_rem,
        "facts_chars": len(facts_ttl),
        "error": payload.get("error", ""),
    })
    print()

# ── Write summary CSV ─────────────────────────────────────────────────────────
csv_path = out_dir / "run_summary.csv"
fieldnames = [
    "idx", "case_key", "status", "http_code",
    "elapsed_s", "chunks_processed", "chunks_remaining", "facts_chars", "error",
]
with csv_path.open("w", newline="", encoding="utf-8") as fh:
    writer = csv.DictWriter(fh, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(summary_rows)

# ── Print run summary ─────────────────────────────────────────────────────────
print("=" * 70)
n_ok    = sum(1 for r in summary_rows if r["status"] == "success")
n_err   = len(summary_rows) - n_ok
total_s = sum(r["elapsed_s"] for r in summary_rows)
avg_s   = total_s / len(summary_rows) if summary_rows else 0

print(f"Run complete : {len(summary_rows)} cases — {n_ok} OK, {n_err} errors")
print(f"Total time   : {total_s:.1f}s   Avg per case: {avg_s:.1f}s")
print(f"Summary CSV  : {csv_path}")
print()
print(f"{'#':>3}  {'Case key':<30}  {'Status':<12}  {'Elapsed':>8}  {'Facts chars':>12}")
print("-" * 70)
for r in summary_rows:
    print(
        f"{r['idx']:>3}  {r['case_key'][:30]:<30}  {str(r['status']):<12}  "
        f"{r['elapsed_s']:>7.1f}s  {r['facts_chars']:>12,}"
    )
