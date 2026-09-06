"""Visualise the domestic-proceedings pathway extracted into an echr: graph.

Reads ONE case's .ttl (whatever pipeline arm produced it -- C1, C2, C3 or C4,
see docs/JURIX_2.md) and renders the chain of echr:DomesticEvent nodes
(echr:DomesticProceeding, echr:AdministrativeAction, echr:EnforcementAction,
echr:ProsecutorialReview) linked by echr:followsProceeding, as a PNG.

    uv run python -m art6.conditions.plot_proceedings <path-to.ttl>
    uv run python -m art6.conditions.plot_proceedings <path-to.ttl> --variant dag
    uv run python -m art6.conditions.plot_proceedings <path-to.ttl> --out figures/foo.png

Lanes (y) are assigned from echr:followsProceeding topology ALONE, never from
participant labels -- see assign_chain_lanes. An event with no predecessor
starts a lane; a sole child continues its parent's lane; a second child
branches into a new one; an event with several predecessors (e.g. one decree
following two separate applicants' appeals) takes the lane of one of them and
every other predecessor's lane simply ends there, drawn as a dashed
converging edge. This is deliberate: participant data can be wrong (a merged
-applicant defect makes two real people look like one, see the code review
that found this in 001-68183's C4 output) while the procedural chain itself
still reads correctly, so the layout follows the chain, not the party. Each
lane is labelled with whichever applicant most of its events agree on, or
"Track N" where they don't.

Two variants:
    swimlane (default)  x = real date where the graph gives one, interpolated
                         from the procedural sequence elsewhere.
                         echr:PreTrialDetention is drawn as a bar under the
                         lane it belongs to.
    dag                 x = topological rank in the echr:followsProceeding
                         chain only, ignoring dates entirely -- useful when a
                         case's dates are sparse or this is a purely
                         structural read of the extraction.

Node fill = echr:hasInstanceLevel (categorical, fixed order); a two-letter
badge on the node repeats the same information as text, so identity never
rests on colour alone. Border style = the echr:DomesticEvent subclass.
"""

from __future__ import annotations

import argparse
import itertools
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import matplotlib.dates as mdates
from rdflib import RDF, RDFS, Graph, Namespace
from rdflib.term import Node

ECHR = Namespace("https://growgraph.dev/echr#")


def to_num(d: date) -> float:
    """Matplotlib's date epoch (1970) differs from date.toordinal()'s (year 1)."""
    return mdates.date2num(d)


# -- palette (light-mode categorical, fixed order -- see dataviz skill) -----
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
SURFACE = "#fcfcfb"

LEVEL_ORDER = [
    ("LevelInvestigative", "Investigative", "I", "#2a78d6"),
    ("LevelAdministrativeReview", "Administrative review", "AR", "#eb6834"),
    ("LevelFirstInstance", "First instance", "FI", "#1baf7a"),
    ("LevelAppeal", "Appeal", "AP", "#eda100"),
    ("LevelCassation", "Cassation", "CA", "#e87ba4"),
    ("LevelSupervisoryReview", "Supervisory review", "SR", "#008300"),
    ("LevelReopening", "Reopening", "RO", "#4a3aa7"),
]
LEVEL_STYLE = {
    local: (label, badge, color) for local, label, badge, color in LEVEL_ORDER
}
UNKNOWN_STYLE = ("Unknown / unspecified", "?", INK_MUTED)

EVENT_CLASSES = {
    "DomesticProceeding": "solid",
    "AdministrativeAction": "dashed",
    "EnforcementAction": "dashdot",
    "ProsecutorialReview": "dotted",
}


def local_name(node: Node) -> str:
    s = str(node)
    return re.split(r"[#/]", s)[-1]


