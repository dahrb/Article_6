"""
run_single_case.py
------------------
Minimalistic OntoCast test: feed ONE case through OntoCast, print the
ontology changes it made and the new triples added to Fuseki.

Optionally (SHOW_GRAPH = True) renders a NetworkX graph where
  blue  = pre-existing metadata nodes/edges already in Fuseki
  red   = new nodes/edges added by OntoCast this run

Output files go to  results/ontocast_tests/
  <timestamp>_<case>_<model>.txt
  <timestamp>_<case>_<model>_graph.png  (when SHOW_GRAPH is True)
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from collections import defaultdict

import polars as pl
import requests
from dotenv import load_dotenv

# ── Toggles ────────────────────────────────────────────────────────────────
SHOW_GRAPH: bool = True   # set False to skip the NetworkX / matplotlib graph
CASE_INDEX: int  = 121    # HARUTYUNYAN AND HAKOBYAN v. ARMENIA (001-229423) — shortest Art.6 violation, 4,068 chars
# ───────────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT  = SCRIPT_DIR.parent

# ── Load environment ────────────────────────────────────────────────────────
for _env_path in (REPO_ROOT / "keys.env", SCRIPT_DIR / "ontology.env"):
    if not _env_path.exists():
        sys.exit(f"Missing env file: {_env_path}")
    load_dotenv(_env_path, override=True)

from ontocast.config import Config        
from ontocast.onto.state import AgentState  
from ontocast.stategraph import create_agent_graph  
from ontocast.toolbox import ToolBox        

# ── Load case from parquet ──────────────────────────────────────────────────
# this just filters out 1 english case to test with 
_parquet = REPO_ROOT / "data" / "sample_metadata.parquet"
if not _parquet.exists():
    raise FileNotFoundError('No parquet suitable')

df  = pl.read_parquet(_parquet)
eng = df.filter(
    pl.col("full_text").is_not_null()
    & (pl.col("full_text").cast(pl.Utf8).str.len_chars() > 0)
    & (pl.col("languageisocode").cast(pl.Utf8).str.to_uppercase() == "ENG")
)

one_case   = eng.row(CASE_INDEX, named=True)
case_key   = str(one_case.get("itemid") or one_case.get("ecli") or "unknown")
input_text = str(one_case["full_text"])
_model     = os.getenv("LLM_MODEL_NAME", "unknown-model")

print(f"Case     : {case_key}")
print(f"Name     : {one_case.get('case_name')}")
print(f"Source   : {one_case.get('source')}")
print(f"Text len : {len(input_text):,} chars")
print(f"Model    : {_model}")
# _case_iri is defined after the Fuseki helpers block; echo below instead
_case_id_slug = re.sub(r"[^A-Za-z0-9]+", "_", case_key.strip()).strip("_").lower()
print(f"Case IRI : https://github.com/dahrb/Art_6/tree/main/ontology/metadata.ttl#case_{_case_id_slug}")

# ── Fuseki SPARQL helpers ───────────────────────────────────────────────────
_fuseki_base    = (os.getenv("FUSEKI_URI") or "http://localhost:3032").rstrip("/")
_fuseki_dataset = os.getenv("FUSEKI_DATASET", "Art_6_Ontology")

# Parse credentials back to (user, password) tuple for requests
_auth_str = os.getenv("FUSEKI_AUTH", "")
if "/" in _auth_str:
    _fuseki_creds: tuple[str, str] | None = tuple(_auth_str.split("/", 1))  # type: ignore[assignment]
elif ":" in _auth_str:
    _fuseki_creds = tuple(_auth_str.split(":", 1))  # type: ignore[assignment]
else:
    _fuseki_creds = None

# Must match the IRI minting logic in ingest_metadata.py
_DATA_BASE_IRI  = "https://github.com/dahrb/Art_6/tree/main/ontology/metadata.ttl#"
_DATA_GRAPH_IRI = "https://github.com/dahrb/Art_6/tree/main/ontology/metadata.ttl"
_SEED_GRAPH_IRI = "https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl"


def _case_iri(case_id: str) -> str:
    """Reproduce ingest_metadata.slugify to build the exact case IRI."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", case_id.strip()).strip("_").lower()
    return f"{_DATA_BASE_IRI}case_{slug or 'unknown'}"


