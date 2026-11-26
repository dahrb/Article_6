"""The reasonable-time period (Art. 6 § 1) inferred from the extracted chain.

A deterministic rule, no model: it reads only the sets of proceedings (a gold
annotation or a system's graph, both in the set model) and the case metadata HUDOC gives every judgment -- the Article 6
limb, the respondent state and the date of the Court's judgment. The Court's
own statement of the period (in The Law) is never an input; it is the label
the rule is scored against (art6/evaluation/score_reasonable_time.py).

The rule follows the Court's doctrine (Frydlender; Pélissier and Sassi):

  chain   one set of proceedings. The period's chain is the one, among
          sets holding a qualifying start act, with the longest dated span
          (ties: more acts, then earliest start).
  start   civil: the first act before a court (authority Judicial; a court
          rung where the authority is unknown) -- the institution of
          proceedings. Criminal: the first act by which the applicant was
          "charged" in the Convention's sense -- an arrest, a charge,
          detention, a judicial or prosecutorial investigative act, or failing
          those a trial. A filing counts from its start date, a decision from
          its decision date.
          Clipped to the date from which the Court may examine applications
          against the state (ratification, or the former Article 25
          declaration), where the table below knows it.
  end     the chain's latest dated act -- unless that act leaves the chain
          open: its set is pending, or it quashed and remitted. Then the
          period runs to the Court's judgment.

Dates keep their precision. A year- or month-only date is ranked by the last
day it could be (so a precisely dated act in the same month or year wins) and
reported at its midpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

COURT_LEVELS = {"FirstInstance", "Appeal", "Cassation", "CaseReview", "Reopening"}
CHARGE_MATTERS = {"Arrest", "Charge", "Detention", "Bail"}

# The date from which the Court can examine applications against the state:
# entry into force of the Convention, or for the older members the former
# Article 25 declaration. Only matters for proceedings that began earlier.
PETITION_FROM = {
    "ALB": "1996-10-02", "ARM": "2002-04-26", "AZE": "2002-04-15",
    "BGR": "1992-09-07", "BIH": "2002-07-12", "CZE": "1992-03-18",
    "EST": "1996-04-16", "ESP": "1979-10-04", "FRA": "1981-10-02",
    "GEO": "1999-05-20", "GRC": "1985-11-20", "HRV": "1997-11-05",
    "HUN": "1992-11-05", "ITA": "1973-08-01", "LTU": "1995-06-20",
    "LVA": "1997-06-27", "MDA": "1997-09-12", "MKD": "1997-04-10",
    "MNE": "2004-03-03", "POL": "1993-05-01", "PRT": "1978-11-09",
    "ROU": "1994-06-20", "RUS": "1998-05-05", "SRB": "2004-03-03",
    "SVK": "1992-03-18", "SVN": "1994-06-28", "TUR": "1987-01-28",
    "UKR": "1997-09-11",
}  # fmt: skip


# ---------------------------------------------------------------------------
# Dates with precision
# ---------------------------------------------------------------------------


def interval(text: str | None) -> tuple[date, date] | None:
    """'2016', '2016-10' or '2016-10-05' -> the first and last day it covers."""
    if not text:
        return None
    try:
        parts = [int(p) for p in str(text)[:10].split("-")]
    except ValueError:
        return None
    if len(parts) == 3:
        d = date(*parts)
        return d, d
    if len(parts) == 2:
        lo = date(parts[0], parts[1], 1)
        nxt = date(parts[0] + parts[1] // 12, parts[1] % 12 + 1, 1)
        return lo, nxt - timedelta(days=1)
    return date(parts[0], 1, 1), date(parts[0], 12, 31)


def midpoint(iv: tuple[date, date]) -> date:
    return iv[0] + (iv[1] - iv[0]) / 2


# ---------------------------------------------------------------------------
# Acts: the events of a set-model document
# ---------------------------------------------------------------------------


@dataclass
class Act:
    key: object
    authority: str | None = None
    level: str | None = None
    matters: set[str] = field(default_factory=set)
    start: str | None = None
    decision: str | None = None
    outcome: str | None = None
    pending: bool = False
    edges: set = field(default_factory=set)

    @property
    def when(self) -> tuple[date, date] | None:
        """A filing counts from its start, a decision from its decision."""
        return interval(self.start) or interval(self.decision)

    @property
    def latest(self) -> tuple[date, date] | None:
        return interval(self.decision) or interval(self.start)


def acts_from_doc(doc: dict) -> list[Act]:
    """One act per event of a flat set-model document (art6/model.py, or a
    graph read by art6/evaluation/kg.py). Each set is one chain: every event
    is linked to its set's first event; an event outside any set stands alone.
    A pending set marks all its acts pending."""
    pending = {s["id"]: bool(s.get("pending")) for s in doc["sets"]}
    first: dict = {}
    out = []
    for e in doc["events"]:
        sid = e.get("set")
        chained = sid not in (None, "unassigned")
        anchor = first.setdefault(sid, e["id"]) if chained else e["id"]
        start = e["role"] == "start"
        out.append(
            Act(
                key=e["id"],
                authority=e.get("authority_kind"),
                level=e.get("level"),
                matters={str(m) for m in e.get("matter") or []},
                start=None if not start else e.get("date"),
                decision=None if start else e.get("date"),
                outcome=e.get("outcome"),
                pending=chained and pending.get(sid, False),
                edges={anchor} - {e["id"]},
            )
        )
    return out


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


@dataclass
class Period:
    start: tuple[date, date]
    end: tuple[date, date]
    start_act: object
    end_act: object  # None when the period runs to the Court's judgment
    pending: bool
    clipped: bool
    chain_size: int

    @property
    def days(self) -> int:
        return (midpoint(self.end) - midpoint(self.start)).days


def chains(acts: list[Act]) -> list[list[Act]]:
    parent = {a.key: a.key for a in acts}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in acts:
        for t in a.edges:
            if t in parent:
                parent[find(a.key)] = find(t)
    groups: dict = {}
    for a in acts:
        groups.setdefault(find(a.key), []).append(a)
    return list(groups.values())


def starts_period(act: Act, limb: str) -> bool:
    if limb == "Criminal":
        return bool(act.matters & CHARGE_MATTERS) or (
            "Investigation" in act.matters
            and act.authority in {"Judicial", "Prosecutorial"}
        )
    if act.authority == "Judicial":
        return True
    return act.authority in {None, "Unknown"} and act.level in COURT_LEVELS


def _trial(act: Act) -> bool:
    return "Conviction" in act.matters


def period(
    acts: list[Act], limb: str, judgment_date: str, state: str | None = None
) -> Period | None:
    """The period the rule infers, or None if no chain has a start act."""
    best = None
    for chain in chains(acts):
        dated = [a for a in chain if a.when]
        cand = [a for a in dated if starts_period(a, limb)]
        if not cand and limb == "Criminal":
            cand = [a for a in dated if _trial(a)]
        if not cand:
            continue
        first = min(cand, key=lambda a: (a.when[1], a.when[0], str(a.key)))
        last = max(dated, key=lambda a: (a.latest[1], str(a.key)))
        span = (last.latest[1] - first.when[1]).days
        rank = (span, len(chain), -first.when[1].toordinal())
        if best is None or rank > best[0]:
            best = (rank, chain, first, last)
    if best is None:
        return None
    _, chain, first, last = best
    start, clipped = first.when, False
    floor = interval(PETITION_FROM.get(state or ""))
    if floor and start[1] < floor[0]:
        start, clipped = floor, True
    # The chain is open if what ends it -- the act(s) on its latest date -- is
    # pending or sent the case back. A pending flag on an earlier act (an
    # appeal pending at the time, decided later) does not count.
    tail = [a for a in dated if a.latest[1] >= last.latest[0]]
    still_open = any(a.pending or a.outcome == "Remitted" for a in tail)
    if still_open:
        return Period(
            start, interval(judgment_date), first.key, None, True, clipped, len(chain)
        )
    return Period(start, last.latest, first.key, last.key, False, clipped, len(chain))
