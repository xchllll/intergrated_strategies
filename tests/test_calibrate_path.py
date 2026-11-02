import json
import os
import sys
import unittest
import uuid

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "board"))
sys.path.insert(0, os.path.join(ROOT_DIR, "python_model"))

import calibration as cal  # noqa: E402
import calibrate_path  # noqa: E402


class CalibratePathTests(unittest.TestCase):
    def setUp(self):
        self._old_table = cal.CAL_TABLE
        self._old_meta = cal.CAL_META

    def tearDown(self):
        cal.CAL_TABLE = self._old_table
        cal.CAL_META = self._old_meta

    def test_builds_table_from_raw_sweeps(self):
        freqs = [20.0, 100.0, 1000.0, 20000.0]
        sweeps = []
        for round_index in range(2):
            sweeps.append([
                (freq, 20000.0 + 10.0 * round_index, 0.01 * index)
                for index, freq in enumerate(freqs)
            ])

        tmp_dir = os.path.join(ROOT_DIR, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        table_path = os.path.join(tmp_dir, "test_cal_%s.json" % uuid.uuid4().hex)
        try:
            result = calibrate_path.build_calibration_table(
                sweeps, table_path=table_path, source="unit-test")

            self.assertEqual(result["repeat"], 2)
            self.assertEqual(result["points"], len(freqs))
            self.assertTrue(os.path.isfile(table_path))

            with open(table_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["meta"]["repeat"], 2)
            self.assertEqual(payload["meta"]["source"], "unit-test")
            self.assertEqual(len(payload["rows"]), len(freqs))
            self.assertIn("direct", payload["rows"][0])
        finally:
            try:
                os.remove(table_path)
            except OSError:
                pass

    def test_rejects_empty_data(self):
        with self.assertRaises(ValueError):
            calibrate_path.build_calibration_table([])


if __name__ == "__main__":
    unittest.main()
