import os
import sys
import unittest
from unittest import mock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from diagnosis_system.dashboard import app as dashboard_app


class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        dashboard_app.app.config.update(TESTING=True)
        self.client = dashboard_app.app.test_client()

    def test_index_loads_local_dashboard_assets(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("/static/plotly.min.js", body)
        self.assertIn("/static/dashboard.js", body)

    def test_status_reports_demo_source_honestly(self):
        response = self.client.get("/api/status")
        result = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(result["source"]["mode"], "demo_csv")
        self.assertFalse(result["source"]["is_real"])

    def test_demo_sweep_returns_full_curve_and_explicit_warning(self):
        fake_ai = {
            "label": "RC 低通滤波器（演示）",
            "confidence": 0.9,
            "model_note": "测试桩",
        }
        with mock.patch.object(
            dashboard_app, "_run_ai", return_value=(fake_ai, None)
        ):
            response = self.client.post("/api/start_sweep", json={"use_buffer": False})
        result = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["data"]["freqs"]), 240)
        self.assertEqual(len(result["data"]["mag_db"]), 240)
        self.assertEqual(len(result["data"]["phase_deg"]), 240)
        self.assertFalse(result["source"]["is_real"])
        self.assertTrue(any("演示 CSV" in item for item in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
