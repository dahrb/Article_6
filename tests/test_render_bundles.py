"""Render modes of the stage 1 -> stage 2 handoff."""

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


def test_evidence_mode_adds_the_events_sentence_and_description():
    out = render(PAYLOAD, SOURCE, "evidence")
    assert 'evidence: "On 15 March 1999 he sued his employer."' in out
    assert "The weather was fine" not in out
    assert "description (a summary, not a quote): The applicant sued" in out


def test_paragraph_mode_is_verbatim_and_skips_eventless_paragraphs():
    out = render(PAYLOAD, SOURCE, "paragraphs")
    assert "**6.** On 15 March 1999 he sued his employer. The weather was fine." in out
    assert "**8.** On 5 June 2003" in out
    assert "Nothing happened" not in out and "[...]" in out
    assert "EVENT INDEX" not in out


def test_index_modes_with_and_without_links():
    linked = render(PAYLOAD, SOURCE, "paragraphs+index")
    assert "e2 | decided: 5 June 2003 | by: Kyiv District Court" in linked
    assert "part_of: e1" in linked
    assert "part_of" not in render(PAYLOAD, SOURCE, "paragraphs+index-nolinks")


def test_dated_mode_adds_dated_facts_paragraphs_only():
    out = render(PAYLOAD, SOURCE, "dated+index")
    assert "born in 1960" not in out  # a bare year is not a date here
    assert "entered into force" not in out  # outside the Facts
