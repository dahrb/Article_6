"""
normalise.py
------------
Maps every condition's output into the common comparison form, so that
ontology-guided and unguided extraction can be scored on the same terms.

FULLY DETERMINISTIC. No model is involved in normalisation at all.

  O2  SPARQL projection over the graph.
  O1  field pass-through: O1 decodes under the common-form schema already.

This replaces an earlier design in which an LLM parse ran over every condition,
on the reasoning that a shared parser shares its error rather than charging it
to the baseline. Measured on the 2026-08-24 ten-document set, that reasoning
did not survive contact with the data. The parse was faithful where the graph
speaks -- 253 field comparisons against a SPARQL projection, 100% agreement,
zero values dropped -- but it RESCUED 63 fields the graph does not assert,
reading a deciding body, a date or an outcome out of an rdfs:label or a
supporting quote where the corresponding triple simply is not there.

That is fatal for the headline comparison. 22 of 70 deciding bodies in the
normalised O2 form were never asserted as echr:hasCourt by O2; the parser
recovered them from the evidence text. The comparison would then credit the
ontology condition with structure the ontology condition did not produce --
and precisely the structural defect the pipeline is being measured on (19 L10
proceedings carrying no hasCourt at all) becomes invisible in the measurement
built to detect it. A projection cannot do this: it can only report the triples
that exist.

With both remaining conditions projected deterministically, the instrument adds
no variance of its own to either side of the comparison, and normalisation
costs 0.2s per condition rather than 90s.

Usage:
  # O2 -- point at any directory of .facts.ttl (raw/ and repaired/ both)
  uv run python -m art6.conditions.normalise \\
      --condition o2 --in-dir results/.../nochunk_ttl_mv1/raw \\
      --out-dir results/jurix/normalised/o2_raw

  # O1 -- point at a run_conditions.py output directory
  uv run python -m art6.conditions.normalise \\
      --condition o1 --in-dir results/jurix/o1_gemma \\
      --out-dir results/jurix/normalised/o1
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import time
from pathlib import Path

from rdflib import Graph, Namespace, URIRef

from art6.conditions.schema import NormalisedDocument, NormalisedProceeding
from art6.paths import relative

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
ECHR = Namespace("https://growgraph.dev/echr#")

# What to read for each condition. The O2 entry is a glob over Turtle files so
# the same command normalises raw/ and repaired/ -- the study scores both
# checkpoints, because recall is a property of raw/ and precision of repaired/.
CONDITION_INPUTS = {
    "o2": "*.facts.ttl",
    "o1": "*.o1.json",
}


def document_key(path: Path) -> str:
    """The `input.L<N>` stem shared by every condition's file for one document.

    The study is within-document -- each document is extracted under every
    condition and compared against itself -- so the pairing is only as good as
    this key. Derived from the filename rather than from anything inside the
    file, so a format with no place for an id still pairs.
    """
    stem = path.name
    for suffix in (".facts.ttl", ".o1.json", ".o1.txt"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return path.stem


# Every echr:DomesticEvent subclass, projected together. The chain is not only
# court hearings: an administrative decision, an enforcement step and a
# prosecutorial review are steps in it, and a projection that took only
# echr:DomesticProceeding would silently under-report O2's recall against
# conditions that were asked for "domestic proceedings" in the ordinary sense.
PROJECTION_SPARQL = """
PREFIX echr: <https://growgraph.dev/echr#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?event ?bodyName ?bodyLabel ?date ?level ?outcome ?quote WHERE {
  VALUES ?cls { echr:DomesticProceeding echr:AdministrativeAction
                echr:EnforcementAction echr:ProsecutorialReview }
  ?event a ?cls .
  OPTIONAL { ?event echr:hasCourt ?body .
             OPTIONAL { ?body echr:hasAuthorityName ?bodyName }
             OPTIONAL { ?body rdfs:label ?bodyLabel } }
  OPTIONAL { ?event echr:hasDecisionDate ?date }
  OPTIONAL { ?event echr:hasInstanceLevel ?level }
  OPTIONAL { ?event echr:hasOutcome ?outcome }
  OPTIONAL { ?event echr:hasSupportingQuote ?quote }
}
"""

# Participants, queried separately rather than as more OPTIONALs above. Joining
# them into the main projection would multiply every event row by its number of
# participants and turn the single-valued fields into a cross product that the
# first-wins collapse below would then resolve arbitrarily.
PARTIES_SPARQL = """
PREFIX echr: <https://growgraph.dev/echr#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?event ?partyName ?partyLabel ?side WHERE {
  ?event echr:hasParticipation ?participation .
  OPTIONAL { ?participation echr:participatingParty ?party .
             OPTIONAL { ?party echr:hasPartyName ?partyName }
             OPTIONAL { ?party rdfs:label ?partyLabel } }
  OPTIONAL { ?participation echr:hasPartySide ?side }
}
"""

# Custodial measures, likewise separate and likewise many-per-event.
CUSTODY_SPARQL = """
PREFIX echr: <https://growgraph.dev/echr#>
SELECT ?event ?measure ?start ?end WHERE {
  ?event echr:hasPreTrialDetention ?detention .
  OPTIONAL { ?detention echr:hasCustodialMeasure ?measure }
  OPTIONAL { ?detention echr:hasDetentionStartDate ?start }
  OPTIONAL { ?detention echr:hasDetentionEndDate ?end }
}
"""


def humanise_vocabulary_term(term: object | None) -> str | None:
    """`echr:LevelFirstInstance` -> `first instance`.

    The common form is free text on instance_level and outcome, because two of
    the three conditions were never given a vocabulary. Rendering O2's coded
    terms into ordinary English is what puts it on the same footing rather than
    leaving it with strings no other condition could ever produce. The type
    prefix is dropped and the CamelCase split; nothing is translated or mapped,
    so a new vocabulary member needs no change here.
    """
    if term is None:
        return None
    local = str(term).split("#")[-1]
    for prefix in ("Level", "Outcome", "Type", "Authority", "Side", "Gender"):
        if local.startswith(prefix) and len(local) > len(prefix):
            local = local[len(prefix) :]
            break
    return re.sub(r"(?<!^)(?=[A-Z])", " ", local).lower()


def project_o2(path: Path) -> NormalisedDocument:
    """One O2 graph into the common form, deterministically.

    Reports only what the graph asserts. Where a proceeding carries no
    echr:hasCourt, deciding_body is null -- even when the body is named plainly
    in the node's own label or supporting quote. That is the point: the study
    measures what the condition structured, and reading the court back out of
    the evidence text would report a link the extraction never made.

    Multiple values on one event (several quotes, an authority with both a name
    and a label) resolve to the first the projection sees. Only quotes occur in
    quantity, and the common form carries one exemplar rather than an evidence
    set, so the choice is presentational.
    """
    graph = Graph()
    graph.parse(path, format="turtle")

    rows: dict[str, dict] = {}
    for row in graph.query(PROJECTION_SPARQL):
        record = rows.setdefault(str(row.event), {})
        body = row.bodyName or row.bodyLabel
        record.setdefault("deciding_body", str(body) if body else None)
        record.setdefault("decision_date", str(row.date) if row.date else None)
        record.setdefault("instance_level", humanise_vocabulary_term(row.level))
        record.setdefault("outcome", humanise_vocabulary_term(row.outcome))
        record.setdefault("supporting_quote", str(row.quote) if row.quote else None)

    # O2 reifies a participant as a Participation node carrying a party and a
    # side; the common form wants "name (side)". Flattening here rather than in
    # the schema is what lets a condition that never reified anything be scored
    # on the same field.
    parties: dict[str, set[str]] = {}
    for row in graph.query(PARTIES_SPARQL):
        name = row.partyName or row.partyLabel
        if not name:
            continue
        side = humanise_vocabulary_term(row.side)
        parties.setdefault(str(row.event), set()).add(
            f"{name} ({side})" if side else str(name)
        )

    custody: dict[str, set[str]] = {}
    for row in graph.query(CUSTODY_SPARQL):
        measure = humanise_vocabulary_term(row.measure) or "detention"
        span = " to ".join(str(d) for d in (row.start, row.end) if d)
        custody.setdefault(str(row.event), set()).add(
            f"{measure} ({span})" if span else measure
        )

    for event, record in rows.items():
        record["parties"] = sorted(parties[event]) if event in parties else None
        record["custodial_measure"] = (
            "; ".join(sorted(custody[event])) if event in custody else None
        )

    # Undated events sort last rather than first: a step the extraction could
    # not date is not thereby the earliest step, and putting it at the front
    # would corrupt every subsequent order value.
    ordered_events = sorted(
        rows, key=lambda e: rows[e]["decision_date"] or "9999-99-99"
    )
    # followsProceeding is an edge between event IRIs; the common form carries
    # it as `order` values, so the mapping can only be built once the ordering
    # exists. An edge pointing outside this projection (a target that is not a
    # DomesticEvent, or was never extracted) is dropped rather than guessed at.
    order_of = {event: index for index, event in enumerate(ordered_events, start=1)}
    for event in ordered_events:
        targets = sorted(
            order_of[str(target)]
            for target in graph.objects(URIRef(event), ECHR.followsProceeding)
            if str(target) in order_of
        )
        rows[event]["follows"] = targets or None

    return NormalisedDocument(
        proceedings=[
            NormalisedProceeding(order=index, **rows[event])
            for index, event in enumerate(ordered_events, start=1)
        ]
    )


def project_o1(path: Path) -> NormalisedDocument:
    """One O1 output into the common form.

    A validation pass, not a conversion: O1 decodes under NormalisedDocument
    itself, so its output is already the common form and the only work here is
    to confirm that and renumber `order` into a dense 1..N sequence. Running an
    LLM over this could only lose rows.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload["proceedings"] if isinstance(payload, dict) else payload
    document = NormalisedDocument.model_validate({"proceedings": entries})

    # Renumbering `order` densely would orphan every `follows` reference, which
    # points at order values. Remap them in the same pass. A reference to an
    # order the model never emitted is dropped -- it names nothing.
    remap = {p.order: index for index, p in enumerate(document.proceedings, start=1)}
    for index, proceeding in enumerate(document.proceedings, start=1):
        if proceeding.follows:
            kept = sorted(
                {remap[o] for o in proceeding.follows if o in remap} - {index}
            )
            proceeding.follows = kept or None
        proceeding.order = index
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[3])
    parser.add_argument("--condition", required=True, choices=sorted(CONDITION_INPUTS))
    parser.add_argument("--in-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    pattern = CONDITION_INPUTS[args.condition]
    inputs = sorted(args.in_dir.glob(pattern))
    if not inputs:
        raise SystemExit(f"no {pattern} under {relative(args.in_dir)}")
    if args.limit:
        inputs = inputs[: args.limit]
    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"normalising {len(inputs)} {args.condition.upper()} output(s)")
    print(f"  in:  {relative(args.in_dir)}")
    print(f"  out: {relative(args.out_dir)}")

    run_start = time.perf_counter()
    records: list[dict] = []
    failures = 0

    for path in inputs:
        key = document_key(path)
        try:
            if args.condition == "o2":
                started = time.perf_counter()
                parsed = project_o2(path)
                timing = {
                    "seconds": round(time.perf_counter() - started, 2),
                    "instrument": "sparql",
                }
            else:
                started = time.perf_counter()
                parsed = project_o1(path)
                timing = {
                    "seconds": round(time.perf_counter() - started, 2),
                    "instrument": "passthrough",
                }
        except Exception as exc:  # noqa: BLE001 - a failure here is a result
            failures += 1
            print(f"  {key}: FAILED {type(exc).__name__}: {str(exc)[:160]}")
            records.append(
                {
                    "document": key,
                    "error": type(exc).__name__,
                    "message": str(exc)[:300],
                }
            )
            continue

        payload = {
            "document": key,
            "condition": args.condition,
            "source": str(relative(path)),
            "proceedings": [p.model_dump() for p in parsed.proceedings],
        }
        (args.out_dir / f"{key}.normalised.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        records.append(
            {"document": key, "proceedings": len(parsed.proceedings), **timing}
        )
        print(f"  {key}: {len(parsed.proceedings)} proceeding(s), {timing['seconds']}s")

    run_seconds = time.perf_counter() - run_start
    counts = [r["proceedings"] for r in records if "proceedings" in r]
    instruments = sorted(
        {r.get("instrument", "?") for r in records if "instrument" in r}
    )
    report = {
        "condition": args.condition,
        "instrument": instruments[0] if len(instruments) == 1 else instruments,
        "in_dir": str(relative(args.in_dir)),
        "documents": len(inputs),
        "failures": failures,
        "proceedings_total": sum(counts),
        "seconds_total": round(run_seconds, 1),
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "documents_detail": records,
    }
    (args.out_dir / "normalise_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(
        f"\n{args.condition.upper()}: {sum(counts)} proceeding(s) across "
        f"{len(counts)} document(s) in {run_seconds:.1f}s"
    )
    if failures:
        print(f"  {failures} document(s) could not be normalised")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
