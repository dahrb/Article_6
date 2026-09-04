"""Regression tests for the repair applier's `remove` path.

Built from the four operations that silently no-opped on
`results/jurix_phase1/o2_low_jsonld/repaired/input.L1.facts.repairs.json`
(doc:admin_action_1, the Ruse-guardian / Rila-mayor conflation). All four were
audited as "skipped: triple not present" against a graph that plainly held the
triple; the conflation shipped. See the LEXICAL FALLBACK comment in
apply_patch for the measurement.
"""

import pytest
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, XSD

from art6.ontology.repair_facts import (
    RepairGroup,
    RepairPatch,
    TripleOp,
    apply_patch,
)

DOC_NS = "https://growgraph.dev/doc/test/"
DOC = Namespace(DOC_NS)
ECHR = Namespace("https://growgraph.dev/echr#")


def _graph():
    g = Graph()
    g.bind("doc", DOC)
    g.bind("echr", ECHR)
    g.bind("rdfs", RDFS)
    g.bind("xsd", XSD)
    g.add((DOC.admin_action_1, RDF.type, ECHR.AdministrativeAction))
    # Two labels: the false merge repair is sent to undo.
    g.add(
        (
            DOC.admin_action_1,
            RDFS.label,
            Literal("Appointment of guardian by Ruse Municipal Council", lang="en"),
        )
    )
    g.add(
        (
            DOC.admin_action_1,
            RDFS.label,
            Literal("mayor of Rila's refusal to bring court action", lang="en"),
        )
    )
    # Two decision dates, one typed and one plain -- both forms occur in the
    # shipped graphs.
    g.add(
        (
            DOC.admin_action_1,
            ECHR.hasDecisionDate,
            Literal("2002-05-23", datatype=XSD.date),
        )
    )
    g.add(
        (
            DOC.admin_action_1,
            ECHR.hasDecisionDate,
            Literal("2005-09-16", datatype=XSD.date),
        )
    )
    return g


def _apply(op):
    patch = RepairPatch(groups=[RepairGroup(finding="f", rationale="r", ops=[op])])
    return apply_patch(_graph(), patch, DOC_NS)


# The four ops exactly as the model emitted them, verbatim from the audit log.
@pytest.mark.parametrize(
    "obj,datatype,lang,predicate,survivor",
    [
        # 1. Turtle quoting, no datatype (unwrap_literal handles the quotes,
        #    the missing xsd:date used to kill it anyway).
        ('"2005-09-16"', None, None, "echr:hasDecisionDate", "2002-05-23"),
        # 2. Turtle quoting on a label carrying @en.
        (
            '"mayor of Rila\'s refusal to bring court action"',
            None,
            None,
            "rdfs:label",
            "Appointment of guardian by Ruse Municipal Council",
        ),
        # 3. Bare text, datatype guessed as xsd:string, graph holds @en.
        (
            "mayor of Rila's refusal to bring court action",
            "xsd:string",
            None,
            "rdfs:label",
            "Appointment of guardian by Ruse Municipal Council",
        ),
        # 4. Bare date, datatype guessed as xsd:string, graph holds xsd:date.
        ("2005-09-16", "xsd:string", None, "echr:hasDecisionDate", "2002-05-23"),
    ],
)
def test_remove_lands_despite_datatype_and_lang_mismatch(
    obj, datatype, lang, predicate, survivor
):
    out, audit = _apply(
        TripleOp(
            action="remove",
            subject="doc:admin_action_1",
            predicate=predicate,
            object=obj,
            object_is_literal=True,
            datatype=datatype,
            lang=lang,
        )
    )
    assert audit[0]["status"].startswith("applied"), audit[0]["status"]
    remaining = [
        str(o) for o in out.objects(DOC.admin_action_1, resolve_pred(out, predicate))
    ]
    assert remaining == [survivor], remaining


def resolve_pred(g, curie):
    prefix, _, local = curie.partition(":")
    return URIRef(str(dict(g.namespaces())[prefix]) + local)


def test_exact_match_still_reports_applied_not_fallback():
    """An op that names the term exactly must not go through the fallback."""
    out, audit = _apply(
        TripleOp(
            action="remove",
            subject="doc:admin_action_1",
            predicate="rdfs:label",
            object="mayor of Rila's refusal to bring court action",
            object_is_literal=True,
            datatype=None,
            lang="en",
        )
    )
    assert audit[0]["status"] == "applied"
    assert len(list(out.objects(DOC.admin_action_1, RDFS.label))) == 1