def _fuseki_triples(
    case_id: str,
    *,
    all_graphs: bool = False,
) -> set[tuple[str, str, str]]:
    """Return the 1-hop subgraph from Fuseki for *case_id*.

    When all_graphs=False (before-snapshot): queries only the named metadata
    graph, which contains the structured ingest data.

    When all_graphs=True (after-snapshot): queries across ALL named graphs in
    the dataset so OntoCast's facts graph (urn:data:default or similar) is
    included alongside the metadata graph, capturing any new triples added.
    """
    iri = _case_iri(case_id)
    if all_graphs:
        # Search every named graph — picks up OntoCast's facts graph too
        graph_clause = ""
        inner = f"""
    GRAPH ?_g {{
      {{
        <{iri}> ?p ?o .
        BIND(<{iri}> AS ?s)
      }} UNION {{
        <{iri}> ?_hop ?mid .
        FILTER(isIRI(?mid))
        ?mid ?p ?o .
        BIND(?mid AS ?s)
      }}
    }}"""
    else:
        inner = f"""
    GRAPH <{_DATA_GRAPH_IRI}> {{
      {{
        <{iri}> ?p ?o .
        BIND(<{iri}> AS ?s)
      }} UNION {{
        <{iri}> ?_hop ?mid .
        FILTER(isIRI(?mid))
        ?mid ?p ?o .
        BIND(?mid AS ?s)
      }}
    }}"""
    sparql = f"SELECT ?s ?p ?o WHERE {{{inner}\n}}"
    url = f"{_fuseki_base}/{_fuseki_dataset}/sparql"
    try:
        resp = requests.post(
            url,
            data={"query": sparql},
            headers={"Accept": "application/sparql-results+json"},
            auth=_fuseki_creds,
            timeout=30,
        )
        resp.raise_for_status()
        bindings = resp.json().get("results", {}).get("bindings", [])
        return {
            (b["s"]["value"], b["p"]["value"], b["o"]["value"])
            for b in bindings
            if "s" in b and "p" in b and "o" in b
        }
    except Exception as exc:
        print(f"[WARN] Fuseki SPARQL query failed: {exc}")
        return set()


def _fuseki_triples_with_graph(case_id: str) -> set[tuple[str, str, str, str]]:
    """Like _fuseki_triples(all_graphs=True) but also returns the named-graph IRI.

    Returns a set of (s, p, o, graph_iri) 4-tuples so callers can track which
    named graph each triple lives in (metadata.ttl vs OntoCast's facts graph).
    """
    iri = _case_iri(case_id)
    sparql = f"""SELECT ?s ?p ?o ?g WHERE {{
  GRAPH ?g {{
    {{
      <{iri}> ?p ?o .
      BIND(<{iri}> AS ?s)
    }} UNION {{
      <{iri}> ?_hop ?mid .
      FILTER(isIRI(?mid))
      ?mid ?p ?o .
      BIND(?mid AS ?s)
    }}
  }}
}}"""
    url = f"{_fuseki_base}/{_fuseki_dataset}/sparql"
    try:
        resp = requests.post(
            url,
            data={"query": sparql},
            headers={"Accept": "application/sparql-results+json"},
            auth=_fuseki_creds,
            timeout=30,
        )
        resp.raise_for_status()
        bindings = resp.json().get("results", {}).get("bindings", [])
        return {
            (
                b["s"]["value"],
                b["p"]["value"],
                b["o"]["value"],
                b["g"]["value"],
            )
            for b in bindings
            if "s" in b and "p" in b and "o" in b and "g" in b
        }
    except Exception as exc:
        print(f"[WARN] Fuseki SPARQL query (with-graph) failed: {exc}")
        return set()


_ONTOCAST_ONTOLOGIES_DATASET = "ontocast--test--ontologies"


def _flush_ontocast_graphs() -> None:
    """Drop all hash-versioned artifact graphs from the ontologies dataset.

    OntoCast accumulates versioned graphs named like:
        <seed_iri>#<sha256-hash>
    After each run these become stale and cause an identity-conflict crash on
    the next run (the old graph uses 'seed' as prefix, the fresh seed.ttl uses
    'echr').  Dropping them before every run keeps the store clean.

    The base seed.ttl graph itself is preserved.
    """
    url_sparql = f"{_fuseki_base}/{_ONTOCAST_ONTOLOGIES_DATASET}/sparql"
    url_update = f"{_fuseki_base}/{_ONTOCAST_ONTOLOGIES_DATASET}/update"
    try:
        resp = requests.post(
            url_sparql,
            data={"query": "SELECT DISTINCT ?g WHERE { GRAPH ?g { ?s ?p ?o } }"},
            headers={"Accept": "application/sparql-results+json"},
            auth=_fuseki_creds,
            timeout=15,
        )
        resp.raise_for_status()
        graphs = [b["g"]["value"] for b in resp.json().get("results", {}).get("bindings", [])]
        dropped = 0
        for g in graphs:
            # Keep the canonical seed.ttl graph; drop all hash-suffixed versions
            if g != _SEED_GRAPH_IRI and g.startswith(_SEED_GRAPH_IRI):
                drop_resp = requests.post(
                    url_update,
                    data={"update": f"DROP GRAPH <{g}>"},
                    auth=_fuseki_creds,
                    timeout=15,
                )
                if drop_resp.ok:
                    dropped += 1
                else:
                    print(f"[WARN] Could not drop graph {g}: {drop_resp.status_code}")
        if dropped:
            print(f"  Flushed {dropped} stale OntoCast artifact graph(s)")
    except Exception as exc:
        print(f"[WARN] _flush_ontocast_graphs failed: {exc}")


