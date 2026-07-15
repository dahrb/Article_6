"""
Script to generate descriptive statistics and visualizations for ECHR Article 6 data.

Outputs written to:
  results/exploration/    — RDF graph stats, seed/metadata structure diagrams,
                            temporal coverage charts
  results/quarto/figures/ — country choropleth maps, GIF, animated HTML

Last Updated:
21.05.26

Status:
Done

History:
v1_0 - RDF graph analysis, pygraphviz structure diagrams, text report
v1_1 - ported temporal coverage charts and country choropleth maps from notebook
v1_2 - added rdf2dot full-graph view and networkx neato view for seed (mirroring notebook cell)
"""

from __future__ import annotations

import io
import os
import re
import shutil
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

from rdflib.tools.rdf2dot import rdf2dot

import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
import polars as pl
import pygraphviz as pgv
from PIL import Image
import plotly.express as px
import plotly.graph_objects as go
import pycountry
from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import OWL


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
QUARTO_DIR = ROOT / "results" / "quarto" / "figures"
DATA_DIR = ROOT / "data"
SEED_TTL = ROOT / "ontology" / "seed.ttl"
METADATA_TTL = ROOT / "ontology" / "metadata.ttl"
REPORT_PATH = OUT_DIR / "graph_descriptive_report.txt"

ECHR = Namespace("https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl#")
ECHR_PREFIX = str(ECHR)

TARGET_PER_LEVEL = 50

EUROPE_ISO3 = {
    "ALB", "AND", "ARM", "AUT", "AZE", "BEL", "BIH", "BGR", "HRV", "CYP", "CZE",
    "DNK", "EST", "FIN", "FRA", "GEO", "DEU", "GRC", "HUN", "ISL", "IRL", "ITA",
    "LVA", "LIE", "LTU", "LUX", "MLT", "MDA", "MCO", "MNE", "NLD", "MKD", "NOR",
    "POL", "PRT", "ROU", "RUS", "SMR", "SRB", "SVK", "SVN", "ESP", "SWE", "CHE",
    "TUR", "UKR", "GBR",
}

_FIXED_GEO = dict(
    scope="world",
    projection=dict(type="mercator"),
    center=dict(lat=54, lon=15),
    lataxis=dict(range=[30, 73]),
    lonaxis=dict(range=[-25, 55]),
)

_COUNTRY_ALIASES: dict[str, str] = {
    "turkey": "TUR",
    "republic of turkey": "TUR",
    "turkiye": "TUR",
    "türkiye": "TUR",
    "türkiye cumhuriyeti": "TUR",
    "russia": "RUS",
    "russian federation": "RUS",
    "moldova": "MDA",
    "czech republic": "CZE",
    "north macedonia": "MKD",
    "bosnia and herzegovina": "BIH",
    "united kingdom": "GBR",
}


# ---
# Graphviz path helper
# ---

def _ensure_graphviz_on_path() -> str | None:
    """Locate the Graphviz `dot` binary and patch PATH so pygraphviz can find it."""
    candidates = [
        shutil.which("dot"),
        str(Path.home() / "AppData" / "Local" / "GraphvizPortable" / "Graphviz-14.1.5-win64" / "bin" / "dot.exe"),
        str(Path.home() / "AppData" / "Local" / "Graphviz" / "bin" / "dot.exe"),
        r"C:\Program Files\Graphviz\bin\dot.exe",
    ]
    dot_path = next((p for p in candidates if p and Path(p).exists()), None)
    if dot_path is None:
        return None
    dot_bin = str(Path(dot_path).parent)
    if dot_bin not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = dot_bin + os.pathsep + os.environ.get("PATH", "")
    windows_fonts = r"C:\Windows\Fonts"
    if Path(windows_fonts).exists():
        os.environ.setdefault("GDFONTPATH", windows_fonts)
        os.environ.setdefault("DOTFONTPATH", windows_fonts)
    return dot_path


# ---
# RDF loading
# ---

def load_rdf_graph(path: Path) -> Graph:
    """Parse a Turtle file into an rdflib Graph."""
    """Parse a Turtle file into an rdflib Graph."""
    if not path.exists():
        raise FileNotFoundError(f"Missing TTL file: {path}")
    g = Graph()
    g.parse(path.as_posix(), format="turtle")
    return g


# keep alias for backward compat with any external callers
load_graph = load_rdf_graph


def qname(g: Graph, term: Any) -> str:
    if isinstance(term, URIRef):
        try:
            return g.namespace_manager.normalizeUri(term)
        except Exception:
            return str(term)
    if isinstance(term, Literal):
        return str(term)
    if isinstance(term, BNode):
        return f"_:{term}"
    return str(term)


def humanize_term(term: URIRef | str) -> str:
    value = str(term)
    if "#" in value:
        value = value.rsplit("#", 1)[-1]
    elif "/" in value:
        value = value.rsplit("/", 1)[-1]
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = value.replace("_", " ")
    return value.strip()


def is_echr_term(term: Any) -> bool:
    return isinstance(term, URIRef) and str(term).startswith(ECHR_PREFIX)


# ---
# Pygraphviz: seed structure diagram
# ---

def _style_seed_node(agraph: pgv.AGraph, term: URIRef) -> None:
    agraph.add_node(
        str(term),
        label=humanize_term(term),
        shape="box",
        style="rounded,filled",
        fillcolor="#dceefe",
        color="#4a6fa5",
        fontname="Arial",
        fontsize="10",
    )


