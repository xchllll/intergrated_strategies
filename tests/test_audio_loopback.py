import math
import os
import sys
import unittest

import numpy as np

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "board"))

import audio_loopback


class AudioLoopbackAnalysisTests(unittest.TestCase):
    def test_signed24_decodes_both_signs(self):
        raw = np.array(
            [0x000001, 0x7FFFFF, 0x800000, 0xFFFFFF],
            dtype=np.int32,
        )
        decoded = audio_loopback.signed24(raw)
        self.assertEqual(decoded.tolist(), [1, 8388607, -8388608, -1])

    def test_tone_capture_produces_nonzero_iq_and_correct_frequency(self):
        sample_rate = 48_000
        frequency = 1_000.0
        frame_count = int(1.2 * sample_rate)
        time_axis = np.arange(frame_count, dtype=np.float64) / sample_rate
        signal = np.zeros(frame_count, dtype=np.float64)
        onset = int(audio_loopback.SILENCE_SECONDS * sample_rate)
        signal[onset:] = (
            0.15
            * (1 << 23)
            * np.cos(2.0 * np.pi * frequency * time_axis[onset:])
        )
        signal += 1200.0
        rng = np.random.default_rng(42)
        signal += rng.normal(0.0, 200.0, frame_count)

        result = audio_loopback.analyze_tone(
            np.rint(signal).astype(np.int32),
            sample_rate,
            frequency,
            measure_seconds=0.5,
        )

        self.assertTrue(result["nonzero"])
        self.assertGreater(result["ac_rms_code"], 500_000)
        self.assertGreater(result["peak_bin_ratio"], 100.0)
        self.assertLess(abs(result["frequency_error_hz"]), 5.0)
        self.assertAlmostEqual(
            math.hypot(result["acc_i"], result["acc_q"]),
            result["amplitude_code"] * result["window_n"] / 2.0,
            delta=2.0,
        )

    def test_dc_only_capture_is_not_reported_as_a_valid_tone(self):
        sample_rate = 48_000
        frame_count = int(1.2 * sample_rate)
        signal = np.full(frame_count, 20_000, dtype=np.int32)
        with self.assertRaisesRegex(RuntimeError, "激励"):
            audio_loopback.analyze_tone(
                signal,
                sample_rate,
                1_000.0,
                measure_seconds=0.5,
            )


if __name__ == "__main__":
    unittest.main()