def _flush_local_ontologies_dir() -> None:
    """Delete all stale OntoCast-generated TTL artifacts from the ontologies directory.

    On startup, OntoCast globs every *.ttl in ONTOCAST_ONTOLOGY_DIRECTORY and loads
    them all via FilesystemTripleStoreManager.fetch_ontologies().  Files left over
    from a previous run (ontology_seed_*.ttl, facts_*.ttl, facts_*_clean.ttl) are
    re-ingested as ontologies, which either pollutes the OntologyManager context or
    crashes with an identity-conflict error if the prefix/IRI has changed since.

    We preserve only seed.ttl and delete everything else.
    """
    protected = {"seed.ttl"}
    removed = 0
    for ttl_file in sorted(_ontocast_ont_dir.glob("*.ttl")):
        if ttl_file.name not in protected:
            try:
                ttl_file.unlink()
                removed += 1
            except Exception as exc:
                print(f"[WARN] Could not delete {ttl_file.name}: {exc}")
    if removed:
        print(f"  Removed {removed} stale TTL artifact(s) from {_ontocast_ont_dir}")


def _refresh_seed_graph() -> None:
    """Push the current local seed.ttl into Fuseki's ontologies dataset.

    OntoCast resolves the ontology context from Fuseki (ontocast--test--ontologies),
    not from the local file.  This ensures the live seed.ttl (with DomesticProceeding
    and all recent additions) is what OntoCast actually sees during every run.
    Without this, any class or property added locally after the last ingest_metadata
    run will appear absent to OntoCast's facts and critic agents.
    """
    seed_path = _ontocast_ont_dir / "seed.ttl"
    if not seed_path.exists():
        print(f"[WARN] _refresh_seed_graph: {seed_path} not found; skipping")
        return
    url = (
        f"{_fuseki_base}/{_ONTOCAST_ONTOLOGIES_DATASET}/data"
        f"?graph={_SEED_GRAPH_IRI}"
    )
    try:
        with seed_path.open("rb") as fh:
            resp = requests.put(
                url,
                data=fh,
                headers={"Content-Type": "text/turtle"},
                auth=_fuseki_creds,
                timeout=30,
            )
        resp.raise_for_status()
        print(f"  Seed graph refreshed in {_ONTOCAST_ONTOLOGIES_DATASET} ({seed_path.stat().st_size:,} bytes)")
    except Exception as exc:
        print(f"[WARN] _refresh_seed_graph failed: {exc}")


def _flush_facts_graphs() -> None:
    """Drop all OntoCast-written graphs from the facts dataset before a run.

    Keeps the canonical metadata.ttl and seed.ttl graphs; drops everything
    else (e.g. https://growgraph.dev/doc/<hash>/ graphs written by OntoCast
    on a previous run).  Prevents stale triples from polluting the diff and
    avoids OntoCast loading old ontology artifacts that cause identity
    conflicts.
    """
    url_sparql = f"{_fuseki_base}/{_fuseki_dataset}/sparql"
    url_update = f"{_fuseki_base}/{_fuseki_dataset}/update"
    _keep = {_DATA_GRAPH_IRI, _SEED_GRAPH_IRI}
    try:
        resp = requests.post(
            url_sparql,
            data={"query": "SELECT DISTINCT ?g WHERE { GRAPH ?g { ?s ?p ?o } }"},
            headers={"Accept": "application/sparql-results+json"},
            auth=_fuseki_creds,
            timeout=15,
        )
        resp.raise_for_status()
        graphs = [b["g"]["value"] for b in resp.json().get("results", {}).get("bindings", [])]
        dropped = 0
        for g in graphs:
            if g not in _keep:
                drop_resp = requests.post(
                    url_update,
                    data={"update": f"DROP GRAPH <{g}>"},
                    auth=_fuseki_creds,
                    timeout=15,
                )
                if drop_resp.ok:
                    dropped += 1
                else:
                    print(f"[WARN] Could not drop facts graph {g}: {drop_resp.status_code}")
        if dropped:
            print(f"  Flushed {dropped} stale facts graph(s) from {_fuseki_dataset}")
    except Exception as exc:
        print(f"[WARN] _flush_facts_graphs failed: {exc}")


