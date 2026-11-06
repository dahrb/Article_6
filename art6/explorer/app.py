"""Run with: uv run --extra viz streamlit run art6/explorer/app.py"""

import json

import streamlit as st

from art6.explorer.data import (
    event_rows,
    filter_rows,
    load_demo,
    span,
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
    st.caption("DOMESTIC PROCEEDINGS EXPLORER · TEN VALIDATION CASES")
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
        search = st.text_input("Search case name or ID").strip()
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
    st.caption(
        f"[Open source document in HUDOC](https://hudoc.echr.coe.int/eng?i={case_id})"
    )
    if run == demo["default"]:
        st.caption(
            "Nine-run voting · major events only. Choose ‘Original run’ to include minor events."
        )
    with st.sidebar:
        st.header("Filter events")
        filters = {}

        def add_filter(key, label):
            options = sorted({r[key] for r in rows})
            widget_key = f"{run}:{case_id}:{key}"
            if widget_key not in st.session_state:
                st.session_state[widget_key] = options
            filters[key] = st.multiselect(label, options, key=widget_key)

        add_filter("significance", "Major / minor")
        add_filter("outcome", "Outcome")
        with st.expander("Courts, tracks and other filters"):
            for key, label in [
                ("court", "Court / authority"),
                ("direction", "Outcome direction"),
                ("level", "Instance level"),
                ("type", "Proceeding type"),
                ("track", "Procedural track"),
            ]:
                add_filter(key, label)
        query_key = f"{run}:{case_id}:query"
        event_search = st.text_input(
            "Search descriptions and quotations", key=query_key
        )

        def reset_filters():
            for field in filters:
                st.session_state[f"{run}:{case_id}:{field}"] = sorted(
                    {r[field] for r in rows}
                )
            st.session_state[query_key] = ""

        st.button("Reset event filters", on_click=reset_filters, width="stretch")
    visible = filter_rows(rows, filters, event_search)
    metrics = st.columns(4)
    metrics[0].metric("Events shown", f"{len(visible)} / {len(rows)}")
    metrics[1].metric("Major", sum(r["significance"] == "major" for r in visible))
    metrics[2].metric("Minor", sum(r["significance"] == "minor" for r in visible))
    metrics[3].metric(
        "Authority labels",
        len({r["court"] for r in visible}),
        help="Distinct extracted names. Variants and generic labels have not been resolved to unique institutions.",
    )
    if not visible:
        st.info(
            "No events match the filters. Select more values or clear the event search."
        )
        return
    timeline_tab, table_tab, provenance_tab = st.tabs(
        ["Timeline", "Event table", "About these results"]
    )
    with timeline_tab:
        st.iframe(
            timeline(
                visible,
                rows,
                title=case_name(case),
                case_id=case_id,
                run_id=run,
                source_label=datasets[run]["label"],
            ),
            height=790,
            alt="Interactive procedural timeline with event cards, connections and evidence",
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


if __name__ == "__main__":
    main()
