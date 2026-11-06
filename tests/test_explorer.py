"""Precision, identity and filtering invariants for the saved-results explorer."""

import unittest
from datetime import date

from art6.explorer.data import (
    date_bounds,
    event_rows,
    filter_rows,
    load_demo,
    visible_links,
)
from art6.explorer.timeline import timeline


class ExplorerDataTests(unittest.TestCase):
    def test_partial_dates_and_invalid_dates(self):
        self.assertEqual(date_bounds("2000"), (date(2000, 1, 1), date(2000, 12, 31)))
        self.assertEqual(date_bounds("2000-02"), (date(2000, 2, 1), date(2000, 2, 29)))
        self.assertIsNone(date_bounds("2001-02-29"))
        self.assertIsNone(date_bounds("Later that day"))

    def test_filtering_never_rewires_relations(self):
        payload = {
            "events": [
                {"id": "a", "significance": "major", "decision_date_iso": "2000"},
                {"id": "b", "significance": "minor", "follows": "a"},
                {"id": "c", "significance": "major", "follows": "b"},
            ]
        }
        rows = event_rows(payload)
        filtered = filter_rows(rows, {"significance": ["major"]})
        self.assertEqual(visible_links(filtered), [])
        self.assertEqual(payload["events"][2]["follows"], "b")
        self.assertEqual(filter_rows(rows, {"significance": []}), [])

    def test_track_containment_and_cycles(self):
        rows = event_rows(
            {
                "events": [
                    {"id": "a", "track": "T1"},
                    {"id": "b", "part_of": "a"},
                    {"id": "c", "part_of": "d"},
                    {"id": "d", "part_of": "c"},
                ]
            }
        )
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["b"]["track"], "T1")
        self.assertEqual(by_id["c"]["track"], "Unassigned")

    def test_packaged_artifacts_preserve_all_events_and_links(self):
        demo = load_demo()
        case_sets = []
        counts = {}
        for run, dataset in demo["datasets"].items():
            cases = dataset["cases"]
            self.assertEqual(len(cases), 10)
            case_sets.append({c["id"] for c in cases})
            counts[run] = 0
            for case in cases:
                payload = case["payload"]
                rows = event_rows(payload)
                counts[run] += len(rows)
                self.assertEqual(
                    {r["id"] for r in rows}, {e["id"] for e in payload["events"]}
                )
                self.assertEqual(len(rows), len({r["id"] for r in rows}))
                self.assertEqual(len(case["sha256"]), 64)
                by_id = {r["id"]: r for r in rows}
                for source, target, kind in visible_links(rows):
                    self.assertEqual(by_id[source]["raw"][kind], target)
                # Building every chart exercises date intervals, lane allocation,
                # relation arrows and simultaneous dates on the real artifacts.
                fig = timeline(rows, rows)
                point_ids = {
                    p[0]
                    for trace in fig.data
                    if trace.customdata is not None
                    for p in trace.customdata
                }
                self.assertEqual(point_ids, {r["id"] for r in rows if r["bounds"]})
        self.assertEqual(case_sets[0], case_sets[1])
        self.assertEqual(counts, {"vote9_v9_luna": 212, "v9_luna": 265})


if __name__ == "__main__":
    unittest.main()
