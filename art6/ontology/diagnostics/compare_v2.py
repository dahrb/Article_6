"""Old repair vs new_repair across arms: the SHACL gate AND what it cannot see.

A falling violation count is not on its own evidence of a better graph -- the
cheapest way to satisfy a shape is to delete whatever breaks it. So every run
is reported alongside the content axes that deletion would move the wrong way.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rdflib import RDF, Graph, URIRef

from art6.ontology.new_repair import load_source_texts
from art6.ontology.repair_facts import find_shape_violations, find_unverified_quotes

ECHR = "https://growgraph.dev/echr#"
EVENT_CLASSES = (
    "DomesticProceeding",
    "AdministrativeAction",
    "EnforcementAction",
    "ProsecutorialReview",
)
AXES = (
    "triples",
    "events",
    "participations",
    "persons",
    "quotes",
    "follows",
    "unverif",
)


def measure(path: Path, source_text: str | None) -> dict[str, int]:
    graph = Graph()
    graph.parse(path, format="turtle")

    def typed(name: str) -> int:
        return len(set(graph.subjects(RDF.type, URIRef(ECHR + name))))

    return {
        "triples": len(graph),
        "events": sum(typed(c) for c in EVENT_CLASSES),
        "participations": typed("Participation"),
        "persons": typed("NaturalPerson"),
        "quotes": len(list(graph.objects(None, URIRef(ECHR + "hasSupportingQuote")))),
        "follows": len(
            list(graph.triples((None, URIRef(ECHR + "followsProceeding"), None)))
        ),
        "unverif": len(find_unverified_quotes(graph, source_text))
        if source_text
        else 0,
        "shacl": len(find_shape_violations(graph)),
    }


def main() -> None:
    results = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("results/jurix_phase1")
    arms = sys.argv[2:] or sorted(
        p.name for p in results.iterdir() if (p / "repaired_v2").is_dir()
    )
    sources = load_source_texts(results / "input.jsonl")

    header = f"{'arm':22} {'stage':10} {'SHACL':>6} " + " ".join(
        f"{a:>14}" for a in AXES
    )
    print(header)
    print("-" * len(header))
    for arm in arms:
        for stage, sub in (("raw", "raw"), ("old", "repaired"), ("new", "repaired_v2")):
            directory = results / arm / sub
            if not directory.is_dir():
                continue
            totals = dict.fromkeys((*AXES, "shacl"), 0)
            for facts in sorted(directory.glob("*.facts.ttl")):
                key = facts.name.removesuffix(".facts.ttl")
                for axis, value in measure(facts, sources.get(key)).items():
                    totals[axis] += value
            print(
                f"{arm:22} {stage:10} {totals['shacl']:6} "
                + " ".join(f"{totals[a]:>14}" for a in AXES)
            )
        print()


if __name__ == "__main__":
    main()
