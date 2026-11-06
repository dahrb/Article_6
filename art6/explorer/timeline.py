"""Readable procedural swimlanes, rendered as a small, self-contained SVG viewer.

Horizontal positions are chronological slots within each extracted track. They
are deliberately not a duration scale. Layout never adds or contracts relations.
"""

import json
from collections import Counter
from pathlib import Path

from art6.explorer.data import visible_links

LEVELS = {
    "investigative": ("I", "Investigative", "#2878bd"),
    "administrative review": ("AR", "Administrative review", "#bc6030"),
    "first instance": ("FI", "First instance", "#16856b"),
    "appeal": ("AP", "Appeal", "#a8750b"),
    "cassation": ("CA", "Cassation", "#a44e88"),
    "case review": ("CR", "Case review", "#47804b"),
    "reopening": ("RO", "Reopening", "#6552a3"),
    "unknown": ("—", "Level unspecified", "#6c7a89"),
}
DIRECTIONS = {
    "favourable to applicant": ("▲", "Favourable to applicant", "#16724f"),
    "unfavourable to applicant": ("▼", "Unfavourable to applicant", "#b34642"),
    "mixed": ("◆", "Mixed for applicant", "#986b13"),
    "unknown": ("—", "Direction not recorded", "#76818f"),
}
CARD_WIDTH, CARD_HEIGHT = 232, 184
COLUMN_STEP, LANE_HEIGHT, LEFT = 292, 278, 206


def build_scene(rows: list[dict], all_rows: list[dict]) -> dict:
    """Keep identities and relation kinds intact, including undated events."""
    counts = Counter(r["track"] for r in all_rows)
    tracks = sorted({r["track"] for r in rows}, key=lambda t: (-counts[t], t))
    nodes, lanes = [], []
    for lane_index, track in enumerate(tracks):
        members = [r for r in rows if r["track"] == track]
        # Exact ties follow the source's event order rather than lexical e1/e10/e2.
        members.sort(
            key=lambda r: (
                r["bounds"][0].isoformat() if r["bounds"] else "9999",
                r["raw"].get("order", 0),
                r["id"],
            )
        )
        lanes.append(
            {
                "id": track,
                "label": members[0]["track_label"],
                "count": len(members),
                "y": 48 + lane_index * LANE_HEIGHT,
            }
        )
        for column, row in enumerate(members):
            node = {k: v for k, v in row.items() if k != "bounds"}
            node.update(
                x=LEFT + column * COLUMN_STEP,
                y=94 + lane_index * LANE_HEIGHT,
                level_style=LEVELS.get(row["level"], LEVELS["unknown"]),
                direction_style=DIRECTIONS.get(row["direction"], DIRECTIONS["unknown"]),
                precision=(
                    "day"
                    if row["bounds"] and row["bounds"][0] == row["bounds"][1]
                    else "partial"
                    if row["bounds"]
                    else "undated"
                ),
            )
            nodes.append(node)
    edges = [
        {"from": target, "to": source, "kind": kind}
        for source, target, kind in visible_links(rows)
    ]
    return {
        "nodes": nodes,
        "lanes": lanes,
        "edges": edges,
        "width": max((n["x"] + CARD_WIDTH + 38 for n in nodes), default=700),
        "height": max(360, 48 + len(lanes) * LANE_HEIGHT),
        "cardWidth": CARD_WIDTH,
        "cardHeight": CARD_HEIGHT,
        "laneHeight": LANE_HEIGHT,
        "left": LEFT,
    }


def timeline(
    rows: list[dict], all_rows: list[dict], *, title: str = "Domestic proceedings"
) -> str:
    scene = build_scene(rows, all_rows)
    scene["title"] = title
    # Extraction text is data, never executable HTML. The viewer inserts it via
    # textContent; escape script delimiters as well as JS line separators here.
    data = (
        json.dumps(scene, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return (
        Path(__file__)
        .with_name("timeline.html")
        .read_text(encoding="utf-8")
        .replace("/*__SCENE__*/", data)
    )
