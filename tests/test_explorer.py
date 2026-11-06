"""Precision, identity and filtering invariants for the saved-results explorer."""

import unittest
from datetime import date
from itertools import pairwise

from art6.explorer.data import (
    date_bounds,
    event_rows,
    filter_rows,
    load_demo,
    visible_links,
)
from art6.explorer.timeline import build_scene, timeline


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
                scene = build_scene(rows, rows)
                self.assertEqual({n["id"] for n in scene["nodes"]}, set(by_id))
                self.assertEqual(
                    {(e["to"], e["from"], e["kind"]) for e in scene["edges"]},
                    set(visible_links(rows)),
                )
                for lane in scene["lanes"]:
                    members = [n for n in scene["nodes"] if n["track"] == lane["id"]]
                    for left, right in pairwise(members):
                        self.assertGreaterEqual(
                            right["x"] - left["x"], scene["cardWidth"]
                        )
                for node in scene["nodes"]:
                    self.assertEqual(node["outcome"], by_id[node["id"]]["outcome"])
                    self.assertEqual(node["raw"], by_id[node["id"]]["raw"])
        self.assertEqual(case_sets[0], case_sets[1])
        self.assertEqual(counts, {"vote9_v9_luna": 212, "v9_luna": 265})

    def test_undated_filtered_and_cyclic_events_remain_explorable(self):
        rows = event_rows(
            {
                "events": [
                    {"id": "a", "track": "T1", "follows": "b", "significance": "major"},
                    {"id": "b", "track": "T1", "follows": "a", "significance": "minor"},
                ]
            }
        )
        scene = build_scene(rows, rows)
        self.assertEqual(len(scene["nodes"]), 2)
        self.assertTrue(all(n["precision"] == "undated" for n in scene["nodes"]))
        filtered = filter_rows(rows, {"significance": ["major"]})
        self.assertEqual(build_scene(filtered, rows)["edges"], [])
        self.assertEqual(build_scene(filtered, rows)["nodes"][0]["raw"]["follows"], "b")

    def test_extraction_text_cannot_escape_the_embedded_json(self):
        malicious = '</script><script>alert("injected")</script>'
        rows = event_rows({"events": [{"id": "e1", "description": malicious}]})
        page = timeline(rows, rows, title=malicious)
        self.assertNotIn(malicious, page)
        self.assertIn(r"\u003c/script\u003e", page)
        self.assertEqual(page.count("</script>"), 1)


if __name__ == "__main__":
    unittest.main()
