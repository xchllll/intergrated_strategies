#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibration.py - 解调后处理与四态校准（幅值/相位还原）【直连/缓冲口径版】【校准闭环版】

输入：ps_ctrl.read_IQ() 返回的 48 位累加值 acc_I / acc_Q，
     以及本次测量实际使用的积分点数 N（ps_ctrl.measure_once() 的返回值）
输出：真实物理幅值（V）与相位（度）

归一化口径（与 board/ps_ctrl.py 统一，改模型时必须同步修改）：
    NORM_FACTOR = N * ADC_GAIN * LO_GAIN
      N         = 本次积分点数（== ps_window_max，由 ps_ctrl 按
                  round(periods * SAMPLE_CLK / f) 计算并原样返回；
                  【关键】不再使用固定值，否则幅值系统性失真）
      ADC_GAIN  = ADC 数字化增益：
                  * 定点模型口径取 10000（模拟 16 位 ADC 的放大倍数）；
                  * 若直接使用编解码器 16 位采样链路（如 PYNQ 音频
                    codec），应改为 1.0，并把 ADC_FULL_SCALE_V 设为
                    codec 输入满量程电压（如 ±1 Vrms）。
      LO_GAIN   = 32767（DDS 正弦表 16 位满量程）
    I_float = acc_I * 2 / NORM_FACTOR
    Q_float = acc_Q * 2 / NORM_FACTOR
    mag     = hypot(I_float, Q_float) * ADC_FULL_SCALE_V  -> 电压（V）
    phase   = degrees(atan2(Q_float, I_float))            -> 度

四态校准（直连 / 缓冲口径，与网页"直连与缓冲校准已在PS端应用"一致）：
    每个频点上用 4 个已知参考状态实测后标定，前两态分别是：
      态1 直连校准（短接）      相位 0°  -> 标定直连路径幅值/相位基准
      态2 缓冲校准（电压跟随器）相位 0°  -> 标定缓冲路径幅值/相位基准
      态3 直连 + 90° 参考相位            -> 参与直连路径相位偏移平均
      态4 直连 + 180° 参考相位           -> 参与直连路径相位偏移平均
    mag_gain     = A_ref / mean(measured_mag)     （真实/测得幅值之比）
    phase_offset = 平均回绕相位差 (θ_ref - phase_meas)
    标定结果按路径（CAL_PATH_DIRECT / CAL_PATH_BUFFER）分别写入
    CAL_TABLE，apply_calibration() 按 path 参数查表线性插值修正，
    与网页端"启用缓冲器(电压跟随)"开关一一对应。

v3（校准闭环，解决"校准表为空且 four_state_calibrate 从未被调用"的缺口）：
    * CAL_TABLE_DEFAULT_PATH  校准表默认落盘/读取路径（data/calibration_table.json）
    * ensure_calibration_loaded()  扫频前调用一次即自动加载已保存的校准表
    * run_four_state_calibration() 四态校准完整流程：逐频点实测 4 个参考态 ->
      填充 CAL_TABLE -> 保存 JSON（此前该函数只定义不执行）
    * calibration_status()    如实上报校准状态，供网页端显示真实校准情况，
      不再出现"已校准"文案但校准表为空的误导
    * apply_calibration()     表为空且默认校准表文件存在时自动加载；警告只提示一次
    注意：真实四态校准依赖参考态硬件（直连/缓冲/+90°/+180° 可切换参考源），
    仓库当前无该硬件驱动，real 流程需由调用方提供 measure_state 回调；
    mock=True 可先跑通软件流程（自检/预演）。
