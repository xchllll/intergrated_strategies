import os
import re
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
        # 新版 Vivado 导出的 hwh 把 100 MHz 写成 CLKFREQUENCY="1e+08"，
        # 旧版写 "100000000"；统一按数值比较，避免依赖导出工具的字面格式。
        clock_hz = {
            float(match.group(1))
            for match in re.finditer(r'CLKFREQUENCY="([^"]+)"', hardware_xml)
        }
        self.assertTrue(clock_hz, "design_1.hwh 中未找到 CLKFREQUENCY 声明")
        for value in clock_hz:
            self.assertAlmostEqual(value, ps_ctrl.SAMPLE_CLK, delta=1.0)
        self.assertEqual(ps_ctrl.SAMPLE_CLK, 100e6)


if __name__ == "__main__":
    unittest.main()