def _fuseki_ontocast_triples() -> set[tuple[str, str, str, str]]:
    """Return all (s, p, o, graph_iri) triples in OntoCast's output graphs.

    Queries every named graph in the dataset EXCEPT metadata.ttl and seed.ttl,
    which are managed by ingest_metadata.py.  Whatever remains is what OntoCast
    has written, regardless of which IRI scheme it used for the named graph.

    Note: FILTER must be OUTSIDE the GRAPH ?g block.  In Jena/ARQ, a FILTER
    inside GRAPH ?g { } that tests the graph variable itself evaluates to zero
    results — the variable is not in scope for the filter expression. Moving it
    outside produces correct behaviour.
    """
    sparql = f"""SELECT ?s ?p ?o ?g WHERE {{
  GRAPH ?g {{
    ?s ?p ?o .
  }}
  FILTER(?g NOT IN (<{_DATA_GRAPH_IRI}>, <{_SEED_GRAPH_IRI}>))
}}"""
    url = f"{_fuseki_base}/{_fuseki_dataset}/sparql"
    try:
        resp = requests.post(
            url,
            data={"query": sparql},
            headers={"Accept": "application/sparql-results+json"},
            auth=_fuseki_creds,
            timeout=60,
        )
        resp.raise_for_status()
        bindings = resp.json().get("results", {}).get("bindings", [])
        return {
            (b["s"]["value"], b["p"]["value"], b["o"]["value"], b["g"]["value"])
            for b in bindings
            if "s" in b and "p" in b and "o" in b and "g" in b
        }
    except Exception as exc:
        print(f"[WARN] Fuseki SPARQL (OntoCast graphs) failed: {exc}")
        return set()


# ── Before snapshot ─────────────────────────────────────────────────────────
# Resolve OntoCast directory paths early — needed by the flush helpers below.
_ontocast_work_dir = Path(os.getenv("ONTOCAST_WORKING_DIRECTORY", str(SCRIPT_DIR / "ontologies")))
_ontocast_ont_dir  = Path(os.getenv("ONTOCAST_ONTOLOGY_DIRECTORY", str(_ontocast_work_dir)))
_ontocast_work_dir.mkdir(parents=True, exist_ok=True)
_ontocast_ont_dir.mkdir(parents=True, exist_ok=True)

print("\nQuerying Fuseki — before run …")
_flush_local_ontologies_dir()  # removes stale ontology_seed_*.ttl / facts_*.ttl artefacts
_refresh_seed_graph()      # keeps Fuseki's seed schema in sync with the local ontologies/seed.ttl
_flush_ontocast_graphs()   # clears stale hash artifacts from ontocast--test--ontologies
_flush_facts_graphs()      # clears old growgraph.dev/* facts from Art_6_Ontology_Sub
before_triples       = _fuseki_triples(case_key)
before_ontocast_full = _fuseki_ontocast_triples()
print(f"  {len(before_triples)} triple(s) in metadata graph for this case")
print(f"  {len(before_ontocast_full)} triple(s) in OntoCast graphs (baseline)")

#write before triples for inspection 
_before_txt = SCRIPT_DIR / f"before_triples_{re.sub(r'[^A-Za-z0-9_.-]+', '_', case_key)}.txt"
with _before_txt.open("w", encoding="utf-8") as _f:
    _f.write(f"Before-snapshot triples for case: {case_key}\n")
    _f.write(f"Case IRI: {_case_iri(case_key)}\n")
    _f.write(f"Total: {len(before_triples)} triple(s)\n")
    _f.write("=" * 70 + "\n\n")
    for s, p, o in sorted(before_triples):
        _f.write(f"S  {s}\nP  {p}\nO  {o}\n\n")
print(f"  Before-triples written : {_before_txt}")

# ── Serialise before_triples as compact Turtle for facts agent context ────────
# The full 1-hop metadata subgraph is injected into the facts prompt so the LLM
# can link (owl:sameAs) its doc: instances to the canonical metadata IRIs.
# Uses a simple prefix map; grouped by subject for readability.
# doc: is the case-specific facts namespace — must be declared so the model
# knows which IRI it resolves to (prevents fallback to seed: namespace).
_FACTS_IRI_BASE = f"https://github.com/dahrb/Art_6/tree/main/facts/{case_key}#"
_META_PREFIXES: dict[str, str] = {
    "https://github.com/dahrb/Art_6/tree/main/ontology/metadata.ttl#": "meta",
    "https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl#": "seed",
    _FACTS_IRI_BASE: "doc",
    "http://www.w3.org/2000/01/rdf-schema#": "rdfs",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#": "rdf",
    "http://www.w3.org/2002/07/owl#": "owl",
    "http://www.wikidata.org/entity/": "wd",
}

def _shorten_iri(iri: str) -> str:
    for ns, prefix in _META_PREFIXES.items():
        if iri.startswith(ns):
            return f"{prefix}:{iri[len(ns):]}"
    return f"<{iri}>"

_by_subj: dict[str, list[tuple[str, str]]] = {}
for _s, _p, _o in sorted(before_triples):
    _by_subj.setdefault(_s, []).append((_p, _o))

_ttl_lines: list[str] = [
    f"@prefix {pfx}: <{ns}> ." for ns, pfx in _META_PREFIXES.items()
] + [""]
for _subj, _pos in sorted(_by_subj.items()):
    _ttl_lines.append(_shorten_iri(_subj))
    for _i, (_p, _o) in enumerate(_pos):
        _sep = " ;" if _i < len(_pos) - 1 else " ."
        _ttl_lines.append(f"    {_shorten_iri(_p)} {_shorten_iri(_o)}{_sep}")
    _ttl_lines.append("")