def render_seed_structure(seed: Graph, output_path: Path) -> None:
    """
    Visualize the seed ontology schema as a directed graph.

    Nodes represent ECHR-defined OWL classes; edges represent object property
    domain→range links (solid) and subClassOf hierarchies (dashed). Layout is
    left-to-right with orthogonal splines for readability.

    Output: PNG at output_path (seed_structure.png).
    """
    _ensure_graphviz_on_path()
    agraph = pgv.AGraph(
        directed=True, strict=False,
        rankdir="LR", splines="ortho", concentrate=True,
        nodesep="0.35", ranksep="0.85",
    )
    agraph.node_attr.update(fontname="Arial", fontsize="10")
    agraph.edge_attr.update(fontname="Arial", fontsize="9", color="#57606a", arrowsize="0.75")

    seed_classes = sorted({s for s in seed.subjects(RDF.type, OWL.Class) if is_echr_term(s)}, key=str)
    seed_properties = sorted({p for p in seed.subjects(RDF.type, OWL.ObjectProperty) if is_echr_term(p)}, key=str)
    # named individuals grouped by their declared ECHR class type
    seed_individuals = sorted(
        {s for s in seed.subjects(RDF.type, OWL.NamedIndividual) if is_echr_term(s)},
        key=str,
    )

    for cls in seed_classes:
        _style_seed_node(agraph, cls)

    # individuals: diamond nodes in a distinct colour, one per value
    for ind in seed_individuals:
        agraph.add_node(
            str(ind),
            label=humanize_term(ind),
            shape="diamond",
            style="filled",
            fillcolor="#fdf3c8",
            color="#b5860d",
            fontname="Arial",
            fontsize="9",
        )

    edge_labels: dict[tuple[str, str], set[str]] = {}
    for prop in seed_properties:
        domains = [d for d in seed.objects(prop, RDFS.domain) if is_echr_term(d)]
        ranges = [r for r in seed.objects(prop, RDFS.range) if is_echr_term(r)]
        for domain in domains:
            for range_term in ranges:
                src, dst = str(domain), str(range_term)
                _style_seed_node(agraph, domain)
                _style_seed_node(agraph, range_term)
                edge_labels.setdefault((src, dst), set()).add(humanize_term(prop))

    for child, _, parent in seed.triples((None, RDFS.subClassOf, None)):
        if is_echr_term(child) and is_echr_term(parent):
            src, dst = str(child), str(parent)
            _style_seed_node(agraph, child)
            _style_seed_node(agraph, parent)
            edge_labels.setdefault((src, dst), set()).add("subClassOf")

    # connect each individual to its declared ECHR class (skip owl:NamedIndividual hub)
    for ind in seed_individuals:
        for ind_type in seed.objects(ind, RDF.type):
            if is_echr_term(ind_type):
                edge_labels.setdefault((str(ind), str(ind_type)), set()).add("instanceOf")

    for (src, dst), labels in sorted(edge_labels.items()):
        label_text = "\n".join(sorted(labels))
        if labels == {"subClassOf"}:
            style, color = "dashed", "#9aa0a6"
        elif labels == {"instanceOf"}:
            style, color = "dotted", "#b5860d"
        else:
            style, color = "solid", "#2a5d84"
        agraph.add_edge(src, dst, label=label_text, color=color, style=style)

    agraph.layout(prog="dot")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        agraph.draw(output_path.as_posix())
    print(f"Wrote seed diagram: {output_path}")


# ---
# Pygraphviz: metadata sample structure diagram
# ---

def _case_label(graph: Graph, case_uri: URIRef) -> str:
    for predicate in (ECHR.hasCaseName, RDFS.label, ECHR.hasItemId):
        values = [obj for obj in graph.objects(case_uri, predicate)]
        if values:
            return str(values[0])
    return humanize_term(case_uri)


def render_metadata_structure(metadata: Graph, output_path: Path) -> None:
    """
    Visualize a structured 18-case sample of the metadata knowledge graph.

    Each CaseDocument node (blue box) is linked to its related entities (judges,
    countries, keywords, findings, etc.) colour-coded by rdf:type. Edge labels
    show the property names. Reveals the typical predicate structure around a
    case and the density of node types in the metadata graph.

    Output: PNG at output_path (metadata_structure.png).
    """
    _ensure_graphviz_on_path()
    agraph = pgv.AGraph(
        directed=True, strict=False,
        rankdir="LR", splines="spline", concentrate=True,
        nodesep="0.28", ranksep="0.65",
    )
    agraph.node_attr.update(fontname="Arial", fontsize="10")
    agraph.edge_attr.update(fontname="Arial", fontsize="8", color="#6b7280", arrowsize="0.70")

    _FILL_MAP = {
        ECHR.Application: "#fff2cc",
        ECHR.Judge: "#f4cccc",
        ECHR.Country: "#d9ead3",
        ECHR.ConventionArticle: "#ead1dc",
        ECHR.Keyword: "#ffe599",
        ECHR.CourtFormation: "#d0e0e3",
        ECHR.Violation: "#fce5cd",
        ECHR.NonViolation: "#fce5cd",
    }

    case_nodes = sorted({s for s in metadata.subjects(RDF.type, ECHR.CaseDocument)}, key=str)[:18]
    edge_labels: dict[tuple[str, str], set[str]] = {}

    for case_uri in case_nodes:
        agraph.add_node(
            str(case_uri),
            label=_case_label(metadata, case_uri),
            shape="box", style="rounded,filled",
            fillcolor="#cfe8ff", color="#4878a8",
            fontname="Arial", fontsize="10",
        )
        for predicate, obj in metadata.predicate_objects(case_uri):
            if not isinstance(obj, URIRef):
                continue
            if not str(obj).startswith(ECHR_PREFIX):
                continue
            if predicate in {RDF.type, RDFS.label}:
                continue
            obj_type = next((t for t in metadata.objects(obj, RDF.type) if isinstance(t, URIRef)), None)
            fill = _FILL_MAP.get(obj_type, "#e8e8e8")
            agraph.add_node(
                str(obj),
                label=humanize_term(obj),
                shape="ellipse", style="filled",
                fillcolor=fill, color="#666666",
                fontname="Arial", fontsize="9",
            )
            edge_labels.setdefault((str(case_uri), str(obj)), set()).add(humanize_term(predicate))

    for (src, dst), labels in sorted(edge_labels.items()):
        agraph.add_edge(src, dst, label="\n".join(sorted(labels)), color="#3b6ea8", style="solid")

    agraph.layout(prog="dot")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        agraph.draw(output_path.as_posix())
    print(f"Wrote metadata structure diagram: {output_path}")


