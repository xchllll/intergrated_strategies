import os
import sys
import unittest
from unittest import mock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "board"))

import ps_ctrl


class FakeMmio:
    def __init__(self, state, base_address):
        self.state = state
        self.base_address = base_address

    def write(self, offset, value):
        if self.base_address == ps_ctrl.AXI_GPIO_0_BASE and offset == ps_ctrl.REG_GPIO2_DATA:
            self.state["start"] = bool(value & 0x1)
        self.state["writes"][(self.base_address, offset)] = value

    def read(self, offset):
        if self.base_address == ps_ctrl.AXI_GPIO_1_BASE and offset == ps_ctrl.REG_GPIO2_DATA:
            return 1 if self.state.get("done", True) and self.state.get("start") else 0
        if self.base_address == ps_ctrl.AXI_GPIO_2_BASE:
            value = self.state["acc_i"] & ((1 << 48) - 1)
            return (value >> 32) if offset == ps_ctrl.REG_GPIO2_DATA else value & 0xFFFFFFFF
        if self.base_address == ps_ctrl.AXI_GPIO_3_BASE:
            value = self.state["acc_q"] & ((1 << 48) - 1)
            return (value >> 32) if offset == ps_ctrl.REG_GPIO2_DATA else value & 0xFFFFFFFF
        return 0


class PsCtrlTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "acc_i": -123456789,
            "acc_q": -140737488355328,
            "writes": {},
            "done": True,
            "start": False,
        }
        devices = {
            base: FakeMmio(self.state, base)
            for base in (
                ps_ctrl.AXI_GPIO_0_BASE,
                ps_ctrl.AXI_GPIO_1_BASE,
                ps_ctrl.AXI_GPIO_2_BASE,
                ps_ctrl.AXI_GPIO_3_BASE,
            )
        }
        self.mmio_patch = mock.patch.object(
            ps_ctrl, "_get_mmio", side_effect=lambda base: devices[base]
        )
        self.mmio_patch.start()
        ps_ctrl._mmio_cache.clear()

    def tearDown(self):
        self.mmio_patch.stop()
        ps_ctrl._mmio_cache.clear()

    def test_signed_48_boundaries(self):
        self.assertEqual(ps_ctrl._to_signed_48(0), 0)
        self.assertEqual(ps_ctrl._to_signed_48((1 << 47) - 1), (1 << 47) - 1)
        self.assertEqual(ps_ctrl._to_signed_48(1 << 47), -(1 << 47))
        self.assertEqual(ps_ctrl._to_signed_48((1 << 48) - 1), -1)

    def test_measure_once_waits_for_done_and_reads_signed_values(self):
        acc_i, acc_q, window_n = ps_ctrl.measure_once(
            1000.0, periods=1, margin_s=0.01, poll_interval_s=0.0001
        )
        self.assertEqual(acc_i, self.state["acc_i"])
        self.assertEqual(acc_q, self.state["acc_q"])
        self.assertEqual(
            window_n, self.state["writes"][(ps_ctrl.AXI_GPIO_1_BASE, ps_ctrl.REG_GPIO_DATA)]
        )
        self.assertFalse(self.state["start"])

    def test_measure_once_times_out_instead_of_returning_stale_values(self):
        self.state["done"] = False
        with self.assertRaisesRegex(RuntimeError, "out_done"):
            ps_ctrl.measure_once(
                1000.0, periods=1, margin_s=0.001, poll_interval_s=0.0001
            )
        self.assertFalse(self.state["start"])

    def test_delivered_bitstream_clock_matches_default(self):
        self.assertEqual(ps_ctrl.SAMPLE_CLK, 50e6)


if __name__ == "__main__":
    unittest.main()
