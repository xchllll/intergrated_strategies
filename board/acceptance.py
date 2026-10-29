#!/usr/bin/env python3
"""PYNQ-Z2 minimum acceptance for the FPGA audio measurement chain."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import zipfile
from pathlib import Path

BOARD_DIR = Path(__file__).resolve().parent
# 兼容两种部署位置：仓库中的 board/ 子目录，或直接部署到 project/ 根目录
ROOT_DIR = BOARD_DIR.parent if (BOARD_DIR.parent / "python_model").is_dir() else BOARD_DIR
BIT_PATH = BOARD_DIR / "design_1_wrapper.bit"
XSA_PATH = BOARD_DIR / "design_1_wrapper.xsa"
DEFAULT_OUTPUT = ROOT_DIR / "data" / "acceptance_latest.json"

sys.path.insert(0, str(BOARD_DIR))
sys.path.insert(0, str(ROOT_DIR / "python_model"))

from audio_loopback import AudioLoopback


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


def _write_codec_register(fd: int, register: int, value: int) -> None:
    os.write(fd, bytes([(register >> 8) & 0xFF, register & 0xFF, value]))


def _read_codec_register(fd: int, register: int):
    # ADAU1761 reads need a repeated start: write the 2-byte register address,
    # then read 1 byte in the same I2C_RDWR transfer.  The old os.write+os.read
    # pair issued two separate transactions and could return the last written
    # byte (0x03) or the previous register instead of the real value.
    import ctypes
    import fcntl

    class _I2CMsg(ctypes.Structure):
        _fields_ = [("addr", ctypes.c_uint16),
                    ("flags", ctypes.c_uint16),
                    ("len", ctypes.c_uint16),
                    ("buf", ctypes.POINTER(ctypes.c_uint8))]

    class _I2CRdwr(ctypes.Structure):
        _fields_ = [("msgs", ctypes.POINTER(_I2CMsg)),
                    ("nmsgs", ctypes.c_uint32)]

    w = (ctypes.c_uint8 * 2)((register >> 8) & 0xFF, register & 0xFF)
    r = (ctypes.c_uint8 * 1)()
    msgs = (_I2CMsg * 2)(_I2CMsg(0x3B, 0, 2, w), _I2CMsg(0x3B, 1, 1, r))
    fcntl.ioctl(fd, 0x0707, _I2CRdwr(msgs, 2))
    return r[0]


# Exact sequences from PYNQ libaudio.so (audio_adau1761.cpp), captured by
# disassembling config_audio_codec / select_line_in.  Keeping them here lets us
# reprogram the codec after load_design() through the custom design's PS I2C1,
# without the BaseOverlay audio-codec-ctrl UIO.
CODEC_CORE = (
    (0x400A, 0x00), (0x400C, 0x00), (0x400E, 0xB3), (0x400F, 0xB3),
    (0x4010, 0x01), (0x4014, 0x20), (0x4015, 0x00), (0x4019, 0x33),
    (0x401C, 0x00), (0x401D, 0x00), (0x401E, 0x00), (0x401F, 0x00),
    (0x4023, 0xE5), (0x4024, 0xE5), (0x4029, 0x03), (0x402A, 0x03),
    (0x40F2, 0x01), (0x40F3, 0x01), (0x40F5, 0x01), (0x40F6, 0x01),
    (0x40F9, 0x7F), (0x40FA, 0x03),
)
CODEC_LINE_IN = ((0x400A, 0x01), (0x400B, 0x07),
                 (0x400C, 0x01), (0x400D, 0x07))
CODEC_VERIFY = {0x400A: 0x01, 0x4019: 0x33, 0x4023: 0xE5,
                0x4024: 0xE5, 0x4029: 0x03, 0x402A: 0x03}


def configure_codec_direct() -> dict:
    """Reprogram ADAU1761 after load_design() via the custom design's PS I2C1.

    The BaseOverlay path uses libaudio/UIO; after the custom design is loaded
    that UIO is gone, so the codec must be configured through /dev/i2c-1.
    """
    import fcntl

    fd = os.open("/dev/i2c-1", os.O_RDWR)
    try:
        fcntl.ioctl(fd, 0x0703, 0x3B)
        pll_locked = False
        for _ in range(5):
            _write_codec_register(fd, 0x4000, 0x0E)
            os.write(fd, bytes([0x40, 0x02, 0x02, 0x71,
                                0x02, 0x3C, 0x21, 0x03]))
            time.sleep(0.05)
            os.write(fd, bytes([0x40, 0x02]))
            pll = os.read(fd, 6)
            if len(pll) >= 6 and (pll[5] & 0x02):
                pll_locked = True
                break
        _write_codec_register(fd, 0x4000, 0x0F)
        for register, value in CODEC_CORE:
            _write_codec_register(fd, register, value)
        for register, value in CODEC_LINE_IN:
            _write_codec_register(fd, register, value)
        return {"pll_locked": bool(pll_locked),
                "regs": {reg: _read_codec_register(fd, reg)
                         for reg in CODEC_VERIFY}}
    finally:
        os.close(fd)


def configure_codec() -> dict:
    """Initialize ADAU1761 while the BaseOverlay AXI-IIC is still present."""
    import fcntl
    from pathlib import Path

    from pynq import Overlay

    base_package = Path(__import__("pynq.overlays.base", fromlist=["base"]).__file__)
    base = Overlay(str(base_package.parent / "base.bit"))
    from pynq.ps import Clocks

    if abs(Clocks.fclk0_mhz - 100.0) > 0.01:
        Clocks.fclk0_mhz = 100.0
        time.sleep(0.1)
    audio = getattr(base, "audio_codec_ctrl_0", None)
    if audio is None:
        raise RuntimeError("官方 base overlay 未暴露 audio_codec_ctrl_0")
    audio.iic_index = 1
    audio.uio_index = __import__("pynq.lib.audio", fromlist=["get_uio_index"]).get_uio_index(
        "audio-codec-ctrl"
    )
    audio.sample_rate = 48000
    i2c_fd = os.open("/dev/i2c-1", os.O_RDWR)
    try:
        fcntl.ioctl(i2c_fd, 0x0703, 0x3B)
        _write_codec_register(i2c_fd, 0x4000, 0x0E)
        # 10 MHz MCLK -> 49.152 MHz PLL, written directly to avoid libaudio's
        # blocking PLL-lock helper on rebooted PYNQ images.
        os.write(i2c_fd, bytes([0x40, 0x02, 0x02, 0x71, 0x02, 0x3C, 0x21, 0x03]))
        time.sleep(0.05)
        _write_codec_register(i2c_fd, 0x4000, 0x0F)
    finally:
        os.close(i2c_fd)
    audio._libaudio.config_audio_codec(audio.iic_index)
    if hasattr(audio, "set_volume"):
        audio.set_volume(60)
    if hasattr(audio, "select_line_in"):
        audio.select_line_in()

    return {
        "driver": type(audio).__name__,
        "line_in_selected": True,
        "dac_unmuted": True,
        "pll_rearmed": True,
        "_audio": audio,
        "_base": base,
    }


def load_design(bit_path: Path, clock_hz: float) -> dict:
    from pynq import Overlay
    from pynq.ps import Clocks

    design_path = bit_path
    if bit_path.suffix.lower() == ".bit":
        xsa_candidate = bit_path.with_suffix(".xsa")
        if xsa_candidate.is_file():
            design_path = xsa_candidate
    overlay = Overlay(str(design_path))
    before_mhz = Clocks.fclk0_mhz
    requested_mhz = clock_hz / 1_000_000.0
    if abs(before_mhz - requested_mhz) > 0.01:
        Clocks.fclk0_mhz = requested_mhz
        time.sleep(0.2)
    actual_mhz = Clocks.fclk0_mhz
    if abs(actual_mhz - requested_mhz) > 1.0:
        raise RuntimeError(
            "FCLK_CLK0 设置失败：请求 %.3f MHz，实际 %.3f MHz"
            % (requested_mhz, actual_mhz)
        )
    # The BaseOverlay audio-codec-ctrl UIO is gone once the custom design is
    # loaded; reprogram the codec through the custom design's PS I2C1.
    codec_direct = configure_codec_direct()
    return {
        "_overlay": overlay,
        "loaded_path": str(design_path),
        "clock_before_mhz": before_mhz,
        "clock_actual_mhz": actual_mhz,
        "codec_direct": codec_direct,
    }


def measure_one(freq_hz: float, periods: int) -> dict:
    import calibration as cal
    import ps_ctrl

    phase_inc = ps_ctrl.set_freq(freq_hz)
    window_n = ps_ctrl.set_window(freq_hz, periods)
    started = time.monotonic()
    acc_i, acc_q, window_n = ps_ctrl.measure_once(freq_hz, periods=periods)
    elapsed = time.monotonic() - started
    magnitude, phase = cal.calc_mag_phase(acc_i, acc_q, window_n)
    mask_48 = (1 << 48) - 1
    return {
        "freq_hz": freq_hz,
        "periods_requested": periods,
        "phase_inc": phase_inc,
        "phase_inc_hex": "0x%08X" % phase_inc,
        "window_n": window_n,
        "fpga_clock_hz": ps_ctrl.FPGA_CLK_HZ,
        "audio_sample_rate_hz": ps_ctrl.AUDIO_SAMPLE_RATE_HZ,
        "integration_seconds": window_n / ps_ctrl.AUDIO_SAMPLE_RATE_HZ,
        "elapsed_seconds": elapsed,
        "done": True,
        "acc_i": acc_i,
        "acc_q": acc_q,
        "acc_i_hex": "0x%012X" % (acc_i & mask_48),
        "acc_q_hex": "0x%012X" % (acc_q & mask_48),
        "nonzero": bool(acc_i or acc_q),
        "magnitude_raw_v": magnitude,
        "phase_raw_deg": phase,
        "calibration": cal.calibration_status(),
    }


def measure_audio_loopback(freq_hz: float, repeats: int = 3) -> dict:
    """Measure the real HP+MIC -> LINE IN path through the official codec."""
    import calibration as cal

    driver = AudioLoopback()
    result = driver.measure_repeated(freq_hz, repeats=repeats)
    magnitude = result["amplitude_code_median"] / float(1 << 23)
    phase = result["phase_deg"]
    result.update(
        {
            "freq_hz": float(freq_hz),
            "sample_clock_hz": result["sample_rate_hz"],
            "audio_sample_rate_hz": result["sample_rate_hz"],
            "integration_seconds": result["window_n"] / result["sample_rate_hz"],
            "done": True,
            "magnitude_raw_v": magnitude,
            "magnitude_note": "归一化满量程，不是已校准电压",
            "phase_raw_deg": phase,
            "calibration": cal.calibration_status(),
        }
    )
    return result


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
    parser.add_argument("--freq", type=float, default=1000.0)
    parser.add_argument("--periods", type=int, default=10)
    parser.add_argument(
        "--backend",
        choices=("auto", "audio_loopback", "rtl"),
        default="auto",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--clock-hz", type=float, default=100_000_000)
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--skip-codec-init", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "backend": None,
        "bitstream": None,
        "codec": None,
        "design": None,
        "measurement": None,
        "warnings": [],
    }

    try:
        backend = args.backend
        if backend == "auto":
            backend = "audio_loopback" if sys.platform.startswith("linux") else "rtl"
        report["backend"] = backend
        if backend == "audio_loopback":
            report["codec"] = {
                "driver": "AudioADAU1761",
                "sample_rate": args.sample_rate,
                "line_in_selected": True,
                "dac_unmuted": True,
            }
            report["design"] = {
                "overlay": "BaseOverlay",
                "path": "official PYNQ audio controller",
            }
            report["measurement"] = measure_audio_loopback(
                args.freq,
                repeats=args.repeats,
            )
        else:
            report["bitstream"] = str(ensure_bitstream())
            if args.skip_codec_init:
                report["codec"] = {"driver": "external"}
            else:
                report["codec"] = configure_codec()
            report["design"] = load_design(Path(report["bitstream"]), args.clock_hz)
            report["measurement"] = measure_one(args.freq, args.periods)
    except Exception as exc:
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        print(json.dumps(json_safe(report), ensure_ascii=False, indent=2))
        return 2

    measurement = report["measurement"]
    checks = {"nonzero": bool(measurement["nonzero"])}
    if report["backend"] == "audio_loopback":
        checks["physical_signal"] = measurement.get("amplitude_code", 0.0) > 50_000.0
        checks["frequency_locked"] = abs(
            measurement.get("frequency_error_hz", math.inf)
        ) <= max(30.0, args.freq * 0.02)
        checks["spectral_peak"] = measurement.get("peak_bin_ratio", 0.0) >= 50.0
        checks["repeated_trials"] = (
            measurement.get("valid_trials", 0) >= min(2, args.repeats)
        )
        checks["amplitude_stable"] = (
            measurement.get("amplitude_spread_ratio", math.inf) <= 0.35
        )
    else:
        expected_audio_rate = args.clock_hz / 2048.0
        checks["clock_matches"] = math.isclose(
            measurement["audio_sample_rate_hz"],
            expected_audio_rate,
            rel_tol=0.02,
        )
        checks["magnitude_valid"] = (
            measurement.get("magnitude_raw_v", 0.0) > 0.0001
            or abs(measurement.get("acc_i", 0)) + abs(measurement.get("acc_q", 0)) > 1000
        )
    if not measurement["nonzero"]:
        report["warnings"].append("I/Q 均为 0，未检测到有效模拟回环")
    for check, passed in checks.items():
        if not passed:
            report["warnings"].append("验收项失败：%s" % check)
    report["checks"] = checks
    report["passed"] = all(checks.values())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(json_safe(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(json_safe(report), ensure_ascii=False, indent=2))
    print("\n报告已写入：%s" % args.output)
    return 0 if report["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