def test_genuinely_absent_literal_still_skips():
    """The fallback must not turn a wrong op into a silent success."""
    out, audit = _apply(
        TripleOp(
            action="remove",
            subject="doc:admin_action_1",
            predicate="rdfs:label",
            object="a label this node never had",
            object_is_literal=True,
            datatype=None,
            lang=None,
        )
    )
    assert audit[0]["status"] == "skipped: triple not present"
    assert len(list(out.objects(DOC.admin_action_1, RDFS.label))) == 2


def test_fallback_does_not_touch_other_subjects():
    g = _graph()
    g.add((DOC.other_node, RDF.type, ECHR.AdministrativeAction))
    g.add(
        (
            DOC.other_node,
            RDFS.label,
            Literal("mayor of Rila's refusal to bring court action", lang="en"),
        )
    )
    patch = RepairPatch(
        groups=[
            RepairGroup(
                finding="f",
                rationale="r",
                ops=[
                    TripleOp(
                        action="remove",
                        subject="doc:admin_action_1",
                        predicate="rdfs:label",
                        object="mayor of Rila's refusal to bring court action",
                        object_is_literal=True,
                        datatype="xsd:string",
                        lang=None,
                    )
                ],
            )
        ]
    )
    out, audit = apply_patch(g, patch, DOC_NS)
    assert audit[0]["status"].startswith("applied")
    assert len(list(out.objects(DOC.other_node, RDFS.label))) == 1


def test_uri_object_remove_unaffected():
    """Non-literal removes keep exact-match semantics."""
    g = _graph()
    g.add((DOC.admin_action_1, ECHR.hasCourt, DOC.councilRuse))
    patch = RepairPatch(
        groups=[
            RepairGroup(
                finding="f",
                rationale="r",
                ops=[
                    TripleOp(
                        action="remove",
                        subject="doc:admin_action_1",
                        predicate="echr:hasCourt",
                        object="doc:councilRuse",
                        object_is_literal=False,
                        datatype=None,
                        lang=None,
                    )
                ],
            )
        ]
    )
    out, audit = apply_patch(g, patch, DOC_NS)
    assert audit[0]["status"] == "applied"
    assert (DOC.admin_action_1, ECHR.hasCourt, DOC.councilRuse) not in out


# --------------------------------------------------------------------------
# Participation balance guard.
#
# From o2_cf_low_jsonld L6, the first arm run after the literal-remove fix:
# repair correctly diagnosed a shared participation node, removed 20
# hasParticipation links and added back 5, leaving 10 of 15 events with no
# party information at all. Before the remove fix those ops silently no-opped
# and the defect was invisible.
# --------------------------------------------------------------------------


def _shared_graph():
    """Three events all pointing at one participation node -- the L6 shape."""
    g = Graph()
    g.bind("doc", DOC)
    g.bind("echr", ECHR)
    # The party must be a real typed node: apply_patch's referential-integrity
    # guard refuses an add whose doc: object was never minted.
    g.add((DOC.applicant, RDF.type, ECHR.Party))
    shared = DOC.participation_1_applicant
    g.add((shared, RDF.type, ECHR.Participation))
    g.add((shared, ECHR.participatingParty, DOC.applicant))
    g.add((shared, ECHR.hasPartySide, ECHR.SideInitiating))
    for n in (1, 2, 3):
        ev = DOC[f"proceeding_{n}"]
        g.add((ev, RDF.type, ECHR.DomesticProceeding))
        g.add((ev, ECHR.hasParticipation, shared))
    return g


def _op(action, subject, obj, predicate="echr:hasParticipation"):
    return TripleOp(
        action=action,
        subject=subject,
        predicate=predicate,
        object=obj,
        object_is_literal=False,
        datatype=None,
        lang=None,
    )


def test_bare_unlink_is_refused_when_nothing_replaces_it():
    patch = RepairPatch(
        groups=[
            RepairGroup(
                finding="participation reused across events",
                rationale="r",
                ops=[
                    _op("remove", "doc:proceeding_2", "doc:participation_1_applicant")
                ],
            )
        ]
    )
    out, audit = apply_patch(_shared_graph(), patch, DOC_NS)
    assert "would leave the event with no participation" in audit[0]["status"]
    assert len(list(out.objects(DOC.proceeding_2, ECHR.hasParticipation))) == 1


def test_empty_object_wipe_is_refused_too():
    """`remove` with an empty object means every value -- the same hazard."""
    patch = RepairPatch(
        groups=[
            RepairGroup(
                finding="f", rationale="r", ops=[_op("remove", "doc:proceeding_2", "")]
            )
        ]
    )
    out, audit = apply_patch(_shared_graph(), patch, DOC_NS)
    assert "would leave the event with no participation" in audit[0]["status"]
    assert len(list(out.objects(DOC.proceeding_2, ECHR.hasParticipation))) == 1


