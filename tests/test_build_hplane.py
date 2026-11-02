import importlib.util
import json
import math
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_hplane", ROOT / "tools" / "build_hplane.py"
)
build_hplane = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_hplane)


class ValueParsingTests(unittest.TestCase):
    def test_si_suffixes(self):
        self.assertAlmostEqual(build_hplane.parse_value("97.2k", "R"), 97200.0)
        self.assertAlmostEqual(build_hplane.parse_value("11.6k", "R"), 11600.0)
        self.assertAlmostEqual(build_hplane.parse_value("10n", "C"), 10e-9)
        self.assertAlmostEqual(build_hplane.parse_value("10.26nF", "C"), 10.26e-9)
        self.assertAlmostEqual(build_hplane.parse_value("9.8mH", "L"), 9.8e-3)
        self.assertAlmostEqual(build_hplane.parse_value("98.11n", "C"), 98.11e-9)
        self.assertAlmostEqual(build_hplane.parse_value("97.2kΩ", "R"), 97200.0)

    def test_bad_value_rejected(self):
        with self.assertRaises(ValueError):
            build_hplane.parse_value("1k02", "R")

    def test_topology_inference(self):
        self.assertEqual(build_hplane.normalize_topology("", False, False), "resistor")
        self.assertEqual(build_hplane.normalize_topology("", False, True), "series_rc")
        self.assertEqual(build_hplane.normalize_topology("", True, False), "series_rl")
        self.assertEqual(build_hplane.normalize_topology("", True, True), "series_rlc")
        self.assertEqual(build_hplane.normalize_topology("rc", False, True), "series_rc")
        self.assertEqual(
            build_hplane.normalize_topology("rc_lowpass", False, True), "rc_lowpass"
        )
        self.assertEqual(
            build_hplane.normalize_topology("RL-HP", True, False), "rl_highpass"
        )


class StandardSpecTests(unittest.TestCase):
    def test_spec_parsing(self):
        std = build_hplane.parse_standard_spec(
            "label=rc1k98,topology=series_rc,R=1.98k,C=10n,sweep=abc"
        )
        self.assertEqual(std["label"], "rc1k98")
        self.assertEqual(std["topology"], "series_rc")
        self.assertAlmostEqual(std["R"], 1980.0)
        self.assertAlmostEqual(std["C"], 10e-9)
        self.assertEqual(std["sweep"], "abc")

    def test_json_parsing(self):
        tmpdir = ROOT / "tmp"
        tmpdir.mkdir(exist_ok=True)
        path = tmpdir / "hplane_std_test.json"
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "standards": [
                            {"label": "r1", "topology": "resistor", "R": "1k", "sweep": "s1"}
                        ]
                    },
                    fh,
                )
            items = build_hplane.load_standards_json(str(path))
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["label"], "r1")
        self.assertEqual(items[0]["topology"], "resistor")


class ImpedanceTests(unittest.TestCase):
    def test_series_rlc_resonance(self):
        r, l, c = 1000.0, 9.8e-3, 98.11e-9
        f0 = 1.0 / (2.0 * math.pi * math.sqrt(l * c))
        z = build_hplane.impedance(
            [f0], {"R": r, "L": l, "C": c}
        )[0]
        self.assertAlmostEqual(z.real, r, delta=1e-6)
        self.assertAlmostEqual(z.imag, 0.0, delta=1e-3)

    def test_shunt_reference_transfer_rolloff(self):
        freqs = np.array([20.0, 2000.0, 20000.0])
        rc = {
            "topology": "rc_lowpass",
            "R": 1000.0,
            "C": 100e-9,
            "L": None,
        }
        h_rc = np.abs(build_hplane.reference_transfer(freqs, rc, 10000.0))
        self.assertGreater(h_rc[0], h_rc[1])
        self.assertGreater(h_rc[1], h_rc[2])
        self.assertLess(h_rc[-1], 0.1)

        rl = {
            "topology": "rl_highpass",
            "R": 1000.0,
            "C": None,
            "L": 100e-3,
        }
        h_rl = np.abs(build_hplane.reference_transfer(freqs, rl, 10000.0))
        self.assertLess(h_rl[0], 0.1)
        self.assertGreater(h_rl[-1], h_rl[0])

    def test_shunt_standard_parsing_requires_right_element(self):
        with self.assertRaises(ValueError):
            build_hplane._standard_from_dict(
                {
                    "label": "bad_rc",
                    "topology": "rc_lowpass",
                    "R": "1k",
                    "L": "10m",
                    "sweep": "s",
                },
                "test",
            )
        with self.assertRaises(ValueError):
            build_hplane._standard_from_dict(
                {
                    "label": "bad_rl",
                    "topology": "rl_highpass",
                    "R": "1k",
                    "C": "10n",
                    "sweep": "s",
                },
                "test",
            )


