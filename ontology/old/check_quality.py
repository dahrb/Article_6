"""
check_quality.py
----------------
Post-run quality check for a single OntoCast facts run.

Usage:
    python check_quality.py <case_id>          # e.g. 001-5074
    python check_quality.py 001-5074

Queries Fuseki and prints a structured quality report covering:

  1. Namespace discipline  — are case-specific individuals in doc: or seed:?
  2. Case linkage          — is the case IRI the subject of >= 1 triple?
  3. Typed literals        — xsd:date and xsd:boolean present vs plain strings
  4. Unknown predicates    — predicates not declared in seed.ttl
  5. Triple inventory      — total facts written per named graph

Exit codes: 0 = all checks passed, 1 = one or more checks failed.
"""

import re
import sys
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

# ── Bootstrap env ────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT  = SCRIPT_DIR.parent

for _env in (REPO_ROOT / "keys.env", SCRIPT_DIR / "ontology.env"):
    if _env.exists():
        load_dotenv(_env, override=True)

# ── Fuseki connection ────────────────────────────────────────────────────────
_fuseki_base    = (os.getenv("FUSEKI_URI") or "http://localhost:3032").rstrip("/")
_fuseki_dataset = os.getenv("FUSEKI_DATASET", "Art_6_Ontology_Sub")
_auth_str       = os.getenv("FUSEKI_AUTH", "")
if "/" in _auth_str:
    _creds: tuple[str, str] | None = tuple(_auth_str.split("/", 1))  # type: ignore[assignment]
elif ":" in _auth_str:
    _creds = tuple(_auth_str.split(":", 1))  # type: ignore[assignment]
else:
    _creds = None

# ── IRI constants ────────────────────────────────────────────────────────────
_DATA_BASE_IRI  = "https://github.com/dahrb/Art_6/tree/main/ontology/metadata.ttl#"
_DATA_GRAPH_IRI = "https://github.com/dahrb/Art_6/tree/main/ontology/metadata.ttl"
_SEED_GRAPH_IRI = "https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl"
_SEED_NS        = "https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl#"
_FACTS_BASE     = "https://github.com/dahrb/Art_6/tree/main/facts/"