def test_unlink_allowed_when_the_patch_mints_a_replacement():
    """The legitimate repair: split the shared node, give the event its own."""
    patch = RepairPatch(
        groups=[
            RepairGroup(
                finding="f",
                rationale="r",
                ops=[
                    _op(
                        "add",
                        "doc:participation_2_applicant",
                        "echr:Participation",
                        predicate="rdf:type",
                    ),
                    _op(
                        "add",
                        "doc:participation_2_applicant",
                        "doc:applicant",
                        predicate="echr:participatingParty",
                    ),
                    _op("remove", "doc:proceeding_2", "doc:participation_1_applicant"),
                    _op("add", "doc:proceeding_2", "doc:participation_2_applicant"),
                ],
            )
        ]
    )
    out, audit = apply_patch(_shared_graph(), patch, DOC_NS)
    assert all("skipped" not in a["status"] for a in audit), [
        a["status"] for a in audit
    ]
    got = list(out.objects(DOC.proceeding_2, ECHR.hasParticipation))
    assert got == [DOC.participation_2_applicant]


def test_removing_one_of_several_participations_still_allowed():
    """The guard is about emptying an event, not about touching it at all."""
    g = _shared_graph()
    g.add((DOC.participation_extra, RDF.type, ECHR.Participation))
    g.add((DOC.proceeding_2, ECHR.hasParticipation, DOC.participation_extra))
    patch = RepairPatch(
        groups=[
            RepairGroup(
                finding="f",
                rationale="r",
                ops=[
                    _op("remove", "doc:proceeding_2", "doc:participation_1_applicant")
                ],
            )
        ]
    )
    out, audit = apply_patch(g, patch, DOC_NS)
    assert audit[0]["status"] == "applied"
    assert list(out.objects(DOC.proceeding_2, ECHR.hasParticipation)) == [
        DOC.participation_extra
    ]


def test_guard_does_not_touch_other_predicates():
    g = _shared_graph()
    g.add((DOC.proceeding_2, ECHR.hasCourt, DOC.someCourt))
    patch = RepairPatch(
        groups=[
            RepairGroup(
                finding="f",
                rationale="r",
                ops=[
                    _op(
                        "remove",
                        "doc:proceeding_2",
                        "doc:someCourt",
                        predicate="echr:hasCourt",
                    )
                ],
            )
        ]
    )
    _out, audit = apply_patch(g, patch, DOC_NS)
    assert audit[0]["status"] == "applied"


# --------------------------------------------------------------------------
# Repair may delete evidence anchors, never mint them.
# --------------------------------------------------------------------------


def _quote_graph():
    g = Graph()
    g.bind("doc", DOC)
    g.bind("echr", ECHR)
    g.add((DOC.proceeding_1, RDF.type, ECHR.DomesticProceeding))
    g.add(
        (
            DOC.proceeding_1,
            ECHR.hasSupportingQuote,
            Literal("a paraphrase that is not in the source", lang="en"),
        )
    )
    return g


SOURCE = "On 16 November 2007 the Commercial Court left the claim unexamined."


def test_add_supporting_quote_is_refused_even_when_verbatim():
    """Verbatim is not the test -- repair has no standing to choose an anchor."""
    patch = RepairPatch(
        groups=[
            RepairGroup(
                finding="f",
                rationale="r",
                ops=[
                    TripleOp(
                        action="add",
                        subject="doc:proceeding_1",
                        predicate="echr:hasSupportingQuote",
                        object=SOURCE,
                        object_is_literal=True,
                        datatype=None,
                        lang="en",
                    )
                ],
            )
        ]
    )
    out, audit = apply_patch(_quote_graph(), patch, DOC_NS, source_text=SOURCE)
    assert audit[0]["status"] == (
        "skipped: repair may not add evidence anchors, only remove them"
    )
    assert len(list(out.objects(DOC.proceeding_1, ECHR.hasSupportingQuote))) == 1


def test_removing_a_bad_quote_still_works():
    patch = RepairPatch(
        groups=[
            RepairGroup(
                finding="f",
                rationale="r",
                ops=[
                    TripleOp(
                        action="remove",
                        subject="doc:proceeding_1",
                        predicate="echr:hasSupportingQuote",
                        object="a paraphrase that is not in the source",
                        object_is_literal=True,
                        datatype="xsd:string",
                        lang=None,
                    )
                ],
            )
        ]
    )
    out, audit = apply_patch(_quote_graph(), patch, DOC_NS, source_text=SOURCE)
    assert audit[0]["status"].startswith("applied")
    assert list(out.objects(DOC.proceeding_1, ECHR.hasSupportingQuote)) == []


