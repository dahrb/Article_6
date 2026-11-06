"""Run with: uv run --extra viz streamlit run art6/explorer/app.py"""

import json

import streamlit as st

from art6.explorer.data import (
    RELATIONS,
    event_rows,
    filter_rows,
    load_demo,
    span,
    visible_links,
)
from art6.explorer.timeline import timeline

st.set_page_config(
    page_title="Domestic proceedings explorer", page_icon="⚖", layout="wide"
)


@st.cache_data
def demo_data():
    return load_demo()


def case_name(case):
    return span(case["payload"]["case"].get("case_name_span")) or case["id"]


def country(case):
    return span(case["payload"]["case"].get("respondent_state_span")) or "Unspecified"


def main():
    demo = demo_data()
    st.title("Domestic proceedings explorer")
    st.caption("Ten validation cases · Explore the extracted procedural history")
    datasets = demo["datasets"]
    with st.sidebar:
        st.header("Browse cases")
        run = st.selectbox(
            "Results",
            list(datasets),
            index=list(datasets).index(demo["default"]),
            format_func=lambda key: datasets[key]["label"],
        )
        cases = datasets[run]["cases"]
        selected_country = st.selectbox(
            "Respondent country",
            ["All countries"] + sorted({country(c) for c in cases}),
        )
        search = st.text_input("Search case name or ID")
        matches = [
            c
            for c in cases
            if (selected_country == "All countries" or country(c) == selected_country)
            and search.casefold() in f"{case_name(c)} {c['id']}".casefold()
        ]
        if not matches:
            st.info("No cases match. Clear the search or change the country.")
            return
        case_id = st.selectbox(
            "Case",
            [c["id"] for c in matches],
            format_func=lambda key: next(
                f"{case_name(c)} · {key}" for c in matches if c["id"] == key
            ),
        )
        case = next(c for c in matches if c["id"] == case_id)
        st.caption(f"{len(matches)} of {len(cases)} cases")
    payload = case["payload"]
    rows = event_rows(payload)
    st.subheader(case_name(case))
    meta = payload["case"]
    st.write(
        f"{country(case)} · {span(meta.get('application_number_span')) or 'Application number unavailable'}"
        f" · ECtHR date: {span(meta.get('judgment_date_span')) or 'unavailable'}"
    )
    st.link_button("Open in HUDOC", f"https://hudoc.echr.coe.int/eng?i={case_id}")
    if run == demo["default"]:
        st.info(
            "The voting export contains major events only. Choose ‘Original run’ to explore minor events too."
        )
    with st.sidebar:
        st.header("Filter events")
        filters = {}

        def add_filter(key, label):
            options = sorted({r[key] for r in rows})
            filters[key] = st.multiselect(
                label, options, default=options, key=f"{run}:{case_id}:{key}"
            )

        add_filter("significance", "Major / minor")
        add_filter("outcome", "Outcome")
        with st.expander("Courts, tracks and other filters"):
            for key, label in [
                ("court", "Court / authority"),
                ("direction", "Outcome direction"),
                ("level", "Instance level"),
                ("track", "Procedural track"),
            ]:
                add_filter(key, label)
        event_search = st.text_input("Search descriptions and quotations")
        show_links = st.checkbox("Show procedural links", value=True)
        show_ids = st.checkbox("Show event IDs", value=False)
    visible = filter_rows(rows, filters, event_search)
    metrics = st.columns(4)
    metrics[0].metric("Events shown", f"{len(visible)} / {len(rows)}")
    metrics[1].metric("Major", sum(r["significance"] == "major" for r in visible))
    metrics[2].metric("Minor", sum(r["significance"] == "minor" for r in visible))
    metrics[3].metric("Courts / authorities", len({r["court"] for r in visible}))
    if not visible:
        st.info(
            "No events match the filters. Select more values or clear the event search."
        )
        return
    timeline_tab, table_tab, provenance_tab = st.tabs(
        ["Timeline", "Event table", "About these results"]
    )
    selection_id = None
    with timeline_tab:
        if any(r["bounds"] for r in visible):
            selection = st.plotly_chart(
                timeline(visible, rows, show_links, show_ids),
                width="stretch",
                key=f"timeline:{run}:{case_id}",
                on_select="rerun",
                selection_mode="points",
                config={"scrollZoom": True, "displaylogo": False},
            )
            for point in selection.selection.points:
                custom = point.get("customdata")
                if custom:
                    selection_id = custom[0]
        else:
            st.info(
                "These events have no usable dates. Inspect them in the event table and details below."
            )
        hidden_links = sum(
            bool(r["raw"].get(k)) for r in visible for k in RELATIONS
        ) - len(visible_links(visible))
        st.caption(
            "Colour = instance level · circle = major · diamond = minor/unspecified. "
            "Arrows run from the referenced event to the event that refers to it. "
            "Solid = follows · dashed = part of · dotted = occurs during. "
            "Pale bars show year/month precision; their points sit at the interval midpoint. "
            f"{hidden_links} links have a filtered or unavailable endpoint. No replacement links are inferred."
        )
        undated = [r for r in visible if not r["bounds"]]
        if undated:
            st.write(f"**{len(undated)} events without a usable date**")
            st.dataframe(
                [
                    {k: r[k] for k in ("id", "date", "court", "description")}
                    for r in undated
                ],
                hide_index=True,
            )
    columns = (
        "id",
        "date",
        "track",
        "court",
        "level",
        "significance",
        "outcome",
        "direction",
        "description",
    )
    with table_tab:
        st.dataframe(
            [{k: r[k] for k in columns} for r in visible],
            hide_index=True,
            width="stretch",
        )
        st.download_button(
            "Download visible events (JSON)",
            json.dumps(
                {"case_id": case_id, "run": run, "events": [r["raw"] for r in visible]},
                ensure_ascii=False,
                indent=2,
            ),
            file_name=f"{case_id}-{run}-events.json",
            mime="application/json",
        )
    with provenance_tab:
        st.write(
            "Saved stage-1 extractions, rather than annotations or a newly generated reconstruction. "
            "Descriptions, significance labels, outcomes, tracks and relation targets come from the selected run. "
            "Unknown values remain unknown. Generic court names are displayed as extracted."
        )
        st.code(case["source"])
        st.caption(f"Original artifact SHA-256: {case['sha256']}")
        st.write(
            "The default nine-run vote scored highest on event alignment F1 (89.1%) and "
            "medium-match F1 (78.9%) among the complete saved stage-1 configurations compared. "
            "Scores use the major-event view against the ten-case development annotations; "
            "this is a development demo, not a held-out evaluation. Other voting variants score higher on some links."
        )
        st.caption(
            "Next iteration: country → court → case links and richer cross-case exploration."
        )
    st.divider()
    st.subheader("Event details")
    ids = [r["id"] for r in visible]
    detail_key = f"detail:{run}:{case_id}"
    selection_key = f"selection:{run}:{case_id}"
    if selection_id in ids and selection_id != st.session_state.get(selection_key):
        st.session_state[detail_key] = selection_id
        st.session_state[selection_key] = selection_id
    if st.session_state.get(detail_key) not in ids:
        st.session_state[detail_key] = ids[0]
    event_id = st.selectbox(
        "Select an event (or click a timeline point)",
        ids,
        key=detail_key,
        format_func=lambda key: next(
            f"{r['id']} · {r['date']} · {r['description'][:100]}"
            for r in visible
            if r["id"] == key
        ),
    )
    row = next(r for r in visible if r["id"] == event_id)
    left, right = st.columns([2, 1])
    with left:
        st.write(row["description"] or "No description provided.")
        st.write(f"**Court / authority:** {row['court']}")
        st.write(f"**Track:** {row['track']} · {row['track_label']}")
        st.write(f"**Outcome:** {row['outcome']} · **Direction:** {row['direction']}")
        st.write(
            f"**Date:** {row['date']} · **Level:** {row['level']} · **Significance:** {row['significance']}"
        )
        st.write("**Supporting quotation**")
        if row["quote"]:
            st.text(row["quote"])
        else:
            st.caption("No supporting event quotation provided in this export.")
        parties = row["raw"].get("parties", [])
        st.write(
            "**Parties:** "
            + ("; ".join(span(p.get("name_span")) for p in parties) or "Not provided")
        )
    with right:
        st.write("**Extracted links**")
        for kind, label in RELATIONS.items():
            target = row["raw"].get(kind)
            if target:
                suffix = "" if target in ids else " (filtered or unavailable)"
                st.write(f"{label}: {target}{suffix}")
        if not any(row["raw"].get(k) for k in RELATIONS):
            st.caption("No outgoing link provided.")
        with st.expander("All extracted event fields"):
            st.json(row["raw"])


if __name__ == "__main__":
    main()