# ---
# Rdf2dot full-graph view  (replicates notebook cell)
# ---

def _seed_filtered_graph(seed: Graph) -> Graph:
    """Return a copy of the seed graph with the ontology-header node and sameAs/disjointWith noise removed."""
    header = URIRef(str(SEED_TTL))
    g_plot: Graph = Graph()
    for prefix, namespace in seed.namespace_manager.namespaces():
        g_plot.bind(prefix, namespace)
    skip_predicates = {OWL.sameAs, OWL.disjointWith}
    for s, p, o in seed:
        if s == header or o == header:
            continue
        if p in skip_predicates:
            continue
        g_plot.add((s, p, o))
    return g_plot


def render_seed_rdf2dot(seed: Graph, output_path: Path) -> None:
    """
    Full-schema rdf2dot view of the seed ontology saved as a PNG.

    Replicates the notebook's rdf2dot + graphviz inline approach. Every class,
    property, and named individual in the seed is represented as an HTML-table
    node with its property values as rows. The ontology-header node, owl:sameAs
    cross-references, and owl:disjointWith annotations are stripped so the
    diagram stays focused on the schema structure.

    Output: PNG at output_path (seed_rdf2dot.png).
    """
    _ensure_graphviz_on_path()
    g_plot = _seed_filtered_graph(seed)

    stream = io.StringIO()
    rdf2dot(g_plot, stream)
    dot_data = stream.getvalue()
    # match the notebook's font fix to suppress DejaVu fallback noise
    dot_data = dot_data.replace(
        "digraph {",
        'digraph {\n  graph [fontname="Arial"];\n  node [fontname="Arial"];\n  edge [fontname="Arial"];',
        1,
    )

    agraph = pgv.AGraph(string=dot_data)
    agraph.layout(prog="dot")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        agraph.draw(output_path.as_posix())
    print(f"Wrote rdf2dot seed diagram: {output_path}")


def render_seed_neato(seed: Graph, output_path: Path) -> None:
    """
    Networkx neato-layout view of the seed ontology saved as a PNG.

    Replicates the notebook's visualize_nx filtered view. Triples are reduced
    to a cleaned-up set: rdfs:label, rdfs:comment, owl:sameAs, owl:disjointWith,
    and Wikidata external nodes are stripped, and the owl:NamedIndividual type
    hub is suppressed so named individuals (Gender, ImportanceLevel, ChamberType,
    Article6Limb, LawSystem, SeparateOpinionIndicator) appear connected directly
    to their declared ECHR class rather than all fanning into a single hub node.

    Output: PNG at output_path (seed_neato.png).
    """
    _ensure_graphviz_on_path()

    triples_text = [[qname(seed, t) for t in tri] for tri in seed]
    df = pd.DataFrame(triples_text, columns=["subject", "predicate", "object"]).sort_values(
        ["object", "subject", "predicate"]
    )

    # keep only rows where both subject and object are in the echr: namespace
    df_reduced = df.loc[
        df["subject"].str.startswith("echr:")
        & df["object"].str.startswith("echr:")
        & ~df["predicate"].isin(["rdfs:label", "rdfs:comment", "owl:sameAs", "owl:disjointWith"])
        & (df["object"] != "owl:NamedIndividual")
    ].copy()

    G = nx.DiGraph()
    for _, row in df_reduced.iterrows():
        G.add_edge(row["subject"], row["object"], relation=row["predicate"])

    fig, ax = plt.subplots(figsize=(36, 18), dpi=150)
    pos = nx.nx_agraph.graphviz_layout(G, prog="neato", args="-Goverlap=false")
    nx.draw(
        G, pos, ax=ax,
        with_labels=True,
        node_color="lightblue",
        node_size=3000,
        font_size=10,
        font_weight="bold",
    )
    edge_labels = nx.get_edge_attributes(G, "relation")
    nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, ax=ax)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Wrote neato seed diagram: {output_path}")


