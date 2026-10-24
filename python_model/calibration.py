#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibration.py - 解调后处理与四态校准（幅值/相位还原）【直连/缓冲口径版】

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
    校准表当前为空：待上板测试后由 four_state_calibrate() 填充，
    或手动填写。
"""

import json
import math

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

ADC_GAIN = 10000    # ADC 数字化增益（定点模型口径；codec 直采链路改为 1.0）
LO_GAIN = 32767     # DDS 正弦表满量程

ADC_FULL_SCALE_V = 1.0   # ADC 满量程电压（V）：按实际前端电路填写，默认 ±1V 占位

# ---------------------------------------------------------------------------
# 校准表（待上板测试后填充）
# 每频点一条记录（dict），包含两条路径的标定结果：
#   {"freq": 1000.0,
#    "direct": {"mag_gain": 1.0, "phase_offset_deg": 0.0},   # 直连（短接）
#    "buffer": {"mag_gain": 1.0, "phase_offset_deg": 0.0}}   # 缓冲（电压跟随）
# ---------------------------------------------------------------------------
CAL_TABLE = []


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


def apply_calibration(mag, phase, freq, path=CAL_PATH_DIRECT):
    """查校准表，返回校准后的真实幅值与相位。

    只使用指定路径（直连/缓冲）的标定数据，在校准表频率范围内做线性
    插值（相位偏移先回绕再插值），范围外取最近端点；表为空或该路径
    未标定时返回原值并打印警告。
    返回: (mag_cal, phase_cal)
    """
    if not CAL_TABLE:
        print("WARNING: CAL_TABLE 为空，请先完成四态校准（four_state_calibrate）后重试。")
        return mag, phase

    rows = [r for r in CAL_TABLE if r.get(path)]
    if not rows:
        print("WARNING: 校准表中无 %s 路径的标定数据，"
              "请先用 four_state_calibrate() 标定该路径。" % path)
        return mag, phase
    rows = sorted(rows, key=lambda r: r["freq"])

    def _point(row):
        return row["freq"], row[path]["mag_gain"], row[path]["phase_offset_deg"]

    if freq <= rows[0]["freq"]:
        _f, gain, off = _point(rows[0])
        return mag * gain, _wrap_180(phase + off)
    if freq >= rows[-1]["freq"]:
        _f, gain, off = _point(rows[-1])
        return mag * gain, _wrap_180(phase + off)

    for i in range(len(rows) - 1):
        f0, g0, p0 = _point(rows[i])
        f1, g1, p1 = _point(rows[i + 1])
        if f0 <= freq <= f1:
            t = (freq - f0) / (f1 - f0) if f1 != f0 else 0.0
            gain = g0 + (g1 - g0) * t
            offset = _wrap_180(p0 + _wrap_180(p1 - p0) * t)
            return mag * gain, _wrap_180(phase + offset)

    # 兜底（正常流程到不了这里）
    return mag, phase


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
        CAL_PATH_DIRECT: {"mag_gain": mag_gain_dir, "phase_offset_deg": phase_off_dir},
        CAL_PATH_BUFFER: {"mag_gain": mag_gain_buf, "phase_offset_deg": phase_off_buf},
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


def save_calibration_table(path):
    """把校准表保存为 JSON 文件，便于上板后归档与复用。"""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(CAL_TABLE, fh, ensure_ascii=False, indent=2)


def load_calibration_table(path):
    """从 JSON 文件加载校准表。

    兼容旧格式：旧表每行为 [freq, mag_gain, phase_offset]（仅直连），
    加载时自动转换为新结构（buffer 路径留空，apply_calibration 会提示）。
    """
    global CAL_TABLE
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

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