_meta_graph_ttl = "\n".join(_ttl_lines)
_meta_ctx = (
    "5. EXISTING METADATA GRAPH — the following Turtle is already stored for this case. "
    "Do NOT reproduce or overwrite these triples — treat them as read-only context.\n"
    "Ignore m"
    + _meta_graph_ttl
)
print(f"  Metadata context       : {len(before_triples)} triple(s) serialised into facts prompt")

# ── Running Ontocast ─────────────────────────────────────────────────────────

# Ensure OntoCast's working/ontology directories exist (already created above)
_ontocast_work_dir.mkdir(parents=True, exist_ok=True)
_ontocast_ont_dir.mkdir(parents=True, exist_ok=True)

_facts_instr = (
    f"Target Case IRI: <{_case_iri(case_key)}>. Use this as the subject for ALL facts. "
    f"NAMESPACE: All case-specific individual IRIs MUST use the doc: prefix (= <{_FACTS_IRI_BASE}>). "
    "NEVER use the seed: prefix for case-specific individuals — seed: is reserved for shared ontology vocabulary only. "
    "1. PARTIES: Link all applicants/appellants to the case using seed:Party. "
    "2. DOMESTIC PROCEEDINGS: Extract domestic proceedings as a chronological chain."
    "CLASS: Use seed:DomesticProceeding."
    "IRI NAMING: Use doc:proc_YYYY_MM_DD for proceeding instances. If a date is reused, reuse the SAME IRI."
    "SPLIT RULE: If one date involves multiple courts or decisions, mint unique IRIs (e.g. doc:proc_YYYY_MM_DD_a, doc:proc_YYYY_MM_DD_b). No IRI should have multiple distinct labels."
    "MANDATORY PROPERTIES: Each instance must have exactly one seed:hasDecisionDate (xsd:date) and one seed:hasCourt (foaf:Organization)."
    "CHAINING: Link nodes using seed:followsProceeding, ensure no followsProceeding triple may have subject == object on any node."
    "CONSTRAINT: The object date must precede the subject date; prevent cycles (A follows A)."
    "TYPED LITERALS: All date values MUST use ^^xsd:date (e.g. \"1991-06-05\"^^xsd:date). Boolean values MUST use ^^xsd:boolean. "
    "PREDICATES: Use only declared prefixes (doc:, seed:, rdfs:, rdf:, owl:, schema1:, foaf:, prov:, xsd:). "
    "All new predicates must use the seed: prefix. If no suitable predicate exists, omit the data. "
    "Do not set initiate_search=true — all required ontology vocabulary is already defined in the provided seed schema. "
    "3. SCOPE: Prioritize extraction of: "
    "- Party metadata & demographics such as age, gender, nationality etc."
    "- Chronology of domestic proceedings & outcomes. "
    "- Total length of proceedings leading up to the ECHR case in years. "
    "- Legal issues and concepts raised and the outcome of those individual issues. "
    "4. COMPLETENESS: Map extracted data to the full extent of the seed schema classes. "
    "Try to flesh out the extracted facts as much as possible, linking people to nationality, gender etc "
    "classes and linking courts to their country of origin if known. "
    + _meta_ctx
)

state = AgentState(
    raw_input={f"{case_key}.txt": json.dumps(input_text).encode("utf-8")},
    ontology_user_instruction=(
        "1. NAMESPACE: Use ONLY the IRI base <https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl#> for all new entities. "
        "2. TYPES: Use proper owl:Class for entities (e.g., seed:Hearing). Do not use property IRIs as rdf:type. "
        "3. DATATYPES: Adhere strictly to XSD types (xsd:boolean, xsd:string, xsd:date) for all owl:DatatypeProperty ranges. "
        "4. DOMAIN SEMANTICS: seed:Judgment and seed:Decision are reserved exclusively for ECHR records not domestic proceedings."
        "5. AUGMENT NOT OVERWRITE: You have been given the 1-hop metadata sub-graph, add triples which are missing but don't overwrite existing ones."
    ),
    facts_user_instruction=_facts_instr,
)
config = Config()
tools  = ToolBox(config)


async def _run() -> Any:
    await tools.initialize()
    graph = create_agent_graph(tools)
    return await graph.ainvoke(state)

print("\nRunning OntoCast …")
t0     = perf_counter()
result = asyncio.run(_run())
elapsed = perf_counter() - t0
print(f"Done in {elapsed:.1f}s")

out: dict[str, Any] = (
    result if isinstance(result, dict)
    else (result.model_dump() if hasattr(result, "model_dump") else result.dict())
)