def test_ban_does_not_block_other_predicates():
    patch = RepairPatch(
        groups=[
            RepairGroup(
                finding="f",
                rationale="r",
                ops=[
                    TripleOp(
                        action="add",
                        subject="doc:proceeding_1",
                        predicate="echr:hasInstanceLevel",
                        object="echr:LevelAppeal",
                        object_is_literal=False,
                        datatype=None,
                        lang=None,
                    )
                ],
            )
        ]
    )
    _out, audit = apply_patch(_quote_graph(), patch, DOC_NS, source_text=SOURCE)
    assert audit[0]["status"] == "applied"


# --------------------------------------------------------------------------
# The review stage's exemption, and its price.
#
# First new_repair trial (2026-08-25, o2_low_jsonld L1): the model found two
# real missing events, quoted both verbatim, the quote adds were refused by a
# ban with no exemption, and the events landed unanchored -- worse than either
# intended outcome.
# --------------------------------------------------------------------------

from art6.ontology.new_repair import drop_unanchored_additions

REAL_SPAN = "On 25 November 2004 the applicant asked the prosecutor."


def _event_group(with_quote: bool, quote_text=REAL_SPAN):
    ops = [
        TripleOp(
            action="add",
            subject="doc:new_event",
            predicate="rdf:type",
            object="echr:ProsecutorialReview",
            object_is_literal=False,
            datatype=None,
            lang=None,
        ),
        TripleOp(
            action="add",
            subject="doc:new_event",
            predicate="echr:hasDecisionDate",
            object="2004-11-25",
            object_is_literal=True,
            datatype="xsd:date",
            lang=None,
        ),
    ]
    if with_quote:
        ops.append(
            TripleOp(
                action="add",
                subject="doc:new_event",
                predicate="echr:hasSupportingQuote",
                object=quote_text,
                object_is_literal=True,
                datatype=None,
                lang="en",
            )
        )
    return RepairPatch(
        groups=[RepairGroup(finding="missing event", rationale="r", ops=ops)]
    )


def test_review_may_add_an_anchored_event():
    patch, rejected = drop_unanchored_additions(_event_group(with_quote=True))
    assert rejected == []
    out, audit = apply_patch(
        _quote_graph(), patch, DOC_NS, source_text=REAL_SPAN, allow_quote_adds=True
    )
    assert all(a["status"].startswith("applied") for a in audit), [
        a["status"] for a in audit
    ]
    assert (
        DOC.new_event,
        ECHR.hasSupportingQuote,
        Literal(REAL_SPAN, lang="en"),
    ) in out


def test_unanchored_event_is_refused_as_a_whole_group():
    """The bug: the event must NOT land when its quote does not."""
    patch, rejected = drop_unanchored_additions(_event_group(with_quote=False))
    assert len(rejected) == 1
    assert "no supporting quote" in rejected[0]["status"]
    out, _audit = apply_patch(
        _quote_graph(), patch, DOC_NS, source_text=REAL_SPAN, allow_quote_adds=True
    )
    assert (DOC.new_event, RDF.type, ECHR.ProsecutorialReview) not in out
    assert list(out.objects(DOC.new_event, ECHR.hasDecisionDate)) == []


def test_fabricated_quote_still_refused_even_with_the_exemption():
    patch, rejected = drop_unanchored_additions(
        _event_group(with_quote=True, quote_text="a span that is not in the source")
    )
    assert rejected == []
    _out, audit = apply_patch(
        _quote_graph(), patch, DOC_NS, source_text=REAL_SPAN, allow_quote_adds=True
    )
    quote_ops = [a for a in audit if a["predicate"] == "echr:hasSupportingQuote"]
    assert quote_ops[0]["status"] == (
        "skipped: quote does not appear verbatim in the source document"
    )


def test_loop_still_cannot_add_quotes_at_all():
    """Default stays banned -- the exemption is opt-in for one caller."""
    patch, _ = drop_unanchored_additions(_event_group(with_quote=True))
    _out, audit = apply_patch(_quote_graph(), patch, DOC_NS, source_text=REAL_SPAN)
    quote_ops = [a for a in audit if a["predicate"] == "echr:hasSupportingQuote"]
    assert quote_ops[0]["status"] == (
        "skipped: repair may not add evidence anchors, only remove them"
    )
