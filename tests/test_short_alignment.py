import importlib.util
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "short_alignment", ROOT / "python_model" / "short_alignment.py"
)
short_alignment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(short_alignment)


class ShortAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.fs = 48828.125
        self.freq = np.logspace(np.log10(20.0), np.log10(20000.0), 240)
        # 平滑的复数短接表，避免相位 unwrap 歧义
        base = 4.2e-5 + 1e-7 * np.sin(self.freq / 1000.0)
        phase = np.deg2rad(20.0 * np.sin(self.freq / 3000.0))
        self.ref = base * np.exp(1j * phase)

    def test_estimates_positive_and_negative_integer_delay(self):
        for m_true in (1, -1):
            new = self.ref * np.exp(-1j * 2 * np.pi * self.freq * m_true / self.fs)
            result = short_alignment.estimate_offset(
                self.freq, new, self.ref, fs=self.fs, max_samples=3
            )
            self.assertEqual(result["m_int"], m_true)
            self.assertAlmostEqual(result["m_frac"], m_true, delta=0.02)
            self.assertLess(result["int_phase_rms_deg"], 0.01)

    def test_zero_delay_is_noop(self):
        result = short_alignment.estimate_offset(
            self.freq, self.ref.copy(), self.ref, fs=self.fs
        )
        self.assertEqual(result["m_int"], 0)
        self.assertLess(result["int_phase_rms_deg"], 0.01)
        self.assertLess(result["int_db_rms"], 1e-9)

    def test_gain_change_is_not_fixed_by_delay(self):
        new = self.ref * 10.0 ** (0.5 / 20.0)  # +0.5 dB，纯增益
        result = short_alignment.estimate_offset(
            self.freq, new, self.ref, fs=self.fs
        )
        self.assertEqual(result["m_int"], 0)
        self.assertGreater(result["int_db_rms"], 0.45)
        self.assertGreater(result["frac_db_rms"], 0.45)

    def test_interp_complex_log(self):
        f_old = np.array([20.0, 200.0, 2000.0, 20000.0])
        c_old = np.array([1 + 0j, 2 + 1j, 3 - 1j, 4 + 2j])
        f_new = np.array([20.0, 2000.0, 20000.0])
        c_new = short_alignment.interp_complex_log(f_new, f_old, c_old)
        np.testing.assert_allclose(c_new[[0, 1, 2]], c_old[[0, 2, 3]], atol=1e-12)


if __name__ == "__main__":
    unittest.main()
