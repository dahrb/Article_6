"""The stage 1 -> stage 2 digest."""

from art6.ontology.render_bundles import render
from art6.stage1.to_payload import to_payload

SOURCE = """THE FACTS

**5.** The applicant was born in 1960. He lives in Kyiv.
**6.** On 15 March 1999 he sued his employer.
**8.** On 5 June 2003 the Kyiv District Court dismissed the claim, ordering him to pay the costs.
"""

DOC = {
    "case_id": "001-1",
    "sets": [
        {
            "id": "P1",
            "kind": "Civil",
            "label": "claim against employer",
            "pending": False,
            "enforces": None,
        }
    ],
    "events": [
        {
            "id": "P1.s",
            "set": "P1",
            "role": "start",
            "date": "1999-03-15",
            "body": None,
            "parties": [{"name": "he", "side": "Initiating"}],
            "matter": ["Employment"],
            "quote": "On 15 March 1999 he sued his employer.",
        },
        {
            "id": "D1",
            "set": "P1",
            "role": "decision",
            "date": "2003-06-05",
            "body": "Kyiv District Court",
            "authority_kind": "Judicial",
            "level": "FirstInstance",
            "outcome": "MeritsDecided",
            "direction": "UnfavourableToApplicant",
            "costs": "Applicant",
            "matter": ["Employment"],
            "quote": "On 5 June 2003 the Kyiv District Court dismissed the claim",
        },
    ],
}


def test_digest_carries_sets_classifications_and_offsets():
    out = render(to_payload(DOC, SOURCE))
    assert (
        "SETS OF PROCEEDINGS (1)" in out
        and "P1: claim against employer [kind: civil]" in out
    )
    assert "EVENT e2" in out and "  track: P1 (claim against employer)" in out
    assert "  decision date (ISO): 2003-06-05" in out
    i = SOURCE.index("Kyiv District Court")
    assert (
        f'  authority: "Kyiv District Court" [{i}:{i + len("Kyiv District Court")}]'
        in out
    )
    for line in (
        "instance_level: first instance",
        "outcome: merits decided",
        "outcome_direction: unfavourable to applicant",
        "costs: applicant",
        "matter: employment",
        "follows: e1",
    ):
        assert f"  {line}" in out
