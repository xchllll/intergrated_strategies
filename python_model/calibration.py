#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
calibration.py - 解调后处理与四态校准（幅值/相位还原）

输入：ps_ctrl.read_IQ() 返回的 48 位累加值 acc_I / acc_Q
输出：真实物理幅值（V）与相位（度）

定点还原系数与 python_model/demod_sim.py 保持一致：
    NORM_FACTOR = N * ADC_GAIN * LO_GAIN
      N        = 100   （积分点数，与 demod_sim.py 同步；若上板后以 top.v
                        的 10000 拍积分窗口为准，请把 N 改为实际点数）
      ADC_GAIN = 10000 （模拟 16 位 ADC 的放大倍数）
      LO_GAIN  = 32767 （DDS 正弦表 16 位满量程）
    I_float = acc_I * 2 / NORM_FACTOR
    Q_float = acc_Q * 2 / NORM_FACTOR
    mag     = hypot(I_float, Q_float) * ADC_FULL_SCALE_V  -> 电压（V）
    phase   = degrees(atan2(Q_float, I_float))            -> 度，范围 (-180, 180]

四态校准：
    每个频点上用 4 个已知参考状态（幅值 A_ref、相位 θ1..θ4）实测后标定：
      mag_gain     = A_ref / mean(measured_mag)     （真实/测得幅值之比）
      phase_offset = 平均回绕相位差 (θ_ref - phase_meas)
    标定结果写入 CAL_TABLE（按频率排序），apply_calibration() 查表线性插值修正。
    校准表当前为空：待上板测试后由 four_state_calibrate() 填充，或手动填写。
"""

import json
import math

# ---------------------------------------------------------------------------
# 定点系数（与 python_model/demod_sim.py 单点同步，改动模型时必须同步修改）
# ---------------------------------------------------------------------------
N = 100             # 积分点数（demod_sim.py 当前取值）
ADC_GAIN = 10000    # ADC 放大倍数
LO_GAIN = 32767     # DDS 正弦表满量程
NORM_FACTOR = N * ADC_GAIN * LO_GAIN

ADC_FULL_SCALE_V = 1.0   # ADC 满量程电压（V）：按实际前端电路填写，默认 ±1V 占位

# ---------------------------------------------------------------------------
# 校准表（待上板测试后填充）
# 每行: [freq_Hz, mag_gain, phase_offset_deg]
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


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------
def calc_mag_phase(I_raw, Q_raw):
    """把 48 位纯整数累加值还原为物理幅值（V）与相位（度）。

    参数:
        I_raw, Q_raw: ps_ctrl.read_IQ() 返回的 acc_I / acc_Q。
    返回:
        (mag, phase)：mag 单位 V；phase 单位度，范围 (-180, 180]。
    """
    i_float = I_raw * 2.0 / NORM_FACTOR
    q_float = Q_raw * 2.0 / NORM_FACTOR
    mag = math.hypot(i_float, q_float) * ADC_FULL_SCALE_V
    phase = math.degrees(math.atan2(q_float, i_float))
    return mag, phase


def apply_calibration(mag, phase, freq):
    """查校准表，返回校准后的真实幅值与相位。

    在校准表频率范围内做线性插值（相位偏移先回绕再插值），
    范围外取最近端点；校准表为空时返回原值并打印警告。
    返回: (mag_cal, phase_cal)
    """
    if not CAL_TABLE:
        print("WARNING: CAL_TABLE 为空，请先完成四态校准（four_state_calibrate）后重试。")
        return mag, phase

    table = sorted(CAL_TABLE, key=lambda row: row[0])

    if freq <= table[0][0]:
        _f, gain, off = table[0]
        return mag * gain, _wrap_180(phase + off)
    if freq >= table[-1][0]:
        _f, gain, off = table[-1]
        return mag * gain, _wrap_180(phase + off)

    for i in range(len(table) - 1):
        f0, g0, p0 = table[i]
        f1, g1, p1 = table[i + 1]
        if f0 <= freq <= f1:
            t = (freq - f0) / (f1 - f0) if f1 != f0 else 0.0
            gain = g0 + (g1 - g0) * t
            offset = _wrap_180(p0 + _wrap_180(p1 - p0) * t)
            return mag * gain, _wrap_180(phase + offset)

    # 兜底（正常流程到不了这里）
    return mag, phase


def four_state_calibrate(freq, ref_amp_v, ref_phases_deg, measured):
    """四态校准：用 4 个已知参考状态标定一个频点并写入 CAL_TABLE。

    参数:
        freq:          标定频点（Hz）。
        ref_amp_v:     参考信号幅值（V，已知）。
        ref_phases_deg: 参考相位列表（度），如 [0, 90, 180, 270]。
        measured:      与 ref_phases_deg 一一对应的实测 (mag, phase) 列表。
    返回:
        (mag_gain, phase_offset_deg)，并已写入/更新 CAL_TABLE 中该频点条目。
    """
    if len(ref_phases_deg) != len(measured):
        raise ValueError("参考相位数量(%d)与实测数量(%d)不一致"
                         % (len(ref_phases_deg), len(measured)))
    n = len(measured)
    if n < 2:
        raise ValueError("至少需要 2 个参考状态（建议 4 态：0/90/180/270 度）")

    mag_gain = ref_amp_v / (sum(m[0] for m in measured) / n)
    offsets = [_wrap_180(r - p) for r, (_m, p) in zip(ref_phases_deg, measured)]
    phase_offset = _mean_angle(offsets)

    for row in CAL_TABLE:
        if abs(row[0] - freq) < 1e-9:
            row[1] = mag_gain
            row[2] = phase_offset
            return mag_gain, phase_offset
    CAL_TABLE.append([float(freq), mag_gain, phase_offset])
    return mag_gain, phase_offset


def save_calibration_table(path):
    """把校准表保存为 JSON 文件，便于上板后归档与复用。"""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(CAL_TABLE, fh, ensure_ascii=False, indent=2)


def load_calibration_table(path):
    """从 JSON 文件加载校准表。"""
    global CAL_TABLE
    with open(path, "r", encoding="utf-8") as fh:
        CAL_TABLE = json.load(fh)


if __name__ == "__main__":
    # 自检：用 demod_sim.py 的验收数据验证还原公式
    # 幅值 1.5、相位 45° 的定点累加值（acc_I / acc_Q）来自 demod_sim 定点段
    acc_I_test = int(round(1.5 * math.cos(math.pi / 4) * NORM_FACTOR / 2.0))
    acc_Q_test = int(round(1.5 * math.sin(math.pi / 4) * NORM_FACTOR / 2.0))
    m, p = calc_mag_phase(acc_I_test, acc_Q_test)
    print("自检：acc_I=%d acc_Q=%d -> mag=%.4f V, phase=%.2f deg"
          % (acc_I_test, acc_Q_test, m, p))
    print("期望：mag=1.5000, phase=45.00（与 demod_sim.py 一致）")

    mc, pc = apply_calibration(m, p, 1000.0)
    print("未校准时（表为空）apply_calibration -> mag=%.4f, phase=%.2f" % (mc, pc))
