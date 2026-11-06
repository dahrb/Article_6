"""Opt-in browser regressions; one browser and one tab, never automatic.

Install Playwright separately and set ART6_BROWSER_TESTS=1 to run. See the
explorer README for a resource-limited command. Ordinary test discovery skips
these checks so it cannot unexpectedly launch a browser on the user's machine.
"""

import itertools
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from art6.explorer.data import event_rows, load_demo
from art6.explorer.timeline import timeline


@unittest.skipUnless(
    os.environ.get("ART6_BROWSER_TESTS") == "1", "opt-in browser checks"
)
class ExplorerBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise unittest.SkipTest("Playwright is not installed") from exc
        cls.folder = tempfile.TemporaryDirectory(prefix="art6-explorer-test-")
        cls.addClassCleanup(cls.folder.cleanup)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(
            headless=True, args=["--disable-gpu", "--renderer-process-limit=1"]
        )
        cls.addClassCleanup(cls.browser.close)
        cls.page = cls.browser.new_page(viewport={"width": 1140, "height": 900})
        cls.page.set_default_timeout(10000)
        cls.errors = []
        cls.page.on("pageerror", lambda error: cls.errors.append(str(error)))
        cls.serial = itertools.count()

    def setUp(self):
        self.page.set_viewport_size({"width": 1140, "height": 900})

    def tearDown(self):
        self.assertEqual(self.errors, [])

    def open_case(self, payload, visible_ids=None, title="Regression case"):
        rows = event_rows(payload)
        visible = (
            rows if visible_ids is None else [r for r in rows if r["id"] in visible_ids]
        )
        path = Path(self.folder.name) / f"case-{next(self.serial)}.html"
        path.write_text(
            timeline(
                visible,
                rows,
                title=title,
                case_id="demo-case",
                run_id="demo-run",
                source_label="Original run",
            )
        )
        self.page.goto(path.as_uri())

    def assert_cards_fit(self):
        overflow = self.page.evaluate("""() => [...document.querySelectorAll('.event')]
          .flatMap(g => [...g.querySelectorAll('text')].filter(t => {
            const b=t.getBBox(); return b.x < -.5 || b.x+b.width > 232.5;
          }).map(t => ({id:g.dataset.id, text:t.textContent})))""")
        self.assertEqual(overflow, [])

    def test_all_twenty_views_keep_every_event_and_fit_text(self):
        for dataset in load_demo()["datasets"].values():
            for case in dataset["cases"]:
                with self.subTest(case=case["id"], run=dataset["label"]):
                    self.open_case(case["payload"])
                    self.assertEqual(
                        self.page.locator(".event").count(),
                        len(case["payload"]["events"]),
                    )
                    self.assert_cards_fit()

    def test_filtered_incoming_and_missing_endpoints_are_distinguished(self):
        self.open_case(
            {
                "events": [
                    {
                        "id": "hidden",
                        "track": "T1",
                        "follows": "kept",
                        "authority_span": {"text": "Hidden court"},
                    },
                    {"id": "kept", "track": "T1", "part_of": "missing"},
                    {"id": "next", "track": "T1", "follows": "kept"},
                ]
            },
            visible_ids={"kept", "next"},
        )
        self.page.locator('.event[data-id="kept"]').click()
        hidden = self.page.get_by_role("button", name="Followed by → hidden")
        missing = self.page.get_by_role("button", name="Part of → missing")
        self.assertTrue(hidden.is_disabled())
        self.assertIn("Hidden by event filters", hidden.inner_text())
        self.assertTrue(missing.is_disabled())
        self.assertIn("missing from this extraction", missing.inner_text())
        self.assertEqual(self.page.locator(".event").count(), 2)
        self.assertEqual(self.page.locator(".edge").count(), 1)

    def test_cross_track_navigation_focus_and_overview_keyboard(self):
        self.open_case(
            {
                "events": [
                    {"id": "a", "track": "T1", "order": 1},
                    {"id": "b", "track": "T1", "order": 2, "follows": "a"},
                    {"id": "c", "track": "T1", "order": 3, "follows": "b"},
                    {"id": "e", "track": "T1", "order": 4, "follows": "c"},
                    {"id": "d", "track": "T2", "follows": "b"},
                ]
            }
        )
        self.page.locator("#track").select_option("T1")
        self.page.locator('.event[data-id="b"]').click()
        self.page.mouse.move(0, 0)
        selected_card = self.page.locator(".event.selected .card")
        border = selected_card.evaluate("e=>getComputedStyle(e).stroke")
        self.page.locator(".event.selected").hover()
        self.assertEqual(
            selected_card.evaluate("e=>getComputedStyle(e).stroke"), border
        )
        self.page.get_by_role("button", name="Followed by → d").click()
        self.assertEqual(self.page.locator("#track").input_value(), "all")
        self.assertEqual(
            self.page.locator('.event[aria-pressed="true"]').get_attribute("data-id"),
            "d",
        )
        self.assertEqual(self.page.locator("#inspector").evaluate("e=>e.scrollTop"), 0)
        self.page.get_by_role("button", name="Close event details").click()
        self.assertEqual(self.page.evaluate("document.activeElement.dataset.id"), "d")
        self.page.locator("#minimap").focus()
        self.assertTrue(
            self.page.locator("#viewport").evaluate("e=>e.scrollWidth>e.clientWidth")
        )
        self.page.keyboard.press("ArrowRight")
        self.assertGreater(
            self.page.locator("#viewport").evaluate("e=>e.scrollLeft"), 0
        )
        self.page.keyboard.press("Home")
        self.assertEqual(self.page.locator("#viewport").evaluate("e=>e.scrollLeft"), 0)
        self.page.locator('.event[data-id="b"]').click()
        self.page.get_by_role("button", name="Next event →").click()
        self.assertEqual(
            self.page.locator(".event.selected").get_attribute("data-id"), "c"
        )

    def test_narrow_inspector_does_not_cover_selected_card(self):
        case = load_demo()["datasets"]["vote9_v9_luna"]["cases"][0]
        for width in (740, 430):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 1000})
                self.open_case(case["payload"])
                self.page.get_by_role("button", name="Zoom in", exact=True).click()
                self.page.get_by_role("button", name="Zoom in", exact=True).click()
                self.page.locator(".event").first.click()
                geometry = self.page.evaluate("""() => {
                  const r=e=>e.getBoundingClientRect();
                  const card=r(document.querySelector('.event.selected'));
                  const view=r(document.querySelector('#viewport'));
                  const panel=r(document.querySelector('#inspector'));
                  return {below:panel.top>=view.bottom-1, visible:card.left>=view.left-1 &&
                    card.right<=view.right+1 && card.top>=view.top-1 && card.bottom<=view.bottom+1};
                }""")
                self.assertEqual(geometry, {"below": True, "visible": True})

    def test_long_labels_and_export_title_provenance_and_filename(self):
        title = (
            "Application of a company with a long registered name against the respondent state "
            * 3
        )
        self.open_case(
            {
                "events": [
                    {
                        "id": "a",
                        "track": "T1",
                        "description": "A" * 160,
                        "authority_span": {"text": "W" * 160},
                        "start_date_span": {"text": "On an unspecified date " * 10},
                    }
                ]
            },
            title=title.strip(),
        )
        self.assert_cards_fit()
        with self.page.expect_download() as download:
            self.page.get_by_role("button", name="Download SVG").click()
        self.assertEqual(
            download.value.suggested_filename, "demo-case-demo-run-all-tracks.svg"
        )
        path = Path(self.folder.name) / "export.svg"
        download.value.save_as(path)
        root = ET.parse(path).getroot()
        headings = root.findall("{http://www.w3.org/2000/svg}text")
        text = " ".join("".join(element.itertext()) for element in headings)
        self.assertIn(title.strip(), text)
        self.assertIn("demo-case · Original run · All tracks · 1 of 1 events", text)
        self.page.goto(path.as_uri())
        overflow = self.page.evaluate("""() => [...document.documentElement.children]
          .filter(e=>e.tagName==='text').some(e=>{
            const b=e.getBBox();return b.x+b.width>Number(document.documentElement.getAttribute('width'));
          })""")
        self.assertFalse(overflow)


if __name__ == "__main__":
    unittest.main()
