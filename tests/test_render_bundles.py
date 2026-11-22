"""The stage 1 -> stage 2 digest."""

from art6.ontology.render_bundles import render

SOURCE = """THE FACTS

**5.** The applicant was born in 1960. He lives in Kyiv.
**6.** On 15 March 1999 he sued his employer. The weather was fine.
**7.** Nothing happened for years.
**8.** On 5 June 2003 the Kyiv District Court dismissed the claim.

RELEVANT DOMESTIC LAW

**9.** On 1 January 1990 the Code entered into force.
"""


def span(text):
    start = SOURCE.index(text)
    return {"text": text, "start": start, "end": start + len(text)}


PAYLOAD = {
    "events": [
        {
            "id": "e1",
            "description": "The applicant sued his employer.",
            "what_happened_span": span("he sued his employer"),
            "start_date_span": span("15 March 1999"),
        },
        {
            "id": "e2",
            "what_happened_span": span("dismissed the claim"),
            "decision_date_span": span("5 June 2003"),
            "authority_span": span("Kyiv District Court"),
            "outcome": "merits decided",
            "part_of": "e1",
        },
    ]
}


def test_bundle_renders_circumstances_and_the_strasbourg_application():
    payload = {
        **PAYLOAD,
        "circumstances": [
            {
                "id": "c1",
                "description": "He moved to Kyiv.",
                "what_happened_span": span("He lives in Kyiv"),
                "date_span": span("1960"),
                "kind": "residence",
                "gives_rise_to": "e1",
            }
        ],
        "strasbourg_application": {"date_span": span("5 June 2003")},
    }
    out = render(payload, SOURCE, "bundle")
    assert "CIRCUMSTANCE c1" in out and "kind: residence" in out
    assert "gives_rise_to: e1" in out
    assert 'STRASBOURG APPLICATION\n  lodged: "5 June 2003"' in out
