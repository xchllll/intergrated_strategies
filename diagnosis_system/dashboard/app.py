from __future__ import annotations

import csv
import math
import os
import platform
import sys
import time
from pathlib import Path

# 【永久修复 2】必须在 import pynq 之前让 XILINX_XRT 生效；
# 否则 PYNQ 3.1 不会注册 EmbeddedDevice，且空设备列表会被永久缓存。
os.environ.setdefault("XILINX_XRT", "/usr")

from flask import Flask, jsonify, render_template, request

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "board"))
sys.path.insert(0, str(ROOT_DIR / "python_model"))
sys.path.insert(0, str(ROOT_DIR / "data"))

app = Flask(__name__)

DEMO_CSV = ROOT_DIR / "data" / "dataset_rc_lpf.csv"
SWEEP_POINTS = 240
AUDIO_SWEEP_POINTS = 12
AUDIO_LOOPBACK_DRIVER = None

# 【修复 3】全局标志位：保证自定义 bitstream 只在第一次扫频时烧录
_RTL_DESIGN_LOADED = False


def _runtime_source(mode: str = "rtl") -> dict:
    if platform.system() == "Windows":
        return {
            "mode": "demo_csv",
            "is_real": False,
            "label": "Windows 演示数据",
            "detail": str(DEMO_CSV.relative_to(ROOT_DIR)),
        }
    
    if mode == "audio":
        return {
            "mode": "pynq_audio_loopback",
            "is_real": True,
            "label": "PYNQ 官方音频闭环实测",
            "detail": "DDS 音频激励 -> HP+MIC -> LINE IN -> 48 kHz 采样",
        }
    
    return {
        "mode": "pynq_rtl",
        "is_real": True,
        "label": "自研 PL 解调实测",
        "detail": "DDS -> I2S -> ADAU1761 -> DUT -> I2S RX -> PL demod",
    }


def _calibration_state(calibration_module, cal_path: str = "direct") -> dict:
    """【修复 2】根据当前路径（direct/buffer）如实返回校准状态"""
    try:
        calibration_module.ensure_calibration_loaded()
        
        # 检查底层表里是否真的存在对应路径的校准数据
        has_path_data = False
        if hasattr(calibration_module, "CAL_TABLE") and calibration_module.CAL_TABLE:
            for row in calibration_module.CAL_TABLE:
                if row.get(cal_path):
                    has_path_data = True
                    break
                    
        if has_path_data:
            return {"calibrated": True, "message": f"已加载 {cal_path} 校准表"}
        else:
            return {"calibrated": False, "message": f"未找到 {cal_path} 路径的校准数据，当前为原始测量"}
    except Exception as exc:
        return {"calibrated": False, "message": f"校准状态读取失败：{exc}"}


def _load_demo_csv() -> dict:
    data = {"freqs": [], "mag_db": [], "phase_deg": [],
            "mag_v_raw": [], "phase_deg_raw": []}
    if not DEMO_CSV.is_file():
        raise FileNotFoundError("找不到演示数据：%s" % DEMO_CSV)

    with DEMO_CSV.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            mag_db = float(row["mag"])
            phase = float(row["phase"])
            data["freqs"].append(float(row["freq"]))
            data["mag_db"].append(mag_db)
            data["phase_deg"].append(phase)
            data["mag_v_raw"].append(
                10.0 ** (mag_db / 20.0) if mag_db > -90.0 else 0.0)
            data["phase_deg_raw"].append(phase)

    data["phase_valid"] = [1 if v > 0 else 0 for v in data["mag_v_raw"]]
    if not data["freqs"]:
        raise ValueError("演示 CSV 为空：%s" % DEMO_CSV)
    return data


def _log_spaced_freqs(start_hz: float, stop_hz: float, count: int) -> list[float]:
    if start_hz <= 0 or stop_hz <= start_hz or count < 2:
        raise ValueError("扫频范围或点数无效")
    ratio = (stop_hz / start_hz) ** (1.0 / (count - 1))
    return [start_hz * ratio**index for index in range(count)]


def _ensure_rtl_design():
    """【修复 3】防止每次扫频都重烧，只在第一次调用"""
    global _RTL_DESIGN_LOADED
    if _RTL_DESIGN_LOADED:
        return
    import acceptance
    # 顺序绝对不能反：configure_codec 会加载 base.bit，把自定义设计冲掉
    acceptance.configure_codec()
    acceptance.load_design(acceptance.ensure_bitstream(), 1e8)
    _RTL_DESIGN_LOADED = True


def _current_dac_volume():
    """读 CODEC_CORE 里 0x402B(DAC 数字音量) 的值，供校准表元数据比对。"""
    try:
        import acceptance
        for reg, val in getattr(acceptance, "CODEC_CORE", ()):
            if reg == 0x402B:
                return val
    except Exception:  # noqa: BLE001 非板端环境取不到也无所谓
        pass
    return None


