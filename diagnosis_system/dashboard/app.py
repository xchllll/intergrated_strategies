from __future__ import annotations

import csv
import math
import platform
import sys
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "board"))
sys.path.insert(0, str(ROOT_DIR / "python_model"))
sys.path.insert(0, str(ROOT_DIR / "data"))

app = Flask(__name__)

DEMO_CSV = ROOT_DIR / "data" / "dataset_rc_lpf.csv"
SWEEP_POINTS = 240
AUDIO_SWEEP_POINTS = 12
AUDIO_LOOPBACK_DRIVER = None


def _runtime_source() -> dict:
    """Return the active acquisition path without pretending demo data is real."""
    if platform.system() == "Windows":
        return {
            "mode": "demo_csv",
            "is_real": False,
            "label": "Windows 演示数据",
            "detail": str(DEMO_CSV.relative_to(ROOT_DIR)),
        }
    return {
        "mode": "pynq_audio_loopback",
        "is_real": True,
        "label": "PYNQ 官方音频闭环实测",
        "detail": "DDS 音频激励 -> HP+MIC -> LINE IN -> 48 kHz 采样",
    }


def _calibration_state(calibration_module) -> dict:
    try:
        calibration_module.ensure_calibration_loaded()
        return calibration_module.calibration_status()
    except Exception as exc:
        return {
            "calibrated": False,
            "message": "校准状态读取失败：%s" % exc,
        }


def _load_demo_csv() -> dict:
    data = {"freqs": [], "mag_db": [], "phase_deg": []}
    if not DEMO_CSV.is_file():
        raise FileNotFoundError("找不到演示数据：%s" % DEMO_CSV)

    with DEMO_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            data["freqs"].append(float(row["freq"]))
            data["mag_db"].append(float(row["mag"]))
            data["phase_deg"].append(float(row["phase"]))

    if not data["freqs"]:
        raise ValueError("演示 CSV 为空：%s" % DEMO_CSV)
    return data


def _log_spaced_freqs(start_hz: float, stop_hz: float, count: int) -> list[float]:
    if start_hz <= 0 or stop_hz <= start_hz or count < 2:
        raise ValueError("扫频范围或点数无效")
    ratio = (stop_hz / start_hz) ** (1.0 / (count - 1))
    return [start_hz * ratio**index for index in range(count)]


def _run_rtl_hardware_sweep(use_buffer: bool) -> tuple[dict, dict, list[str]]:
    import calibration as cal
    import ps_ctrl

    cal.ensure_calibration_loaded()
    cal_status = cal.calibration_status()
    cal_path = cal.CAL_PATH_BUFFER if use_buffer else cal.CAL_PATH_DIRECT

    data = {"freqs": [], "mag_db": [], "phase_deg": []}
    warnings = []
    start_hz = max(20.0, ps_ctrl.SAFE_MIN_FREQ_HZ)
    if start_hz > 20.0:
        warnings.append(
            "为避免 48 位累加器在低频溢满量程，板端扫频从 %.1f Hz 开始。"
            % start_hz
        )
    for freq in _log_spaced_freqs(start_hz, 20000.0, SWEEP_POINTS):
        acc_i, acc_q, window_n = ps_ctrl.measure_once(freq, periods=10)
        mag, phase = cal.calc_mag_phase(acc_i, acc_q, window_n)
        mag_cal, phase_cal = cal.apply_calibration(
            mag, phase, freq, path=cal_path
        )
        data["freqs"].append(freq)
        data["mag_db"].append(
            20.0 * math.log10(mag_cal) if mag_cal > 0 else -99.0
        )
        data["phase_deg"].append(phase_cal)

    if not data["freqs"]:
        raise RuntimeError("硬件扫频没有返回任何频点")
    return data, cal_status, warnings


def _log_frequencies(start_hz: float, stop_hz: float, count: int) -> list[float]:
    if start_hz <= 0 or stop_hz <= start_hz or count < 2:
        raise ValueError("音频扫频范围或点数无效")
    step = math.log(stop_hz / start_hz) / (count - 1)
    return [start_hz * math.exp(index * step) for index in range(count)]


def _get_audio_driver():
    global AUDIO_LOOPBACK_DRIVER
    from audio_loopback import AudioLoopback

    if AUDIO_LOOPBACK_DRIVER is None:
        AUDIO_LOOPBACK_DRIVER = AudioLoopback()
        AUDIO_LOOPBACK_DRIVER.initialize()
    return AUDIO_LOOPBACK_DRIVER


