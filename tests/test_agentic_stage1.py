"""Offline tests for the agentic stage 1: everything deterministic, plus one
end-to-end plan -> critic -> fix pass against a scripted model."""

import json
from types import SimpleNamespace
from typing import ClassVar

import pytest

from art6.ontology import agentic_stage1 as a
from art6.ontology import compress as c

JUDGMENT = """THE FACTS

THE CIRCUMSTANCES OF THE CASE

5. On 15 March 1999 the applicant instituted civil proceedings against his employer in the Kyiv District Court.
6. On 5 June 2003 the Kyiv District Court dismissed the claim.
7. On 1 September 2003 the Kyiv Regional Court of Appeal upheld that judgment.
8. Meanwhile, on 2 February 2001 the applicant was arrested by the police on suspicion of fraud.
9. On 10 October 2003 the Kyiv Regional Court of Appeal quashed both decisions and remitted the case.
10. On 4 April 2004 the Supreme Court refused the applicant leave to appeal in cassation, presided over by Judge K. Petrenko.

RELEVANT DOMESTIC LAW

11. Article 5 of the Code provides that on 1 January 1990 the law entered into force.
"""


def span(text):
    start = JUDGMENT.index(text)
    return {"text": text, "start": start, "end": start + len(text)}


def ev(eid, what, date=None, body=None, level="first instance", ptype="civil", **kw):
    e = {
        "id": eid,
        "what_happened_span": span(what),
        "instance_level": level,
        "proceeding_type": ptype,
    }
    if date:
        e["decision_date_span"] = span(date)
    if body:
        e["authority_span"] = span(body)
    e.update(kw)
    return e


def payload():
    return {
        "events": [
            ev(
                "e1",
                "instituted civil proceedings",
                "15 March 1999",
                start_date_span=None,
            ),
            ev(
                "e2",
                "dismissed the claim",
                "5 June 2003",
                "Kyiv District Court",
                outcome="merits decided",
            ),
            ev(
                "e3",
                "upheld that judgment",
                "1 September 2003",
                "Kyiv Regional Court of Appeal",
                level="appeal",
            ),
            ev(
                "e4",
                "was arrested by the police",
                "2 February 2001",
                "the police",
                level="investigative",
                ptype="criminal",
            ),
        ],
        "persons": [
            {"name_span": span("Judge K. Petrenko")},
            {"name_span": span("the applicant"), "role_span": span("the applicant")},
        ],
    }


# -- dates and judges ---------------------------------------------------------


def test_parse_date_precisions():
    assert a.parse_date("on 5 June 2003") == (2003, 6, 5)
    assert a.parse_date("in June 2003") == (2003, 6, 0)
    assert a.parse_date("in 2003") == (2003, 0, 0)
    assert a.parse_date("later") is None


def test_date_cmp_respects_shared_precision():
    assert a.date_cmp((2003, 6, 5), (2003, 6, 0)) == 0
    assert a.date_cmp((2003, 5, 30), (2003, 6, 1)) == -1
    assert a.date_cmp(None, (2003, 6, 1)) is None


@pytest.mark.parametrize(
    "name, role, expected",
    [
        ("Judge K. Petrenko", "", True),
        ("A.E.", "judge of the Court of Appeal", True),
        ("Ministry of Justice", "", False),
        ("the applicant", "complained that the judge was biased", False),
        ("Mr B.", "the applicant's lawyer", False),
    ],
)
def test_is_judge(name, role, expected):
    assert a.is_judge(name, role) is expected


def test_drop_judges_from_persons_and_parties():
    p = payload()
    p["events"][1]["parties"] = [{"name_span": span("Judge K. Petrenko")}]
    dropped = a.drop_judges(p)
    assert dropped.count("Judge K. Petrenko") == 2
    assert [x["name_span"]["text"] for x in p["persons"]] == ["the applicant"]
    assert p["events"][1]["parties"] == []


# -- candidates, plan, finalize ------------------------------------------------


