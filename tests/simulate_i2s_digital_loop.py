#!/usr/bin/env python3
"""Cycle-level model of the fixed DDS -> I2S -> ADC -> demod data path."""

from __future__ import annotations

import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SINE_MEM = ROOT / "src" / "sine.mem"


def signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def signed48(value: int) -> int:
    value &= (1 << 48) - 1
    return value - (1 << 48) if value & (1 << 47) else value


def run(clock_hz: float = 50_000_000, freq_hz: float = 1000.0,
        sample_count_target: int = 244) -> dict:
    rom = [signed16(int(line.strip(), 16)) for line in SINE_MEM.read_text().splitlines()]
    phase_inc = round(freq_hz * (1 << 32) / clock_hz)

    phase = 0
    dds_sine = 0
    dds_cosine = 0
    rst_cnt = 0

    clk_div = 0
    bit_index = 0
    bclk_fall_d = 0
    bclk = 0
    lrclk = 1
    sdata = 0
    audio_latched = 0

    rx_bclk_d1 = 0
    rx_bclk_d2 = 0
    lrclk_d = 1
    rx_bit_cnt = 0
    shift_reg = 0
    left_data = 0
    data_valid = 0

    acc_i = 0
    acc_q = 0
    sample_count = 0
    state = 0
    start = 1 if False else 0
    max_cycles = int(clock_hz / 1000.0 * 100)

    for cycle in range(max_cycles):
        if cycle == 20_000:
            start = 1
        rst_n = rst_cnt >= 0xFF

        old = dict(
            phase=phase, sine=dds_sine, cosine=dds_cosine, rst_cnt=rst_cnt,
            clk_div=clk_div, bit_index=bit_index, bclk_fall_d=bclk_fall_d,
            bclk=bclk, lrclk=lrclk, sdata=sdata, audio_latched=audio_latched,
            rx_d1=rx_bclk_d1, rx_d2=rx_bclk_d2, lrclk_d=lrclk_d,
            rx_cnt=rx_bit_cnt, shift=shift_reg, left=left_data,
            valid=data_valid, acc_i=acc_i, acc_q=acc_q,
            count=sample_count, state=state,
        )

        # DDS
        if not rst_n:
            phase = dds_sine = dds_cosine = 0
        else:
            phase = (old["phase"] + phase_inc) & 0xFFFFFFFF
            address = old["phase"] >> 22
            dds_sine = rom[address]
            dds_cosine = rom[(address + 256) & 0x3FF]
        if old["rst_cnt"] != 0xFF:
            rst_cnt = old["rst_cnt"] + 1

        # I2S TX with sample-and-hold
        if not rst_n:
            clk_div = bit_index = bclk_fall_d = bclk = sdata = audio_latched = 0
            lrclk = 1
        else:
            bclk_fall_d = int(old["clk_div"] == 31)
            if old["clk_div"] == 15:
                bclk = 1
            elif old["clk_div"] == 31:
                bclk = 0
            else:
                bclk = old["bclk"]
            audio_latched = old["audio_latched"]
            lrclk = old["lrclk"]
            bit_index = old["bit_index"]
            sdata = old["sdata"]
            if old["bclk_fall_d"]:
                if old["bit_index"] == 31:
                    bit_index = 0
                    lrclk ^= 1
                    audio_latched = old["sine"] & 0xFFFF
                    sdata = 0
                else:
                    bit_index = old["bit_index"] + 1
                    bit_number = old["bit_index"] + 1
                    sdata = (
                        (old["audio_latched"] >> (16 - bit_number)) & 1
                        if 1 <= bit_number <= 16
                        else 0
                    )
            clk_div = 0 if old["clk_div"] == 31 else old["clk_div"] + 1

        # I2S RX, synchronized by LRCLK
        rx_bclk_d1 = old["bclk"]
        rx_bclk_d2 = old["rx_d1"]
        bclk_rising = old["rx_d1"] and not old["rx_d2"]
        lrclk_changed = old["lrclk"] ^ old["lrclk_d"]
        lrclk_d = old["lrclk"]
        shift_reg = old["shift"]
        rx_bit_cnt = old["rx_cnt"]
        left_data = old["left"]
        data_valid = 0
        if not rst_n:
            rx_bit_cnt = shift_reg = left_data = 0
        elif lrclk_changed:
            rx_bit_cnt = 0
            shift_reg = 0
        elif bclk_rising:
            candidate = ((old["shift"] << 1) | old["sdata"]) & 0xFFFF
            shift_reg = candidate
            if old["rx_cnt"] == 16:
                if old["lrclk"] == 1:
                    left_data = signed16(candidate)
                    data_valid = 1
            rx_bit_cnt = (old["rx_cnt"] + 1) & 0x1F

        # Top FSM counts sample-valid events, not FPGA clocks.
        enable = data_valid and old["state"] == 1
        clear = old["state"] == 0
        if not rst_n:
            state = sample_count = acc_i = acc_q = 0
        else:
            if clear:
                acc_i = acc_q = 0
            elif enable:
                acc_i = signed48(old["acc_i"] + left_data * old["cosine"])
                acc_q = signed48(old["acc_q"] + left_data * (-old["sine"]))

            if old["state"] == 0:
                if start:
                    state, sample_count = 1, 0
            elif old["state"] == 1:
                if data_valid:
                    if old["count"] + 1 >= sample_count_target:
                        state = 2
                    else:
                        sample_count = old["count"] + 1
            elif old["state"] == 2 and not start:
                state = 0

        if old["state"] == 1 and state == 2:
            break

    sample_rate = clock_hz / 2048.0
    magnitude = math.hypot(acc_i, acc_q) * 2.0 / (sample_count_target * 32767.0)
    return {
        "clock_hz": clock_hz,
        "audio_sample_rate_hz": sample_rate,
        "freq_hz": freq_hz,
        "phase_inc": phase_inc,
        "sample_count_target": sample_count_target,
        "acc_i": acc_i,
        "acc_q": acc_q,
        "magnitude_normalized": magnitude,
        "phase_deg": math.degrees(math.atan2(acc_q, acc_i)),
        "nonzero": bool(acc_i or acc_q),
    }


class DigitalLoopTests(unittest.TestCase):
    def test_fixed_digital_loop_produces_stable_nonzero_iq(self):
        result = run()
        self.assertTrue(result["nonzero"])
        self.assertGreater(abs(result["acc_i"]) + abs(result["acc_q"]), 1_000_000)


if __name__ == "__main__":
    print(run())