"""

import json
import math
import os
import tempfile

# ---------------------------------------------------------------------------
# 校准路径（与网页端"启用缓冲器(电压跟随)"开关、测量流程一一对应）
# ---------------------------------------------------------------------------
CAL_PATH_DIRECT = "direct"   # 直连校准（短接）：被测点短路，系统自身基线
CAL_PATH_BUFFER = "buffer"   # 缓冲校准（电压跟随器）：信号经缓冲器后进测量链

# ---------------------------------------------------------------------------
# 定点系数（与 board/ps_ctrl.py 同步；改动模型时必须同步修改）
# ---------------------------------------------------------------------------
# 兜底积分点数：仅当调用方漏传 N 时使用。正常路径一律传
# ps_ctrl.measure_once() 返回的实际窗长（随频率变化），不得用固定值。
DEFAULT_INTEGRATION_N = 10000

ADC_GAIN = 1.0    # ADC 数字化增益（定点模型口径；codec 直采链路改为 1.0）
LO_GAIN = 32767     # DDS 正弦表满量程

ADC_FULL_SCALE_V = 1.0   # ADC 满量程电压（V）：按实际前端电路填写，默认 ±1V 占位

# ---------------------------------------------------------------------------
# 校准表格式 v2：复数系数 + 元数据 + 有效性判据
# ---------------------------------------------------------------------------
CAL_FORMAT_VERSION = 2

# 校准表元数据（与表一起落盘）：created / ref_amp / dac_volume / sample_rate_hz / ...
CAL_META = {}

# 当前驱动的实际电平，由调用方(网页/脚本)在启动时登记，用于和表里记录的值比对
DRIVER_STATE = {}

# 幅值低于该值（ADC 码）视为没有可用信号，相位不可信（满量程 32767，16 码约 -66 dBFS）
MIN_VALID_MAG = 16.0

# ---------------------------------------------------------------------------
# 校准表（上板实测后由 run_four_state_calibration() 自动填充）
# 每频点一条记录（dict），包含两条路径的标定结果：
#   {"freq": 1000.0,
#    "direct": {"mag_gain": 1.0, "phase_offset_deg": 0.0},   # 直连（短接）
#    "buffer": {"mag_gain": 1.0, "phase_offset_deg": 0.0}}   # 缓冲（电压跟随）
# ---------------------------------------------------------------------------
CAL_TABLE = []

# 默认校准表文件路径（仓库 data/ 目录），可由环境/调用方覆盖
CAL_TABLE_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "calibration_table.json")

# 四态参考态顺序（与 four_state_calibrate / run_four_state_calibration 一致）
REF_STATE_NAMES = ("direct_0", "buffer_0", "direct_90", "direct_180")

# 警告去重：同一类警告只打印一次，避免 240 个频点刷屏
_WARNED = set()


def _warn_once(key, message):
    """带 key 去重的单次警告。"""
    if key not in _WARNED:
        _WARNED.add(key)
        print("WARNING: " + message)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _wrap_180(deg):
    """把角度回绕到 (-180, 180]。"""
    deg = math.fmod(deg, 360.0)
    if deg > 180.0:
        deg -= 360.0
    elif deg <= -180.0:
        deg += 360.0
    return deg


def _mean_angle(deg_list):
    """角度平均（单位圆平均，避免 179° 与 -179° 平均成 0° 的错误）。"""
    n = len(deg_list)
    x = sum(math.cos(math.radians(d)) for d in deg_list) / n
    y = sum(math.sin(math.radians(d)) for d in deg_list) / n
    return _wrap_180(math.degrees(math.atan2(y, x)))


def norm_factor(n):
    """按实际积分点数 N 计算归一化因子（口径与 ps_ctrl.py 一致）。"""
    return n * ADC_GAIN * LO_GAIN


def phase_is_valid(mag, min_mag=None):
    """幅值够不够大：低于门限说明没有可用信号，此时相位只是噪声。"""
    if mag is None:
        return False
    return mag > (MIN_VALID_MAG if min_mag is None else min_mag)


def make_cal_entry(mag_gain, phase_offset_deg):
    """构造一条 v2 校准记录：复数系数为权威值，幅值/相位字段便于人读与兼容。

    约定 H_dut = H_meas * H_cal，其中 H_cal = mag_gain * exp(j*phase_offset)。
    """
    off = _wrap_180(phase_offset_deg)
    g = float(mag_gain)
    return {
        "mag_gain": g,
        "phase_offset_deg": off,
        "h_re": g * math.cos(math.radians(off)),
        "h_im": g * math.sin(math.radians(off)),
    }


def entry_gain_phase(entry):
    """从一条校准记录取 (幅值增益, 相位偏移)；v2 用复数，旧表回落到两个标量。"""
    if "h_re" in entry and "h_im" in entry:
        return (math.hypot(entry["h_re"], entry["h_im"]),
                math.degrees(math.atan2(entry["h_im"], entry["h_re"])))
    return float(entry["mag_gain"]), float(entry["phase_offset_deg"])


def note_driver(**kw):
    """登记当前驱动电平（如 dac_volume），供 apply_calibration 与表内元数据比对。"""
    DRIVER_STATE.update({k: v for k, v in kw.items() if v is not None})


def _check_driver_level():
    """表的标定电平与当前驱动不一致时告警一次（幅值会整体缩放，相位不受影响）。"""
    if not CAL_META or not DRIVER_STATE:
        return
    calibrated = CAL_META.get("dac_volume")
    current = DRIVER_STATE.get("dac_volume")
    if calibrated is None or current is None or calibrated == current:
        return
    _warn_once("drv_level",
               "校准表标定于 dac_volume=0x%02X，当前为 0x%02X：幅值基准会整体"
               "偏移，请重新标定。" % (calibrated, current))


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def calc_mag_phase(I_raw, Q_raw, n=None):
    """把 48 位纯整数累加值还原为物理幅值（V）与相位（度）。

    参数:
        I_raw, Q_raw: ps_ctrl.read_IQ() 返回的 acc_I / acc_Q。
        n:            本次实际积分点数（ps_ctrl.measure_once() 返回的
                      window_n）。缺省时使用 DEFAULT_INTEGRATION_N 兜底，
                      正常调用必须显式传入。
    返回:
        (mag, phase)：mag 单位 V；phase 单位度，范围 (-180, 180]。
    """
    if n is None:
        n = DEFAULT_INTEGRATION_N
    if n <= 0:
        raise ValueError("积分点数 n 必须为正数，收到 %r" % (n,))

    nf = norm_factor(n)
    i_float = I_raw * 2.0 / nf
    q_float = Q_raw * 2.0 / nf
    mag = math.hypot(i_float, q_float) * ADC_FULL_SCALE_V
    phase = math.degrees(math.atan2(q_float, i_float))
    return mag, phase


def calibration_status():
    """如实返回当前校准状态（供网页端/日志展示，避免"已校准"但表为空的误导）。

    返回 dict：
        calibrated:   是否已有校准数据
        freq_points:  已标定频点数
        freq_min/max: 标定频率范围（无数据时为 None）
        paths:        各路径已标定频点数 {"direct": n, "buffer": n}
        table_file:   默认校准表文件路径（存在时），否则 None
        message:      面向用户的一句话状态说明
    """
    rows = [r for r in CAL_TABLE if isinstance(r, dict)]
    freq_points = sorted({r["freq"] for r in rows
                          if isinstance(r.get("freq"), (int, float))})
    path_counts = {p: sum(1 for r in rows if r.get(p) is not None)
                   for p in (CAL_PATH_DIRECT, CAL_PATH_BUFFER)}
    table_exists = os.path.isfile(CAL_TABLE_DEFAULT_PATH)
    if freq_points:
        msg = "校准表已就绪：%d 个频点（%.0f~%.0f Hz）" % (
            len(freq_points), freq_points[0], freq_points[-1])
    else:
        msg = "校准表为空：尚未完成四态校准（run_four_state_calibration）"
    return {
        "calibrated": bool(freq_points),
        "freq_points": len(freq_points),
        "freq_min": freq_points[0] if freq_points else None,
        "freq_max": freq_points[-1] if freq_points else None,
        "paths": path_counts,
        "table_file": CAL_TABLE_DEFAULT_PATH if table_exists else None,
        "meta": CAL_META,
        "message": msg,
    }


def ensure_calibration_loaded(path=None):
    """若校准表文件存在且内存表为空，则加载之；返回是否已有可用校准数据。

    建议在扫频前调用一次（gen_dataset.py / app.py 的扫频入口），使校准自动生效；
    加载失败只提示一次，不阻断测量。
    """
    global CAL_TABLE
    path = path or CAL_TABLE_DEFAULT_PATH
    if CAL_TABLE:
        return True
    if not os.path.isfile(path):
        return False
    try:
        load_calibration_table(path)
    except Exception as exc:  # noqa: BLE001 加载失败不阻断测量，仅提示一次
        _warn_once("cal_load", "加载校准表 %s 失败（%s），本次测量将使用未校准值"
                   % (path, exc))
        return False
    print("已加载校准表：%s（%d 个频点）" % (path, len(CAL_TABLE)))
    return bool(CAL_TABLE)


def apply_calibration(mag, phase, freq, path=CAL_PATH_DIRECT):
    """查校准表，返回校准后的真实幅值与相位。

    只使用指定路径（直连/缓冲）的标定数据，在校准表频率范围内做线性
    插值（相位偏移先回绕再插值），范围外取最近端点；表为空时若默认校准
    表文件存在则自动加载，仍无数据或该路径未标定时返回原值并提示一次。
    返回: (mag_cal, phase_cal)
    """
    if not CAL_TABLE:
        if not ensure_calibration_loaded():
            _warn_once("cal_empty",
                       "校准表为空：尚未完成四态校准（run_four_state_calibration），"
                       "本次返回未校准值。")
            return mag, phase

    rows = [r for r in CAL_TABLE if isinstance(r, dict) and r.get(path)]
    if not rows:
        _warn_once("cal_path_" + path,
                   "校准表中无 %s 路径的标定数据（four_state_calibrate 未覆盖该路径），"
                   "本次返回未校准值。" % path)
        return mag, phase
    _check_driver_level()
    rows = sorted(rows, key=lambda r: r["freq"])

    def _point(row):
        return row["freq"], entry_gain_phase(row[path])

    # 找出频点所在的区间（范围外用最近端点外推）
    if freq <= rows[0]["freq"]:
        _f, (gain, off) = _point(rows[0])
    elif freq >= rows[-1]["freq"]:
        _f, (gain, off) = _point(rows[-1])
    else:
        gain = off = None
        for i in range(len(rows) - 1):
            f0, (g0, p0) = _point(rows[i])
            f1, (g1, p1) = _point(rows[i + 1])
            if f0 <= freq <= f1:
                t = (freq - f0) / (f1 - f0) if f1 != f0 else 0.0
                # 幅值按对数插值（更接近物理），相位按增量插值（自动跨 ±180）
                if g0 > 0 and g1 > 0:
                    gain = math.exp(math.log(g0) +
                                    (math.log(g1) - math.log(g0)) * t)
                else:
                    gain = g0 + (g1 - g0) * t
                off = _wrap_180(p0 + _wrap_180(p1 - p0) * t)
                break
        if gain is None:                      # 兜底（正常流程到不了这里）
            return mag, phase

    # 复数域去嵌入：H_dut = H_meas * H_cal
    h_meas = complex(mag * math.cos(math.radians(phase)),
                     mag * math.sin(math.radians(phase)))
    h_cal = complex(gain * math.cos(math.radians(off)),
                    gain * math.sin(math.radians(off)))
    h_dut = h_meas * h_cal
    return (abs(h_dut),
            _wrap_180(math.degrees(math.atan2(h_dut.imag, h_dut.real))))


def four_state_calibrate(freq, ref_amp_v, measured):
    """四态校准：4 个已知参考状态标定一个频点，前两态为直连/缓冲校准。

    四态定义（与网页文案"直连与缓冲校准已在PS端应用"口径一致）：
      态1 直连（短接）相位 0°       -> 标定直连路径幅值/相位基准
      态2 缓冲（电压跟随器）相位 0°  -> 标定缓冲路径幅值/相位基准
      态3 直连 + 90° 参考相位      -> 参与直连路径相位偏移平均
      态4 直连 + 180° 参考相位     -> 参与直连路径相位偏移平均

    参数:
        freq:      标定频点（Hz）。
        ref_amp_v: 参考信号幅值（V，已知）。
        measured:  与 4 个状态一一对应的实测 (mag, phase) 列表，
                   顺序必须为 [直连0°, 缓冲0°, 直连90°, 直连180°]。
    返回:
        {"direct": (mag_gain, phase_offset_deg),
         "buffer": (mag_gain, phase_offset_deg)}
        并已写入/更新 CAL_TABLE 中该频点两条路径的条目。
    """
    if len(measured) != 4:
        raise ValueError("四态校准需要恰好 4 个实测状态，"
                         "顺序为 [直连0°, 缓冲0°, 直连90°, 直连180°]，"
                         "收到 %d 个" % len(measured))
    if any(m[0] <= 0 for m in measured):
        raise ValueError("四态实测幅值必须为正数，收到 %r"
                         % ([m[0] for m in measured],))

    # 态1=直连0°、态2=缓冲0°、态3=直连90°、态4=直连180°
    direct_idx = (0, 2, 3)      # 直连路径参考状态（取平均，降低单点噪声）
    ref_phases = {0: 0.0, 1: 0.0, 2: 90.0, 3: 180.0}

    # ---- 直连路径（态1/3/4）----
    mags_direct = [measured[i][0] for i in direct_idx]
    mag_gain_dir = ref_amp_v / (sum(mags_direct) / len(mags_direct))
    offsets_dir = [_wrap_180(ref_phases[i] - measured[i][1]) for i in direct_idx]
    phase_off_dir = _mean_angle(offsets_dir)

    # ---- 缓冲路径（态2，电压跟随器）----
    mag_gain_buf = ref_amp_v / measured[1][0]
    phase_off_buf = _wrap_180(ref_phases[1] - measured[1][1])

    row = {
        "freq": float(freq),
        CAL_PATH_DIRECT: make_cal_entry(mag_gain_dir, phase_off_dir),
        CAL_PATH_BUFFER: make_cal_entry(mag_gain_buf, phase_off_buf),
    }
    for r in CAL_TABLE:
        if abs(r["freq"] - freq) < 1e-9:
            r[CAL_PATH_DIRECT] = row[CAL_PATH_DIRECT]
            r[CAL_PATH_BUFFER] = row[CAL_PATH_BUFFER]
            break
    else:
        CAL_TABLE.append(row)

    return {CAL_PATH_DIRECT: (mag_gain_dir, phase_off_dir),
            CAL_PATH_BUFFER: (mag_gain_buf, phase_off_buf)}


def run_four_state_calibration(freqs, ref_amp_v, measure_state=None, periods=10,
                               output_path=None, mock=False):
    """四态校准完整流程：逐频点实测 4 个参考态 -> 填充 CAL_TABLE -> 保存 JSON。

    参考态顺序（与 four_state_calibrate 一致，见 REF_STATE_NAMES）：
        态0 直连（短接）0°；态1 缓冲（电压跟随）0°；
        态2 直连 +90°；态3 直连 +180°。

    参数:
        freqs:        待标定频点列表（Hz），建议与扫频频点一致或取其子集。
        ref_amp_v:    参考信号幅值（V，已知）。
        measure_state: callable(freq_hz, state_index) -> (mag_v, phase_deg)。
                       回调内应先切换参考态（直连/缓冲/+90°/+180° 参考源）再做
                       一次单点测量；为 None 且 mock=False 时报错（仓库暂无参考态
                       硬件驱动，真实校准须由调用方提供该回调）。
        periods:      预留给默认 ps_ctrl 实测路径的积分周期数；当前 real 流程
                       由 measure_state 回调自行决定测量方式，本参数暂不生效。
        output_path:  校准表 JSON 保存路径；默认 data/calibration_table.json。
        mock:         True 时以与参考完全一致的模拟数据跑通全流程（自检/预演），
                      不依赖任何硬件。
    返回:
        results: [(freq, {"direct": (mag_gain, phase_offset_deg),
                          "buffer": (mag_gain, phase_offset_deg)}), ...]
        并已把全部频点写入 CAL_TABLE 与 JSON 文件。
    """
    path = output_path or CAL_TABLE_DEFAULT_PATH
    if not freqs:
        raise ValueError("freqs 不能为空")
    if ref_amp_v <= 0:
        raise ValueError("ref_amp_v 必须为正数，收到 %r" % (ref_amp_v,))
    if measure_state is None and not mock:
        raise RuntimeError(
            "四态校准缺少参考态硬件/回调：请提供 measure_state(freq, state_index) -> "
            "(mag_v, phase_deg) 回调（内部先切换参考态再测量）；或先以 mock=True 预演。"
            "参考态硬件（直连/缓冲/+90°/+180° 参考源）就绪前无法生成有效校准表。")

    ref_phases = {0: 0.0, 1: 0.0, 2: 90.0, 3: 180.0}
    results = []
    for f in freqs:
        measured = []
        for st in range(4):
            if mock:
                measured.append((ref_amp_v, ref_phases[st]))
            else:
                measured.append(measure_state(f, st))
        res = four_state_calibrate(f, ref_amp_v, measured)
        results.append((f, res))
        print("[cal] f=%9.2f Hz | direct gain=%.4f off=%.2f° | "
              "buffer gain=%.4f off=%.2f°"
              % (f, res[CAL_PATH_DIRECT][0], res[CAL_PATH_DIRECT][1],
                 res[CAL_PATH_BUFFER][0], res[CAL_PATH_BUFFER][1]))

    save_calibration_table(path)
    print("四态校准完成：%d 个频点已写入 CAL_TABLE，校准表已保存至 %s"
          % (len(freqs), path))
    return results


def save_calibration_table(path):
    """把校准表保存为 JSON（v2：meta + rows）。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = {
        "format": CAL_FORMAT_VERSION,
        "meta": CAL_META,
        "rows": CAL_TABLE,
    }
    # 先写临时文件再原子替换：中途失败（异常/断电）不会把原表截成 0 字节
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_calibration_table(path):
    """从 JSON 文件加载校准表。

    兼容旧格式：旧表每行为 [freq, mag_gain, phase_offset]（仅直连），
    加载时自动转换为新结构（buffer 路径留空，apply_calibration 会提示）。
    """
    global CAL_TABLE, CAL_META
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        _warn_once("cal_zero", "校准表文件为空或不存在：%s，按空表处理" % path)
        CAL_TABLE = []
        CAL_META = {}
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as exc:
        _warn_once("cal_badjson", "校准表 JSON 解析失败（%s）：%s，按空表处理"
                   % (exc, path))
        CAL_TABLE = []
        CAL_META = {}
        return

    CAL_META = {}
    if isinstance(data, dict):                 # v2
        CAL_META = data.get("meta") or {}
        data = data.get("rows") or []
    CAL_TABLE = []
    for row in data:
        if isinstance(row, list) and len(row) >= 3:     # 旧格式：仅直连
            CAL_TABLE.append({
                "freq": float(row[0]),
                CAL_PATH_DIRECT: {"mag_gain": row[1], "phase_offset_deg": row[2]},
                CAL_PATH_BUFFER: None,
            })
        else:
            CAL_TABLE.append(row)