def test_candidates_exclude_later_dated_events():
    cands = a.candidate_predecessors(payload()["events"])
    # e4 (2001) is narrated after e3 (2003) but dated before it, so it may
    # precede e3 -- and e3 may never precede e4.
    assert "e4" in cands["e3"]
    assert "e3" not in cands["e4"]
    assert cands["e1"] == ["e4"] or cands["e1"] == []
    # same-procedure candidates are ranked first
    assert cands["e3"][:2] == ["e2", "e1"]


def test_apply_plan_refuses_non_candidates_and_cross_track_links():
    p = payload()
    cands = a.candidate_predecessors(p["events"])
    plan = {
        "tracks": [
            {"id": "T1", "label": "civil claim", "events": ["e1", "e2", "e3"]},
            {"id": "T2", "label": "criminal", "events": ["e4"]},
        ],
        "links": [
            {
                "event": "e2",
                "predecessor": "e1",
                "kind": "continues",
                "evidence_span": "dismissed the claim",
            },
            {
                "event": "e3",
                "predecessor": "e2",
                "kind": "continues",
                "evidence_span": "upheld that judgment",
            },
            {"event": "e3", "predecessor": "e1", "kind": "continues"},
            {"event": "e4", "predecessor": "e3", "kind": "continues"},
            {"event": "e1", "predecessor": "e4", "kind": "continues"},
        ],
    }
    audit = a.apply_plan(p, plan, cands, JUDGMENT)
    assert audit["links_accepted"] == 2
    reasons = sorted(r["reason"] for r in audit["links_refused"])
    # e4 -> e3 and e1 -> e4 both point at an event dated after their own
    assert reasons == [
        "predecessor is not one of the event's candidates",
        "predecessor is not one of the event's candidates",
        "second link for one event",
    ]
    assert p["events"][2]["link"]["span"]["text"] == "upheld that judgment"

    p = payload()
    plan["links"] = [{"event": "e3", "predecessor": "e4", "kind": "continues"}]
    audit = a.apply_plan(p, plan, a.candidate_predecessors(p["events"]), JUDGMENT)
    assert [r["reason"] for r in audit["links_refused"]] == ["continues across tracks"]
    plan["links"][0]["kind"] = "during"
    assert a.apply_plan(payload(), plan, cands, JUDGMENT)["links_accepted"] == 1


def test_finalize_types_relations_and_points_at_roots():
    p = {
        "events": [
            ev(
                "a",
                "instituted civil proceedings",
                "15 March 1999",
                "Kyiv District Court",
            ),
            ev("b", "dismissed the claim", "5 June 2003", "Kyiv District Court"),
            ev(
                "c",
                "upheld that judgment",
                "1 September 2003",
                "Kyiv Regional Court of Appeal",
                level="appeal",
            ),
            ev(
                "d",
                "was arrested by the police",
                "2 February 2001",
                "the police",
                level="investigative",
                ptype="criminal",
            ),
        ],
        "tracks": [{"id": "T1", "label": "civil"}, {"id": "T9", "label": "crim"}],
    }
    for e in p["events"]:
        e["track"] = "T9" if e["id"] == "d" else "T1"
    p["events"][1]["link"] = {"to": "a", "kind": "continues"}
    p["events"][2]["link"] = {"to": "b", "kind": "continues"}
    p["events"][3]["link"] = {"to": "a", "kind": "during"}
    out = a.finalize(p)
    by_what = {e["what_happened_span"]["text"]: e for e in p["events"]}
    filing = by_what["instituted civil proceedings"]["id"]
    # same court, rung, procedure and track -> part_of the filing
    assert by_what["dismissed the claim"]["part_of"] == filing
    # the appeal continues the decision, but follows the PROCEEDING's root
    assert by_what["upheld that judgment"]["follows"] == filing
    assert by_what["was arrested by the police"]["occurs_during"] == filing
    assert [e["id"] for e in p["events"]] == ["e1", "e2", "e3", "e4"]
    assert [t["id"] for t in p["tracks"]] == ["T1", "T2"]
    assert out["relations"] == {
        "follows": 1,
        "part_of": 1,
        "occurs_during": 1,
        "cycles_broken": 0,
    }


