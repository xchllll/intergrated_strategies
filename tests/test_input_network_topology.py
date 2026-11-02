import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "input_network", ROOT / "python_model" / "input_network.py"
)
input_network = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(input_network)


class TopologyAwareTableTests(unittest.TestCase):
    def _write_table(self):
        freqs = [20.0, 1000.0, 20000.0]
        # Two rows deliberately share the same H at 20 kHz but have different Zin.
        h_series = np.array([0.9 + 0.0j, 0.5 + 0.0j, 0.1 + 0.0j])
        h_shunt = np.array([0.1 + 0.0j, 0.5 + 0.0j, 0.9 + 0.0j])
        z_series = np.array([10000.0 + 0.0j] * 3)
        z_shunt = np.array([2000.0 + 0.0j] * 3)
        payload = {
            "format": 3,
            "freqs": freqs,
            "levels": [0.5, 0.5],
            "level_f": [list(np.abs(h_series)), list(np.abs(h_shunt))],
            "h_re": [list(h_series.real), list(h_shunt.real)],
            "h_im": [list(h_series.imag), list(h_shunt.imag)],
            "zin_re": [list(z_series.real), list(z_shunt.real)],
            "zin_im": [list(z_series.imag), list(z_shunt.imag)],
            "L_re": [0.0, 0.0],
            "L_im": [0.0, 0.0],
            "R_ref": 10000.0,
            "meta": {
                "rows": ["series_row", "shunt_row"],
                "table_kind": "topology_aware",
                "standards": [
                    {"label": "series_row", "topology": "resistor"},
                    {"label": "shunt_row", "topology": "rc_lowpass"},
                ],
            },
        }
        fd = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        try:
            json.dump(payload, fd, ensure_ascii=False)
            return fd.name
        finally:
            fd.close()

    def test_series_and_shunt_groups_are_separate(self):
        path = self._write_table()
        try:
            input_network.load(path)
        finally:
            Path(path).unlink(missing_ok=True)
        self.assertEqual(input_network.table_kind(), "topology_aware")
        self.assertEqual(
            input_network.topologies(), ["resistor", "rc_lowpass"]
        )
        # Shared H at 20 kHz: the default (series) path must pick the series row,
        # while the explicit shunt path must pick the shunt row.
        h_shared = 0.1 + 0.0j
        z_series = input_network.zin_at(20000.0, h_shared)
        z_shunt = input_network.zin_at(20000.0, h_shared, topology="shunt")
        self.assertAlmostEqual(z_series.real, 10000.0, delta=1e-6)
        self.assertAlmostEqual(z_shunt.real, 2000.0, delta=1e-6)

    def test_series_deembed_ignores_shunt_rows(self):
        path = self._write_table()
        try:
            input_network.load(path)
        finally:
            Path(path).unlink(missing_ok=True)
        h = 0.9 + 0.0j
        d = input_network.deembed_complex(h, 20000.0)
        self.assertAlmostEqual(abs(d), 0.9, delta=1e-9)
        self.assertAlmostEqual(d.real, 0.9, delta=1e-9)
        with self.assertRaises(ValueError):
            input_network.deembed_complex(h, 20000.0, topology="shunt")

    def test_shunt_equivalent_path_uses_shunt_rows(self):
        path = self._write_table()
        try:
            input_network.load(path)
        finally:
            Path(path).unlink(missing_ok=True)
        h = 0.1 + 0.0j
        d = input_network.deembed_shunt_equivalent(h, 20000.0)
        # Zin=2000, Z_dut=2000*(1-H)/H=18000, R_ref=10000
        self.assertAlmostEqual(abs(d), 10000.0 / 28000.0, delta=1e-9)


if __name__ == "__main__":
    unittest.main()
