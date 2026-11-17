from types import SimpleNamespace as NS

from art6.baselines.openie import chained, events_from

TEXT = (
    "The applicant lodged an appeal on 3 May 1999. "
    "The weather was fine in 1999. "
    "On 4 June 2000 the Supreme Court dismissed the appeal. "
    "The court dismissed it."
)


def sent(text, s, triples):
    lo = TEXT.index(s)
    return NS(
        token=[NS(beginChar=lo, endChar=lo + len(s))],
        openieTriple=[NS(subject=a, relation=b, object=c) for a, b, c in triples],
    )


def doc():
    return NS(
        sentence=[
            sent(
                TEXT,
                "The applicant lodged an appeal on 3 May 1999.",
                [("applicant", "lodged", "appeal")],
            ),
            sent(TEXT, "The weather was fine in 1999.", [("weather", "was", "fine")]),
            sent(
                TEXT,
                "On 4 June 2000 the Supreme Court dismissed the appeal.",
                [("Supreme Court", "dismissed", "appeal")],
            ),
            sent(TEXT, "The court dismissed it.", [("court", "dismissed", "it")]),
        ]
    )


def test_one_event_per_dated_legal_sentence():
    ev = events_from(doc(), TEXT)
    assert len(ev) == 2  # no legal verb / no date -> no event
    assert ev[0]["start_date_iso"] == "1999-05-03" and "decision_date_iso" not in ev[0]
    assert ev[1]["decision_date_iso"] == "2000-06-04"
    assert ev[1]["authority_span"]["text"] == "Supreme Court"


def test_chain_follows_date_order_not_text_order():
    ev = events_from(doc(), TEXT)
    ev.reverse()  # text order no longer matches date order
    out = {e["id"]: e for e in chained(ev)}
    assert out["e2"]["follows"] == "e1" and "follows" not in out["e1"]