# ── After snapshot + diff ───────────────────────────────────────────────────
print("\nQuerying Fuseki — after run …")
# Diff OntoCast's own graphs (everything except metadata.ttl / seed.ttl).
# This captures all new facts regardless of whether they link back to the case IRI.
after_ontocast_full = _fuseki_ontocast_triples()
new_triples_4       = after_ontocast_full - before_ontocast_full
new_triples         = {(s, p, o) for s, p, o, _g in new_triples_4}
_graph_of: dict[tuple[str, str, str], str] = {
    (s, p, o): g for s, p, o, g in new_triples_4
}
# OntoCast may commit its Fuseki push slightly after asyncio.run() returns.
# Fallback: load from the clean facts TTL that OntoCast writes synchronously.
_new_triples_source = "Fuseki"
if not new_triples:
    from rdflib import Graph as _RDFGraph, BNode as _BNd
    _SKIP_PRED = {
        "http://www.w3.org/ns/prov#wasDerivedFrom",
        "http://www.w3.org/ns/prov#generatedAtTime",
        "https://schema.org/position",
    }
    _facts_files = sorted(_ontocast_work_dir.glob("facts_*_clean.ttl"))
    if not _facts_files:
        _facts_files = sorted(_ontocast_work_dir.glob("facts_*.ttl"),
                              key=lambda p: ("_clean" not in p.stem, p.name))
    if _facts_files:
        print(f"  [INFO] Fuseki returned 0 — loading from {_facts_files[-1].name}")
        _ttl_g = _RDFGraph()
        try:
            _ttl_g.parse(str(_facts_files[-1]), format="turtle")
        except Exception as _exc:
            print(f"  [WARN] TTL parse error: {_exc}")
        _ttl_graph_iri = f"file://ontologies/{_facts_files[-1].name}"
        for _ts, _tp, _to in _ttl_g:
            if isinstance(_ts, _BNd) or isinstance(_to, _BNd):
                continue
            if str(_tp) in _SKIP_PRED:
                continue
            _sstr, _pstr, _ostr = str(_ts), str(_tp), str(_to)
            new_triples.add((_sstr, _pstr, _ostr))
            new_triples_4.add((_sstr, _pstr, _ostr, _ttl_graph_iri))
        _graph_of = {(s, p, o): g for s, p, o, g in new_triples_4}
        _new_triples_source = f"{_facts_files[-1].name} (TTL fallback)"
print(f"  {len(new_triples)} new triple(s) — source: {_new_triples_source}")

# Also fetch the metadata 1-hop for case context in the .txt file
after_metadata_full = _fuseki_triples_with_graph(case_key)

# Write after triples: metadata context section + OntoCast additions, grouped by graph
_after_txt = SCRIPT_DIR / f"after_triples_{re.sub(r'[^A-Za-z0-9_.-]+', '_', case_key)}.txt"
_triples_by_graph: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
for s, p, o, g in sorted(after_metadata_full, key=lambda x: (x[3], x[0], x[1], x[2])):
    _triples_by_graph[g].append((s, p, o))
for s, p, o, g in sorted(new_triples_4, key=lambda x: (x[3], x[0], x[1], x[2])):
    _triples_by_graph[g].append((s, p, o))
with _after_txt.open("w", encoding="utf-8") as _f:
    _f.write(f"After-snapshot triples for case: {case_key}\n")
    _f.write(f"Case IRI: {_case_iri(case_key)}\n")
    _f.write(f"Metadata context: {len(after_metadata_full)} triple(s)  |  New (OntoCast): {len(new_triples)}\n")
    _f.write("=" * 70 + "\n\n")
    for _g_iri, _g_triples in sorted(_triples_by_graph.items()):
        _g_new   = sum(1 for t in _g_triples if t in new_triples)
        _g_label = "[OntoCast]" if _g_iri != _DATA_GRAPH_IRI else "[metadata]"
        _f.write(
            f"── GRAPH {_g_label}: {_g_iri}\n"
            f"   {len(_g_triples)} triple(s), {_g_new} new\n\n"
        )
        for s, p, o in sorted(_g_triples):
            tag = "  ★ NEW" if (s, p, o) in new_triples else ""
            _f.write(f"S  {s}\nP  {p}\nO  {o}{tag}\n\n")
print(f"  After-triples written  : {_after_txt}")

# ── Extract ontology changes ────────────────────────────────────────────────
# Primary: TTL diff — compare latest post-run ontology_seed_*.ttl against base seed.ttl.
# This works regardless of whether OntoCast's state fields were populated, and is
# immune to the "2 anchor artifacts / normalization skipped" warning.

