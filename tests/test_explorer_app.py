"""Exercise the Streamlit UI against all twenty saved case/run combinations."""

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "art6" / "explorer" / "app.py"


class ExplorerAppTests(unittest.TestCase):
    def test_all_cases_and_run_switching(self):
        app = AppTest.from_file(str(APP), default_timeout=30).run()
        self.assertFalse(app.exception)
        for run in ("vote9_v9_luna", "v9_luna"):
            app.sidebar.selectbox[0].select(run).run()
            for case in app.sidebar.selectbox[2].options:
                case_id = case.rsplit(" · ", 1)[1]
                app.sidebar.selectbox[2].select(case_id).run()
                self.assertFalse(app.exception, (run, case_id))
                self.assertEqual(len(app.metric), 4)
        app.sidebar.selectbox[1].select("BULGARIA").run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.sidebar.selectbox[2].options), 1)
        app.sidebar.selectbox[1].select("All countries").run()
        # No-match searches and empty selections must remain navigable.
        app.sidebar.text_input[0].set_value("not-a-case-id").run()
        self.assertFalse(app.exception)
        self.assertTrue(app.info)
        app.sidebar.text_input[0].set_value("STANEV").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.sidebar.selectbox[2].value, "001-108690")
        app.sidebar.multiselect[0].set_value([]).run()
        self.assertFalse(app.exception)
        self.assertTrue(app.info)


if __name__ == "__main__":
    unittest.main()