def _run_audio_loopback_sweep() -> tuple[dict, dict, list[str]]:
    """Measure real 1k-class loopback points through the official PYNQ codec."""
    import calibration as cal

    cal.ensure_calibration_loaded()
    calibration = cal.calibration_status()
    warnings = []
    if not calibration.get("calibrated"):
        warnings.append(
            "校准表为空；曲线为官方音频闭环的未校准原始测量。"
        )

    data = {"freqs": [], "mag_db": [], "phase_deg": []}
    driver = _get_audio_driver()
    for freq in _log_frequencies(100.0, 10000.0, AUDIO_SWEEP_POINTS):
        measurement = driver.capture(freq)
        if (
            measurement["peak_bin_ratio"] < 50.0
            or abs(measurement["frequency_error_hz"]) > max(30.0, freq * 0.02)
        ):
            warnings.append("跳过无效频点 %.1f Hz" % freq)
            continue
        magnitude = measurement["amplitude_full_scale"]
        phase = measurement["phase_deg"]
        magnitude, phase = cal.apply_calibration(
            magnitude,
            phase,
            freq,
            path=cal.CAL_PATH_DIRECT,
        )
        data["freqs"].append(float(freq))
        data["mag_db"].append(
            20.0 * math.log10(magnitude) if magnitude > 0.0 else -99.0
        )
        data["phase_deg"].append(float(phase))
    if not data["freqs"]:
        raise RuntimeError("音频闭环扫频没有得到任何有效频点")
    return data, calibration, warnings


def _run_hardware_sweep(use_buffer: bool) -> tuple[dict, dict, list[str]]:
    if use_buffer:
        return _run_rtl_hardware_sweep(use_buffer)
    return _run_audio_loopback_sweep()


def _run_ai(data: dict, is_real: bool) -> tuple[dict | None, str | None]:
    try:
        from diagnosis_system.ml.infer import predict_topology

        result = predict_topology(data)
        result["model_note"] = (
            "模型推理基于当前硬件扫频数据"
            if is_real
            else "演示 CSV + 预训练演示模型，不代表硬件实测"
        )
        return result, None
    except Exception as exc:
        return None, str(exc)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def status():
    source = _runtime_source()
    try:
        import calibration as cal

        calibration = _calibration_state(cal)
    except Exception as exc:
        calibration = {"calibrated": False, "message": str(exc)}
    return jsonify(
        {
            "status": "success",
            "source": source,
            "calibration": calibration,
            "points": (
                AUDIO_SWEEP_POINTS
                if source["mode"] == "pynq_audio_loopback"
                else SWEEP_POINTS
            ),
        }
    )


@app.route("/api/iq", methods=["GET"])
def iq_acceptance():
    source = _runtime_source()
    if source["mode"] != "pynq_audio_loopback":
        return jsonify(
            {
                "status": "error",
                "msg": "当前不是 PYNQ 官方音频闭环环境",
            }
        ), 400
    try:
        frequency_hz = float(request.args.get("freq", "1000"))
        repeats = max(1, min(int(request.args.get("repeats", "3")), 5))
        measurement = _get_audio_driver().measure_repeated(
            frequency_hz,
            repeats=repeats,
        )
        return jsonify(
            {
                "status": "success",
                "source": source,
                "measurement": measurement,
            }
        )
    except Exception as exc:
        app.logger.exception("I/Q 验收失败")
        return jsonify({"status": "error", "msg": str(exc)}), 500


@app.route("/api/start_sweep", methods=["POST"])
def start_sweep():
    payload = request.get_json(silent=True) or {}
    use_buffer = bool(payload.get("use_buffer", False))
    source = _runtime_source()

    try:
        if source["mode"] == "demo_csv":
            time.sleep(0.2)
            data = _load_demo_csv()
            calibration = {
                "calibrated": False,
                "message": "演示 CSV 未经过本次硬件校准",
            }
        else:
            data, calibration, sweep_warnings = _run_hardware_sweep(use_buffer)
    except Exception as exc:
        app.logger.exception("扫频失败")
        return jsonify({"status": "error", "msg": str(exc)}), 500

    ai_result, ai_error = _run_ai(data, source["is_real"])
    response = {
        "status": "success",
        "source": source,
        "calibration": calibration,
        "data": data,
        "ai": ai_result,
        "warnings": [],
    }
    if source["mode"] in ("pynq_hardware", "pynq_audio_loopback"):
        response["warnings"].extend(sweep_warnings)
    if ai_error:
        response["warnings"].append("AI 推理未完成：%s" % ai_error)
    if not source["is_real"]:
        response["warnings"].append(
            "当前展示的是演示 CSV，不是 PYNQ 硬件测量结果。"
        )
    if source["is_real"] and not calibration.get("calibrated"):
        response["warnings"].append(
            "校准表为空；曲线为未校准原始测量，不能宣称已校准。"
        )
    return jsonify(response)


if __name__ == "__main__":
    if platform.system() != "Windows":
        from audio_loopback import AudioLoopback

        AUDIO_LOOPBACK_DRIVER = AudioLoopback()
        AUDIO_LOOPBACK_DRIVER.initialize()
    app.run(host="0.0.0.0", port=5000, debug=False)