def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _ontology_diff_lines() -> list[str]:
    """Return human-readable lines describing what changed in the seed ontology."""
    try:
        from rdflib import Graph as RDFLibGraph, RDF, OWL, RDFS, URIRef
        base_g = RDFLibGraph()
        base_g.parse(str(SCRIPT_DIR / "seed.ttl"), format="turtle")

        _onto_candidates = sorted(_ontocast_work_dir.glob("ontology_seed_*.ttl"),
                                   key=lambda p: p.name)
        updated_path = _onto_candidates[-1] if _onto_candidates else None
        if updated_path is None:
            return ["  (no ontology_seed_*.ttl found — OntoCast did not write an update)"]

        updated_g = RDFLibGraph()
        updated_g.parse(str(updated_path), format="turtle")

        base_set    = set(base_g)
        updated_set = set(updated_g)
        added   = updated_set - base_set
        removed = base_set   - updated_set

        if not added and not removed:
            return [f"  (no changes — {updated_path.name} is identical to seed.ttl)"]

        OWL_CLASS    = OWL.Class
        OWL_OP       = OWL.ObjectProperty
        OWL_DP       = OWL.DatatypeProperty
        RDF_TYPE     = RDF.type

        def _short(node) -> str:
            s = str(node)
            for sep in ("#", "/"):
                if sep in s:
                    return s.rsplit(sep, 1)[-1]
            return s

        # Categorize added triples
        new_classes  = [s for s, p, o in added if p == RDF_TYPE and o == OWL_CLASS]
        new_obj_props= [s for s, p, o in added if p == RDF_TYPE and o == OWL_OP]
        new_dat_props= [s for s, p, o in added if p == RDF_TYPE and o == OWL_DP]
        other_added  = [(s, p, o) for s, p, o in added
                        if not (p == RDF_TYPE and o in (OWL_CLASS, OWL_OP, OWL_DP))]
        del_triples  = list(removed)

        lines: list[str] = [f"  TTL diff: +{len(added)} triple(s), -{len(removed)} triple(s)"]

        if new_classes:
            lines.append(f"\n  New owl:Class ({len(new_classes)}):")
            for c in sorted(new_classes, key=str):
                lines.append(f"    + {_short(c)}")
        if new_obj_props:
            lines.append(f"\n  New owl:ObjectProperty ({len(new_obj_props)}):")
            for p in sorted(new_obj_props, key=str):
                lines.append(f"    + {_short(p)}")
        if new_dat_props:
            lines.append(f"\n  New owl:DatatypeProperty ({len(new_dat_props)}):")
            for p in sorted(new_dat_props, key=str):
                lines.append(f"    + {_short(p)}")
        if other_added:
            lines.append(f"\n  Other added triples ({len(other_added)}):")
            for s, p, o in sorted(other_added, key=lambda t: (str(t[0]), str(t[1])))[:20]:
                lines.append(f"    + {_short(s)}  {_short(p)}  {_short(o)}")
            if len(other_added) > 20:
                lines.append(f"    … and {len(other_added) - 20} more")
        if del_triples:
            lines.append(f"\n  Removed triples ({len(del_triples)}):")
            for s, p, o in sorted(del_triples, key=lambda t: str(t[0]))[:10]:
                lines.append(f"    - {_short(s)}  {_short(p)}  {_short(o)}")

        return lines
    except Exception as exc:
        return [f"  [WARN] TTL diff failed: {exc}"]


# Secondary: state fields — collect from all four GraphUpdate lists
def _state_update_lines() -> list[str]:
    state_obj = result if not isinstance(result, dict) else out
    lines: list[str] = []
    for field_label, field_name in [
        ("ontology (applied)", "ontology_updates_applied"),
        ("ontology (pending)", "ontology_updates"),
        ("facts (applied)",    "facts_updates_applied"),
        ("facts (pending)",    "facts_updates"),
    ]:
        updates = _get(state_obj, field_name) or []
        for idx, upd in enumerate(updates, 1):
            if hasattr(upd, "generate_diff_summary"):
                try:
                    summary = upd.generate_diff_summary()
                    if summary.strip():
                        lines.append(f"\n  [{field_label} #{idx}]")
                        for ln in summary.splitlines():
                            lines.append(f"    {ln}")
                except Exception:
                    pass
    return lines

ont_lines = _ontology_diff_lines()
state_lines = _state_update_lines()
if state_lines:
    ont_lines += ["\n  ── State-reported updates ──"] + state_lines

# ── Build output ─────────────────────────────────────────────────────────────
ts_str     = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
safe_key   = re.sub(r"[^A-Za-z0-9_.-]+", "_", case_key)
safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", _model)

output_lines: list[str] = [
    "=" * 70,
    "OntoCast Single-Case Test",
    f"  Timestamp : {ts_str}",
    f"  Case      : {case_key}",
    f"  Name      : {one_case.get('case_name')}",
    f"  Source    : {one_case.get('source')}",
    f"  Model     : {_model}",
    f"  Duration  : {elapsed:.1f}s",
    "=" * 70,
    "",
    "── ONTOLOGY CHANGES ──────────────────────────────────────────────────",
]
output_lines.extend(ont_lines if ont_lines else ["  (none detected)"])

output_lines += [
    "",
    f"── NEW TRIPLES — {_new_triples_source.upper()} ({len(new_triples)}) "
    + "─" * max(0, 30 - len(str(len(new_triples)))),
]

