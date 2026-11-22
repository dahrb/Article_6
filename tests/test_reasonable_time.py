"""The chain-based reasonable-time rule."""

from datetime import date

from art6.ontology import reasonable_time as rt


def act(key, authority="Judicial", level="FirstInstance", edges=(), **kw):
    return rt.Act(key=key, authority=authority, level=level, edges=set(edges), **kw)


def test_dates_keep_their_precision():
    assert rt.interval("2016") == (date(2016, 1, 1), date(2016, 12, 31))
    assert rt.interval("2016-02") == (date(2016, 2, 1), date(2016, 2, 29))
    assert rt.interval("2016-12-05") == (date(2016, 12, 5), date(2016, 12, 5))


def test_civil_starts_at_the_first_court_act_and_ends_at_the_last():
    acts = [
        act(1, authority="Disciplinary", level="FirstInstance", decision="2013-04-17"),
        act(2, start="2016", edges=[1]),  # year only: ranks after a dated act
        act(3, start="2016-10-05", edges=[1]),
        act(4, level="Cassation", decision="2021-12-09", edges=[3]),
    ]
    p = rt.period(acts, "Civil", "2026-02-12")
    assert (p.start_act, p.end_act, p.pending) == (3, 4, False)


def test_criminal_starts_at_the_charge_not_at_a_court():
    acts = [
        act(
            1,
            authority="Prosecutorial",
            level="Investigative",
            decision="2007-11-16",
            matters={"Arrest"},
        ),
        act(2, decision="2013-10-04", matters={"Conviction"}, edges=[1]),
    ]
    assert rt.period(acts, "Criminal", "2020-01-23").start_act == 1


def test_a_last_remittal_leaves_the_period_running_to_the_judgment():
    acts = [
        act(1, start="2008-01-10", outcome=None),
        act(2, level="Appeal", decision="2014-03-18", outcome="Remitted", edges=[1]),
    ]
    p = rt.period(acts, "Civil", "2020-01-23")
    assert p.pending and p.end == (date(2020, 1, 23), date(2020, 1, 23))


def test_an_earlier_pending_flag_does_not_open_the_chain():
    acts = [
        act(1, start="2013-01-01", pending=True),
        act(2, level="Appeal", decision="2015-06-01", edges=[1]),
    ]
    assert not rt.period(acts, "Civil", "2020-01-23").pending


def test_the_start_is_clipped_to_the_states_acceptance_of_petitions():
    acts = [act(1, start="1993-04-13"), act(2, decision="1996-03-26", edges=[1])]
    p = rt.period(acts, "Civil", "1999-10-15", "POL")
    assert p.clipped and p.start[0] == date(1993, 5, 1)


def test_the_longest_chain_with_a_start_act_is_the_period():
    acts = [
        act(1, start="2001-01-01"),
        act(2, decision="2001-06-01", edges=[1]),
        act(3, start="2002-01-01"),
        act(4, decision="2009-01-01", edges=[3]),
    ]
    assert rt.period(acts, "Civil", "2012-01-01").start_act == 3
