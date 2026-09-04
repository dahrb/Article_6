"""
ablation_auto_eval.py
----------------------
Mechanical (no-LLM) metrics for a C0-C4 ablation run produced by
`art6/ontology/run_ablation.sh`, at whatever scale it was run.

Every number here is computed from the run's own output files -- the O1 JSON
for C0, the facts graphs for C1-C4, and the run's own source records -- with
no model call in the loop, so a report's numbers can always be regenerated and
diffed. This deliberately mirrors the methodology used by hand in
docs/ablation_c0_c4_20case.md, so the 250-case numbers are comparable to the
20-case tuning numbers rather than a different measurement.

Reused rather than reimplemented:
  - quote verification: art6.ontology.diagnostics.validate_source_quotes
    (normalize + ellipsis-aware substring check)
  - SHACL structural validity: art6.ontology.validate_shapes
  - proceeding extraction from a facts graph: the same "has a decision date or
    an instance level" selection rule as art6.conditions.render_for_judge,
    which exists because most proceedings are typed by SUBCLASS
    (ProsecutorialReview, AdministrativeAction, ...) and selecting on the
    literal echr:DomesticProceeding type alone undercounts by 3-4x.

What is NOT here: the "deciding body contradicts its own quote" and "Scores"
(1-5 rated dimensions) sections of the 20-case report. The first needs a
judgement call this script cannot safely automate without a large false-
positive rate (an authority named two different ways -- "the Regional Court"
vs "the Sofia Regional Court" -- is not a contradiction); the second is
explicitly subjective scoring by a human who read every document, which is
exactly the LLM-as-judge substitute this script exists to avoid reintroducing
by hand. Both remain future manual-inspection work, not auto-eval gaps.

Usage:
  uv run python -m art6.ontology.diagnostics.ablation_auto_eval \\
      --run-dir results/ablation_250_mv1 \\
      --source-json data/art6_eval_sample_judgments_flat.json \\
      --out results/ablation_250_mv1/auto_eval.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from rdflib import RDF, Graph, Namespace

from art6.ontology.diagnostics.validate_source_quotes import normalize, quote_verifies
from art6.paths import relative

ECHR = Namespace("https://growgraph.dev/echr#")

LAWYER_KEYWORDS = re.compile(
    r"\b(lawyer|advocate|counsel|attorney|solicitor|legal representative|"
    r"practising (?:law|in))\b",
    re.IGNORECASE,
)


def _short_label(text: str) -> str:
    if "#" in text:
        text = text.rsplit("#", 1)[1]
    for prefix in ("Side", "Authority", "Instance", "Outcome", "Level"):
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix) :]
    return text


def _norm_name(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


# ---------------------------------------------------------------------------
# Per-condition proceeding extraction, into one common record shape:
#   {body, quote, level, outcome, follows: bool, parties: [(name, side)]}
# ---------------------------------------------------------------------------


def extract_o1(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    entries = payload if isinstance(payload, list) else payload.get("proceedings") or []
    out = []
    for entry in entries:
        parties = []
        for raw in entry.get("parties") or []:
            match = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", raw)
            if match:
                parties.append((match.group(1).strip(), match.group(2).strip()))
            else:
                parties.append((raw.strip(), ""))
        out.append(
            {
                "body": entry.get("deciding_body"),
                "quote": entry.get("supporting_quote"),
                "level": entry.get("instance_level"),
                "outcome": entry.get("outcome"),
                "follows": bool(entry.get("follows")),
                "parties": parties,
            }
        )
    return out


def extract_graph(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    graph = Graph()
    try:
        graph.parse(path, format="turtle")
    except Exception:  # noqa: BLE001 - a malformed graph is a data point, not a crash
        return None
    # Same rule as render_for_judge.render_graph: a proceeding is identified
    # by what it HAS (a decision date or an instance level), not by asserted
    # rdf:type, because most proceedings are typed by subclass.
    events = [
        subject
        for subject in set(graph.subjects())
        if graph.value(subject, ECHR.hasDecisionDate) is not None
        or graph.value(subject, ECHR.hasInstanceLevel) is not None
    ]
    lawyer_nodes = {
        node for node in graph.subjects(RDF.type, ECHR.LegalRepresentative)
    } | {obj for _, _, obj in graph.triples((None, ECHR.isRepresentedBy, None))}

    out = []
    for event in events:
        body = graph.value(event, ECHR.hasCourt)
        body_label = None
        if body is not None:
            for predicate in (ECHR.hasAuthorityName,):
                value = graph.value(body, predicate)
                if value:
                    body_label = str(value)
                    break
        quote = graph.value(event, ECHR.hasSupportingQuote)
        level = graph.value(event, ECHR.hasInstanceLevel)
        outcome = graph.value(event, ECHR.hasOutcome)
        follows = graph.value(event, ECHR.followsProceeding) is not None
        parties = []
        for participation in graph.objects(event, ECHR.hasParticipation):
            party = graph.value(participation, ECHR.participatingParty)
            side = graph.value(participation, ECHR.hasPartySide)
            name = None
            if party is not None:
                name = str(graph.value(party, ECHR.hasPersonName) or party)
            side_label = _short_label(str(side)) if side else ""
            parties.append(
                (name or "", side_label, party in lawyer_nodes if party else False)
            )
        out.append(
            {
                "body": body_label,
                "quote": str(quote) if quote else None,
                "level": _short_label(str(level)) if level else None,
                "outcome": str(outcome) if outcome else None,
                "follows": follows,
                "parties": [(n, s) for n, s, _ in parties],
                "lawyer_party": any(is_lawyer for _, _, is_lawyer in parties),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def evaluate_condition(
    label: str,
    proceedings_by_doc: list[list[dict] | None],
    sources: list[str],
) -> dict:
    m = Counter()
    unverified_examples: list[str] = []

    for doc_idx, (proceedings, source_text) in enumerate(
        zip(proceedings_by_doc, sources)
    ):
        if proceedings is None:
            m["documents_no_output"] += 1
            continue
        if not proceedings:
            m["documents_zero_proceedings"] += 1
        m["proceedings"] += len(proceedings)

        normalized_source = normalize(source_text)
        quote_seen_this_doc: Counter = Counter()

        for p in proceedings:
            if not p.get("level"):
                m["missing_instance_level"] += 1
            if not p.get("outcome"):
                m["missing_outcome"] += 1
            if p.get("follows"):
                m["follows_links"] += 1

            parties = p.get("parties") or []
            if parties:
                m["with_party"] += 1

            body_norm = _norm_name(p.get("body"))
            party_names = {_norm_name(n) for n, _ in parties}
            if body_norm and body_norm in party_names:
                m["deciding_body_as_party"] += 1

            if p.get("lawyer_party"):
                m["lawyer_as_party"] += 1
            else:
                for name, _ in parties:
                    if LAWYER_KEYWORDS.search(name or ""):
                        m["lawyer_as_party"] += 1
                        break

            sides = {s for _, s in parties if s}
            has_initiating = any(
                s.lower() in ("initiating", "applicant") for s in sides
            )
            has_responding = any(
                s.lower() in ("responding", "respondent", "defendant") for s in sides
            )
            if len(parties) >= 2 and (
                (has_initiating and has_responding) or (not sides and len(parties) >= 2)
            ):
                m["two_sided"] += 1
            elif len(parties) == 1:
                m["one_sided"] += 1
            elif not parties:
                m["no_party"] += 1

            quote = p.get("quote")
            if quote:
                m["quotes_total"] += 1
                if quote_verifies(quote, normalized_source):
                    m["quotes_verified"] += 1
                else:
                    if len(unverified_examples) < 10:
                        unverified_examples.append(
                            f"{label} doc{doc_idx + 1}: {quote[:100]!r}"
                        )
                key = normalize(quote)
                quote_seen_this_doc[key] += 1

        m["duplicate_quotes"] += sum(
            count - 1 for count in quote_seen_this_doc.values() if count > 1
        )

    total_docs = len(proceedings_by_doc)
    result = dict(m)
    result["documents_total"] = total_docs
    result["quote_verbatim_rate"] = (
        m["quotes_verified"] / m["quotes_total"] if m["quotes_total"] else None
    )
    result["pct_with_party"] = (
        m["with_party"] / m["proceedings"] if m["proceedings"] else None
    )
    result["pct_two_sided"] = (
        m["two_sided"] / m["proceedings"] if m["proceedings"] else None
    )
    result["unverified_examples"] = unverified_examples
    return result


def run_shacl(facts_dir: Path) -> dict | None:
    if not facts_dir.is_dir():
        return None
    from art6.ontology import validate_shapes

    shapes_graph = validate_shapes.load_shapes()
    reports = validate_shapes.run_directory(facts_dir, shapes_graph)
    total_v = sum(r.violations for r in reports)
    total_w = sum(r.warnings for r in reports)
    n_conform = sum(1 for r in reports if r.conforms)
    return {
        "files": len(reports),
        "conforming": n_conform,
        "violations": total_v,
        "warnings": total_w,
        "median_violations": sorted(r.violations for r in reports)[len(reports) // 2]
        if reports
        else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--source-json", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    run = args.run_dir
    records = json.loads(args.source_json.read_text(encoding="utf-8"))
    if args.limit:
        records = records[: args.limit]
    sources = [r["text"] for r in records]
    n = len(sources)
    print(f"evaluating {n} document(s) from {relative(args.source_json)}")

    conditions = {
        "C0": ("o1", run / "C0", "input"),
        "C1": ("ttl", run / "C1_C2" / "raw", "input"),
        "C2": ("ttl", run / "C1_C2" / "repaired", "input"),
        "C3": ("ttl", run / "C3_C4" / "raw", "bundles"),
        "C4": ("ttl", run / "C3_C4" / "repaired", "bundles"),
    }

    results: dict[str, dict] = {}
    for label, (kind, directory, stem) in conditions.items():
        proceedings_by_doc: list[list[dict] | None] = []
        for i in range(1, n + 1):
            if kind == "o1":
                path = directory / f"{stem}.L{i}.o1.json"
                proceedings_by_doc.append(extract_o1(path))
            else:
                path = directory / f"{stem}.L{i}.facts.ttl"
                proceedings_by_doc.append(extract_graph(path))

        stats = evaluate_condition(label, proceedings_by_doc, sources)
        if kind == "ttl":
            stats["shacl"] = run_shacl(directory)
        results[label] = stats
        print(
            f"  {label}: {stats['documents_total'] - stats.get('documents_no_output', 0)}/"
            f"{stats['documents_total']} produced output, "
            f"{stats['proceedings']} proceeding(s), "
            f"verbatim={stats['quote_verbatim_rate']}"
        )

    out_path = args.out or (run / "auto_eval.json")
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {relative(out_path)}")


if __name__ == "__main__":
    main()