def collect_basic_stats(g: Graph) -> dict[str, Any]:
    """Return counts of triples, subjects, predicates, objects and top-frequency terms."""
    triples = list(g)
    subjects = {s for s, _, _ in triples}
    predicates = {p for _, p, _ in triples}
    objects = {o for _, _, o in triples}

    literal_objects = [o for o in objects if isinstance(o, Literal)]
    uri_subjects = [s for s in subjects if isinstance(s, URIRef)]
    bnode_subjects = [s for s in subjects if isinstance(s, BNode)]

    predicate_counts = Counter(p for _, p, _ in triples)
    type_counts = Counter(o for s, p, o in triples if p == RDF.type and isinstance(o, URIRef))

    return {
        "triples": len(triples),
        "unique_subjects": len(subjects),
        "unique_predicates": len(predicates),
        "unique_objects": len(objects),
        "uri_subjects": len(uri_subjects),
        "bnode_subjects": len(bnode_subjects),
        "literal_objects": len(literal_objects),
        "top_predicates": predicate_counts.most_common(20),
        "top_types": type_counts.most_common(20),
    }


def collect_metadata_extras(g: Graph) -> dict[str, Any]:
    """Return domain-specific counts (cases, judgments, violations, keywords, etc.)."""
    case_docs = sorted({s for s in g.subjects(RDF.type, ECHR.CaseDocument)}, key=str)
    judgments = sorted({s for s in g.subjects(RDF.type, ECHR.Judgment)}, key=str)
    decisions = sorted({s for s in g.subjects(RDF.type, ECHR.Decision)}, key=str)
    findings = sorted({o for _, _, o in g.triples((None, ECHR.hasFinding, None)) if isinstance(o, URIRef)}, key=str)

    case_triple_counts = []
    for case_uri in case_docs:
        case_triple_counts.append(sum(1 for _ in g.predicate_objects(case_uri)))

    return {
        "case_documents": len(case_docs),
        "judgments": len(judgments),
        "decisions": len(decisions),
        "cases_with_findings": len({s for s, _, _ in g.triples((None, ECHR.hasFinding, None))}),
        "finding_nodes": len(findings),
        "violation_nodes": len({s for s in g.subjects(RDF.type, ECHR.Violation)}),
        "nonviolation_nodes": len({s for s in g.subjects(RDF.type, ECHR.NonViolation)}),
        "keyword_nodes": len({s for s in g.subjects(RDF.type, ECHR.Keyword)}),
        "country_nodes": len({s for s in g.subjects(RDF.type, ECHR.Country)}),
        "application_nodes": len({s for s in g.subjects(RDF.type, ECHR.Application)}),
        "conclusion_literals": len({o for _, _, o in g.triples((None, ECHR.hasConclusionReference, None)) if isinstance(o, Literal)}),
        "case_triple_counts": case_triple_counts,
    }


def plot_bar_from_counter(g: Graph, pairs: list[tuple[Any, int]], title: str, x_label: str, output_path: Path) -> None:
    """Write a horizontal bar chart of (term, count) pairs to output_path."""
    labels = [qname(g, term) for term, _ in pairs]
    values = [count for _, count in pairs]

    plt.figure(figsize=(13, 7))
    plt.barh(labels[::-1], values[::-1], color="#2a9d8f")
    plt.title(title)
    plt.xlabel(x_label)
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def plot_case_triple_distribution(case_triple_counts: list[int], output_path: Path) -> None:
    """Histogram of outgoing triple counts per CaseDocument node."""
    plt.figure(figsize=(11, 6))
    plt.hist(case_triple_counts, bins=25, color="#457b9d", alpha=0.9)
    plt.title("Metadata Graph: Distribution of Outgoing Triples per CaseDocument")
    plt.xlabel("Outgoing triples per case")
    plt.ylabel("Number of cases")
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()

def render_report(seed: Graph, metadata: Graph) -> str:
    """Compile a plain-text descriptive statistics report for seed and metadata graphs."""
    seed_stats = collect_basic_stats(seed)
    meta_stats = collect_basic_stats(metadata)
    meta_extra = collect_metadata_extras(metadata)

    lines: list[str] = []
    lines.append("ECHR Graph Descriptive Statistics Report")
    lines.append(f"Seed source: {SEED_TTL}")
    lines.append(f"Metadata source: {METADATA_TTL}")
    lines.append("")

    lines.append("[Seed Graph Summary]")
    for k in ("triples", "unique_subjects", "unique_predicates", "unique_objects", "uri_subjects", "bnode_subjects", "literal_objects"):
        lines.append(f"- {k}: {seed_stats[k]}")
    lines.append("- top predicates:")
    for term, count in seed_stats["top_predicates"][:15]:
        lines.append(f"  - {qname(seed, term)}: {count}")
    lines.append("- top rdf:type objects:")
    for term, count in seed_stats["top_types"][:15]:
        lines.append(f"  - {qname(seed, term)}: {count}")
    lines.append("")

    lines.append("[Metadata Graph Summary]")
    for k in ("triples", "unique_subjects", "unique_predicates", "unique_objects", "uri_subjects", "bnode_subjects", "literal_objects"):
        lines.append(f"- {k}: {meta_stats[k]}")

    lines.append("- domain-focused stats:")
    for k in (
        "case_documents",
        "judgments",
        "decisions",
        "cases_with_findings",
        "finding_nodes",
        "violation_nodes",
        "nonviolation_nodes",
        "keyword_nodes",
        "country_nodes",
        "application_nodes",
        "conclusion_literals",
    ):
        lines.append(f"  - {k}: {meta_extra[k]}")

    if meta_extra["case_triple_counts"]:
        counts = sorted(meta_extra["case_triple_counts"])
        n = len(counts)
        q1 = counts[int((n - 1) * 0.25)]
        q2 = counts[int((n - 1) * 0.50)]
        q3 = counts[int((n - 1) * 0.75)]
        lines.append("- outgoing triples per case (CaseDocument subject only):")
        lines.append(f"  - min: {counts[0]}")
        lines.append(f"  - q1: {q1}")
        lines.append(f"  - median: {q2}")
        lines.append(f"  - q3: {q3}")
        lines.append(f"  - max: {counts[-1]}")

    lines.append("- top predicates:")
    for term, count in meta_stats["top_predicates"][:20]:
        lines.append(f"  - {qname(metadata, term)}: {count}")

    lines.append("- top rdf:type objects:")
    for term, count in meta_stats["top_types"][:20]:
        lines.append(f"  - {qname(metadata, term)}: {count}")

    return "\n".join(lines)


