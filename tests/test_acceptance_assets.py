import os
import sys
import unittest
import zipfile

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "board"))
sys.path.insert(0, os.path.join(ROOT_DIR, "python_model"))

import acceptance
import ps_ctrl


class AcceptanceAssetTests(unittest.TestCase):
    def test_bitstream_exists_and_matches_xsa_member(self):
        bit_path = acceptance.ensure_bitstream()
        self.assertTrue(bit_path.is_file())
        with zipfile.ZipFile(acceptance.XSA_PATH) as archive:
            expected = archive.read("design_1_wrapper.bit")
        self.assertEqual(bit_path.read_bytes(), expected)

    def test_xsa_clock_matches_ps_ctrl_default(self):
        with zipfile.ZipFile(acceptance.XSA_PATH) as archive:
            hardware_xml = archive.read("design_1.hwh").decode("utf-8")
        self.assertIn('CLKFREQUENCY="50000000"', hardware_xml)
        self.assertEqual(ps_ctrl.SAMPLE_CLK, 50e6)


if __name__ == "__main__":
    unittest.main()
