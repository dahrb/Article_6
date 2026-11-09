"""The cross-kind second alignment pass pairs one act recorded as a filing on one
side and a decision on the other, on the same date with overlapping quotes."""

from art6.ontology.diagnostics import score_graph_metrics as sgm

Q = "On 3 November 2005 the applicant asked to confront M.T. His request was rejected by the investigator."


def test_filing_and_decision_of_one_act_align_only_with_cross_kind():
    gold = [
        sgm.Node(
            key=8,
            body="the investigator",
            decision_date=None,
            start_date="2005-11-03",
            quote=Q,
        )
    ]
    arm = [
        sgm.Node(
            key="e9",
            body="the investigator",
            decision_date="2005-11-03",
            start_date=None,
            quote=Q,
        )
    ]
    assert sgm.align(gold, arm, cross_kind=False) == {}
    assert sgm.align(gold, arm, cross_kind=True) == {"e9": 8}


def test_cross_kind_needs_the_same_date():
    gold = [
        sgm.Node(key=8, body=None, decision_date=None, start_date="2005-11-03", quote=Q)
    ]
    arm = [
        sgm.Node(
            key="e9", body=None, decision_date="2005-11-04", start_date=None, quote=Q
        )
    ]
    assert sgm.align(gold, arm, cross_kind=True) == {}