# ---
# Processed metadata loading
# ---

def _derive_year_expr() -> pl.Expr:
    """Polars expression that extracts a four-digit year from judgementdate or ecli."""
    return pl.coalesce([
        pl.col("judgementdate").cast(pl.Utf8, strict=False).str.slice(0, 4).cast(pl.Int32, strict=False),
        pl.col("ecli").cast(pl.Utf8, strict=False).str.extract(r":(\d{4}):", 1).cast(pl.Int32, strict=False),
    ]).alias("year")


def load_processed_metadata() -> pl.DataFrame:
    """Load and concatenate the processed judgments and decisions JSONL files."""
    j_path = DATA_DIR / "art_6_judgments_metadata_processed.json"
    d_path = DATA_DIR / "art_6_decisions_metadata_processed.json"
    if not j_path.exists() or not d_path.exists():
        raise FileNotFoundError(
            f"Missing processed metadata JSON files in {DATA_DIR}. "
            "Expected art_6_judgments_metadata_processed.json and "
            "art_6_decisions_metadata_processed.json."
        )
    judgments = pl.read_ndjson(j_path, infer_schema_length=None).with_columns(
        pl.lit("judgments").alias("source"),
        pl.col("itemid").cast(pl.Utf8),
    )
    decisions = pl.read_ndjson(d_path, infer_schema_length=None).with_columns(
        pl.lit("decisions").alias("source"),
        pl.col("itemid").cast(pl.Utf8),
    )
    return pl.concat([judgments, decisions], how="diagonal_relaxed").with_columns(_derive_year_expr())


# ---
# Corpus sampling (temporal spread per court level)
# ---

def _temporal_spread_sample(group_df: pl.DataFrame, target_n: int) -> pl.DataFrame:
    """Down-sample a group to target_n rows, evenly spaced by year order."""
    n = group_df.height
    if n <= target_n:
        return group_df
    sorted_df = group_df.sort("year")
    idx = np.linspace(0, n - 1, num=target_n, dtype=int)
    return (
        sorted_df.with_row_index("_row_idx")
        .filter(pl.col("_row_idx").is_in(idx.tolist()))
        .drop("_row_idx")
    )


