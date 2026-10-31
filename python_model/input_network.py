#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""input_network.py - 输入端频变阻抗的去嵌入（v1）

用法（在 board / dashboard 里）:
    import input_network
    input_network.load("/home/xilinx/jupyter_notebooks/project/data/input_network.json")
    mag_disp, phase_disp = input_network.deembed(mag_cal, phase_cal, freq_hz)

原理:
    短接校准后 H = Zin/(R_dut+Zin)（+ 小串扰 L）。
    先扣串扰: A = H*(1+L) - L = Zin/(R_dut+Zin)
    反解 DUT 串联阻抗: Z_dut = Zin*(1-A)/A
    再用恒定 R_ref 重新嵌入: H_disp = R_ref/(Z_dut+R_ref)

这样纯电阻的 Z_dut=R（实数），H_disp 在全频段是平的 0°；
电抗性 DUT 则得到"接一个恒定 R_ref 负载"时的真实频响。
"""
import json
import math
import os

import numpy as np

_TABLE = None
_F = None
_LEVELS = None
_LEVEL_F = None
_H = None
_Z = None
_L = None
_RREF = None
_SHORT_REF = None


def load(path=None):
    """加载 input_network.json；path=None 时在模块旁和 ../data 下查找。"""
    global _TABLE, _F, _LEVELS, _LEVEL_F, _H, _Z, _L, _RREF, _SHORT_REF
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        for cand in (os.path.join(here, "input_network.json"),
                     os.path.join(here, "..", "data", "input_network.json")):
            if os.path.isfile(cand):
                path = cand
                break
        if path is None:
            raise FileNotFoundError("input_network.json not found")
    with open(path, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    _TABLE = d
    _F = np.asarray(d["freqs"], float)
    _LEVELS = np.asarray(d["levels"], float)
    _LEVEL_F = (np.asarray(d["level_f"], float) if d.get("level_f") else None)
    _H = ((np.asarray(d["h_re"], float) + 1j * np.asarray(d["h_im"], float))
          if d.get("h_re") else None)
    _Z = (np.asarray(d["zin_re"], float) + 1j * np.asarray(d["zin_im"], float))
    _L = np.asarray(d["L_re"], float) + 1j * np.asarray(d["L_im"], float)
    _RREF = float(d.get("R_ref", 10900.0))
    _SHORT_REF = None
    sr = d.get("short_ref")
    if sr and sr.get("mag_gain"):
        _SHORT_REF = (np.asarray(sr["mag_gain"], float),
                      np.asarray(sr["phase_offset_deg"], float))
    return d


def _ensure():
    if _TABLE is None:
        load()


def zin_at(freq, level_or_h):
    """按频率和测量点插值输入阻抗。

    新表（format 3）存了每条标定曲线的复数 H（h_re/h_im）。此时用
    **H 平面反距离加权**：在 (Re H, Im H) 平面上找与当前测量点最接近的
    标定行，而不是只按 |H| 插值。这样同一电平下相位不同的 DUT（纯电阻
    vs 容性 RC）会各自取到正确的 Zin，避免 6.6-6.9 kHz 那种“电平交叉”
    造成的假陷波。
    旧表没有 h_re/h_im 时退回原来的 |H| 一维插值。
    """
    _ensure()
    lf = np.log(max(freq, 1e-9))
    ltab = np.log(_F)
    if _H is not None:
        h = complex(level_or_h)
        hs, zs = [], []
        for i in range(_Z.shape[0]):
            hr = float(np.interp(lf, ltab, _H[i].real))
            hi = float(np.interp(lf, ltab, _H[i].imag))
            zr = float(np.interp(lf, ltab, _Z[i].real))
            zi = float(np.interp(lf, ltab, _Z[i].imag))
            hs.append(complex(hr, hi))
            zs.append(complex(zr, zi))
        hs = np.array(hs)
        zs = np.array(zs)
        d2 = np.abs(h - hs) ** 2
        w = 1.0 / (d2 + 1e-6)
        return complex(np.sum(w * zs) / np.sum(w))

    level = abs(level_or_h)
    if _LEVEL_F is not None:
        us, zs = [], []
        for i in range(_Z.shape[0]):
            ui = float(np.interp(lf, ltab, _LEVEL_F[i]))
            zr = float(np.interp(lf, ltab, _Z[i].real))
            zi = float(np.interp(lf, ltab, _Z[i].imag))
            us.append(ui)
            zs.append(complex(zr, zi))
        order = np.argsort(us)
        us = np.array(us)[order]
        zs = np.array(zs)[order]
        if level <= us[0]:
            return complex(zs[0])
        if level >= us[-1]:
            return complex(zs[-1])
        j = int(np.searchsorted(us, level) - 1)
        t = (level - us[j]) / (us[j + 1] - us[j])
        return complex(zs[j] * (1 - t) + zs[j + 1] * t)

    z = np.array([np.interp(lf, ltab, row) for row in _Z])
    if level <= _LEVELS[0]:
        return complex(z[0])
    if level >= _LEVELS[-1]:
        return complex(z[-1])
    j = int(np.searchsorted(_LEVELS, level) - 1)
    t = (level - _LEVELS[j]) / (_LEVELS[j + 1] - _LEVELS[j])
    return complex(z[j] * (1 - t) + z[j + 1] * t)


def crosstalk_at(freq):
    _ensure()
    return complex(np.interp(np.log(max(freq, 1e-9)), np.log(_F), _L.real),
                   np.interp(np.log(max(freq, 1e-9)), np.log(_F), _L.imag))


def deembed_complex(h_cal, freq, use_crosstalk=False):
    """输入: 短接校准后的复数 H；输出: 重新嵌入恒定 R_ref 后的复数 H。"""
    _ensure()
    h = complex(h_cal)
    # 极低阻/短接：|H|≈1，去嵌入只会放大噪声，直接返回。
    # 0.999 仍能保护真正的短接（|H|=1.000），但让 |H|=0.99~0.999 的近短路
    # 容性/感性 DUT（例如高频段的 10 nF）也能走校正。
    if abs(h) > 0.999:
        return h
    if abs(h) < 1e-5:
        return 0j
    L = crosstalk_at(freq) if use_crosstalk else 0j
    A = h * (1 + L) - L
    if abs(A) < 1e-9:
        return 0j
    zin = zin_at(freq, h)
    z_dut = zin * (1 - A) / A
    if abs(z_dut) > 1e12:
        return 0j
    return _RREF / (z_dut + _RREF)


def deembed(mag, phase_deg, freq, use_crosstalk=False):
    """输入: 短接校准后的幅值/相位；输出: 去嵌入后的 (mag, phase_deg)。"""
    h = mag * complex(math.cos(math.radians(phase_deg)),
                      math.sin(math.radians(phase_deg)))
    hd = deembed_complex(h, freq, use_crosstalk=use_crosstalk)
    return abs(hd), math.degrees(math.atan2(hd.imag, hd.real))


def apply_short_change(short_cur):
    """把 H 平面表从建表短接 S_ref 旋转到当前进程的短接 S_cur。

    short_cur 是 calibration.CAL_TABLE 风格的 rows（含 freq 与 direct 的
    mag_gain/phase_offset_deg）。返回 True 表示已旋转。

    H_new = H_old * S_cur/S_ref
    Z_dut = Z_old*(1-H_old)/H_old
    Z_new = H_new*Z_dut/(1-H_new)
    """
    global _H, _Z, _LEVEL_F, _LEVELS, _RREF
    _ensure()
    if _SHORT_REF is None or _H is None:
        return False
    gref, oref = _SHORT_REF
    cur = {}
    for row in short_cur:
        e = row.get("direct") or {}
        g = e.get("mag_gain")
        p = e.get("phase_offset_deg")
        if g and p is not None:
            cur[round(float(row["freq"]), 6)] = (float(g), float(p))
    try:
        gcur = np.array([cur[round(float(f), 6)][0] for f in _F])
        ocur = np.array([cur[round(float(f), 6)][1] for f in _F])
    except KeyError:
        return False
    ratio = (gcur / gref) * np.exp(1j * np.deg2rad(ocur - oref))
    Hn = _H * ratio[None, :]
    Zd = _Z * (1 - _H) / _H
    Zn = Hn * Zd / (1 - Hn)
    _H = Hn
    _Z = Zn
    _LEVEL_F = np.abs(Hn)
    _LEVELS = np.median(_LEVEL_F, axis=1)
    try:
        names = (_TABLE.get("meta") or {}).get("rows") or []
        idx = names.index("97k2")
        lf = _F < 100
        _RREF = float(np.median(Zn[idx][lf].real))
    except (ValueError, IndexError):
        pass
    _TABLE["h_re"] = [[float(v) for v in row.real] for row in Hn]
    _TABLE["h_im"] = [[float(v) for v in row.imag] for row in Hn]
    _TABLE["zin_re"] = [[float(v) for v in row.real] for row in Zn]
    _TABLE["zin_im"] = [[float(v) for v in row.imag] for row in Zn]
    _TABLE["level_f"] = [[float(v) for v in row] for row in _LEVEL_F]
    _TABLE["levels"] = [float(v) for v in _LEVELS]
    _TABLE["R_ref"] = _RREF
    return True


def parameters():
    """返回拟合的等效网络参数（供报告/网页显示）。"""
    _ensure()
    return dict(_TABLE.get("rlc", {}))
