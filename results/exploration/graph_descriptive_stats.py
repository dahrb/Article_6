"""Generate descriptive statistics and visualizations for seed and metadata RDF graphs.

Outputs are written to results/exploration:
- graph_descriptive_report.txt
- seed_structure.png
- metadata_top_predicates.png
- metadata_top_types.png
- metadata_case_triple_distribution.png
- metadata_structure_sample.png
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
from typing import Any

import matplotlib.pyplot as plt
import networkx as nx
import pydotplus
from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import OWL


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
SEED_TTL = ROOT / "ontology" / "seed.ttl"
METADATA_TTL = ROOT / "ontology" / "metadata.ttl"
REPORT_PATH = OUT_DIR / "graph_descriptive_report.txt"

ECHR = Namespace("https://github.com/dahrb/Art_6/tree/main/ontology/seed.ttl#")
ECHR_PREFIX = str(ECHR)


def load_graph(path: Path) -> Graph:
    if not path.exists():
        raise FileNotFoundError(f"Missing TTL file: {path}")
    g = Graph()
    g.parse(path.as_posix(), format="turtle")
    return g


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


def collect_basic_stats(g: Graph) -> dict[str, Any]:
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
    plt.figure(figsize=(11, 6))
    plt.hist(case_triple_counts, bins=25, color="#457b9d", alpha=0.9)
    plt.title("Metadata Graph: Distribution of Outgoing Triples per CaseDocument")
    plt.xlabel("Outgoing triples per case")
    plt.ylabel("Number of cases")
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def draw_nx_graph(graph: nx.DiGraph, title: str, output_path: Path, max_nodes: int | None = None) -> None:
    g = graph.copy()
    if max_nodes is not None and g.number_of_nodes() > max_nodes:
        top_nodes = sorted(g.degree, key=lambda x: x[1], reverse=True)[:max_nodes]
        keep = {n for n, _ in top_nodes}
        g = g.subgraph(keep).copy()

    plt.figure(figsize=(16, 12))
    pos = nx.spring_layout(g, k=1.0, iterations=120, seed=42)
    nx.draw_networkx_nodes(g, pos, node_size=700, node_color="#f4a261", alpha=0.9)
    nx.draw_networkx_edges(g, pos, edge_color="#7f8c8d", alpha=0.5, arrows=True, arrowsize=10)
    nx.draw_networkx_labels(g, pos, font_size=8)
    plt.title(title)
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(output_path, dpi=180)
    plt.close()


def build_seed_structure_dot(seed: Graph) -> pydotplus.Dot:
    dot = pydotplus.Dot(graph_type="digraph", rankdir="LR", splines="spline", concentrate=True)
    dot.set_node_defaults(shape="box", style="rounded,filled", fillcolor="#edf6ff", color="#4a6fa5", fontname="Arial", fontsize="10")
    dot.set_edge_defaults(color="#57606a", fontname="Arial", fontsize="9", arrowsize="0.7")

    class_nodes = {s for s in seed.subjects(RDF.type, OWL.Class) if is_echr_term(s)}
    object_properties = {s for s in seed.subjects(RDF.type, OWL.ObjectProperty) if is_echr_term(s)}

    node_added: set[str] = set()

    def add_node(term: URIRef, *, shape: str = "box", fillcolor: str = "#edf6ff", color: str = "#4a6fa5") -> str:
        node_id = qname(seed, term)
        if node_id in node_added:
            return node_id
        node_added.add(node_id)
        dot.add_node(
            pydotplus.Node(
                node_id,
                label=humanize_term(term),
                shape=shape,
                style="rounded,filled",
                fillcolor=fillcolor,
                color=color,
                fontname="Arial",
                fontsize="10",
            )
        )
        return node_id

    edge_labels: dict[tuple[str, str], set[str]] = {}
    edge_attrs: dict[tuple[str, str], dict[str, str]] = {}

    def remember_edge(src: URIRef, dst: URIRef, label: str, *, color: str = "#57606a", style: str = "solid") -> None:
        src_id = add_node(src)
        dst_id = add_node(dst)
        key = (src_id, dst_id)
        edge_labels.setdefault(key, set()).add(label)
        edge_attrs[key] = {"color": color, "style": style}

    for cls in sorted(class_nodes, key=str):
        add_node(cls)

    for prop in sorted(object_properties, key=str):
        prop_name = humanize_term(prop)
        domains = [d for d in seed.objects(prop, RDFS.domain) if is_echr_term(d)]
        ranges = [r for r in seed.objects(prop, RDFS.range) if is_echr_term(r)]
        for domain in domains:
            for range_term in ranges:
                remember_edge(domain, range_term, prop_name, color="#3a5a7a")

    for child, _, parent in seed.triples((None, RDFS.subClassOf, None)):
        if is_echr_term(child) and is_echr_term(parent):
            remember_edge(child, parent, "subClassOf", color="#9aa0a6", style="dashed")

    for (src_id, dst_id), labels in sorted(edge_labels.items()):
        attrs = edge_attrs[(src_id, dst_id)]
        label_text = "\n".join(sorted(labels))
        dot.add_edge(
            pydotplus.Edge(
                src_id,
                dst_id,
                label=label_text,
                color=attrs["color"],
                style=attrs["style"],
                fontname="Arial",
                fontsize="9",
                arrowhead="normal",
            )
        )

    return dot


def render_seed_structure(seed: Graph, output_path: Path) -> None:
    dot = build_seed_structure_dot(seed)
    png_bytes = dot.create_png()
    output_path.write_bytes(png_bytes)


def build_metadata_sample_graph(metadata: Graph, max_cases: int = 40) -> nx.DiGraph:
    dg = nx.DiGraph()
    cases = sorted({s for s in metadata.subjects(RDF.type, ECHR.CaseDocument)}, key=str)[:max_cases]
    case_set = set(cases)

    # Include one-hop outgoing and incoming URIs around selected cases.
    for case in cases:
        case_label = qname(metadata, case)
        dg.add_node(case_label)

        for p, o in metadata.predicate_objects(case):
            if isinstance(o, URIRef):
                on = qname(metadata, o)
                dg.add_node(on)
                dg.add_edge(case_label, on)

        for s, p in metadata.subject_predicates(case):
            if isinstance(s, URIRef) and s not in case_set:
                sn = qname(metadata, s)
                dg.add_node(sn)
                dg.add_edge(sn, case_label)

    return dg


def render_report(seed: Graph, metadata: Graph) -> str:
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


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    seed_graph = load_graph(SEED_TTL)
    metadata_graph = load_graph(METADATA_TTL)

    report = render_report(seed_graph, metadata_graph)
    REPORT_PATH.write_text(report, encoding="utf-8")

    render_seed_structure(seed_graph, OUT_DIR / "seed_structure.png")

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

    metadata_sample = build_metadata_sample_graph(metadata_graph, max_cases=40)
    draw_nx_graph(
        metadata_sample,
        title="Metadata Graph Structure Sample (40 Cases + 1-Hop URIs)",
        output_path=OUT_DIR / "metadata_structure_sample.png",
        max_nodes=180,
    )

    print("Wrote report:", REPORT_PATH)
    print("Wrote visualizations to:", OUT_DIR)


if __name__ == "__main__":
    main()
