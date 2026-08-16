"""
Joins HUDOC metadata (metadata.ttl) with per-case OntoCast semantic facts
(*_clean.ttl) into one unified RDF graph saved to results/.

Merge policy per predicate group:
  METADATA_ONLY   – authoritative HUDOC predicates; any semantic triple for
                    these is discarded (case name, ECLI, language, etc.).
  SEMANTIC_ONLY   – predicates absent from HUDOC (parties, operative
                    provisions, conclusion text, domestic timeline, etc.);
                    taken verbatim from semantic, including full sub-graphs.
  SIMILARITY_MERGE – both sources may contribute (judges, findings);
                    metadata triples always included first, then semantic
                    triples whose object label clears the cosine-similarity
                    threshold (i.e. not already covered by metadata).

AGG settings are read from ontology.env:
  AGG_EMBEDDING_MODEL   – sentence-transformers model name
  AGG_SIMILARITY_THRESHOLD – float 0-1; semantic object added only when
                              max similarity < threshold

Last Updated:
29.05.26

Status:
Needs rebuilding

History:
v1_0 - initial three-way merge with optional sentence-transformers similarity
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from rdflib import BNode, Graph, Literal, Namespace, OWL, RDF, RDFS, URIRef, XSD
from rdflib.namespace import DCTERMS

# ---
# Paths
# ---
SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent

DEFAULT_ENV_FILE = SCRIPT_DIR / "ontology.env"
DEFAULT_METADATA_TTL = SCRIPT_DIR / "metadata.ttl"
DEFAULT_SEM_DIR = SCRIPT_DIR / "ontologies"
DEFAULT_OUTPUT = REPO_ROOT / "results" / "joined_graph.ttl"

SCHEMA_IRI = "https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl"
SCHEMA_BASE_IRI = f"{SCHEMA_IRI}#"
FACTS_BASE = "https://github.com/dahrb/Art_6/tree/main/facts/"

# ---
# Merge policy – predicate local-names under SCHEMA_BASE_IRI
# ---

# Authoritative HUDOC predicates – semantic versions discarded
METADATA_ONLY: frozenset[str] = frozenset(
    [
        "hasItemId",
        "hasEcli",
        "hasCaseTextPath",
        "hasConclusionReference",
        "hasLanguageCode",
        "hasKeyword",
        "hasKeywordCode",
        "referencesSecondaryApplication",
        "hasImportanceLevel",
        "hasCaseName",
        "hasYear",
        "hasJudgmentDate",
        "hasChamberType",
        "hasCourtFormation",
        "hasJudgmentType",
        "hasSeparateOpinion",
        "hasLawSystem",
        "concernsArticle",
        "hasApplicantName",
        "hasApplication",
        "hasApplicationNumber",
        "hasRespondentState",
        "hasArticle6Limb",
        "hasLegalRepresentative",  # when present in metadata, it's authoritative
    ]
)

# Predicates absent from HUDOC – semantic adds genuine new information
SEMANTIC_ONLY: frozenset[str] = frozenset(
    [
        "hasConclusionText",
        "hasOperativeProvision",
        "hasParty",
        "hasLegalStatus",
        "hasVulnerabilityStatus",
        "hasEconomicStatus",
        "hasNationality",
        "findingRefersToArticle",
        "hasViolationGrounds",
        "appealFiledBy",
        "supremeCourtAppealLodgedOn",
        "legalAidRequestedOn",
        "legalAidGrantedOn",
        "lawyerAppointedOn",
        "hasDecisionDate",
        "hasCourt",           # domestic court reference on DomesticProceeding sub-nodes
    ]
)

# Both sources may have triples – similarity-deduplication
SIMILARITY_MERGE: frozenset[str] = frozenset(["hasJudge", "hasFinding"])

# ---
# Logging
# ---
log = logging.getLogger(__name__)


# ---
# Env loading
# ---

def load_agg_settings(env_file: Path) -> tuple[str, float]:
    """Return (embedding_model_name, similarity_threshold) from env file."""
    load_dotenv(REPO_ROOT / "keys.env", override=False)
    load_dotenv(env_file, override=False)
    model_name = os.getenv(
        "AGG_EMBEDDING_MODEL",
        "paraphrase-multilingual-MiniLM-L12-v2",
    )
    threshold = float(os.getenv("AGG_SIMILARITY_THRESHOLD", "0.80"))
    return model_name, threshold


# ---
# Similarity helpers
# ---

def _load_model(model_name: str) -> Any | None:
    """Load sentence-transformers model; return None if unavailable."""
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        log.info("Loading embedding model: %s", model_name)
        return SentenceTransformer(model_name)
    except ImportError:
        log.warning(
            "sentence-transformers not installed – similarity merge falls back "
            "to always-include for SIMILARITY_MERGE predicates."
        )
        return None


def _get_label_text(graph: Graph, node: Any) -> str:
    """Return a human-readable text representation of an RDF node for similarity."""
    if isinstance(node, Literal):
        return str(node).strip()
    if isinstance(node, (URIRef, BNode)):
        # prefer rdfs:label
        label = graph.value(node, RDFS.label)
        if label is not None:
            return str(label).strip()
        if isinstance(node, URIRef):
            # fall back to local name
            uri = str(node)
            local = re.split(r"[#/]", uri)[-1]
            return local.replace("_", " ").strip()
    return ""


def _max_cosine_similarity(query: str, candidates: list[str], model: Any) -> float:
    """Return the maximum cosine similarity between query and any candidate."""
    if not candidates or not query:
        return 0.0
    import numpy as np  # type: ignore

    texts = [query] + candidates
    embeddings = model.encode(texts, normalize_embeddings=True)
    q_vec = embeddings[0]
    c_vecs = embeddings[1:]
    sims = (c_vecs @ q_vec).tolist()
    return float(max(sims))


# ---
# Item ID extraction
# ---

def _extract_item_id(sem_graph: Graph) -> str | None:
    """
    Extract the HUDOC item ID from the doc1 namespace prefix in a semantic TTL.

    The TTL may contain two facts-base prefixes:
      @prefix doc:  <https://github.com/dahrb/Art_6/tree/main/facts/doc/{hash}/>
      @prefix doc1: <https://github.com/dahrb/Art_6/tree/main/facts/{itemid}#>

    HUDOC item IDs match \\d{3}-\\d+ (e.g. 001-103192).  We look specifically
    for that pattern so the doc/{hash}/ namespace is ignored.
    """
    _ITEM_ID_RE = re.compile(r"(\d{3}-\d+)#?/?$")
    for _prefix, namespace in sem_graph.namespaces():
        ns_str = str(namespace)
        if ns_str.startswith(FACTS_BASE):
            tail = ns_str[len(FACTS_BASE):]
            m = _ITEM_ID_RE.match(tail)
            if m:
                return m.group(1)
    # fallback 2: look for seed:hasItemId on any CaseDocument node
    schema = Namespace(SCHEMA_BASE_IRI)
    for _s, _p, obj in sem_graph.triples((None, schema.hasItemId, None)):
        if isinstance(obj, Literal):
            return str(obj).strip()
    # fallback 3: scan subject/object URIRefs for embedded item ID in facts IRIs
    # (handles files where OntoCast emitted full IRIs instead of the doc1: prefix)
    _ITEM_ID_IRI_RE = re.compile(r"/facts/(\d{3}-\d+)[/#]")
    for s, _p, o in sem_graph:
        for node in (s, o):
            if isinstance(node, URIRef):
                m = _ITEM_ID_IRI_RE.search(str(node))
                if m:
                    return m.group(1)
    return None


def _sem_case_uri(item_id: str, sem_graph: Graph) -> URIRef | None:
    """
    Find the canonical case node in the semantic graph for the given item_id.

    Priority:
    1. <FACTS_BASE/{item_id}#case_{normalised_item_id}>
    2. Any seed:CaseDocument node whose namespace matches item_id
    3. Any seed:CaseDocument node in the graph (first found)
    """
    schema = Namespace(SCHEMA_BASE_IRI)
    normalised = item_id.replace("-", "_")
    expected = URIRef(f"{FACTS_BASE}{item_id}#case_{normalised}")
    if (expected, RDF.type, schema.CaseDocument) in sem_graph:
        return expected

    # scan all CaseDocument nodes whose IRI contains the item_id
    candidates = []
    for s in sem_graph.subjects(RDF.type, schema.CaseDocument):
        if isinstance(s, URIRef) and item_id.replace("-", "_") in str(s):
            candidates.append(s)
    if candidates:
        return candidates[0]

    # fallback: any CaseDocument
    for s in sem_graph.subjects(RDF.type, schema.CaseDocument):
        if isinstance(s, URIRef):
            return s

    return None


# ---
# Subgraph copy
# ---

def _copy_subgraph(
    src: Graph,
    node: URIRef | BNode,
    dest: Graph,
    visited: set,
    depth: int = 0,
    max_depth: int = 12,
) -> None:
    """Recursively copy all triples reachable from node into dest."""
    key = (id(src), node)
    if key in visited or depth > max_depth:
        return
    visited.add(key)
    for _s, pred, obj in src.triples((node, None, None)):
        dest.add((node, pred, obj))
        if isinstance(obj, (URIRef, BNode)):
            _copy_subgraph(src, obj, dest, visited, depth + 1, max_depth)


# ---
# Metadata index
# ---

def build_metadata_index(meta_graph: Graph) -> dict[str, URIRef]:
    """Return mapping item_id → case URIRef from the metadata graph."""
    schema = Namespace(SCHEMA_BASE_IRI)
    index: dict[str, URIRef] = {}
    for subj, _, obj in meta_graph.triples((None, schema.hasItemId, None)):
        if isinstance(subj, URIRef) and isinstance(obj, Literal):
            index[str(obj).strip()] = subj
    return index


# ---
# Per-case merge
# ---

def merge_case(
    meta_graph: Graph,
    meta_case_uri: URIRef,
    sem_graph: Graph,
    sem_case_uri: URIRef,
    combined_graph: Graph,
    model: Any | None,
    threshold: float,
    subgraph_visited: set,
) -> dict[str, int]:
    """
    Merge semantic facts for one case into combined_graph.

    Returns a stats dict with counts of added triples per policy group.
    """
    schema = Namespace(SCHEMA_BASE_IRI)
    stats = {"semantic_only": 0, "similarity_added": 0, "similarity_skipped": 0}

    # --- SEMANTIC_ONLY ---
    for pred_name in SEMANTIC_ONLY:
        pred = schema[pred_name]
        for sem_obj in sem_graph.objects(sem_case_uri, pred):
            combined_graph.add((meta_case_uri, pred, sem_obj))
            stats["semantic_only"] += 1
            if isinstance(sem_obj, (URIRef, BNode)):
                _copy_subgraph(sem_graph, sem_obj, combined_graph, subgraph_visited)

    # --- SIMILARITY_MERGE ---
    for pred_name in SIMILARITY_MERGE:
        pred = schema[pred_name]
        # get objects already in combined graph from metadata
        existing_objs = list(combined_graph.objects(meta_case_uri, pred))
        existing_texts = [_get_label_text(combined_graph, o) for o in existing_objs]
        existing_texts = [t for t in existing_texts if t]

        for sem_obj in sem_graph.objects(sem_case_uri, pred):
            sem_text = _get_label_text(sem_graph, sem_obj)

            if model is not None and existing_texts and sem_text:
                sim = _max_cosine_similarity(sem_text, existing_texts, model)
                if sim >= threshold:
                    log.debug(
                        "Skipping semantic %s object (sim=%.3f): %s",
                        pred_name, sim, sem_text,
                    )
                    stats["similarity_skipped"] += 1
                    continue

            combined_graph.add((meta_case_uri, pred, sem_obj))
            stats["similarity_added"] += 1
            if isinstance(sem_obj, (URIRef, BNode)):
                _copy_subgraph(sem_graph, sem_obj, combined_graph, subgraph_visited)

    return stats


# ---
# Main join
# ---

def join(
    metadata_ttl: Path,
    sem_dir: Path,
    output_path: Path,
    model_name: str,
    threshold: float,
    limit: int | None,
    no_similarity: bool,
    intersection: bool = False,
) -> None:
    """
    Load metadata and semantic facts, apply merge policy, write joined graph.

    intersection=True  →  only cases that appear in BOTH metadata AND a
                          semantic TTL are included (inner join / intersection).
    intersection=False →  all metadata cases are included; semantic content
                          enriches matched cases (left outer join, default).
    """
    log.info("Loading metadata: %s", metadata_ttl)
    meta_graph = Graph()
    meta_graph.parse(str(metadata_ttl), format="turtle")
    log.info("Metadata triples: %d", len(meta_graph))

    meta_index = build_metadata_index(meta_graph)
    log.info("Metadata cases indexed: %d", len(meta_index))

    model = None if no_similarity else _load_model(model_name)

    combined = Graph()
    # copy namespace bindings from metadata
    for prefix, ns in meta_graph.namespaces():
        combined.bind(prefix, ns)

    if not intersection:
        # left-outer join: start with the full metadata graph
        for triple in meta_graph:
            combined.add(triple)

    ttl_files = sorted(sem_dir.glob("facts_*_clean.ttl"))
    if limit is not None:
        ttl_files = ttl_files[:limit]

    total = len(ttl_files)
    log.info("Semantic TTL files found: %d", total)

    matched = 0
    unmatched = 0
    skipped = 0
    total_stats: dict[str, int] = {
        "semantic_only": 0,
        "similarity_added": 0,
        "similarity_skipped": 0,
    }
    subgraph_visited: set = set()

    for idx, ttl_path in enumerate(ttl_files, 1):
        if idx % 100 == 0 or idx == total:
            log.info("Processing %d/%d ...", idx, total)

        sem_graph = Graph()
        try:
            # rdflib emits logging warnings for invalid xsd:date values like
            # "2009-00-00"; suppress at the rdflib term logger level
            _rdflib_log = logging.getLogger("rdflib.term")
            _prev = _rdflib_log.level
            _rdflib_log.setLevel(logging.ERROR)
            try:
                sem_graph.parse(str(ttl_path), format="turtle")
            finally:
                _rdflib_log.setLevel(_prev)
        except Exception as exc:
            log.warning("Could not parse %s: %s", ttl_path.name, exc)
            skipped += 1
            continue

        item_id = _extract_item_id(sem_graph)
        if item_id is None:
            log.debug("No item ID found in %s – skipping", ttl_path.name)
            skipped += 1
            continue

        meta_case_uri = meta_index.get(item_id)
        if meta_case_uri is None:
            if intersection:
                log.debug("Item %s not in metadata – skipping (intersection mode)", item_id)
                skipped += 1
            else:
                log.debug("Item %s not in metadata – adding semantic graph as-is", item_id)
                # case not in metadata: copy all semantic triples verbatim
                for triple in sem_graph:
                    combined.add(triple)
                unmatched += 1
            continue

        sem_case_uri = _sem_case_uri(item_id, sem_graph)
        if sem_case_uri is None:
            # No CaseDocument root node: OntoCast extracted facts but omitted the
            # top-level CaseDocument.  Copy all semantic triples verbatim so the
            # individual fact nodes (DomesticProceeding, Appeal, etc.) are
            # preserved in the joined graph, even without a proper predicate-level
            # merge onto the metadata case node.
            log.debug(
                "No CaseDocument node in %s – adding semantic triples verbatim",
                ttl_path.name,
            )
            if intersection:
                # also copy the metadata subtree for this case
                _copy_subgraph(meta_graph, meta_case_uri, combined, set())
            for triple in sem_graph:
                combined.add(triple)
            matched += 1
            continue

        if intersection:
            # copy only this case's metadata subtree into combined
            _copy_subgraph(meta_graph, meta_case_uri, combined, set())

        stats = merge_case(
            meta_graph=meta_graph,
            meta_case_uri=meta_case_uri,
            sem_graph=sem_graph,
            sem_case_uri=sem_case_uri,
            combined_graph=combined,
            model=model,
            threshold=threshold,
            subgraph_visited=subgraph_visited,
        )
        for k, v in stats.items():
            total_stats[k] += v
        matched += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    log.info("Serialising joined graph (%d triples) → %s", len(combined), output_path)
    combined.serialize(str(output_path), format="turtle")

    log.info(
        "Done. matched=%d  unmatched=%d  skipped=%d | "
        "sem_only_triples=%d  sim_added=%d  sim_skipped=%d",
        matched, unmatched, skipped,
        total_stats["semantic_only"],
        total_stats["similarity_added"],
        total_stats["similarity_skipped"],
    )


# ---
# Fuseki count helper
# ---

def _fuseki_case_count(fuseki_uri: str, dataset: str, auth: tuple[str, str]) -> int:
    """Query Fuseki for distinct CaseDocument subjects across all named graphs."""
    import urllib.request, urllib.parse, json as _json, base64

    endpoint = f"{fuseki_uri}/{dataset}/query"
    q = (
        "SELECT (COUNT(DISTINCT ?s) AS ?c) WHERE { "
        "GRAPH ?g { ?s a "
        f"<{SCHEMA_BASE_IRI}CaseDocument> "
        "} }"
    )
    data = urllib.parse.urlencode({"query": q}).encode()
    creds = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Accept": "application/sparql-results+json",
            "Authorization": f"Basic {creds}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = _json.loads(resp.read())
            return int(result["results"]["bindings"][0]["c"]["value"])
    except Exception as exc:
        log.warning("Fuseki query failed: %s", exc)
        return -1


# ---
# CLI
# ---

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Join HUDOC metadata TTL with per-case OntoCast semantic facts "
            "into a single unified RDF graph."
        )
    )
    p.add_argument(
        "--metadata-ttl",
        type=Path,
        default=DEFAULT_METADATA_TTL,
        help="Path to metadata.ttl (default: ontology/metadata.ttl)",
    )
    p.add_argument(
        "--sem-dir",
        type=Path,
        default=DEFAULT_SEM_DIR,
        help="Directory containing facts_*_clean.ttl files",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output path for the joined graph TTL",
    )
    p.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="Env file for AGG settings",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N semantic TTL files (for testing)",
    )
    p.add_argument(
        "--no-similarity",
        action="store_true",
        help="Disable similarity dedup – always add all SIMILARITY_MERGE triples",
    )
    p.add_argument(
        "--intersection",
        action="store_true",
        help=(
            "Produce an intersection graph: only include cases present in BOTH "
            "metadata and a semantic TTL (inner join). Default is left outer join."
        ),
    )
    p.add_argument(
        "--fuseki-count",
        action="store_true",
        help="Query Fuseki for the current named-graph case count and print it",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    model_name, threshold = load_agg_settings(args.env_file)
    log.info(
        "AGG settings: model=%s  threshold=%.2f", model_name, threshold
    )

    # ---
    # Fuseki count (if requested)
    # ---
    if args.fuseki_count:
        load_dotenv(REPO_ROOT / "keys.env", override=False)
        load_dotenv(args.env_file, override=False)
        fuseki_uri = os.getenv("FUSEKI_URI", "http://localhost:3032")
        dataset = os.getenv("FUSEKI_DATASET", "Art_6_Facts_Ontology")
        raw_auth = os.getenv("FUSEKI_AUTH", "admin/test345")
        user, _, pwd = raw_auth.partition("/")
        count = _fuseki_case_count(fuseki_uri, dataset, (user, pwd))
        log.info("Fuseki named-graph CaseDocument count: %d", count)

    # ---
    # Local TTL count
    # ---
    sem_dir = args.sem_dir
    clean_ttls = sorted(sem_dir.glob("facts_*_clean.ttl"))
    log.info(
        "Local semantic TTL files (clean): %d  →  %d cases",
        len(clean_ttls), len(clean_ttls),
    )

    join(
        metadata_ttl=args.metadata_ttl,
        sem_dir=sem_dir,
        output_path=args.output,
        model_name=model_name,
        threshold=threshold,
        limit=args.limit,
        no_similarity=args.no_similarity,
        intersection=args.intersection,
    )


if __name__ == "__main__":
    main()