def test_finalize_breaks_cycles():
    p = payload()
    for e in p["events"]:
        e["track"] = "T1"
    p["tracks"] = [{"id": "T1", "label": ""}]
    p["events"][0]["link"] = {"to": "e2", "kind": "continues"}
    p["events"][1]["link"] = {"to": "e1", "kind": "continues"}
    assert a.finalize(p)["relations"]["cycles_broken"] == 1


# -- hints, findings, fixes ---------------------------------------------------


def test_hints_find_uncovered_paragraph_inside_facts_only():
    p = payload()
    for e in p["events"]:
        e["track"] = "T1"
    hints = a.find_hints(p, JUDGMENT)
    uncovered = [h["text"] for h in hints if h["kind"] == "uncovered_paragraph"]
    assert any("quashed both decisions" in t for t in uncovered)
    assert any("Supreme Court refused" in t for t in uncovered)
    assert not any(
        "entered into force" in t for t in uncovered
    )  # domestic law, not facts


def test_validate_findings_requires_locatable_evidence():
    raw = {
        "findings": [
            {
                "kind": "missing_event",
                "evidence_span": "quashed both decisions and remitted the case",
            },
            {"kind": "missing_event", "evidence_span": "the court reopened the case"},
            {
                "kind": "wrong_field",
                "events": ["e9"],
                "evidence_span": "upheld that judgment",
            },
            {
                "kind": "bogus",
                "events": ["e1"],
                "evidence_span": "upheld that judgment",
            },
        ]
    }
    kept, refused = a.validate_findings(raw, payload(), JUDGMENT)
    assert [f["id"] for f in kept] == ["f1"]
    assert len(refused) == 3


def test_apply_fixes_validates_every_op():
    p = payload()
    for e in p["events"]:
        e["track"] = "T1"
    p["tracks"] = [{"id": "T1", "label": "civil"}]
    findings = [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}]
    ops = {
        "ops": [
            {
                "op": "add_event",
                "finding": "f1",
                "track": "T1",
                "predecessor": "e3",
                "kind": "continues",
                "event": {
                    "what_happened_span": "quashed both decisions and remitted the case",
                    "decision_date_span": "10 October 2003",
                    "outcome": "remitted",
                    "instance_level": "appeal",
                    "bogus_key": 1,
                },
            },
            {
                "op": "add_event",
                "finding": "f1",
                "event": {"what_happened_span": "invented words"},
            },
            {
                "op": "set_field",
                "finding": "f2",
                "id": "e2",
                "field": "outcome",
                "value": "won",
            },
            {
                "op": "set_field",
                "finding": "f2",
                "id": "e3",
                "field": "outcome",
                "value": "Merits decided",
            },
            {"op": "set_link", "finding": "f3", "id": "e1", "predecessor": "e3"},
            {"op": "remove_event", "finding": "f9", "id": "e4"},
        ]
    }
    audit = a.apply_fixes(p, ops, findings, JUDGMENT)
    statuses = [x["status"] for x in audit]
    assert statuses[0].startswith("applied")
    assert statuses[1] == "refused: what_happened_span not verbatim"
    assert statuses[2].startswith("refused: 'won'")
    assert statuses[3] == "applied"
    assert statuses[4] == "refused: predecessor dated after the event"
    assert statuses[5] == "refused: answers no finding"
    added = next(e for e in p["events"] if e["id"] == "n1")
    assert "bogus_key" not in added and added["link"]["to"] == "e3"
    assert added["decision_date_span"]["start"] == JUDGMENT.index("10 October 2003")
    assert p["events"][2]["outcome"] == "merits decided"


# -- end to end, scripted model -----------------------------------------------