def build_corpus_sample(
    all_cases: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """
    Stratify cases by court level and draw a temporally spread sample.

    For each court level, up to TARGET_PER_LEVEL cases are selected with even
    spacing across the sorted year axis so that the sample mirrors the full
    corpus temporal distribution.

    Returns:
        metadata_for_sampling: filtered full corpus (valid court_level + year)
        sampled_metadata: the stratified sample
        sampling_summary: per-level comparison of full vs sample size and year range
    """
    metadata_for_sampling = all_cases.filter(
        pl.col("court_level").is_not_null() & pl.col("year").is_not_null()
    )
    sampled_groups = []
    for level in sorted(metadata_for_sampling["court_level"].unique().to_list()):
        level_df = metadata_for_sampling.filter(pl.col("court_level") == level)
        sampled_groups.append(_temporal_spread_sample(level_df, TARGET_PER_LEVEL))
    sampled_metadata = pl.concat(sampled_groups, how="diagonal_relaxed")

    full_summary = metadata_for_sampling.group_by("court_level").agg(
        pl.len().alias("full_n"),
        pl.col("year").min().alias("full_year_min"),
        pl.col("year").max().alias("full_year_max"),
    )
    sample_summary = sampled_metadata.group_by("court_level").agg(
        pl.len().alias("sample_n"),
        pl.col("year").min().alias("sample_year_min"),
        pl.col("year").max().alias("sample_year_max"),
    )
    sampling_summary = full_summary.join(sample_summary, on="court_level", how="left").sort("court_level")
    return metadata_for_sampling, sampled_metadata, sampling_summary


# ---
# Temporal coverage charts
# ---

def plot_temporal_coverage(
    metadata_for_sampling: pl.DataFrame,
    sampled_metadata: pl.DataFrame,
    sampling_summary: pl.DataFrame,
    out_dir: Path,
) -> None:
    """
    Produce two charts showing temporal distribution of the ECHR Article 6 corpus.

    Chart 1 — temporal_trend_by_court_level.png:
        Line chart of annual case counts per court level for the full corpus.
        Shows which court formations dominated in which decades.

    Chart 2 — temporal_normalized_by_court_level.png:
        Normalized (yearly share) comparison of full corpus vs sampled subset,
        one line per court level. Solid = full corpus; dashed = sample.
        Confirms the temporal spread sample is representative.
    """
    levels = sampling_summary["court_level"].to_list()


    # 2. Line: full corpus case counts by court level over time
    full_yearly = (
        metadata_for_sampling
        .group_by(["year", "court_level"])
        .agg(pl.len().alias("full_case_count"))
        .sort(["court_level", "year"])
        .to_pandas()
    )
    levels_sorted_full = sorted(full_yearly["court_level"].dropna().unique())
    colors_full = plt.cm.Set2(np.linspace(0, 1, len(levels_sorted_full)))
    plt.figure(figsize=(13, 7))
    for color, level in zip(colors_full, levels_sorted_full):
        ldf = full_yearly[full_yearly["court_level"] == level]
        plt.plot(
            ldf["year"], ldf["full_case_count"],
            color=color, linewidth=2.0, marker="o", markersize=3, alpha=0.9, label=str(level),
        )
    plt.xlabel("Year")
    plt.ylabel("Case count")
    plt.title("Full corpus case counts by court level over time")
    plt.legend(title="Court level", bbox_to_anchor=(1.02, 1), loc="upper left")
    plt.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig(out_dir / "temporal_trend_by_court_level.png", dpi=180)
    plt.close()

    # 3. Normalized comparison per court level: full corpus vs sampled subset
    yearly_full = (
        metadata_for_sampling
        .group_by(["year", "court_level"])
        .agg(pl.len().alias("full_case_count"))
        .with_columns(
            (pl.col("full_case_count") / pl.col("full_case_count").sum().over("court_level"))
            .alias("full_case_share")
        )
        .sort(["court_level", "year"])
    )
    yearly_sample = (
        sampled_metadata
        .group_by(["year", "court_level"])
        .agg(pl.len().alias("sample_case_count"))
        .with_columns(
            (pl.col("sample_case_count") / pl.col("sample_case_count").sum().over("court_level"))
            .alias("sample_case_share")
        )
        .sort(["court_level", "year"])
    )
    levels_sorted = yearly_full["court_level"].unique().sort().to_list()
    colors = plt.cm.tab10(np.linspace(0, 1, len(levels_sorted)))
    plt.figure(figsize=(13, 7))
    for color, level in zip(colors, levels_sorted):
        lf = yearly_full.filter(pl.col("court_level") == level)
        ls = yearly_sample.filter(pl.col("court_level") == level)
        plt.plot(
            lf["year"].to_list(), lf["full_case_share"].to_list(),
            color=color, linewidth=1.8, alpha=0.8, label=f"{level} - full",
        )
        plt.plot(
            ls["year"].to_list(), ls["sample_case_share"].to_list(),
            color=color, linestyle="--", marker="o", markersize=3,
            linewidth=1.6, alpha=0.95, label=f"{level} - sample",
        )
    plt.xlabel("Year")
    plt.ylabel("Normalized yearly share")
    plt.title("ECHR yearly distribution by court level: full vs sample (normalized)")
    plt.legend(title="Court level", bbox_to_anchor=(1.02, 1), loc="upper left")
    plt.grid(alpha=0.25)
    plt.tight_layout()
    plt.savefig(out_dir / "temporal_normalized_by_court_level.png", dpi=180)
    plt.close()

    print(f"Wrote temporal coverage charts to: {out_dir}")


# ---
# Country ISO3 helpers
# ---

def _first_country_name(value: Any) -> str | None:
    """Extract the first non-empty string from a scalar or list country_name value."""
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            if item is not None and str(item).strip():
                return str(item).strip()
        return None
    return str(value).strip() or None


def _extract_respondent_alpha3(value: Any) -> str | None:
    """Parse the first three-letter uppercase code from a respondent field value."""
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            text = str(item).strip().upper()
            if re.fullmatch(r"[A-Z]{3}", text):
                return text
        return None
    text = str(value).strip()
    hits = re.findall(r"[A-Z]{3}", text.upper())
    return hits[0] if hits else None


def _to_iso3(country_name: str | None, respondent_code: str | None) -> str | None:
    """Resolve a country name or respondent code to an ISO 3166-1 alpha-3 code."""
    if country_name:
        key = country_name.strip().lower()
        if key in _COUNTRY_ALIASES:
            return _COUNTRY_ALIASES[key]
        try:
            match = pycountry.countries.lookup(country_name)
            if hasattr(match, "alpha_3"):
                return match.alpha_3
        except LookupError:
            pass
    if respondent_code:
        code = respondent_code.strip().upper()
        if len(code) == 3:
            return code
        try:
            match = pycountry.countries.lookup(code)
            if hasattr(match, "alpha_3"):
                return match.alpha_3
        except LookupError:
            pass
    return None


def _build_map_df(all_cases: pl.DataFrame) -> pd.DataFrame:
    """Build a pandas DataFrame with an iso3 column resolved for every case."""
    pdf = all_cases.select(["itemid", "source", "year", "country_name", "respondent"]).to_pandas()
    pdf["country_name_first"] = pdf["country_name"].apply(_first_country_name)
    pdf["respondent_code"] = pdf["respondent"].apply(_extract_respondent_alpha3)
    pdf["iso3"] = [
        _to_iso3(cn, rc)
        for cn, rc in zip(pdf["country_name_first"], pdf["respondent_code"])
    ]
    mapped = pdf[pdf["iso3"].notna()].copy()
    print(
        f"Country mapping: {len(pdf):,} total cases -> {len(mapped):,} mapped "
        f"({mapped['iso3'].nunique():,} countries)"
    )
    return mapped


# ---
# Country choropleth: static Europe map
# ---

def plot_country_map_static(all_cases: pl.DataFrame, out_dir: Path) -> None:
    """
    Static choropleth of Article 6 case share by European country (full period).

    Each country is shaded by its percentage of all European Art. 6 cases across
    the entire dataset (1959–2025). Turkey is annotated separately because it
    often dominates the colour scale and would otherwise be visually ambiguous.

    Outputs:
        art6_country_share_full_period_europe.html — interactive Plotly figure
        art6_country_share_full_period_europe.png  — static export (1400×900)
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    map_df = _build_map_df(all_cases)

    static_df = map_df[map_df["iso3"].isin(EUROPE_ISO3)].copy()
    static_counts = (
        static_df.groupby("iso3", dropna=False)
        .size()
        .reset_index(name="case_count")
    )
    static_counts = (
        static_counts.set_index("iso3")
        .reindex(sorted(EUROPE_ISO3), fill_value=0)
        .rename_axis("iso3")
        .reset_index()
    )
    total = static_counts["case_count"].sum()
    static_counts["pct_share"] = (static_counts["case_count"] / total) * 100.0

    fig = px.choropleth(
        static_counts,
        locations="iso3",
        color="pct_share",
        color_continuous_scale="YlOrRd",
        range_color=(0, float(static_counts["pct_share"].max())),
        title="Article 6 Cases by Country (% of all Europe cases, full period)",
        labels={"pct_share": "% share"},
    )
    fig.update_layout(margin=dict(l=0, r=0, t=60, b=0), geo=_FIXED_GEO)

    html_path = out_dir / "art6_country_share_full_period_europe.html"
    png_path = out_dir / "art6_country_share_full_period_europe.png"
    fig.write_html(html_path)
    fig.write_image(png_path, scale=2, width=1400, height=900)
    print(f"Wrote static country map HTML: {html_path}")
    print(f"Wrote static country map PNG:  {png_path}")


# ---
# Country choropleth: animated GIF + interactive HTML
# ---

def generate_country_gif(all_cases: pl.DataFrame, out_dir: Path) -> None:
    """
    Animated choropleth showing Article 6 case share per country across 6-year windows.

    Cases are binned into 6-year periods starting from 1959. For each period a
    Plotly choropleth frame is rendered as a PNG; frames are then stitched into a
    looping GIF (each period displayed for ~3 s). A fully interactive animated
    HTML version with a play button and period slider is also produced.

    Outputs:
        art6_country_share_6year.gif          — animated GIF (all periods)
        art6_country_share_6year_animated.html — interactive Plotly animation
        art6_country_share_6y_frames/         — individual per-period PNGs
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    map_df = _build_map_df(all_cases)

    temporal_df = map_df[
        map_df["year"].notna()
        & map_df["iso3"].isin(EUROPE_ISO3)
        & (map_df["year"].astype(int) <= 2025)
    ].copy()
    temporal_df["year"] = temporal_df["year"].astype(int)

    if temporal_df.empty:
        raise ValueError("No Europe-mapped cases with valid year available for GIF generation.")

    def _period(y: int) -> tuple[int, int]:
        if y <= 1964:
            return 1959, 1964
        if y >= 2020:
            return 2020, 2025
        start = 1965 + ((y - 1965) // 5) * 5
        return start, start + 4

    bounds = temporal_df["year"].apply(_period)
    temporal_df["bin_start"] = bounds.apply(lambda t: t[0])
    temporal_df["bin_end"] = bounds.apply(lambda t: t[1])
    temporal_df["period"] = temporal_df["bin_start"].astype(str) + "-" + temporal_df["bin_end"].astype(str)

    bin_country_counts = (
        temporal_df.groupby(["period", "bin_start", "iso3"], dropna=False)
        .size()
        .reset_index(name="case_count")
    )
    bin_totals = (
        temporal_df.groupby(["period", "bin_start"], dropna=False)
        .size()
        .reset_index(name="period_total")
    )

    periods_df = bin_totals[["period", "bin_start"]].drop_duplicates().sort_values("bin_start")
    countries_df = pd.DataFrame({"iso3": sorted(EUROPE_ISO3)})
    all_period_country = (
        periods_df.assign(_k=1)
        .merge(countries_df.assign(_k=1), on="_k", how="inner")
        .drop(columns=["_k"])
    )
    anim_df = all_period_country.merge(
        bin_country_counts, on=["period", "bin_start", "iso3"], how="left"
    )
    anim_df["case_count"] = anim_df["case_count"].fillna(0).astype(int)
    anim_df = anim_df.merge(bin_totals, on=["period", "bin_start"], how="left")
    anim_df["pct_share"] = (anim_df["case_count"] / anim_df["period_total"]) * 100.0
    anim_df = anim_df.sort_values(["bin_start", "iso3"]).reset_index(drop=True)

    period_order = (
        anim_df[["period", "bin_start"]]
        .drop_duplicates()
        .sort_values("bin_start")["period"]
        .tolist()
    )
    max_pct = float(anim_df["pct_share"].max())

    frames_dir = out_dir / "art6_country_share_6y_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_paths: list[Path] = []

    print(f"Rendering {len(period_order)} choropleth frames...")
    for period in period_order:
        frame_df = anim_df[anim_df["period"] == period]
        fig = px.choropleth(
            frame_df,
            locations="iso3",
            color="pct_share",
            color_continuous_scale="YlOrRd",
            range_color=(0, max_pct),
            title="Article 6 Cases by Country (% share), Europe",
            labels={"pct_share": "% share"},
        )
        fig.update_layout(margin=dict(l=0, r=0, t=60, b=0), geo=_FIXED_GEO)
        fig.add_annotation(
            x=0.99, y=0.99, xref="paper", yref="paper",
            text=f"Period: {period}", showarrow=False,
            xanchor="right", yanchor="top",
            font=dict(size=20, color="black"),
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="rgba(0,0,0,0.35)", borderwidth=1,
        )
        frame_path = frames_dir / f"art6_country_share_{period}.png"
        fig.write_image(frame_path, scale=2, width=1400, height=900)
        frame_paths.append(frame_path)

    # Stitch GIF: each period repeated 3x at 1s/subframe -> 3s effective display
    gif_path = out_dir / "art6_country_share_6year.gif"
    repeat_each = 3
    expanded: list[Image.Image] = []
    for path in frame_paths:
        with Image.open(path) as im:
            base = im.convert("P", palette=Image.ADAPTIVE)
            for _ in range(repeat_each):
                expanded.append(base.copy())

    expanded[0].save(
        gif_path,
        save_all=True,
        append_images=expanded[1:],
        duration=1000,
        loop=0,
        optimize=False,
        disposal=2,
    )

    # Interactive animated HTML
    fig_anim = px.choropleth(
        anim_df,
        locations="iso3",
        color="pct_share",
        animation_frame="period",
        color_continuous_scale="YlOrRd",
        range_color=(0, max_pct),
        title="Article 6 Cases by Country (% share), Europe, 6-year windows",
        labels={"pct_share": "% share", "period": "Period"},
        category_orders={"period": period_order},
    )
    fig_anim.update_layout(margin=dict(l=0, r=0, t=60, b=0), geo=_FIXED_GEO)
    for fr in fig_anim.frames:
        fr.layout.update(annotations=[dict(
            x=0.99, y=0.99, xref="paper", yref="paper",
            text=f"Period: {fr.name}", showarrow=False,
            xanchor="right", yanchor="top",
            font=dict(size=20, color="black"),
            bgcolor="rgba(255,255,255,0.85)",
            bordercolor="rgba(0,0,0,0.35)", borderwidth=1,
        )])
    if fig_anim.layout.updatemenus and len(fig_anim.layout.updatemenus) > 0:
        fig_anim.layout.updatemenus[0].buttons[0].args[1]["frame"]["duration"] = 3000
        fig_anim.layout.updatemenus[0].buttons[0].args[1]["transition"]["duration"] = 0
    if fig_anim.layout.sliders and len(fig_anim.layout.sliders) > 0:
        for step in fig_anim.layout.sliders[0].steps:
            step.args[1]["frame"]["duration"] = 3000
            step.args[1]["transition"]["duration"] = 0

    anim_html = out_dir / "art6_country_share_6year_animated.html"
    fig_anim.write_html(anim_html)

    print(f"Wrote GIF ({len(frame_paths)} periods x {repeat_each} subframes): {gif_path}")
    print(f"Wrote animated HTML: {anim_html}")
    print(f"Wrote frame PNGs: {frames_dir}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- RDF ontology + metadata structure ---
    print("\n=== RDF graph analysis ===")
    seed_graph = load_rdf_graph(SEED_TTL)
    metadata_graph = load_rdf_graph(METADATA_TTL)

    report = render_report(seed_graph, metadata_graph)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Wrote report: {REPORT_PATH}")

    render_seed_structure(seed_graph, OUT_DIR / "seed_structure.png")
    render_metadata_structure(metadata_graph, OUT_DIR / "metadata_structure.png")
    render_seed_rdf2dot(seed_graph, OUT_DIR / "seed_rdf2dot.png")
    render_seed_neato(seed_graph, OUT_DIR / "seed_neato.png")

    meta_stats = collect_basic_stats(metadata_graph)
    plot_bar_from_counter(
        metadata_graph,
        meta_stats["top_predicates"][:20],
        title="Metadata Graph: Top Predicates",
        x_label="Triple count",
        output_path=OUT_DIR / "metadata_top_predicates.png",
    )
    plot_bar_from_counter(
        metadata_graph,
        meta_stats["top_types"][:20],
        title="Metadata Graph: Top rdf:type Classes",
        x_label="Instance count",
        output_path=OUT_DIR / "metadata_top_types.png",
    )
    metadata_extra = collect_metadata_extras(metadata_graph)
    plot_case_triple_distribution(
        metadata_extra["case_triple_counts"],
        output_path=OUT_DIR / "metadata_case_triple_distribution.png",
    )

    # --- Processed metadata: temporal + country analyses ---
    print("\n=== Temporal coverage analysis ===")
    all_cases = load_processed_metadata()
    metadata_for_sampling, sampled_metadata, sampling_summary = build_corpus_sample(all_cases)
    plot_temporal_coverage(metadata_for_sampling, sampled_metadata, sampling_summary, OUT_DIR)

    print("\n=== Country choropleth maps ===")
    plot_country_map_static(all_cases, OUT_DIR)
    generate_country_gif(all_cases, OUT_DIR)

    print(f"\nAll outputs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
