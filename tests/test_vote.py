"""Stage 1b: the vote across stage-1 runs."""

import copy

from art6.ontology import vote as v

ACTS = {
    "e1": ("2001-01-10", "The applicant brought a claim before the District Court."),
    "e2": ("2002-02-20", "The District Court dismissed the claim in its entirety."),
    "e3": ("2003-03-30", "The Regional Court upheld that judgment on appeal."),
    "e4": ("2004-04-15", "The Supreme Court refused leave to appeal on points of law."),
}


def ev(eid, **extra):
    date, quote = ACTS[eid]
    return {
        "id": eid,
        "decision_date_iso": date,
        "what_happened_span": {"text": quote, "start": 0, "end": len(quote)},
        "significance": "major",
        **extra,
    }


def run(*events):
    return {"events": [copy.deepcopy(e) for e in events]}


def test_an_event_only_one_run_has_is_voted_out():
    base = run(ev("e1"), ev("e2", follows="e1"), ev("e4", follows="e2"))
    other = run(ev("e1"), ev("e2", follows="e1"))
    voted = v.vote(base, [other], min_runs=2)
    assert [e["id"] for e in voted["events"]] == ["e1", "e2"]


def test_the_majority_predecessor_wins_and_a_tie_keeps_the_base():
    base = run(ev("e1"), ev("e2", follows="e1"), ev("e3", follows="e2"))
    agree = run(ev("e1"), ev("e2", follows="e1"), ev("e3", follows="e1"))
    voted = v.vote(base, [agree, copy.deepcopy(agree)], min_runs=2)
    assert {e["id"]: e.get("follows") for e in voted["events"]}["e3"] == "e1"
    tie = v.vote(base, [agree], min_runs=2)
    assert {e["id"]: e.get("follows") for e in tie["events"]}["e3"] == "e2"


def test_links_walk_through_events_the_vote_dropped():
    base = run(ev("e1"), ev("e4", follows="e1"), ev("e3", follows="e4"))
    other = run(ev("e1"), ev("e3", follows="e1"))
    voted = v.vote(base, [other], min_runs=2)
    assert {e["id"]: e.get("follows") for e in voted["events"]} == {
        "e1": None,
        "e3": "e1",
    }


def test_minor_events_are_dropped_for_the_vote_and_put_back_after():
    hearing = ev("e4", follows="e2", significance="minor")
    full = run(ev("e1"), ev("e2", follows="e1"), hearing, ev("e3", follows="e4"))
    assert [e["id"] for e in v.major_only(full)["events"]] == ["e1", "e2", "e3"]
    assert v.major_only(full)["events"][2]["follows"] == "e2"  # contracted
    doc, added = v.vote_document({"payload": full}, [v.major_only(full)], min_runs=2)
    by = {e["id"]: e for e in doc["payload"]["events"]}
    assert added == 1 and by["e4"]["significance"] == "minor"
    assert by["e4"]["follows"] == "e2" and by["e3"]["follows"] == "e2"
