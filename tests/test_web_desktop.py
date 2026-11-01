import json
import tempfile
import unittest
from pathlib import Path

from strategic_game.web_desktop import DesktopApi, _clean_countries, _frontend_path


class WebDesktopTests(unittest.TestCase):
    def test_default_config_is_safe_and_uses_eight_country_scenario(self):
        config = DesktopApi().get_config()
        self.assertEqual(len(config["countries"]), 8)
        self.assertEqual(config["model"], "deepseek-flash")
        self.assertNotIn("global_presence", config["countries"]["C1"])
        self.assertNotIn("strategic_culture_knowledge", config["countries"]["C1"])

    def test_frontend_entry_has_stable_name(self):
        self.assertEqual(_frontend_path().name, "index.html")

    def test_api_key_is_never_returned_by_status_or_config(self):
        api = DesktopApi()
        self.assertNotIn("api_key", api.get_config())
        self.assertNotIn("api_key", api.get_status())

    def test_status_is_lightweight_and_run_view_is_revisioned(self):
        api = DesktopApi()
        with tempfile.TemporaryDirectory() as folder:
            api.run_file = Path(folder) / "run.json"
            api.run_file.write_text(json.dumps({"run_status":"running", "initial_state":{"countries":{}},
                                                "rounds":[{"round":1,"decisions":{},"state":{}}]}), encoding="utf-8")
            api._refresh_view()
            status = api.get_status()
            self.assertNotIn("data", status)
            self.assertEqual(status["view_revision"], 1)
            self.assertEqual(api.get_run_view()["rounds"][0]["round"], 1)


if __name__ == "__main__":
    unittest.main()