def humanize(local: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", " ", local.replace("_", " "))
    return s[:1].upper() + s[1:]


def parse_date(literal) -> tuple[date | None, str]:
    """Return (best-effort date, original string) for xsd:date/gYearMonth/gYear."""
    if literal is None:
        return None, ""
    text = str(literal)
    m = re.match(r"^(-?\d{4})-(\d{2})-(\d{2})", text)
    if m:
        y, mo, d = m.groups()
        try:
            return date(int(y), int(mo), int(d)), text
        except ValueError:
            return date(int(y), int(mo), 1), text
    m = re.match(r"^(-?\d{4})-(\d{2})$", text)
    if m:
        y, mo = m.groups()
        return date(int(y), int(mo), 1), text
    m = re.match(r"^(-?\d{4})$", text)
    if m:
        return date(int(m.group(1)), 1, 1), text
    return None, text


@dataclass
class Detention:
    detainee: str | None
    start: date | None
    start_text: str
    duration_days: int | None


@dataclass
class Event:
    uri: Node
    cls: str
    label: str
    court: str | None
    level_local: str | None
    outcome_local: str | None
    decision_date: date | None
    decision_text: str
    start_date: date | None
    follows: list[Node] = field(default_factory=list)
    participants: list[tuple[Node, str, str, bool]] = field(
        default_factory=list
    )  # (party_uri, label, side, is_applicant)
    detentions: list[Detention] = field(default_factory=list)

    @property
    def date_for_x(self) -> date | None:
        return self.decision_date or self.start_date


def get_label(g: Graph, node: Node, fallback_props=()) -> str:
    lbl = g.value(node, RDFS.label)
    if lbl is not None:
        return str(lbl)
    for prop in fallback_props:
        v = g.value(node, prop)
        if v is not None:
            return str(v)
    return humanize(local_name(node))


def parse_iso_duration_days(text: str) -> int | None:
    m = re.match(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?$", text)
    if not m:
        return None
    y, mo, d = (int(x) if x else 0 for x in m.groups())
    if y == mo == d == 0:
        return None
    return y * 365 + mo * 30 + d


def load_events(g: Graph) -> tuple[dict, list[Event]]:
    case_node = next(g.subjects(RDF.type, ECHR.CaseDocument), None)
    case_info = {
        "name": None,
        "applicants": [],
        "applicant_uris": [],
        "respondent": None,
        "judgment_date": None,
    }
    applicant_uris: list[Node] = []
    if case_node is not None:
        v = g.value(case_node, ECHR.hasCaseName)
        case_info["name"] = str(v) if v is not None else None
        v = g.value(case_node, ECHR.hasRespondentState)
        case_info["respondent"] = str(v) if v is not None else None
        d, _ = parse_date(g.value(case_node, ECHR.hasJudgmentDate))
        case_info["judgment_date"] = d
        applicant_uris = list(g.objects(case_node, ECHR.hasApplicant))
        case_info["applicants"] = [
            get_label(g, a, (ECHR.hasPersonName, ECHR.hasPartyName))
            for a in applicant_uris
        ]
    case_info["applicant_uris"] = applicant_uris
    applicant_set = set(applicant_uris)

    events: list[Event] = []
    for cls_local in EVENT_CLASSES:
        for node in g.subjects(RDF.type, ECHR[cls_local]):
            court_node = g.value(node, ECHR.hasCourt)
            court_label = (
                get_label(g, court_node, (ECHR.hasAuthorityName,))
                if court_node is not None
                else None
            )
            level = g.value(node, ECHR.hasInstanceLevel)
            outcome = g.value(node, ECHR.hasOutcome)
            dec_date, dec_text = parse_date(g.value(node, ECHR.hasDecisionDate))
            start_date, _ = parse_date(g.value(node, ECHR.hasProceedingStartDate))
            follows = list(g.objects(node, ECHR.followsProceeding))

            participants = []
            for part in g.objects(node, ECHR.hasParticipation):
                party = g.value(part, ECHR.participatingParty)
                if party is None:
                    continue
                side = g.value(part, ECHR.hasPartySide)
                plabel = get_label(
                    g,
                    party,
                    (ECHR.hasPersonName, ECHR.hasPartyName, ECHR.hasAuthorityName),
                )
                participants.append(
                    (
                        party,
                        plabel,
                        local_name(side) if side else "SideUnknown",
                        party in applicant_set,
                    )
                )

            detentions = []
            for det in g.objects(node, ECHR.hasPreTrialDetention):
                detainee_node = g.value(det, ECHR.hasDetainee)
                detainee = (
                    get_label(g, detainee_node, (ECHR.hasPersonName, ECHR.hasPartyName))
                    if detainee_node is not None
                    else None
                )
                dstart, dstart_text = parse_date(
                    g.value(det, ECHR.hasDetentionStartDate)
                )
                dur_lit = g.value(det, ECHR.hasStatedDetentionDuration)
                duration_days = (
                    parse_iso_duration_days(str(dur_lit))
                    if dur_lit is not None
                    else None
                )
                detentions.append(
                    Detention(detainee, dstart, dstart_text, duration_days)
                )

            events.append(
                Event(
                    uri=node,
                    cls=cls_local,
                    label=get_label(g, node),
                    court=court_label,
                    level_local=local_name(level) if level else None,
                    outcome_local=local_name(outcome) if outcome else None,
                    decision_date=dec_date,
                    decision_text=dec_text,
                    start_date=start_date,
                    follows=follows,
                    participants=participants,
                    detentions=detentions,
                )
            )
    return case_info, events


def topo_rank(events: list[Event]) -> dict[Node, int]:
    by_uri = {e.uri: e for e in events}
    successors: dict[Node, list[Node]] = defaultdict(list)
    indegree: dict[Node, int] = {e.uri: 0 for e in events}
    for e in events:
        for pred in e.follows:
            if pred in by_uri:
                successors[pred].append(e.uri)
                indegree[e.uri] += 1

    rank: dict[Node, int] = {}
    frontier = [u for u, d in indegree.items() if d == 0]
    seen_indeg = dict(indegree)
    while frontier:
        nxt = []
        for u in frontier:
            rank[u] = max(rank.get(u, 0), rank.get(u, 0))
        for u in frontier:
            for v in successors[u]:
                rank[v] = max(rank.get(v, 0), rank[u] + 1)
                seen_indeg[v] -= 1
                if seen_indeg[v] == 0:
                    nxt.append(v)
        frontier = nxt
    for e in events:
        rank.setdefault(e.uri, 0)
    return rank


def assign_x(events: list[Event], variant: str) -> dict[Node, float]:
    ranks = topo_rank(events)
    if variant == "dag":
        return {e.uri: float(ranks[e.uri]) for e in events}

    dated = [(ranks[e.uri], to_num(e.date_for_x)) for e in events if e.date_for_x]
    if len(dated) >= 2:
        dated.sort()
        xs_by_rank = {}
        for r, ordv in dated:
            xs_by_rank.setdefault(r, []).append(ordv)
        rank_to_x = {r: sum(v) / len(v) for r, v in xs_by_rank.items()}
        known_ranks = sorted(rank_to_x)
        result = {}
        for e in events:
            r = ranks[e.uri]
            if e.date_for_x:
                result[e.uri] = to_num(e.date_for_x)
            elif r in rank_to_x:
                result[e.uri] = rank_to_x[r]
            else:
                lo = max([k for k in known_ranks if k <= r], default=known_ranks[0])
                hi = min([k for k in known_ranks if k >= r], default=known_ranks[-1])
                if lo == hi:
                    result[e.uri] = rank_to_x[lo]
                else:
                    frac = (r - lo) / (hi - lo)
                    result[e.uri] = rank_to_x[lo] + frac * (
                        rank_to_x[hi] - rank_to_x[lo]
                    )
        return result
    return {e.uri: float(ranks[e.uri]) for e in events}


def assign_chain_lanes(
    events: list[Event],
) -> tuple[dict[Node, int], dict[Node, Node | None]]:
    """Lane per event from echr:followsProceeding topology alone -- never from
    participant labels, which a merged-applicant defect (see plot_proceedings
    module notes) can make actively misleading.

    Standard commit-graph layering: an event with no predecessor starts a new
    lane. An event with exactly one predecessor continues that predecessor's
    lane, UNLESS an earlier sibling already claimed it (two events both
    following the same one is a branch, so the second sibling starts a new
    lane). An event with several predecessors (two proceedings converging,
    e.g. a joint decree following two separate appeals) takes the lane of
    whichever predecessor is unclaimed and lowest-numbered; every OTHER
    predecessor's lane simply ends there, which is what the convergence
    arrow drawn from it is showing.

    Returns (lane_of, primary_of) where primary_of[event] names, for an
    event with multiple predecessors, which one it continued the lane from
    -- the rest are the ones a converging edge should curve in from.
    """
    by_uri = {e.uri: e for e in events}
    ranks = topo_rank(events)
    order = sorted((e.uri for e in events), key=lambda u: ranks[u])

    lane_of: dict[Node, int] = {}
    claimed_by: dict[Node, Node] = {}
    primary_of: dict[Node, Node | None] = {}
    next_lane = 0

    for uri in order:
        preds = [p for p in by_uri[uri].follows if p in by_uri]
        if not preds:
            lane_of[uri] = next_lane
            next_lane += 1
            primary_of[uri] = None
            continue
        candidates = sorted(preds, key=lambda p: lane_of[p])
        primary = next((p for p in candidates if p not in claimed_by), None)
        if primary is None:
            lane_of[uri] = next_lane
            next_lane += 1
            primary_of[uri] = candidates[0]
        else:
            lane_of[uri] = lane_of[primary]
            claimed_by[primary] = uri
            primary_of[uri] = primary if len(preds) > 1 else None

    return lane_of, primary_of


def name_lanes(
    events: list[Event], lane_of: dict[Node, int], applicant_uris: list[Node]
) -> list[tuple[str, str]]:
    """Label per lane by APPLICANT NUMBER (position in echr:hasApplicant), not
    by name string -- a chain is a procedural path, not a person, so one lane
    can carry several applicants (a joint decree both appealed against) and
    one applicant can appear in several lanes (their own track plus a joint
    one). Numbering also survives a merged-applicant defect intact: if two
    real people collapsed into one echr:NaturalPerson, every lane touching
    them reports the SAME applicant number, which is the honest reading --
    one node is standing in for both -- rather than inventing a distinction
    the graph does not contain.

    Returns (header, detail) per lane: header is "Applicant 1" / "Applicants
    1, 2" / "Track N" (no applicant participant at all -- e.g. a purely
    administrative side-chain); detail is the name(s), rendered smaller below
    it so a long name list never has to fight a box for the same line.
    """
    index_of = {uri: i + 1 for i, uri in enumerate(applicant_uris)}
    name_of = {}
    for e in events:
        for party_uri, label, _side, is_applicant in e.participants:
            if is_applicant:
                name_of[party_uri] = label

    by_lane: dict[int, set[Node]] = defaultdict(set)
    for e in events:
        for party_uri, _label, _side, is_applicant in e.participants:
            if is_applicant:
                by_lane[lane_of[e.uri]].add(party_uri)

    result: list[tuple[str, str]] = [("", "")] * (max(lane_of.values()) + 1)
    for lane in range(len(result)):
        applicants = sorted(by_lane.get(lane, ()), key=lambda u: index_of[u])
        if not applicants:
            result[lane] = (f"Track {lane + 1}", "")
            continue
        numbers = ", ".join(str(index_of[u]) for u in applicants)
        detail = truncate(", ".join(name_of[u] for u in applicants), max_len=42)
        word = "Applicant" if len(applicants) == 1 else "Applicants"
        result[lane] = (f"{word} {numbers}", detail)
    return result


def declutter_x(
    events: list[Event], x: dict[Node, float], lanes: dict[Node, int]
) -> dict[Node, float]:
    """Push apart events crowded into the same lane so their labels don't overlap.

    Chronological order within a lane is preserved; only spacing changes.
    """
    vals = list(x.values())
    span = max(vals) - min(vals) if len(vals) > 1 else 1.0
    min_gap = max(span * 0.16, 1e-6)

    by_lane: dict[int, list[Node]] = defaultdict(list)
    for e in events:
        by_lane[lanes[e.uri]].append(e.uri)

    result = dict(x)
    for uris in by_lane.values():
        uris.sort(key=lambda u: result[u])
        for prev, cur in itertools.pairwise(uris):
            if result[cur] - result[prev] < min_gap:
                result[cur] = result[prev] + min_gap
    return result


def wrap(text: str, width: int) -> str:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def truncate(text: str, max_len: int = 26) -> str:
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


def plot(
    case_info: dict, events: list[Event], variant: str, out_path: Path, ttl_path: Path
) -> None:
    import matplotlib.patches as mpatches
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch

    if not events:
        raise SystemExit(f"No echr:DomesticEvent instances found in {ttl_path}")

    x = assign_x(events, variant)
    is_dated = variant != "dag" and any(e.date_for_x for e in events)
    lanes, primary_of = assign_chain_lanes(events)
    lane_names = name_lanes(events, lanes, case_info["applicant_uris"])
    x = declutter_x(events, x, lanes)

    by_uri = {e.uri: e for e in events}
    n_lanes = max(1, len(lane_names))
    longest_label = max((max(len(h), len(d)) for h, d in lane_names), default=0)
    left_margin_in = min(0.55 + 0.065 * longest_label, 3.5)
    fig_w = max(9.0, 1.9 * len(events) + 2.5) + left_margin_in
    fig_h = max(4.0, 1.7 * n_lanes + 2.0)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    left_frac = left_margin_in / fig_w

    lane_y = {i: n_lanes - 1 - i for i in range(n_lanes)}
    for i, (header, detail) in enumerate(lane_names):
        y = lane_y[i]
        ax.axhline(y, color=GRIDLINE, lw=1, zorder=0)
        ax.text(
            -0.03,
            y + (0.12 if detail else 0),
            header,
            transform=ax.get_yaxis_transform(),
            ha="right",
            va="center",
            fontsize=10,
            color=INK_SECONDARY,
            fontweight="bold",
            zorder=5,
        )
        if detail:
            ax.text(
                -0.03,
                y - 0.14,
                detail,
                transform=ax.get_yaxis_transform(),
                ha="right",
                va="center",
                fontsize=8,
                color=INK_MUTED,
                zorder=5,
            )

    xs_all = list(x.values())
    xpad = max((max(xs_all) - min(xs_all)) * 0.08, 1) if len(xs_all) > 1 else 1
    xmin, xmax = min(xs_all) - xpad, max(xs_all) + xpad

    # -- pre-trial detention bars ------------------------------------------------
    for e in events:
        if not e.detentions:
            continue
        y = lane_y[lanes[e.uri]] - 0.28
        for det in e.detentions:
            if det.start is None:
                continue
            x0 = to_num(det.start) if is_dated else x[e.uri] - 0.15
            span = det.duration_days if det.duration_days else 20
            x1 = x0 + span if is_dated else x[e.uri] + 0.15
            ax.add_patch(
                mpatches.FancyBboxPatch(
                    (x0, y - 0.05),
                    x1 - x0,
                    0.10,
                    boxstyle="round,pad=0,rounding_size=0.02",
                    linewidth=0,
                    facecolor=INK_MUTED,
                    alpha=0.35,
                    zorder=1,
                )
            )

    # -- edges: straight within a lane, curved where a chain converges into it --
    for e in events:
        preds = [p for p in e.follows if p in by_uri]
        primary = primary_of.get(e.uri)
        for pred_uri in preds:
            p0 = (x[pred_uri], lane_y[lanes[pred_uri]])
            p1 = (x[e.uri], lane_y[lanes[e.uri]])
            is_convergence = len(preds) > 1 and pred_uri != primary
            rad = 0.0 if p0[1] == p1[1] else 0.18
            arrow = FancyArrowPatch(
                p0,
                p1,
                connectionstyle=f"arc3,rad={rad}",
                arrowstyle="-|>",
                mutation_scale=14,
                lw=1.4 if not is_convergence else 1.1,
                linestyle="solid" if not is_convergence else "dashed",
                color=INK_SECONDARY,
                zorder=2,
                shrinkA=16,
                shrinkB=16,
            )
            ax.add_patch(arrow)

    # -- nodes ----------------------------------------------------------------
    box_w = max((xmax - xmin) / max(len(events), 1) * 0.6, (xmax - xmin) * 0.02)
    box_h = 0.34
    for e in events:
        cx, cy = x[e.uri], lane_y[lanes[e.uri]]
        _level_label, badge, color = LEVEL_STYLE.get(e.level_local or "", UNKNOWN_STYLE)
        ls = EVENT_CLASSES.get(e.cls, "solid")
        box = mpatches.FancyBboxPatch(
            (cx - box_w / 2, cy - box_h / 2),
            box_w,
            box_h,
            boxstyle="round,pad=0.02,rounding_size=0.03",
            linewidth=1.6,
            linestyle=ls,
            edgecolor=INK_PRIMARY,
            facecolor=color,
            alpha=0.92,
            zorder=3,
        )
        ax.add_patch(box)
        ax.text(
            cx - box_w / 2 + box_w * 0.06,
            cy + box_h / 2 - box_h * 0.18,
            badge,
            fontsize=7.5,
            fontweight="bold",
            color="white",
            va="top",
            ha="left",
            zorder=4,
        )
        court_line = truncate(e.court or e.label)
        date_line = e.decision_text or ""
        ax.text(
            cx + box_w * 0.15,
            cy - box_h / 2 - 0.13,
            court_line,
            fontsize=7.6,
            color=INK_PRIMARY,
            ha="right",
            va="top",
            rotation=-30,
            rotation_mode="anchor",
            zorder=4,
        )
        if date_line:
            ax.text(
                cx,
                cy + box_h / 2 + 0.06,
                date_line,
                fontsize=7.6,
                color=INK_SECONDARY,
                ha="center",
                va="bottom",
                zorder=4,
            )

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(-1.7, n_lanes - 0.3)
    ax.set_yticks([])
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)

    if is_dated:
        ax.xaxis_date()
        locator = mdates.AutoDateLocator()
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        ax.set_xlabel("Date", color=INK_SECONDARY, fontsize=9)
    else:
        ax.set_xlabel(
            "Procedural sequence (no usable dates in the graph)",
            color=INK_SECONDARY,
            fontsize=9,
        )
        ax.set_xticks([])

    ax.tick_params(colors=INK_SECONDARY, labelsize=8)

    title = case_info["name"] or ttl_path.stem
    subtitle_bits = []
    if case_info["respondent"]:
        subtitle_bits.append(
            f"v. {case_info['respondent']}" if "v." not in title else ""
        )
    subtitle_bits.append(f"{len(events)} domestic events")
    subtitle_bits.append(f"source: {ttl_path.name}")
    fig.suptitle(
        title, fontsize=14, fontweight="bold", color=INK_PRIMARY, x=0.02, ha="left"
    )
    ax.set_title(
        " · ".join(b for b in subtitle_bits if b),
        fontsize=9.5,
        color=INK_SECONDARY,
        loc="left",
    )

    # -- legend (one block; only entries actually present in this graph) --------
    used_levels = {e.level_local for e in events}
    level_handles = [
        mpatches.Patch(
            facecolor=color, edgecolor=INK_PRIMARY, label=f"{badge}  {label}"
        )
        for local, label, badge, color in LEVEL_ORDER
        if local in used_levels
    ]
    if None in used_levels or any(lvl not in LEVEL_STYLE for lvl in used_levels if lvl):
        level_handles.append(
            mpatches.Patch(
                facecolor=UNKNOWN_STYLE[2], edgecolor=INK_PRIMARY, label="?  Unknown"
            )
        )

    used_classes = {e.cls for e in events}
    class_handles = [
        plt.Line2D(
            [0], [0], color=INK_PRIMARY, lw=1.6, linestyle=ls, label=humanize(cls)
        )
        for cls, ls in EVENT_CLASSES.items()
        if cls in used_classes
    ]

    spacer = mpatches.Patch(facecolor="none", edgecolor="none", label="")
    handles = level_handles + ([spacer] if class_handles else []) + class_handles
    ax.legend(
        handles=handles,
        title="Legend",
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
        fontsize=7.8,
        title_fontsize=9,
        frameon=False,
        handlelength=1.6,
        labelspacing=0.6,
    )

    fig.tight_layout(rect=(left_frac, 0, 0.80, 1))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ttl", type=Path, help="path to a case's .ttl output")
    parser.add_argument(
        "--variant",
        choices=["swimlane", "dag"],
        default="swimlane",
        help="swimlane: real dates + applicant lanes (default). dag: pure topological order.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output PNG path (default: figures/<ttl-stem>.<variant>.png)",
    )
    args = parser.parse_args(argv)

    g = Graph()
    g.parse(args.ttl, format="turtle")
    case_info, events = load_events(g)

    out_path = args.out
    if out_path is None:
        repo_root = Path(__file__).resolve().parents[2]
        out_path = repo_root / "figures" / f"{args.ttl.stem}.{args.variant}.png"

    plot(case_info, events, args.variant, out_path, args.ttl)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
