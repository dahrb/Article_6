"""Render simplified documents to RDF (ontology/simple) by code, and SHACL-check them.

    uv run python -m art6.simple.render IN_DIR OUT_DIR

No LLM: the simplified model is regular enough that the JSON maps 1:1 onto the
ontology. `followsAct` is materialised from derived date order. Prints, per
document, the triple count and whether it conforms to echr-simple-shapes.ttl.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from pyshacl import validate
from rdflib import RDF, RDFS, XSD, Graph, Literal, Namespace

from art6.simple.model import derived_follows

S = Namespace("https://growgraph.dev/echr-simple#")
ONT = Path("ontology/simple/echr-simple.ttl")
SHAPES = Path("ontology/simple/echr-simple-shapes.ttl")

LEVEL = {
    "investigation": "LevelInvestigation",
    "administrative": "LevelAdministrative",
    "first_instance": "LevelFirstInstance",
    "appeal": "LevelAppeal",
    "cassation": "LevelCassation",
    "constitutional": "LevelConstitutional",
    "other": "LevelOther",
}
OUTCOME = {
    "merits": "OutcomeMerits",
    "remitted": "OutcomeRemitted",
    "procedural": "OutcomeProcedural",
    "settled": "OutcomeSettled",
}
DIRECTION = {
    "for_applicant": "ForApplicant",
    "against_applicant": "AgainstApplicant",
    "mixed": "Mixed",
    "unknown": "DirectionUnknown",
}
KIND = {
    "civil": "KindCivil",
    "criminal": "KindCriminal",
    "administrative": "KindAdministrative",
    "disciplinary": "KindDisciplinary",
    "enforcement": "KindEnforcement",
    "length_remedy": "KindLengthRemedy",
    "other": "KindOther",
}


def date_literal(d: str) -> Literal:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return Literal(d, datatype=XSD.date)
    if re.fullmatch(r"\d{4}-\d{2}", d):
        return Literal(d, datatype=XSD.gYearMonth)
    return Literal(d, datatype=XSD.gYear)


def render(doc: dict) -> Graph:
    D = Namespace(f"https://growgraph.dev/case/{doc['case_id']}#")
    g = Graph()
    g.bind("echrs", S)
    g.bind("doc", D)
    case = D["case"]
    g.add((case, RDF.type, S.CaseDocument))
    for s in doc["sets"]:
        n = D[f"set_{s['id']}"]
        g.add((case, S.hasProceedingSet, n))
        g.add((n, RDF.type, S.ProceedingSet))
        g.add((n, S.hasKind, S[KIND.get(s.get("kind"), "KindOther")]))
        g.add((n, S.isPending, Literal(bool(s.get("pending")))))
        if s.get("label"):
            g.add((n, RDFS.label, Literal(s["label"])))
    for e in doc["events"]:
        n = D[f"act_{re.sub(r'[^A-Za-z0-9_]', '_', e['id'])}"]
        st = D[f"set_{e['set']}"]
        g.add((n, RDF.type, S.StartAct if e["role"] == "start" else S.Decision))
        g.add((n, S.inSet, st))
        if e["role"] == "start":
            g.add((st, S.hasStart, n))
        if e.get("date"):
            g.add((n, S.hasDate, date_literal(e["date"])))
        if e.get("body"):
            g.add((n, S.hasBody, Literal(e["body"])))
        if e.get("quote"):
            g.add((n, S.hasQuote, Literal(e["quote"])))
        if e["role"] == "decision":
            g.add((n, S.hasLevel, S[LEVEL.get(e.get("level"), "LevelOther")]))
            g.add((n, S.hasOutcome, S[OUTCOME.get(e.get("outcome"), "OutcomeMerits")]))
            g.add(
                (
                    n,
                    S.hasDirection,
                    S[DIRECTION.get(e.get("direction"), "DirectionUnknown")],
                )
            )
    ids = {
        e["id"]: D[f"act_{re.sub(r'[^A-Za-z0-9_]', '_', e['id'])}"]
        for e in doc["events"]
    }
    for cur, prev in derived_follows(doc).items():
        g.add((ids[cur], S.followsAct, ids[prev]))
    return g


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("in_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    a = ap.parse_args()
    a.out_dir.mkdir(parents=True, exist_ok=True)
    ont = Graph().parse(ONT)
    shapes = Graph().parse(SHAPES)
    ok = total = 0
    for f in sorted(a.in_dir.glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        if "sets" not in doc:
            continue
        g = render(doc)
        g.serialize(a.out_dir / f"{doc['case_id']}.ttl", format="turtle")
        conforms, _, text = validate(
            g, shacl_graph=shapes, ont_graph=ont, inference="rdfs"
        )
        total += 1
        ok += conforms
        print(
            f"{doc['case_id']}: {len(g)} triples, conforms={conforms}"
            + (
                ""
                if conforms
                else " | " + " ".join(re.findall(r"Message: (.*)", text))[:300]
            )
        )
    print(f"{ok}/{total} conform")


if __name__ == "__main__":
    main()