if new_triples:
    for s, p, o in sorted(new_triples):
        g_iri   = _graph_of.get((s, p, o), "unknown")
        g_label = "[OntoCast]" if g_iri != _DATA_GRAPH_IRI else "[metadata]"
        output_lines.append(f"  {g_label} graph: {g_iri}")
        output_lines.append(f"  <{s}>")
        output_lines.append(f"      <{p}>")
        output_lines.append(f"      \"{o}\" ." if not o.startswith("http") else f"      <{o}> .")
        output_lines.append("")
else:
    output_lines.append("  (none)")

output_text = "\n".join(output_lines)
print("\n" + output_text)

results_dir = REPO_ROOT / "results" / "ontocast_tests"
results_dir.mkdir(parents=True, exist_ok=True)

txt_path = results_dir / f"{ts_str}_{safe_key}_{safe_model}.txt"
txt_path.write_text(output_text, encoding="utf-8")
print(f"\nOutput written : {txt_path}")

# ── NetworkX graph ──────────────────────────────────────────────────────────
if SHOW_GRAPH:
    import matplotlib          # noqa: E402
    matplotlib.use("Agg")      # non-interactive backend — safe for any env
    import matplotlib.patches as mpatches   # noqa: E402
    import matplotlib.pyplot as plt         # noqa: E402
    import networkx as nx                   # noqa: E402

    BLUE   = "#2166AC"  # pre-existing metadata graph triples
    RED    = "#D6604D"  # new triples added by OntoCast (any named graph)

    def _short(iri: str, max_len: int = 35) -> str:
        """Return a short human-readable label for an IRI."""
        for sep in ("#", "/"):
            if sep in iri:
                fragment = iri.rsplit(sep, 1)[-1]
                return (fragment[:max_len] + "…") if len(fragment) > max_len else fragment
        return (iri[:max_len] + "…") if len(iri) > max_len else iri

    G: nx.DiGraph = nx.DiGraph()
    before_nodes: set[str] = set()

    # Add pre-existing triples (blue) — from metadata named graph only
    for s, p, o in before_triples:
        ns, np_, no = _short(s), _short(p), _short(o)
        G.add_node(ns, _colour=BLUE)
        G.add_node(no, _colour=BLUE)
        G.add_edge(ns, no, label=np_, _colour=BLUE)
        before_nodes.update((ns, no))

    # Add new triples (red) — from any named graph; nodes already present stay blue
    for s, p, o in new_triples:
        ns, np_, no = _short(s), _short(p), _short(o)
        if ns not in G:
            G.add_node(ns, _colour=RED)
        if no not in G:
            G.add_node(no, _colour=RED)
        G.add_edge(ns, no, label=np_, _colour=RED)

    node_colours = [G.nodes[n].get("_colour", BLUE) for n in G.nodes()]
    edge_colours = [G.edges[e].get("_colour", BLUE) for e in G.edges()]

    fig, ax = plt.subplots(figsize=(20, 15))

    pos = nx.spring_layout(G, seed=42, k=2.0) if len(G) > 0 else {}
    nx.draw_networkx_nodes(G, pos, node_color=node_colours, node_size=400, ax=ax, alpha=0.9)
    nx.draw_networkx_labels(G, pos, font_size=6, ax=ax)
    nx.draw_networkx_edges(
        G, pos,
        edge_color=edge_colours,
        arrows=True,
        arrowsize=14,
        ax=ax,
        connectionstyle="arc3,rad=0.08",
        width=1.2,
    )
    edge_labels = {(u, v): d.get("label", "") for u, v, d in G.edges(data=True)}
    nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, font_size=5, ax=ax)

    case_name_str = str(one_case.get("case_name") or "")
    if len(case_name_str) > 60:
        case_name_str = case_name_str[:60] + "…"

    # Summarise how many new triples come from OntoCast vs metadata graph
    _n_new_ontocast = sum(
        1 for t in new_triples if _graph_of.get(t, "") != _DATA_GRAPH_IRI
    )
    _n_new_metadata = len(new_triples) - _n_new_ontocast

    ax.set_title(
        f"OntoCast — {case_key}  |  {case_name_str}\n"
        f"Model: {_model}   |   Before (metadata graph): {len(before_triples)} triples   "
        f"New (all named graphs): {len(new_triples)}"
        + (f"  [{_n_new_ontocast} OntoCast / {_n_new_metadata} metadata]" if new_triples else ""),
        fontsize=9,
        pad=14,
    )
    legend_patches = [
        mpatches.Patch(color=BLUE, label=f"Pre-existing — metadata graph ({len(before_triples)} triples)"),
        mpatches.Patch(color=RED,  label=f"New — added by OntoCast, all named graphs ({len(new_triples)} triples)"),
    ]
    ax.legend(handles=legend_patches, loc="lower left", fontsize=9)
    ax.axis("off")
    plt.tight_layout()

    png_path = results_dir / f"{ts_str}_{safe_key}_{safe_model}_graph.png"
    plt.savefig(png_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Graph saved    : {png_path}")
