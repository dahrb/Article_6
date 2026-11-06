"""Interactive timeline with explicit date precision and extracted relations."""

import textwrap
from collections import defaultdict
from html import escape

import plotly.graph_objects as go

from art6.explorer.data import RELATIONS, visible_links

COLORS = {
    "first instance": "#16896b",
    "appeal": "#c38513",
    "cassation": "#ac4c8a",
    "investigative": "#277cba",
    "administrative review": "#c46632",
    "case review": "#6953ad",
    "reopening": "#39559d",
    "unknown": "#818b94",
}
EDGE_STYLES = {
    "follows": ("#677787", "solid"),
    "part_of": ("#168d97", "dash"),
    "occurs_during": ("#ac7731", "dot"),
}


def timeline(
    rows: list[dict],
    all_rows: list[dict],
    show_links: bool = True,
    show_ids: bool = False,
) -> go.Figure:
    tracks = list(dict.fromkeys(r["track"] for r in all_rows))
    labels = {r["track"]: r["track_label"] for r in all_rows}
    lanes = {track: i for i, track in enumerate(tracks)}
    dated = [r for r in rows if r["bounds"]]
    positions = {}
    collisions = defaultdict(list)
    for row in dated:
        start, end = row["bounds"]
        midpoint = start + (end - start) // 2
        collisions[(row["track"], midpoint)].append(row)
    for (track, midpoint), group in collisions.items():
        for index, row in enumerate(group):
            offset = (index - (len(group) - 1) / 2) * min(0.12, 0.5 / len(group))
            positions[row["id"]] = (midpoint, lanes[track] + offset)
    fig = go.Figure()
    if show_links:
        for kind, (color, dash) in EDGE_STYLES.items():
            xs, ys, sizes = [], [], []
            for source, target, relation in visible_links(rows):
                if (
                    relation != kind
                    or source not in positions
                    or target not in positions
                ):
                    continue
                x1, y1 = positions[target]
                x2, y2 = positions[source]
                xs.extend([x1, x2, None])
                ys.extend([y1, y2, None])
                sizes.extend([0, 10, 0])
            if xs:
                fig.add_trace(
                    go.Scatter(
                        x=xs,
                        y=ys,
                        mode="lines+markers",
                        name=RELATIONS[kind],
                        line={"color": color, "dash": dash, "width": 1.3},
                        marker={
                            "symbol": "arrow",
                            "angleref": "previous",
                            "size": sizes,
                            "standoff": 8,
                            "color": color,
                        },
                        hoverinfo="skip",
                    )
                )
    for row in dated:
        start, end = row["bounds"]
        if start != end:
            y = positions[row["id"]][1]
            fig.add_trace(
                go.Scatter(
                    x=[start, end],
                    y=[y, y],
                    mode="lines",
                    line={"color": "#b7c4d0", "width": 9},
                    opacity=0.5,
                    showlegend=False,
                    hoverinfo="skip",
                )
            )
    levels = list(dict.fromkeys(r["level"] for r in dated))
    for level in levels:
        group = [r for r in dated if r["level"] == level]
        custom = []
        for row in group:
            description = "<br>".join(
                escape(line) for line in textwrap.wrap(row["description"], 85)
            )
            custom.append(
                [
                    row["id"],
                    escape(row["date"]),
                    escape(row["court"]),
                    escape(row["outcome"]),
                    escape(row["direction"]),
                    description,
                    escape(row["significance"]),
                ]
            )
        fig.add_trace(
            go.Scatter(
                x=[positions[r["id"]][0] for r in group],
                y=[positions[r["id"]][1] for r in group],
                mode="markers+text" if show_ids else "markers",
                text=[r["id"] for r in group],
                textposition="top center",
                name=level.capitalize(),
                customdata=custom,
                marker={
                    "size": [13 if r["significance"] == "major" else 9 for r in group],
                    "symbol": [
                        "circle" if r["significance"] == "major" else "diamond"
                        for r in group
                    ],
                    "color": COLORS.get(level, "#818b94"),
                    "line": {"color": "white", "width": 1.5},
                },
                hovertemplate=(
                    "<b>%{customdata[0]} · %{customdata[1]}</b><br>"
                    "%{customdata[2]}<br>%{customdata[5]}<br><br>"
                    "Outcome: %{customdata[3]}<br>Direction: %{customdata[4]}<br>"
                    "Significance: %{customdata[6]}<extra>%{fullData.name}</extra>"
                ),
            )
        )
    fig.update_layout(
        height=max(430, 130 + 90 * len(tracks)),
        margin={"l": 10, "r": 25, "t": 25, "b": 30},
        paper_bgcolor="white",
        plot_bgcolor="#f7f9fb",
        font={"color": "#243748"},
        xaxis={"type": "date", "title": "Domestic event date", "showgrid": True},
        yaxis={
            "tickmode": "array",
            "tickvals": list(lanes.values()),
            "ticktext": [
                f"{escape(t)} · "
                + "<br>".join(escape(line) for line in textwrap.wrap(labels[t], 34))
                for t in tracks
            ],
            "range": [len(tracks) - 0.5, -0.5],
            "zeroline": False,
        },
        legend={"orientation": "h", "y": 1.15},
        clickmode="event+select",
        hovermode="closest",
        dragmode="pan",
    )
    return fig