def _run_rtl_hardware_sweep(use_buffer: bool) -> tuple[dict, dict, list[str]]:
    import calibration as cal
    import ps_ctrl

    cal.ensure_calibration_loaded()
    cal.note_driver(dac_volume=_current_dac_volume())
    cal_path_str = "buffer" if use_buffer else "direct"
    cal_status = _calibration_state(cal, cal_path_str) # 使用修复后的状态函数
    
    cal_path = cal.CAL_PATH_BUFFER if use_buffer else cal.CAL_PATH_DIRECT

    data = {"freqs": [], "mag_db": [], "phase_deg": [],
            "mag_v_raw": [], "phase_deg_raw": []}
    warnings = []
    start_hz = max(20.0, ps_ctrl.SAFE_MIN_FREQ_HZ)
    if start_hz > 20.0:
        warnings.append("为避免 48 位累加器在低频溢满量程，板端扫频从 %.1f Hz 开始。" % start_hz)
        
    for freq in _log_spaced_freqs(start_hz, 20000.0, SWEEP_POINTS):
        acc_i, acc_q, window_n = ps_ctrl.measure_once(freq, periods=10)
        mag, phase = cal.calc_mag_phase(acc_i, acc_q, window_n)
        mag_cal, phase_cal = cal.apply_calibration(mag, phase, freq, path=cal_path)
        data["freqs"].append(freq)
        data["mag_db"].append(20.0 * math.log10(mag_cal) if mag_cal > 0 else -99.0)
        data["phase_deg"].append(phase_cal)
        data["mag_v_raw"].append(mag)
        data["phase_deg_raw"].append(phase)

    data["phase_valid"] = [1 if cal.phase_is_valid(v) else 0
                           for v in data["mag_v_raw"]]
    if not data["freqs"]:
        raise RuntimeError("硬件扫频没有返回任何频点")
    return data, cal_status, warnings


def _log_frequencies(start_hz: float, stop_hz: float, count: int) -> list[float]:
    if start_hz <= 0 or stop_hz <= start_hz or count < 2:
        raise ValueError("音频扫频范围或点数无效")
    step = math.log(stop_hz / start_hz) / (count - 1)
    return [start_hz * math.exp(index * step) for index in range(count)]


def _get_audio_driver(force_reinit: bool = False):
    global AUDIO_LOOPBACK_DRIVER, _RTL_DESIGN_LOADED
    if force_reinit or AUDIO_LOOPBACK_DRIVER is None:
        # 【P0-2 修复】只要初始化音频，说明 PL 被 reset 了，必须把标志位拉回 False
        _RTL_DESIGN_LOADED = False
        
        from audio_loopback import AudioLoopback
        AUDIO_LOOPBACK_DRIVER = AudioLoopback()
        AUDIO_LOOPBACK_DRIVER.initialize()
    return AUDIO_LOOPBACK_DRIVER


def _run_audio_loopback_sweep() -> tuple[dict, dict, list[str]]:
    import calibration as cal
    cal.ensure_calibration_loaded()
    # 音频闭环默认走 direct 的校准，但我们强制返回未校准提示，因为音频特性差异太大
    calibration = _calibration_state(cal, "direct")
    warnings = []
    if not calibration.get("calibrated"):
        warnings.append("校准表为空；曲线为官方音频闭环的未校准原始测量。")

    data = {"freqs": [], "mag_db": [], "phase_deg": [],
            "mag_v_raw": [], "phase_deg_raw": []}
    # 【修复 4】每次进入 audio 模式强制重新初始化，防止之前 RTL 加载把 UIO 冲掉
    driver = _get_audio_driver(force_reinit=True)
    
    for freq in _log_frequencies(100.0, 10000.0, AUDIO_SWEEP_POINTS):
        measurement = driver.capture(freq)
        if (measurement["peak_bin_ratio"] < 50.0 or abs(measurement["frequency_error_hz"]) > max(30.0, freq * 0.02)):
            warnings.append("跳过无效频点 %.1f Hz" % freq)
            continue
        magnitude = measurement["amplitude_full_scale"]
        phase = measurement["phase_deg"]
        magnitude_raw, phase_raw = magnitude, phase
        magnitude, phase = cal.apply_calibration(magnitude, phase, freq, path=cal.CAL_PATH_DIRECT)
        data["freqs"].append(float(freq))
        data["mag_db"].append(20.0 * math.log10(magnitude) if magnitude > 0.0 else -99.0)
        data["phase_deg"].append(float(phase))
        data["mag_v_raw"].append(float(magnitude_raw))
        data["phase_deg_raw"].append(float(phase_raw))
    data["phase_valid"] = [1 if cal.phase_is_valid(v) else 0
                           for v in data["mag_v_raw"]]
    if not data["freqs"]:
        raise RuntimeError("音频闭环扫频没有得到任何有效频点")
    return data, calibration, warnings