class BuildTests(unittest.TestCase):
    def test_build_reconstructs_known_zin(self):
        freqs = np.array([20.0, 100.0, 1000.0, 10000.0, 20000.0])
        zin_true = 10000.0 + 0.0j
        # 故意给短接系数加一个复数比例，验证 raw*coeff 确实被消掉
        coeff = 2.0 * np.exp(1j * math.radians(30.0))
        cal_coeffs = np.full_like(freqs, coeff, dtype=complex)
        histories = {}
        standards = []
        for label, r in (("97k2", 97200.0), ("1k0", 1000.0)):
            h_true = zin_true / (r + zin_true)
            raw = h_true / coeff
            key = "s_" + label
            histories[key] = {
                "key": key,
                "freq": freqs.copy(),
                "h_raw": raw,
                "source": "synthetic",
                "mode": "test",
            }
            standards.append(
                build_hplane._standard_from_dict(
                    {
                        "label": label,
                        "topology": "resistor",
                        "R": str(r),
                        "sweep": key,
                    },
                    "test",
                )
            )

        rows = build_hplane.build_rows(freqs, cal_coeffs, histories, standards)
        self.assertEqual([row["label"] for row in rows], ["97k2", "1k0"])
        for row in rows:
            np.testing.assert_allclose(row["H"], zin_true / (row["R"] + zin_true), atol=1e-9)
            np.testing.assert_allclose(row["Zin"], zin_true, atol=1e-6)

        r_ref, source = build_hplane._r_ref_from_rows(rows, "97k2")
        self.assertAlmostEqual(r_ref, 10000.0, delta=1e-6)
        self.assertIn("97k2", source)

        payload = build_hplane.build_payload(
            freqs,
            rows,
            r_ref,
            source,
            {},
            "synthetic",
            [],
            None,
            "test",
        )
        self.assertEqual(payload["format"], 3)
        self.assertEqual(payload["meta"]["rows"], ["97k2", "1k0"])
        validation = build_hplane.validate_payload(payload, rows)
        for item in validation:
            self.assertLess(item["db_max"], 1e-6)
            self.assertLess(item["phase_max"], 1e-6)

    def _assert_shunt_roundtrip(self, topology, r, element, value):
        freqs = np.array([20.0, 100.0, 1000.0, 10000.0, 20000.0])
        zin_true = 10000.0 + 0.0j
        std = build_hplane._standard_from_dict(
            {
                "label": "shunt",
                "topology": topology,
                "R": str(r),
                element: str(value),
                "sweep": "s_shunt",
            },
            "test",
        )
        zsh = build_hplane._shunt_element(freqs, std)
        zp = zsh * zin_true / (zsh + zin_true)
        h_true = zp / (r + zp)
        coeff = np.full_like(freqs, 1.0 + 0.0j, dtype=complex)
        histories = {
            "s_shunt": {
                "key": "s_shunt",
                "freq": freqs.copy(),
                "h_raw": h_true.copy(),
                "source": "synthetic",
                "mode": "test",
            }
        }
        rows = build_hplane.build_rows(freqs, coeff, histories, [std])
        np.testing.assert_allclose(rows[0]["H"], h_true, atol=1e-12)
        np.testing.assert_allclose(rows[0]["Zin"], zin_true, atol=1e-6)
        payload = build_hplane.build_payload(
            freqs, rows, 10000.0, "test", {}, "synthetic", [], None, "test"
        )
        for item in build_hplane.validate_payload(payload, rows):
            self.assertLess(item["db_max"], 1e-6)
            self.assertLess(item["phase_max"], 1e-6)

    def test_rc_lowpass_roundtrip(self):
        self._assert_shunt_roundtrip("rc_lowpass", 1000.0, "C", 100e-9)

    def test_rl_highpass_roundtrip(self):
        self._assert_shunt_roundtrip("rl_highpass", 1000.0, "L", 100e-3)

    def test_mixed_table_is_topology_aware(self):
        freqs = np.array([20.0, 1000.0, 20000.0])
        common = {
            "freq": freqs,
            "H": np.full(3, 0.5 + 0.0j),
            "Zin": np.full(3, 10000.0 + 0.0j),
            "Z": np.zeros(3, dtype=complex),
            "level": 0.5,
            "n_points": 3,
            "sweep": "s",
        }
        rows = [
            {**common, "label": "r", "topology": "resistor", "R": 1000.0, "L": None, "C": None},
            {**common, "label": "c", "topology": "rc_lowpass", "R": 1000.0, "L": None, "C": 100e-9},
        ]
        payload = build_hplane.build_payload(
            freqs, rows, 10000.0, "test", {}, "test", [], None, "test"
        )
        self.assertEqual(payload["meta"]["table_kind"], "topology_aware")
        self.assertEqual(
            [s["topology"] for s in payload["meta"]["standards"]],
            ["resistor", "rc_lowpass"],
        )


if __name__ == "__main__":
    unittest.main()