class ScriptedClient:
    """Answers each agent from a script, keyed on a phrase near the start of
    its prompt. A list answers successive calls in turn (its last item
    repeats); every call is recorded with its prompt."""

    base_url = "http://127.0.0.1:9/v1"
    api_key = "x"
    MARKERS: ClassVar[dict[str, str]] = {
        "linking": "Organise the events into TRACKS",
        "link": "already grouped into TRACKS",
        "critic": "You are the CRITIC",
        "tracks": "PARALLEL TRACK agent",
        "fix": "You FIX a draft",
        "applicants": "opening of a European Court",
        "parties": "WHO WAS ON EACH SIDE",
    }

    def __init__(self, script):
        self.script = {
            k: (v if isinstance(v, list) else [v]) for k, v in script.items()
        }
        self.calls, self.prompts = [], []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        agent = next(k for k, m in self.MARKERS.items() if m in prompt[:400])
        self.calls.append(agent)
        self.prompts.append(prompt)
        replies = self.script.get(agent, [{}])
        n = sum(1 for c_ in self.calls if c_ == agent) - 1
        body = json.dumps(replies[min(n, len(replies) - 1)])
        message = SimpleNamespace(content=body)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(choices=[choice], model_dump=dict)


LINK_ALL_CIVIL = {
    "tracks": [{"id": "A", "label": "everything", "events": ["e1", "e2", "e3", "e4"]}],
    "links": [
        {"event": "e2", "predecessor": "e1", "kind": "continues"},
        {
            "event": "e3",
            "predecessor": "e2",
            "kind": "continues",
            "evidence_span": "upheld that judgment",
        },
    ],
}
CLEAN = {
    "needs_fix": "no",
    "parallel_tracks": "no",
    "parties_incomplete": "no",
    "findings": [],
}


def run(client, p=None, **kw):
    p = p or payload()
    stats = {}
    log = a.run_stage1_agents(
        client, "m", JUDGMENT, p, stats, temperature=0.0, capacity=100_000, **kw
    )
    return p, stats, log


@pytest.fixture(autouse=True)
def _offline_tokens(monkeypatch):
    monkeypatch.setattr(c, "count_prompt_tokens", lambda *args: 100)


def test_clean_critic_routes_to_nothing():
    client = ScriptedClient({"linking": LINK_ALL_CIVIL, "critic": CLEAN})
    _, _, log = run(client)
    assert client.calls == ["linking", "applicants", "parties", "critic"]
    assert all(f["how"] == "trusted" for f in log["rounds"][0]["critic"]["flags"])
    assert log["rounds"][0]["fix"] == {"skipped": "needs_fix: no"}


def test_parallel_tracks_route_to_track_agent_then_relink():
    critic = {
        "needs_fix": "yes",
        "parallel_tracks": "yes",
        "parallel_tracks_evidence": "was arrested by the police on suspicion of fraud",
        "parties_incomplete": "no",
        "findings": [
            {
                "kind": "wrong_track",
                "events": ["e4"],
                "evidence_span": "on suspicion of fraud",
                "issue": "criminal",
            }
        ],
    }
    tracks = {
        "tracks": [
            {
                "id": "C",
                "label": "civil claim",
                "kind": "civil claim",
                "subject": "the applicant",
                "events": ["e1", "e2", "e3"],
            },
            {
                "id": "K",
                "label": "prosecution",
                "kind": "prosecution",
                "events": ["e4"],
            },
        ],
        "relations": [
            {
                "track": "K",
                "relation": "during",
                "other": "C",
                "evidence_span": "Meanwhile",
            },
            {
                "track": "K",
                "relation": "arises_from",
                "other": "C",
                "evidence_span": "invented",
            },
        ],
    }
    relink = {
        "links": [
            {"event": "e2", "predecessor": "e1", "kind": "continues"},
            {"event": "e3", "predecessor": "e2", "kind": "continues"},
            {
                "event": "e4",
                "predecessor": "e3",
                "kind": "continues",
            },  # not a candidate
        ]
    }
    client = ScriptedClient(
        {"linking": LINK_ALL_CIVIL, "critic": critic, "tracks": tracks, "link": relink}
    )
    p, _, log = run(client)
    assert client.calls == [
        "linking",
        "applicants",
        "parties",
        "critic",
        "tracks",
        "link",
    ]
    rnd = log["rounds"][0]
    assert rnd["tracks"]["audit"]["relations"] == 1  # unevidenced arises_from refused
    assert rnd["fix"] == {
        "skipped": "no findings left"
    }  # the wrong_track finding was answered
    # the linker could only choose inside the tracks; e4 heads its own
    link_prompt = client.prompts[client.calls.index("link")]
    assert "EVENT e4  [track T2]" in link_prompt
    assert rnd["relink"]["audit"]["links_refused"][0]["reason"].startswith(
        "predecessor is not"
    )
    by_what = {e["what_happened_span"]["text"]: e for e in p["events"]}
    arrest = by_what["was arrested by the police"]
    # the track relation turned into an occurs_during on the prosecution's head
    assert arrest["occurs_during"] == by_what["instituted civil proceedings"]["id"]
    assert [t.get("kind") for t in p["tracks"]] == ["civil claim", "prosecution"]
    assert p["track_relations"][0]["relation"] == "during"