if __name__ == "__main__":
    # 自检：用 demod_sim.py 的验收数据验证还原公式
    # 幅值 1.5、相位 45° 的定点累加值（acc_I / acc_Q）来自 demod_sim 定点段
    # 这里 N 显式传 100（与 demod_sim.py 的积分点数一致）
    N_SELFTEST = 100
    acc_I_test = int(round(1.5 * math.cos(math.pi / 4) * norm_factor(N_SELFTEST) / 2.0))
    acc_Q_test = int(round(1.5 * math.sin(math.pi / 4) * norm_factor(N_SELFTEST) / 2.0))
    m, p = calc_mag_phase(acc_I_test, acc_Q_test, N_SELFTEST)
    print("自检：acc_I=%d acc_Q=%d (N=%d) -> mag=%.4f V, phase=%.2f deg"
          % (acc_I_test, acc_Q_test, N_SELFTEST, m, p))
    print("期望：mag=1.5000, phase=45.00（与 demod_sim.py 一致）")

    # 四态校准自检（前两态=直连/缓冲）：4 个参考态实测与参考一致
    # 态1直连0° / 态2缓冲0° / 态3直连90° / 态4直连180°
    measured = [(1.5, 0.0), (1.5, 0.0), (1.5, 90.0), (1.5, 180.0)]
    res = four_state_calibrate(1000.0, 1.5, measured)
    print("四态校准(1kHz) -> direct=%s, buffer=%s" % (res[CAL_PATH_DIRECT],
                                                      res[CAL_PATH_BUFFER]))
    print("期望：两路径 mag_gain=1.0, phase_offset=0.0")

    mc, pc = apply_calibration(m, p, 1000.0, path=CAL_PATH_DIRECT)
    print("直连校准后 -> mag=%.4f, phase=%.2f" % (mc, pc))
    mc2, pc2 = apply_calibration(m, p, 1000.0, path=CAL_PATH_BUFFER)
    print("缓冲校准后 -> mag=%.4f, phase=%.2f" % (mc2, pc2))

    # ---- v3 校准闭环自检：mock 跑通四态校准全流程 + 状态上报 + 重新加载 ----
    print("\n--- 校准闭环自检（mock=True，不依赖硬件）---")
    with tempfile.TemporaryDirectory() as tmpd:
        tmp_path = os.path.join(tmpd, "cal_table_test.json")
        run_four_state_calibration([500.0, 1000.0, 2000.0], 1.5,
                                   mock=True, output_path=tmp_path)
        st = calibration_status()
        print("校准状态 -> calibrated=%s, 频点数=%d, 范围=%.0f~%.0f Hz, 路径=%s"
              % (st["calibrated"], st["freq_points"], st["freq_min"],
                 st["freq_max"], st["paths"]))
        print("状态说明 -> %s" % st["message"])

        # 模拟"清空后从文件加载"（对应 app.py / gen_dataset.py 扫频前的 ensure 流程）
        CAL_TABLE.clear()
        loaded = ensure_calibration_loaded(tmp_path)
        mc3, pc3 = apply_calibration(m, p, 1000.0, path=CAL_PATH_DIRECT)
        print("重新加载表(loaded=%s)后 apply_calibration -> mag=%.4f, phase=%.2f"
              % (loaded, mc3, pc3))
        print("（期望：mag=1.5000, phase=45.00）")