def _run_hardware_sweep(mode: str, cal_path: str) -> tuple[dict, dict, list[str]]:
    if mode == "rtl":
        _ensure_rtl_design()
        return _run_rtl_hardware_sweep(cal_path == "buffer")
    else:
        return _run_audio_loopback_sweep()


def _run_ai(data: dict, is_real: bool, data_source: str) -> tuple[dict | None, str | None]:
    # 【修复 5】audio 模式强行拦截 AI，绝不用 12 个声卡点去跑 240 点的模型
    if data_source == "pynq_audio_loopback":
        return {
            "label": "不适用",
            "confidence": 0.0,
            "top3": [],
            "model_note": "【拦截】官方音频闭环数据无物理相位参考且频点太少，不执行 AI 拓扑推理。"
        }, None

    try:
        from diagnosis_system.ml.infer import predict_topology
        result = predict_topology(data, data_source=data_source)
        return result, None
    except Exception as exc:
        return None, str(exc)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def status():
    source = _runtime_source()
    # 【P2 修复】从 URL 参数读取 cal_path，默认 direct
    req_cal_path = request.args.get("cal_path", "direct")
    if req_cal_path not in ["direct", "buffer"]:
        req_cal_path = "direct"
        
    try:
        import calibration as cal
        calibration = _calibration_state(cal, req_cal_path)
    except Exception as exc:
        calibration = {"calibrated": False, "message": str(exc)}
    return jsonify(
        {
            "status": "success",
            "source": source,
            "calibration": calibration,
            "points": (AUDIO_SWEEP_POINTS if source["mode"] == "pynq_audio_loopback" else SWEEP_POINTS),
        }
    )


@app.route("/api/iq", methods=["GET"])
def iq_acceptance():
    source = _runtime_source()
    if source["mode"] != "pynq_audio_loopback":
        return jsonify({"status": "error", "msg": "当前不是 PYNQ 官方音频闭环环境"}), 400
    try:
        frequency_hz = float(request.args.get("freq", "1000"))
        repeats = max(1, min(int(request.args.get("repeats", "3")), 5))
        measurement = _get_audio_driver(force_reinit=True).measure_repeated(frequency_hz, repeats=repeats)
        return jsonify({"status": "success", "source": source, "measurement": measurement})
    except Exception as exc:
        app.logger.exception("I/Q 验收失败")
        return jsonify({"status": "error", "msg": str(exc)}), 500


def _record_history(data, source):
    """把本次扫频的未校准数据追加到 data/history.csv。

    gen_dataset.append_history 只接受 mag_v_raw / phase_deg_raw，
    不会把网页上可能已校准的 mag_db / phase_deg 写进历史。
    """
    try:
        import gen_dataset
        path = gen_dataset.append_history(
            data,
            mode=source.get("mode", "unknown"),
        )
        return path, None
    except Exception as exc:
        app.logger.exception("历史数据写入失败")
        return None, str(exc)


@app.route("/api/start_sweep", methods=["POST"])
def start_sweep():
    payload = request.get_json(silent=True) or {}
    
    # 【修复 1】严格白名单校验
    mode = str(payload.get("mode", "rtl")).lower()
    if mode not in ["rtl", "audio"]:
        mode = "rtl"
        
    cal_path = str(payload.get("cal_path", "direct")).lower()
    if cal_path not in ["direct", "buffer"]:
        cal_path = "direct"
        
    if payload.get("use_buffer") is True:
        cal_path = "buffer"

    source = _runtime_source(mode)

    try:
        if source["mode"] == "demo_csv":
            time.sleep(0.2)
            data = _load_demo_csv()
            calibration = {"calibrated": False, "message": "演示 CSV 未经过本次硬件校准"}
            sweep_warnings = []
        else:
            data, calibration, sweep_warnings = _run_hardware_sweep(mode, cal_path)
            
    except Exception as exc:
        app.logger.exception("扫频失败")
        return jsonify({"status": "error", "msg": str(exc)}), 500

    # 每次网页扫频都把数据追加到 data/history.csv（未校准 + 已校准两套值）
    data["calibrated"] = bool(calibration.get("calibrated"))
    _history_path, history_error = _record_history(data, source)

    ai_result, ai_error = _run_ai(data, source["is_real"], source["mode"])
    
    response = {
        "status": "success",
        "source": source,
        "calibration": calibration,
        "data": data,
        "ai": ai_result,
        "warnings": [],
    }
    
    if sweep_warnings:
        response["warnings"].extend(sweep_warnings)
    if ai_error:
        response["warnings"].append("AI 推理未完成：%s" % ai_error)
    if history_error:
        response["warnings"].append("历史数据写入失败：%s" % history_error)
    if not source["is_real"]:
        response["warnings"].append("当前展示的是演示 CSV，不是 PYNQ 硬件测量结果。")
    if source["is_real"] and not calibration.get("calibrated"):
        response["warnings"].append("校准表为空或缺少对应路径；曲线为未校准原始测量。")
        
    return jsonify(response)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)