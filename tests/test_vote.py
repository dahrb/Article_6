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


def test_every_procedural_event_is_voted_and_circumstances_keep_live_pointers():
    hearing = ev("e4", follows="e2")  # a hearing is procedural: voted like the rest
    base = run(ev("e1"), ev("e2", follows="e1"), hearing)
    base["circumstances"] = [
        {"id": "c1", "gives_rise_to": "e4"},
        {"id": "c2", "gives_rise_to": "e1"},
    ]
    other = run(ev("e1"), ev("e2", follows="e1"))
    doc = v.vote_document({"payload": base}, [other], min_runs=2)
    assert [e["id"] for e in doc["payload"]["events"]] == ["e1", "e2"]
    assert [c.get("gives_rise_to") for c in doc["payload"]["circumstances"]] == [
        None,
        "e1",
    ]


def circ(cid, start, end, **extra):
    return {
        "id": cid,
        "what_happened_span": {"text": "x", "start": start, "end": end},
        **extra,
    }


def test_circumstances_are_pooled_across_runs_without_duplicates():
    base = run(ev("e1"), ev("e2", follows="e1"))
    base["circumstances"] = [circ("c1", 300, 340)]
    other = run(ev("e1"), ev("e2", follows="e1"))
    other["circumstances"] = [
        circ("c1", 310, 330),  # the base's c1 again: skipped
        circ("c2", 200, 250, gives_rise_to="e1"),  # new: added, pointer carried
        circ("c3", 10, 30),  # overlaps a kept event's words: skipped
    ]
    doc = v.vote_document({"payload": base}, [other], min_runs=2)
    got = doc["payload"]["circumstances"]
    assert [(c["id"], c["what_happened_span"]["start"]) for c in got] == [
        ("c1", 200),
        ("c2", 300),
    ]
    assert got[0]["gives_rise_to"] == "e1"
