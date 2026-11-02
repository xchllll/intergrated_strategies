from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import platform
import sys
import threading
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

# 持久化 PL/I2S：板子启动后由 board/start_pl_i2s.sh 一次性加载；
# dashboard 重启时只校验 marker，不再碰 Overlay/Codec/I2S。
PL_READY_MARKER = Path("/tmp/pl_i2s_ready.json")
PL_XSA = ROOT_DIR / "board" / "design_1_wrapper.xsa"
PL_BIT = ROOT_DIR / "board" / "design_1_wrapper.bit"

# 一键短接校准的后台状态；不再有“参考扫频”。
SHORT_CAL_MAX_ROUNDS = 10
_SHORT_CAL_LOCK = threading.Lock()
_SHORT_CAL_STATE = {
    "running": False,
    "done": False,
    "error": None,
    "message": "",
    "progress": 0,
    "total": 0,
    "repeat": 0,
    "result": None,
}

_SWEEP_LOCK = threading.Lock()
_SWEEP_STATE = {
    "running": False,
    "done": False,
    "error": None,
    "message": "",
    "progress": 0,
    "total": 0,
}


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
        mode = {}
        if hasattr(calibration_module, "load_cal_mode"):
            try:
                mode = calibration_module.load_cal_mode()
            except Exception:
                mode = {}
        if mode.get(cal_path) is False:
            return {"calibrated": False,
                    "message": f"{cal_path} 校准已在 cal_mode.json 中禁用"}

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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _persistent_pl_ready() -> bool:
    """检查 board/start_pl_i2s.sh 是否已把 PL/I2S 拉起来。

    只读 marker + XSA 哈希，不加载 PL、不配置 Codec、不写任何硬件。
    """
    try:
        info = json.loads(PL_READY_MARKER.read_text(encoding="utf-8"))
        return (
            bool(info.get("ok"))
            and info.get("xsa_sha256") == _sha256(PL_XSA)
            and info.get("bit_sha256") == _sha256(PL_BIT)
        )
    except Exception:
        return False


def _ensure_rtl_design():
    """Dashboard 只附着到常驻 PL，不负责加载 bitstream 或启动 I2S。"""
    global _RTL_DESIGN_LOADED
    if _RTL_DESIGN_LOADED:
        return
    if _persistent_pl_ready():
        _RTL_DESIGN_LOADED = True
        return
    raise RuntimeError(
        "常驻 PL/I2S 尚未初始化。请先停止 dashboard，在板端执行："
        "sudo bash board/start_pl_i2s.sh；之后再启动 dashboard。"
    )


def _current_dac_volume():
    """DAC digital volume is fixed by start_pl_i2s.sh (0x10 = -6 dB).

    Do not import acceptance/pynq here: this runs in a background thread, and
    PYNQ 3.1 needs an asyncio loop in the main thread.  Loading pynq from a
    worker thread produces "There is no current event loop" and can leave the
    device cache unusable.
    """
    return 0x10


def _short_cal_status():
    with _SHORT_CAL_LOCK:
        return dict(_SHORT_CAL_STATE)


def _short_cal_running():
    with _SHORT_CAL_LOCK:
        return bool(_SHORT_CAL_STATE.get("running"))


def _short_cal_set(**kwargs):
    with _SHORT_CAL_LOCK:
        _SHORT_CAL_STATE.update(kwargs)


def _sweep_status():
    with _SWEEP_LOCK:
        return dict(_SWEEP_STATE)


def _sweep_is_running():
    with _SWEEP_LOCK:
        return bool(_SWEEP_STATE.get("running"))


def _sweep_set(**kwargs):
    with _SWEEP_LOCK:
        _SWEEP_STATE.update(kwargs)