def test_unevidenced_parallel_flag_is_read_as_no():
    critic = {
        **CLEAN,
        "parallel_tracks": "yes",
        "parallel_tracks_evidence": "not in the judgment",
    }
    client = ScriptedClient({"linking": LINK_ALL_CIVIL, "critic": critic})
    _, _, log = run(client)
    assert "tracks" not in client.calls
    flags = {f["flag"]: f for f in log["rounds"][0]["critic"]["flags"]}
    assert flags["parallel_tracks"] == {
        "flag": "parallel_tracks",
        "how": "unevidenced",
        "value": "no",
    }


def test_missing_flags_are_retried_once_then_derived_from_findings():
    no_flags = {
        "findings": [
            {
                "kind": "missing_party",
                "events": ["e3"],
                "evidence_span": "upheld that judgment",
                "issue": "x",
            }
        ]
    }
    client = ScriptedClient(
        {
            "linking": LINK_ALL_CIVIL,
            "critic": [no_flags, no_flags],
            "parties": [{}, {}],
            "fix": {"ops": []},
        }
    )
    _, _, log = run(client)
    assert client.calls.count("critic") == 2  # one retry, no more
    assert "omitted" in client.prompts[client.calls.index("critic", 4)]
    flags = {f["flag"]: f for f in log["rounds"][0]["critic"]["flags"]}
    assert {k: (v["how"], v["value"]) for k, v in flags.items()} == {
        "needs_fix": ("defaulted", "yes"),
        "parallel_tracks": ("defaulted", "no"),
        "parties_incomplete": ("defaulted", "yes"),
    }
    # targeted parties ran on e3 alone, and the fixer still saw the finding
    targeted = client.prompts[
        [i for i, k in enumerate(client.calls) if k == "parties"][1]
    ]
    assert "[e3]" in targeted and "[e1]" not in targeted
    assert client.calls[-1] == "fix"


def test_needs_fix_no_is_overridden_by_findings():
    critic = {
        **CLEAN,
        "findings": [
            {
                "kind": "wrong_field",
                "events": ["e2"],
                "field": "outcome",
                "evidence_span": "dismissed the claim",
                "issue": "x",
            }
        ],
    }
    client = ScriptedClient(
        {"linking": LINK_ALL_CIVIL, "critic": critic, "fix": {"ops": []}}
    )
    _, _, log = run(client)
    flags = {f["flag"]: f["how"] for f in log["rounds"][0]["critic"]["flags"]}
    assert flags["needs_fix"] == "inconsistent" and client.calls[-1] == "fix"


def test_chain_aware_parties_show_the_predecessor_and_its_parties():
    p = payload()
    p["events"][1]["parties"] = [{"name_span": span("the applicant")}]
    parties = {
        "parties_by_event": [
            {
                "id": "e3",
                "parties": [
                    {"name_span": "the applicant"},
                    {"name_span": "his employer"},
                ],
            }
        ]
    }
    client = ScriptedClient(
        {"linking": LINK_ALL_CIVIL, "critic": CLEAN, "parties": parties}
    )
    p, _, log = run(client, p)
    prompt = client.prompts[client.calls.index("parties")]
    assert "continues e2 (whose parties are: the applicant)" in prompt
    names = {x["name_span"]["text"] for x in p["events"][2]["parties"]}
    assert names == {"the applicant", "his employer"}
    assert log["persons"]["parties_added"] == 2


def test_parties_can_be_switched_off():
    client = ScriptedClient({"linking": LINK_ALL_CIVIL, "critic": CLEAN})
    run(client, parties=False)
    assert "parties" not in client.calls