def _case_iri(case_id: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", case_id.strip()).strip("_").lower()
    return f"{_DATA_BASE_IRI}case_{slug or 'unknown'}"


def _facts_iri_base(case_id: str) -> str:
    return f"{_FACTS_BASE}{case_id}#"


def _sparql(query: str) -> list[dict]:
    url = f"{_fuseki_base}/{_fuseki_dataset}/sparql"
    try:
        resp = requests.post(
            url,
            data={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            auth=_creds,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("results", {}).get("bindings", [])
    except Exception as exc:
        print(f"  [ERROR] Fuseki query failed: {exc}")
        return []


def _count(query: str) -> int:
    rows = _sparql(query)
    if rows and "n" in rows[0]:
        return int(rows[0]["n"]["value"])
    return 0


# ── Individual checks ────────────────────────────────────────────────────────

def check_triple_inventory(case_id: str) -> tuple[bool, str]:
    """Count total triples in facts graphs (everything except metadata + seed)."""
    rows = _sparql(f"""
        SELECT ?g (COUNT(*) AS ?n) WHERE {{
          GRAPH ?g {{ ?s ?p ?o }}
          FILTER(?g NOT IN (<{_DATA_GRAPH_IRI}>, <{_SEED_GRAPH_IRI}>))
        }} GROUP BY ?g ORDER BY DESC(?n)
    """)
    if not rows:
        return False, "No facts graphs found — OntoCast may not have written anything"
    lines = [f"{int(r['n']['value']):>5}  {r['g']['value']}" for r in rows]
    total = sum(int(r["n"]["value"]) for r in rows)
    return True, f"Total: {total} triple(s) across {len(rows)} graph(s)\n" + "\n".join(f"       {l}" for l in lines)


def check_namespace_discipline(case_id: str) -> tuple[bool, str]:
    """Detect case-specific individuals incorrectly minted in seed: namespace."""
    doc_base  = _facts_iri_base(case_id)

    # Seed-namespace subjects that look like case data (not declared in seed schema itself)
    # We identify them as seed: subjects that appear ONLY in the facts graphs (not seed.ttl)
    seed_polluters = _count(f"""
        SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{
          GRAPH ?g {{ ?s ?p ?o }}
          FILTER(?g NOT IN (<{_DATA_GRAPH_IRI}>, <{_SEED_GRAPH_IRI}>))
          FILTER(STRSTARTS(STR(?s), "{_SEED_NS}"))
          FILTER NOT EXISTS {{
            GRAPH <{_SEED_GRAPH_IRI}> {{ ?s ?anything ?anyval }}
          }}
        }}
    """)

    # doc: subjects correctly using the case namespace
    doc_subjects = _count(f"""
        SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{
          GRAPH ?g {{ ?s ?p ?o }}
          FILTER(?g NOT IN (<{_DATA_GRAPH_IRI}>, <{_SEED_GRAPH_IRI}>))
          FILTER(STRSTARTS(STR(?s), "{doc_base}"))
        }}
    """)

    if seed_polluters > 0:
        return False, (
            f"FAIL — {seed_polluters} seed: subject(s) should be in doc: namespace\n"
            f"       {doc_subjects} subject(s) correctly using doc: namespace"
        )
    if doc_subjects == 0:
        return False, "FAIL — 0 doc: subjects found; facts may be empty or in wrong namespace"
    return True, f"OK — {doc_subjects} subject(s) correctly in doc: namespace, 0 seed: pollution"


def check_case_linkage(case_id: str) -> tuple[bool, str]:
    """Is the case IRI the subject of at least one triple in any facts graph?"""
    case_iri = _case_iri(case_id)
    n = _count(f"""
        SELECT (COUNT(*) AS ?n) WHERE {{
          GRAPH ?g {{ <{case_iri}> ?p ?o }}
          FILTER(?g NOT IN (<{_DATA_GRAPH_IRI}>, <{_SEED_GRAPH_IRI}>))
        }}
    """)
    if n == 0:
        return False, f"FAIL — case IRI <{case_iri}> has 0 outgoing triples in facts graphs"
    return True, f"OK — case IRI linked: {n} triple(s) with case as subject"


def check_typed_literals(case_id: str) -> tuple[bool, str]:
    """Count typed vs untyped dates in facts graphs."""
    typed_dates = _count(f"""
        SELECT (COUNT(*) AS ?n) WHERE {{
          GRAPH ?g {{ ?s ?p ?o }}
          FILTER(?g NOT IN (<{_DATA_GRAPH_IRI}>, <{_SEED_GRAPH_IRI}>))
          FILTER(DATATYPE(?o) = xsd:date)
        }}
    """)
    # Untyped strings that look like dates (YYYY-MM-DD pattern)
    plain_dates = _count(f"""
        SELECT (COUNT(*) AS ?n) WHERE {{
          GRAPH ?g {{ ?s ?p ?o }}
          FILTER(?g NOT IN (<{_DATA_GRAPH_IRI}>, <{_SEED_GRAPH_IRI}>))
          FILTER(DATATYPE(?o) = xsd:string || !isLiteral(?o) = false)
          FILTER(REGEX(STR(?o), "^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$"))
          FILTER(DATATYPE(?o) != xsd:date)
        }}
    """)
    typed_bools = _count(f"""
        SELECT (COUNT(*) AS ?n) WHERE {{
          GRAPH ?g {{ ?s ?p ?o }}
          FILTER(?g NOT IN (<{_DATA_GRAPH_IRI}>, <{_SEED_GRAPH_IRI}>))
          FILTER(DATATYPE(?o) = xsd:boolean)
        }}
    """)
    issues = []
    if plain_dates > 0:
        issues.append(f"{plain_dates} untyped date string(s) (should be ^^xsd:date)")
    msg = (
        f"xsd:date literals: {typed_dates}  |  xsd:boolean: {typed_bools}  |  "
        f"plain date strings: {plain_dates}"
    )
    if issues:
        return False, "FAIL — " + msg
    return True, "OK — " + msg


def check_unknown_predicates(case_id: str) -> tuple[bool, str]:
    """Flag predicates used in facts that are not declared in seed.ttl."""
    rows = _sparql(f"""
        SELECT DISTINCT ?p WHERE {{
          GRAPH ?g {{ ?s ?p ?o }}
          FILTER(?g NOT IN (<{_DATA_GRAPH_IRI}>, <{_SEED_GRAPH_IRI}>))
          FILTER(STRSTARTS(STR(?p), "{_SEED_NS}"))
          FILTER NOT EXISTS {{
            GRAPH <{_SEED_GRAPH_IRI}> {{
              {{ ?p a owl:ObjectProperty }} UNION
              {{ ?p a owl:DatatypeProperty }} UNION
              {{ ?p a owl:AnnotationProperty }} UNION
              {{ ?p a rdf:Property }}
            }}
          }}
        }} ORDER BY ?p
    """)
    if not rows:
        return True, "OK — all seed: predicates declared in seed.ttl"
    unknown = [r["p"]["value"].replace(_SEED_NS, "seed:") for r in rows]
    return False, (
        f"WARN — {len(unknown)} seed: predicate(s) used but not declared in seed.ttl:\n"
        + "\n".join(f"       {p}" for p in unknown)
    )


# ── Main ─────────────────────────────────────────────────────────────────────

def run_report(case_id: str) -> int:
    print(f"\n{'='*60}")
    print(f"  Quality report — case {case_id}")
    print(f"{'='*60}")

    checks = [
        ("Triple inventory",     check_triple_inventory(case_id)),
        ("Namespace discipline", check_namespace_discipline(case_id)),
        ("Case linkage",         check_case_linkage(case_id)),
        ("Typed literals",       check_typed_literals(case_id)),
        ("Unknown predicates",   check_unknown_predicates(case_id)),
    ]

    passed = 0
    failed = 0
    for label, (ok, msg) in checks:
        icon = "✓" if ok else "✗"
        print(f"\n  [{icon}] {label}")
        for line in msg.splitlines():
            print(f"      {line}")
        if ok:
            passed += 1
        else:
            failed += 1

    print(f"\n{'─'*60}")
    print(f"  {passed} passed  |  {failed} failed")
    print(f"{'='*60}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python check_quality.py <case_id>  (e.g. 001-5074)")
        sys.exit(1)
    sys.exit(run_report(sys.argv[1]))