def _run_short_calibration_bg(repeat: int):
    """Measure the short and hand raw sweeps to calibrate_path.py.

    This deliberately does not call _record_history(): short calibration data
    must never enter data/history.csv.
    """
    import calibration as cal
    import ps_ctrl
    import calibrate_path

    try:
        _ensure_rtl_design()
        cal.ensure_calibration_loaded()
        cal.note_driver(dac_volume=_current_dac_volume())

        cur_vol = _current_dac_volume()
        old_vol = (cal.CAL_META or {}).get("dac_volume") or 0x10
        try:
            level_scale = float(cur_vol) / float(old_vol) if cur_vol else 1.0
        except Exception:
            level_scale = 1.0

        expected = {}
        for row in cal.CAL_TABLE:
            entry = row.get(cal.CAL_PATH_DIRECT) or {}
            gain = entry.get("mag_gain")
            if gain and gain > 0:
                expected[round(float(row["freq"]), 6)] = float(gain)

        start_hz = max(20.0, ps_ctrl.SAFE_MIN_FREQ_HZ)
        freqs = _log_spaced_freqs(start_hz, 20000.0, SWEEP_POINTS)
        periods, _pinfo = ps_ctrl.build_period_schedule(
            freqs, n_target=1000, budget_s=0.0)

        total = len(freqs) * repeat
        _short_cal_set(running=True, done=False, error=None,
                       message="正在进行短接校准（0/%d）..." % total,
                       progress=0, total=total, repeat=repeat, result=None)

        sweeps = []
        for round_index in range(repeat):
            sweep = []
            for idx, freq in enumerate(freqs):
                acc_i, acc_q, window_n = ps_ctrl.measure_once(
                    freq, periods=periods[idx])
                mag, phase = cal.calc_mag_phase(acc_i, acc_q, window_n)
                if mag <= 1e-15:
                    raise RuntimeError(
                        "短接校准在 %.1f Hz 幅值为 0；短接是否真的接好？" % freq)

                # 短接判据保持不变：只用前 5 点，按 DAC 音量归一化后
                # 与旧短接表的 mag_gain 比较，要求 0.98~1.02。
                if idx < 5 and expected.get(round(freq, 6)):
                    ratio = (mag * expected[round(freq, 6)]) / level_scale
                    if ratio < 0.98 or ratio > 1.02:
                        raise RuntimeError(
                            "检测到不是短接：%.1f Hz 短接幅度比 %.3f"
                            "（应为 1.000），一键短接校准已停止" % (freq, ratio)
                        )

                sweep.append((float(freq), float(mag), float(phase)))
                done = round_index * len(freqs) + idx + 1
                if done % 10 == 0 or done == total:
                    _short_cal_set(
                        progress=done,
                        message="正在进行短接校准（%d/%d）..." % (done, total))

            sweeps.append(sweep)

        result = calibrate_path.build_calibration_table(
            sweeps,
            state=cal.CAL_PATH_DIRECT,
            ref_amp=1.0,
            dac_volume=cur_vol,
            note="short connected; dashboard one-click calibration",
            source="dashboard one-click short calibration",
        )
        _short_cal_set(
            running=False, done=True, error=None,
            message="短接校准完成：%d 点，%d 轮，已更新校准表"
                    % (result["points"], result["repeat"]),
            progress=total, result=result)
    except Exception as exc:
        _short_cal_set(running=False, done=False, error=str(exc),
                       message="短接校准失败: %s" % exc)
        app.logger.exception("一键短接校准失败")


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
        
    freqs = _log_spaced_freqs(start_hz, 20000.0, SWEEP_POINTS)
    # 每点周期数随频率增加（高频更稳），并按时间预算压缩低频段；
    # 与 board/calibrate_path.py 共用同一套调度，保证标定与测量条件一致
    period_list, _pinfo = ps_ctrl.build_period_schedule(
        freqs, budget_s=ps_ctrl.SWEEP_TIME_BUDGET_S)
    for index, freq in enumerate(freqs):
        period = period_list[index]
        acc_i, acc_q, window_n = ps_ctrl.measure_once(freq, periods=period)
        mag, phase = cal.calc_mag_phase(acc_i, acc_q, window_n)
        mag_cal, phase_cal = cal.apply_calibration(mag, phase, freq, path=cal_path)
        data["freqs"].append(freq)
        data["mag_db"].append(20.0 * math.log10(mag_cal) if mag_cal > 0 else -99.0)
        data["phase_deg"].append(phase_cal)
        data["mag_v_raw"].append(mag)
        data["phase_deg_raw"].append(phase)
        _sweep_set(progress=index + 1,
                   message="扫频中（%d/%d）..." % (index + 1, len(freqs)))

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
            "short_calibration": _short_cal_status(),
            "sweep": _sweep_status(),
        }
    )


@app.route("/api/start_short_calibration", methods=["POST"])
def api_start_short_calibration():
    payload = request.get_json(silent=True) or {}
    try:
        repeat = int(payload.get("repeat", 1))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "msg": "扫频轮次必须是整数"}), 400
    if repeat < 1 or repeat > SHORT_CAL_MAX_ROUNDS:
        return jsonify({
            "status": "error",
            "msg": "扫频轮次必须在 1~%d 之间" % SHORT_CAL_MAX_ROUNDS,
        }), 400
    if _short_cal_running():
        return jsonify({"status": "busy",
                        "msg": "短接校准正在进行，请稍候"}), 409
    if _sweep_is_running():
        return jsonify({"status": "busy",
                        "msg": "扫频测量正在进行，请完成后再校准"}), 409
    try:
        _ensure_rtl_design()
    except Exception as exc:
        return jsonify({"status": "error", "msg": str(exc)}), 503
    _short_cal_set(running=True, done=False, error=None,
                   message="准备短接校准...", progress=0,
                   total=SWEEP_POINTS * repeat, repeat=repeat, result=None)
    threading.Thread(target=_run_short_calibration_bg,
                     args=(repeat,), daemon=True).start()
    return jsonify({"status": "started", "repeat": repeat,
                    "total": SWEEP_POINTS * repeat})


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
    if _short_cal_running():
        return jsonify({"status": "busy",
                        "msg": "正在进行一键短接校准，请完成后再测量"}), 409
    if _sweep_is_running():
        return jsonify({"status": "busy",
                        "msg": "上一次扫频仍在进行，请稍候"}), 409
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
    total = AUDIO_SWEEP_POINTS if source["mode"] == "pynq_audio_loopback" else SWEEP_POINTS
    _sweep_set(running=True, done=False, error=None, progress=0,
               total=total, message="准备扫频...")

    try:
        if source["mode"] == "demo_csv":
            time.sleep(0.2)
            data = _load_demo_csv()
            calibration = {"calibrated": False, "message": "演示 CSV 未经过本次硬件校准"}
            sweep_warnings = []
            _sweep_set(progress=total, message="扫频完成")
        else:
            data, calibration, sweep_warnings = _run_hardware_sweep(mode, cal_path)
            
    except Exception as exc:
        _sweep_set(running=False, error=str(exc),
                   message="扫频失败: %s" % exc)
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

    _sweep_set(running=False, done=True, progress=total,
               message="扫频完成（%d/%d）" % (total, total))
    return jsonify(response)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)