def test_second_round_runs_only_after_a_change():
    client = ScriptedClient({"linking": LINK_ALL_CIVIL, "critic": CLEAN})
    _, _, log = run(client, critic_rounds=3)
    assert len(log["rounds"]) == 1
    finding = {
        "kind": "wrong_field",
        "events": ["e3"],
        "field": "outcome",
        "evidence_span": "upheld that judgment",
        "issue": "x",
    }
    fix = {
        "ops": [
            {
                "op": "set_field",
                "finding": "f1",
                "id": "e3",
                "field": "outcome",
                "value": "merits decided",
            }
        ]
    }
    client = ScriptedClient(
        {
            "linking": LINK_ALL_CIVIL,
            "critic": [{**CLEAN, "needs_fix": "yes", "findings": [finding]}, CLEAN],
            "fix": fix,
        }
    )
    _, _, log = run(client, critic_rounds=3)
    assert [r["round"] for r in log["rounds"]] == [1, 2]


def test_critic_rounds_zero_skips_the_critic():
    client = ScriptedClient({"linking": LINK_ALL_CIVIL})
    p, _, log = run(client, critic_rounds=0)
    assert "critic" not in client.calls and log["rounds"] == []
    assert any(e.get("follows") or e.get("part_of") for e in p["events"])


def test_party_ops_are_validated():
    p = payload()
    for e in p["events"]:
        e["track"] = "T1"
    p["events"][2]["parties"] = [{"name_span": span("the applicant")}]
    ops = {
        "ops": [
            {
                "op": "add_party",
                "finding": "f1",
                "id": "e2",
                "name_span": "his employer",
                "role_span": "instituted civil proceedings against his employer",
            },
            {
                "op": "add_party",
                "finding": "f1",
                "id": "e2",
                "name_span": "Kyiv District Court",
            },
            {
                "op": "add_party",
                "finding": "f1",
                "id": "e2",
                "name_span": "invented party",
            },
            {
                "op": "add_party",
                "finding": "f1",
                "id": "e2",
                "name_span": "Judge K. Petrenko",
            },
            {
                "op": "remove_party",
                "finding": "f1",
                "id": "e3",
                "name": "The applicant",
            },
            {"op": "remove_party", "finding": "f1", "id": "e3", "name": "nobody"},
        ]
    }
    audit = a.apply_fixes(p, ops, [{"id": "f1"}], JUDGMENT)
    assert [x["status"] for x in audit] == [
        "applied",
        "refused: the deciding body is not a party",
        "refused: name_span not verbatim",
        "refused: a judge is not a party",
        "applied",
        "refused: no such party on that event",
    ]
    assert [x["name_span"]["text"] for x in p["events"][1]["parties"]] == [
        "his employer"
    ]
    assert p["events"][2]["parties"] == []


def test_constrained_candidates_follow_tracks_and_containment():
    p = payload()
    for e, t in zip(p["events"], ["T1", "T1", "T1", "T2"]):
        e["track"] = t
    base = a.candidate_predecessors(p["events"])
    assert a.constrain_candidates(p, base)["e3"] == ["e2", "e1"]  # e4 is another track
    p["track_relations"] = [{"track": "T1", "relation": "during", "other": "T2"}]
    assert "e4" in a.constrain_candidates(p, base)["e3"]


def test_read_flag():
    assert a.read_flag({"x": " Yes "}, "x") == "yes"
    assert a.read_flag({"x": "maybe"}, "x") is None
    assert a.read_flag(None, "x") is None


def test_added_event_is_inserted_where_the_judgment_narrates_it():
    p = payload()
    del p["events"][1]  # drop "dismissed the claim"
    ops = {
        "ops": [
            {
                "op": "add_event",
                "finding": "f1",
                "track": "new",
                "event": {"what_happened_span": "dismissed the claim"},
            }
        ]
    }
    a.apply_fixes(p, ops, [{"id": "f1"}], JUDGMENT)
    assert [e["what_happened_span"]["text"] for e in p["events"]][:2] == [
        "instituted civil proceedings",
        "dismissed the claim",
    ]
