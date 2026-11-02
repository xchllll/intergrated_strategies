#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""short_alignment.py - 短接校准表的帧延迟估计与对齐（离线/诊断用）。

背景
----
PYNQ-Z2 + ADAU1761 的 I2S/Codec 帧在每次重启后可能差整数个采样（本项目
fs=48828.125 Hz，一个采样 20.48 µs；20 kHz 处约 147.5°）。这种差异对
“同一 session 的 short + DUT”会抵消，但把新 short 套到旧标准件 raw 上时
会留下残余相位。

本模块只做**非侵入式**软件对齐：
  * 读取两张短接表（format 2，direct 路径）；
  * 估计带符号的整数采样偏移 m，使 S_new(f)·exp(+j2πfm/fs) ≈ S_ref(f)；
  * 可选估计分数偏移 m_frac（相位斜率拟合）；
  * 输出对齐前后的幅度/相位残差。

它不会修改 `data/calibration_table.json`，也不进入 live 测量路径；适合：
  * 比较不同 session 的短接表；
  * 对旧标准件 raw 做 legacy 回放/误差分析；
  * 验证 RTL 启动时序是否真的把偏移压到 0。
"""
from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

DEFAULT_FS = 48828.125


def _entry_coeff(entry: dict) -> complex:
    if "h_re" in entry and "h_im" in entry:
        return complex(float(entry["h_re"]), float(entry["h_im"]))
    gain = float(entry["mag_gain"])
    off = math.radians(float(entry["phase_offset_deg"]))
    return gain * np.exp(1j * off)


def load_short_table(path: str, cal_path: str = "direct") -> Tuple[np.ndarray, np.ndarray, dict]:
    """返回 (freqs, complex_coeffs, meta)。系数满足 H_cal = raw * coeff。"""
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        meta = data.get("meta") or {}
        rows = data.get("rows") or []
    elif isinstance(data, list):
        meta = {}
        rows = data
    else:
        raise ValueError("%s: 不是有效的短接表 JSON" % path)

    freqs: List[float] = []
    coeffs: List[complex] = []
    for row in rows:
        if isinstance(row, list) and len(row) >= 3:
            freq, gain, off = float(row[0]), float(row[1]), float(row[2])
            coeff = gain * np.exp(1j * math.radians(off))
        elif isinstance(row, dict) and "freq" in row:
            entry = row.get(cal_path)
            if not isinstance(entry, dict):
                continue
            freq = float(row["freq"])
            coeff = _entry_coeff(entry)
        else:
            continue
        if freq > 0 and np.isfinite(freq) and np.isfinite(coeff.real) and np.isfinite(coeff.imag):
            freqs.append(freq)
            coeffs.append(coeff)
    if len(freqs) < 2:
        raise ValueError("%s: 没有足够的 %s 校准点" % (path, cal_path))
    order = np.argsort(freqs)
    freqs = np.asarray(freqs, dtype=float)[order]
    coeffs = np.asarray(coeffs, dtype=complex)[order]
    unique, index = np.unique(freqs, return_index=True)
    return unique, coeffs[index], meta


def interp_complex_log(f_new: Sequence[float], f_old: Sequence[float], c_old: Sequence[complex]) -> np.ndarray:
    """在 log f 上对复数实部/虚部分别插值，用于把两张表放到同一频点。"""
    f_new = np.asarray(f_new, dtype=float)
    f_old = np.asarray(f_old, dtype=float)
    c_old = np.asarray(c_old, dtype=complex)
    order = np.argsort(f_old)
    f_old, c_old = f_old[order], c_old[order]
    tol = 1e-9
    if f_new.min() < f_old.min() * (1 - tol) or f_new.max() > f_old.max() * (1 + tol):
        raise ValueError(
            "频率范围不匹配：目标 %.6g..%.6g Hz，源 %.6g..%.6g Hz"
            % (f_new.min(), f_new.max(), f_old.min(), f_old.max())
        )
    lf = np.log(np.clip(f_new, f_old.min(), f_old.max()))
    lo = np.log(f_old)
    return np.interp(lf, lo, c_old.real) + 1j * np.interp(lf, lo, c_old.imag)


def delay_factor(freq: Sequence[float], samples: float, fs: float = DEFAULT_FS) -> np.ndarray:
    """相位因子 exp(+j2π f m/fs)。

    约定：m>0 表示给 S 增加相位超前，用于把 S_new 对齐到 S_ref：
        S_new(f) * delay_factor(f, m) ≈ S_ref(f)
    """
    f = np.asarray(freq, dtype=float)
    return np.exp(1j * 2.0 * math.pi * f * float(samples) / float(fs))


def _wrap(deg: np.ndarray) -> np.ndarray:
    return ((deg + 180.0) % 360.0) - 180.0


def residual(freq: np.ndarray, coeff: np.ndarray, ref: np.ndarray) -> Dict[str, float]:
    """对齐后的幅度/相位残差（相对参考表）。"""
    db = 20.0 * np.log10(np.abs(coeff) + 1e-30) - 20.0 * np.log10(np.abs(ref) + 1e-30)
    dphi = _wrap(np.degrees(np.angle(coeff)) - np.degrees(np.angle(ref)))
    return {
        "db_rms": float(np.sqrt(np.mean(db**2))),
        "db_max": float(np.max(np.abs(db))),
        "phase_rms_deg": float(np.sqrt(np.mean(dphi**2))),
        "phase_max_deg": float(np.max(np.abs(dphi))),
    }


def estimate_offset(
    freq: Sequence[float],
    coeff: Sequence[complex],
    ref: Sequence[complex],
    fs: float = DEFAULT_FS,
    max_samples: int = 3,
    fractional: bool = True,
) -> Dict[str, float]:
    """估计把 coeff 对齐到 ref 所需的带符号采样偏移。

    返回：
      m_int   整数偏移（在 [-max_samples, max_samples] 内搜索）
      m_frac  分数偏移（相位斜率拟合；可作为延迟量）
      以及 raw/int/frac 三组残差指标。
    """
    f = np.asarray(freq, dtype=float)
    c = np.asarray(coeff, dtype=complex)
    r = np.asarray(ref, dtype=complex)
    if not (len(f) == len(c) == len(r)):
        raise ValueError("freq/coeff/ref 长度不一致")
    if len(f) < 3:
        raise ValueError("至少需要 3 个频点")

    raw = residual(f, c, r)

    # 1) 整数搜索：归一化复误差
    best_m = 0
    best_err = None
    for m in range(-int(max_samples), int(max_samples) + 1):
        c_try = c * delay_factor(f, m, fs)
        err = float(np.mean(np.abs(c_try - r) ** 2) / (np.mean(np.abs(r) ** 2) + 1e-30))
        if best_err is None or err < best_err:
            best_err = err
            best_m = m
    c_int = c * delay_factor(f, best_m, fs)
    int_res = residual(f, c_int, r)

    # 2) 分数偏移：对 S_new/S_ref 的相位斜率拟合
    m_frac = float(best_m)
    frac_res = dict(int_res)
    if fractional:
        ratio = c / (r + 1e-30j)
        phase = np.unwrap(np.angle(ratio))
        omega = 2.0 * math.pi * f
        # 加权最小二乘：权重用两者幅值，避免弱信号点主导
        weights = np.sqrt(np.abs(c) * np.abs(r))
        if np.sum(weights) > 0:
            slope, intercept = np.polyfit(omega, phase, 1, w=weights)
            # phase ≈ -omega*tau + const  =>  tau = -slope
            tau = -float(slope)
            cand = tau * fs
            # 只在整数候选附近合理时采用；否则保留整数结果
            if abs(cand - best_m) <= 0.75:
                m_frac = cand
                c_frac = c * delay_factor(f, m_frac, fs)
                frac_res = residual(f, c_frac, r)

    return {
        "m_int": int(best_m),
        "m_frac": float(m_frac),
        "int_error_norm": float(best_err) if best_err is not None else float("nan"),
        "raw_db_rms": raw["db_rms"],
        "raw_db_max": raw["db_max"],
        "raw_phase_rms_deg": raw["phase_rms_deg"],
        "raw_phase_max_deg": raw["phase_max_deg"],
        "int_db_rms": int_res["db_rms"],
        "int_db_max": int_res["db_max"],
        "int_phase_rms_deg": int_res["phase_rms_deg"],
        "int_phase_max_deg": int_res["phase_max_deg"],
        "frac_db_rms": frac_res["db_rms"],
        "frac_db_max": frac_res["db_max"],
        "frac_phase_rms_deg": frac_res["phase_rms_deg"],
        "frac_phase_max_deg": frac_res["phase_max_deg"],
    }


def align_pair(path_new: str, path_ref: str, fs: float = DEFAULT_FS, max_samples: int = 3) -> Dict[str, float]:
    """便捷函数：两张表直接对齐，返回估计结果。"""
    f_ref, c_ref, _ = load_short_table(path_ref)
    f_new, c_new, _ = load_short_table(path_new)
    c_new_on_ref = interp_complex_log(f_ref, f_new, c_new)
    return estimate_offset(f_ref, c_new_on_ref, c_ref, fs=fs, max_samples=max_samples)
