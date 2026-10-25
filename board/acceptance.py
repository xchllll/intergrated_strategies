#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PYNQ-Z2 minimum acceptance: load bitstream, measure one frequency, dump I/Q."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import zipfile
from pathlib import Path

BOARD_DIR = Path(__file__).resolve().parent
ROOT_DIR = BOARD_DIR.parent
BIT_PATH = BOARD_DIR / "design_1_wrapper.bit"
XSA_PATH = BOARD_DIR / "design_1_wrapper.xsa"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "acceptance_latest.json"

sys.path.insert(0, str(BOARD_DIR))
sys.path.insert(0, str(ROOT_DIR / "python_model"))


def ensure_bitstream() -> Path:
    if BIT_PATH.is_file():
        return BIT_PATH
    if not XSA_PATH.is_file():
        raise FileNotFoundError("找不到 %s 或 %s" % (BIT_PATH, XSA_PATH))

    with zipfile.ZipFile(XSA_PATH) as archive:
        member = "design_1_wrapper.bit"
        if member not in archive.namelist():
            raise FileNotFoundError("%s 内没有 %s" % (XSA_PATH, member))
        BIT_PATH.write_bytes(archive.read(member))
    return BIT_PATH


def init_codec(sample_rate: int) -> dict:
    """Use the official base overlay once because the custom bitstream has no IIC."""
    from pynq.overlays.base import BaseOverlay

    base = BaseOverlay("base.bit")
    audio = getattr(base, "audio", None)
    if audio is None:
        # Some PYNQ releases expose an Audio wrapper; others bind the
        # AudioADAU1761 driver directly onto BaseOverlay.audio.
        from pynq.lib.audio import Audio

        audio = Audio(base)
    audio.configure(sample_rate)
    input_selected = False
    if hasattr(audio, "select_line_in"):
        audio.select_line_in()
        input_selected = True
    return {
        "driver": type(audio).__name__,
        "sample_rate": sample_rate,
        "line_in_selected": input_selected,
        "_audio_object": audio,
        "_base_overlay": base,
    }


def load_custom_overlay(bit_path: Path):
    from pynq import Overlay

    return Overlay(str(bit_path))


def measure_one(freq_hz: float, periods: int) -> dict:
    import calibration as cal
    import ps_ctrl

    phase_inc = ps_ctrl.set_freq(freq_hz)
    window_n = ps_ctrl.set_window(freq_hz, periods)
    started = time.monotonic()
    acc_i, acc_q, window_n = ps_ctrl.measure_once(freq_hz, periods=periods)
    elapsed = time.monotonic() - started
    magnitude, phase = cal.calc_mag_phase(acc_i, acc_q, window_n)
    calibration_status = cal.calibration_status()
    mask_48 = (1 << 48) - 1
    return {
        "freq_hz": freq_hz,
        "periods_requested": periods,
        "phase_inc": phase_inc,
        "phase_inc_hex": "0x%08X" % phase_inc,
        "window_n": window_n,
        "sample_clock_hz": ps_ctrl.SAMPLE_CLK,
        "integration_seconds": window_n / ps_ctrl.SAMPLE_CLK,
        "elapsed_seconds": elapsed,
        "done": True,
        "acc_i": acc_i,
        "acc_q": acc_q,
        "acc_i_hex": "0x%012X" % (acc_i & mask_48),
        "acc_q_hex": "0x%012X" % (acc_q & mask_48),
        "nonzero": bool(acc_i or acc_q),
        "magnitude_raw_v": magnitude,
        "phase_raw_deg": phase,
        "calibration": calibration_status,
    }


def json_safe(value):
    if isinstance(value, dict):
        return {
            key: json_safe(item)
            for key, item in value.items()
            if not key.startswith("_")
        }
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="PYNQ-Z2 最低档验收")
    parser.add_argument("--freq", type=float, default=1000.0, help="单点测量频率（Hz）")
    parser.add_argument("--periods", type=int, default=10, help="积分周期数")
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=48000,
        help="传给官方 Audio.configure 的采样率",
    )
    parser.add_argument(
        "--skip-codec-init",
        action="store_true",
        help="codec 已经由同一启动会话初始化时跳过 BaseOverlay 配置",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bitstream": ensure_bitstream(),
        "codec": None,
        "overlay_loaded": False,
        "measurement": None,
        "warnings": [],
    }

    try:
        if not args.skip_codec_init:
            report["codec"] = init_codec(args.sample_rate)
        else:
            report["codec"] = {
                "driver": "external",
                "sample_rate": args.sample_rate,
                "line_in_selected": None,
            }

        load_custom_overlay(report["bitstream"])
        report["overlay_loaded"] = True
        report["measurement"] = measure_one(args.freq, args.periods)
    except Exception as exc:
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        report["traceback_hint"] = (
            "确认板子已从 PYNQ 镜像启动、PYNQ_DEMOD_CLK_HZ 与 XSA FCLK_CLK0 一致，"
            "并检查 3.5 mm 线是否连接 HP+MIC 与 LINE IN。"
        )
        print(json.dumps(json_safe(report), ensure_ascii=False, indent=2))
        return 2

    measurement = report["measurement"]
    clock_hz = measurement["sample_clock_hz"]
    derived_i2s_hz = clock_hz / 32.0 / 64.0
    report["derived_i2s_sample_rate_hz"] = derived_i2s_hz
    if not math.isclose(derived_i2s_hz, args.sample_rate, rel_tol=0.03):
        report["warnings"].append(
            "I2S 派生采样率约 %.1f Hz，与 codec 配置 %d Hz 不一致；"
            "若短接后数据异常，优先检查时钟链。"
            % (derived_i2s_hz, args.sample_rate)
        )
    if not measurement["nonzero"]:
        report["warnings"].append(
            "I/Q 均为 0：done 已到达，但没有采到有效信号；检查 codec、LINE IN 和短接线。"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(json_safe(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(json_safe(report), ensure_ascii=False, indent=2))
    print("\n报告已写入：%s" % args.output)
    return 0 if measurement["nonzero"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
