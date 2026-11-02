#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_hplane.py - 由短接校准表 + 标准件实测扫频 + 万用表参数重建 H 平面表。

为什么要这个脚本
----------------
`data/input_network.json` 是“输入端频变阻抗”的去嵌入表。它的每一行来自一只
已知标准件：

    H_row(f) = raw_row(f) * H_short^-1(f)          # 短接校准后
    Zin(f)   = H_row(f) * Z_dut(f) / (1 - H_row(f))
    Z_dut(f) = R + jwL + 1/(jwC)                   # series 标准件的已知阻抗

其中 `raw_row` 是网页扫频保存的未校准复数响应，`H_short` 是同一进程/同一夹具
的短接校准表。标准件的 R/L/C 由万用表/LCR 表实测，不能用标称值代替。

除 series 标准件外，脚本还支持两种三端 shunt 标准件：

    rc_lowpass  : R 串联，C 并联到地，输出取 C 上的节点
    rl_highpass : R 串联，L 并联到地，输出取 L 上的节点

对这两种拓扑，`Zin` 不能再用串联公式反解。脚本按分压关系先求并联节点阻抗
`Zp = H·R/(1-H)`，再由 `Zp = Zshunt || Zin` 反解 `Zin`。校验时用标准件在
`R_ref` 负载下的真实分压传递函数作参考，而不是强行套 series 的
`R_ref/(Z+R_ref)`。

两类行可以放在同一张 topology-aware 表里：`meta.table_kind` 会标成
`topology_aware`，校验和运行时都按 topology 分组，避免两类 `H→Zin`
样本互相污染。

本脚本把原来散落在 `build_hplane*.py`、`check_hplane.py` 里的硬编码路径/
sweep_id/参数收敛成一个可复现的 CLI：

    python3 tools/build_hplane.py \
      --history data/history.csv \
      --standards-json tools/hplane_standards.example.json \
      --output data/input_network.built.json

短接校准表不需要作为参数传入：脚本固定读取仓库/板端项目根目录下的
`data/calibration_table.json`（format 2，取 `direct` 路径）。

也可以不用 JSON，直接在终端重复传 `--standard`：

    --standard "label=97k2,topology=resistor,R=97.2k,sweep=20261027-214036-429028"

数据要求
--------
* 短接表：脚本自动读取 `data/calibration_table.json`（format 2，
  `{"rows":[{"freq":..., "direct":{...}}]}`），或旧式
  `[freq, mag_gain, phase_offset_deg]` 列表；默认使用 `direct` 路径。
* 扫频数据：长表（history.csv / data/real/*.csv）或单条曲线的宽表。
  必须有“未校准”的幅值与相位列：
      mag_v_raw / phase_deg_raw       （history.csv）
      mag_raw   / phase_raw           （data/real/*.csv）
      mag_db_raw / phase_deg_raw      （dB 形式的原始幅值）
  不接受只有 `mag`/`phase`（已校准 dB）的列，避免把校准值二次校准。
* 标准件参数：R、L、C，支持 SI 后缀（`11.6k`、`10n`、`9.8m`、`98.11n`）。
  R 对 RL 标准件应是“总串联电阻”，即电阻 + 电感 DCR。

输出
----
与 `python_model/input_network.py` 兼容的 format 3 JSON：freqs/levels/
level_f/h_re/h_im/zin_re/zin_im/L_re/L_im/R_ref/rlc/meta。

注意
----
* **短接表必须与标准件扫频来自同一次 session/同一进程上下文**。如果短接表
  是后来重新测的，和旧 history 里的 sweep 混用，会看到 |H|>1、Zin 变负、
  校验误差变大——这不是“算法不收敛”，而是数据配对错了。此时应换回当时
  的短接表（备份/重新采标准件），而不是调参数。
* 本脚本只做“建表”，不会自动覆盖线上 `data/input_network.json`；建议先输出到
  `data/input_network.built.json`，用 `--validate` 看误差，再决定是否替换。
* 校验是“同一批标准件”的自洽检查（in-sample），不是独立测试；它主要用来
  发现短接表/扫频数据/参数串错、频点不匹配、近短路病态等问题。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAL = ROOT / "data" / "calibration_table.json"
DEFAULT_OUTPUT = ROOT / "data" / "input_network.built.json"
DEFAULT_R_REF = 10986.0
DEFAULT_R_REF_STANDARD = "97k2"

# 近短路保护：|H| 接近 1 时 Zin = H*Z/(1-H) 对噪声极敏感。
MIN_POINTS = 5


# ---------------------------------------------------------------------------
# 单位与参数解析
# ---------------------------------------------------------------------------
_SI_FACTORS = {
    "": 1.0,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
}


def parse_value(text, kind: str = "") -> float:
    """解析 `11.6k`、`10n`、`9.8mH`、`97.2kΩ` 之类的数值。"""
    raw = str(text).strip()
    if not raw:
        raise ValueError("空数值")
    # 去掉常见单位后缀；R 的 Ω/ohm 与 L/C 的 H/F 不参与数量级。
    raw = raw.replace("Ω", "").replace("ohm", "").replace("Ohm", "")
    raw = raw.replace("µ", "u").replace("μ", "u")
    if kind.upper() == "L":
        raw = re.sub(r"[Hh]$", "", raw)
    elif kind.upper() == "C":
        raw = re.sub(r"[Ff]$", "", raw)
    raw = raw.strip()
    match = re.fullmatch(
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([pnumkKMG]?)",
        raw,
    )
    if not match:
        raise ValueError("无法解析数值 %r（示例：11.6k / 10n / 9.8mH）" % text)
    value = float(match.group(1))
    suffix = match.group(2)
    return value * _SI_FACTORS[suffix]


def normalize_topology(value: str, has_l: bool, has_c: bool) -> str:
    """把 topology 别名归一化；为空时按 L/C 是否存在推断为 series。"""
    text = (value or "").strip().lower().replace("-", "_")
    aliases = {
        "r": "resistor",
        "res": "resistor",
        "resistor": "resistor",
        "series_r": "resistor",
        "rc": "series_rc",
        "series_rc": "series_rc",
        "rl": "series_rl",
        "series_rl": "series_rl",
        "rlc": "series_rlc",
        "series_rlc": "series_rlc",
        "rc_lowpass": "rc_lowpass",
        "rc_lp": "rc_lowpass",
        "lowpass_rc": "rc_lowpass",
        "rc_low": "rc_lowpass",
        "rl_highpass": "rl_highpass",
        "rl_hp": "rl_highpass",
        "highpass_rl": "rl_highpass",
        "rl_high": "rl_highpass",
    }
    if text in ("", "auto"):
        if has_l and has_c:
            return "series_rlc"
        if has_l:
            return "series_rl"
        if has_c:
            return "series_rc"
        return "resistor"
    if text not in aliases:
        raise ValueError(
            "未知 topology %r（可选：resistor / series_rc / series_rl / "
            "series_rlc / rc_lowpass / rl_highpass）" % value
        )
    return aliases[text]


def _standard_from_dict(item: Dict[str, object], source: str) -> dict:
    """把 --standard 或 JSON 中的一条标准件配置归一化成内部结构。"""
    if not isinstance(item, dict):
        raise ValueError("%s: 每条标准件必须是对象" % source)
    low = {str(k).strip().lower(): v for k, v in item.items()}
    label = str(low.get("label", "")).strip()
    if not label:
        raise ValueError("%s: 缺少 label" % source)

    def pick(*keys):
        for key in keys:
            if key in low and low[key] not in (None, ""):
                return low[key]
        return None

    r_text = pick("r", "resistance", "rtot")
    if r_text is None:
        raise ValueError("%s (%s): 缺少 R" % (source, label))
    r = parse_value(r_text, "R")
    l_text = pick("l", "inductance")
    c_text = pick("c", "capacitance")
    l = parse_value(l_text, "L") if l_text not in (None, "") else None
    c = parse_value(c_text, "C") if c_text not in (None, "") else None
    if l is not None and l <= 0:
        l = None
    if c is not None and c <= 0:
        c = None
    topology = normalize_topology(
        str(pick("topology", "topo") or ""), l is not None, c is not None
    )

    if topology == "resistor" and (l is not None or c is not None):
        raise ValueError("%s (%s): resistor 不应带 L/C" % (source, label))
    if topology == "series_rc" and (c is None or l is not None):
        raise ValueError("%s (%s): series_rc 需要 C、且不能有 L" % (source, label))
    if topology == "series_rl" and (l is None or c is not None):
        raise ValueError("%s (%s): series_rl 需要 L、且不能有 C" % (source, label))
    if topology == "series_rlc" and (l is None or c is None):
        raise ValueError("%s (%s): series_rlc 需要 L 和 C" % (source, label))
    if topology == "rc_lowpass" and (c is None or l is not None):
        raise ValueError(
            "%s (%s): rc_lowpass 需要 C、且不能有 L（R 为串联电阻，C 为并联到地）"
            % (source, label)
        )
    if topology == "rl_highpass" and (l is None or c is not None):
        raise ValueError(
            "%s (%s): rl_highpass 需要 L、且不能有 C（R 为串联电阻，L 为并联到地）"
            % (source, label)
        )

    sweep = pick("sweep", "sweep_id", "curve", "curve_id", "file", "path")
    sweep = str(sweep).strip() if sweep not in (None, "") else label
    return {
        "label": label,
        "topology": topology,
        "R": float(r),
        "L": float(l) if l is not None else None,
        "C": float(c) if c is not None else None,
        "sweep": sweep,
        "note": str(pick("note") or ""),
        "source": source,
    }


def parse_standard_spec(spec: str) -> dict:
    """解析 `label=...,topology=...,R=...,L=...,C=...,sweep=...`。"""
    item = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError("--standard 片段 %r 缺少 '='" % part)
        key, value = part.split("=", 1)
        item[key.strip()] = value.strip()
    return _standard_from_dict(item, "--standard")


def load_standards_json(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        items = data.get("standards")
        if items is None:
            raise ValueError("%s: 需要 {\"standards\": [...]} 或列表" % path)
    else:
        items = data
    if not isinstance(items, list) or not items:
        raise ValueError("%s: standards 为空" % path)
    return [_standard_from_dict(item, path) for item in items]


# ---------------------------------------------------------------------------
# 短接校准表
# ---------------------------------------------------------------------------
def _entry_gain_phase(entry) -> Tuple[float, float]:
    """从校准表 entry 取 (幅值增益, 相位偏移)；优先复数 h_re/h_im。"""
    if not isinstance(entry, dict):
        raise ValueError("校准 entry 不是对象：%r" % (entry,))
    if "h_re" in entry and "h_im" in entry:
        re_ = float(entry["h_re"])
        im_ = float(entry["h_im"])
        return math.hypot(re_, im_), math.degrees(math.atan2(im_, re_))
    return float(entry["mag_gain"]), float(entry["phase_offset_deg"])


def load_short_table(path: str, cal_path: str = "direct") -> Tuple[np.ndarray, np.ndarray, dict]:
    """读取短接校准表，返回 (freqs, 复数系数, meta)。

    系数 `coeff = gain * exp(j*phase)`，满足 `H_cal = raw * coeff`。
    """
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    meta = {}
    if isinstance(data, dict):
        meta = data.get("meta") or {}
        rows = data.get("rows") or []
    elif isinstance(data, list):
        rows = data
    else:
        raise ValueError("%s: 不是有效的校准表 JSON" % path)

    freqs = []
    coeffs = []
    for row in rows:
        if isinstance(row, list) and len(row) >= 3:
            freq, gain, off = float(row[0]), float(row[1]), float(row[2])
        elif isinstance(row, dict) and "freq" in row:
            entry = row.get(cal_path)
            if entry is None:
                continue
            freq = float(row["freq"])
            gain, off = _entry_gain_phase(entry)
        else:
            continue
        if freq <= 0 or not np.isfinite(freq):
            continue
        freqs.append(freq)
        coeffs.append(gain * np.exp(1j * math.radians(off)))

    if len(freqs) < 2:
        raise ValueError("%s: 没有足够的 %s 路径校准点" % (path, cal_path))
    order = np.argsort(freqs)
    freqs = np.asarray(freqs, dtype=float)[order]
    coeffs = np.asarray(coeffs, dtype=complex)[order]
    # 同一频点重复时保留最后一个
    unique, index = np.unique(freqs, return_index=True)
    if len(unique) != len(freqs):
        freqs, coeffs = unique, coeffs[index]
    return freqs, coeffs, meta


# ---------------------------------------------------------------------------
# 扫频数据
# ---------------------------------------------------------------------------
def _row_value(row: Dict[str, str], names: Iterable[str]) -> Optional[str]:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None


def _row_raw_complex(row: Dict[str, str], where: str) -> Tuple[float, complex]:
    freq_text = _row_value(row, ("freq_hz", "freq"))
    if freq_text is None:
        raise ValueError("%s: 缺少 freq_hz/freq" % where)
    freq = float(freq_text)
    if freq <= 0:
        raise ValueError("%s: 频率必须为正数（%r）" % (where, freq))

    mag_text = _row_value(row, ("mag_v_raw", "mag_raw"))
    if mag_text is not None:
        mag = float(mag_text)
    else:
        db_text = _row_value(row, ("mag_db_raw",))
        if db_text is None:
            raise ValueError(
                "%s: 找不到未校准幅值列（mag_v_raw/mag_raw/mag_db_raw）；"
                "拒绝用已校准的 mag 列二次校准" % where
            )
        mag = 10.0 ** (float(db_text) / 20.0)

    phase_text = _row_value(row, ("phase_deg_raw", "phase_raw"))
    if phase_text is None:
        raise ValueError("%s: 缺少 phase_deg_raw/phase_raw" % where)
    phase = float(phase_text)
    if not np.isfinite(freq) or not np.isfinite(mag) or not np.isfinite(phase):
        raise ValueError("%s: 频率/幅值/相位包含 NaN/Inf" % where)
    return freq, mag * np.exp(1j * math.radians(phase))


def _finish_sweep(key: str, rows: List[dict], source: str, mode: str = "") -> dict:
    freqs = np.asarray([r["freq"] for r in rows], dtype=float)
    hs = np.asarray([r["h"] for r in rows], dtype=complex)
    order = np.argsort(freqs)
    freqs, hs = freqs[order], hs[order]
    unique, index = np.unique(freqs, return_index=True)
    if len(unique) != len(freqs):
        freqs, hs = unique, hs[index]
    if len(freqs) < MIN_POINTS:
        raise ValueError(
            "sweep %r 只有 %d 个频点（至少 %d）" % (key, len(freqs), MIN_POINTS)
        )
    return {
        "key": key,
        "freq": freqs,
        "h_raw": hs,
        "source": source,
        "mode": mode,
    }


def load_history(paths: Sequence[str]) -> Dict[str, dict]:
    """读取一个或多个长表/宽表，返回 key -> sweep 的字典。"""
    histories: Dict[str, dict] = {}
    for path in paths:
        path = str(path)
        if not os.path.isfile(path):
            raise FileNotFoundError("找不到扫频数据：%s" % path)
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            fieldnames = reader.fieldnames or []
            key_col = "sweep_id" if "sweep_id" in fieldnames else (
                "curve_id" if "curve_id" in fieldnames else None
            )
            grouped: Dict[str, List[dict]] = {}
            fallback_key = Path(path).stem
            for line_no, row in enumerate(reader, 2):
                key = row.get(key_col) if key_col else fallback_key
                key = str(key or "").strip()
                if not key:
                    raise ValueError("%s:%d: %s 为空" % (path, line_no, key_col))
                try:
                    freq, h = _row_raw_complex(row, "%s:%d" % (path, line_no))
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(str(exc)) from exc
                grouped.setdefault(key, []).append(
                    {"freq": freq, "h": h, "mode": row.get("mode", "")}
                )
            for key, rows in grouped.items():
                mode = next((r.get("mode") or "" for r in rows), "")
                sweep = _finish_sweep(key, rows, path, mode)
                if key in histories:
                    # 同一 key 出现在多个文件：合并去重，保留来源列表
                    old = histories[key]
                    merged = _finish_sweep(
                        key,
                        [{"freq": f, "h": h} for f, h in zip(old["freq"], old["h_raw"])]
                        + [{"freq": f, "h": h} for f, h in zip(sweep["freq"], sweep["h_raw"])],
                        old["source"] + "," + path,
                        old.get("mode") or mode,
                    )
                    histories[key] = merged
                else:
                    histories[key] = sweep
    return histories


def load_curve_file(path: str) -> dict:
    """把单个 CSV 当作一条曲线读取；长表里有多条曲线时拒绝猜测。"""
    data = load_history([path])
    if len(data) != 1:
        raise ValueError(
            "%s 包含 %d 条曲线；请改用 --history 提供文件，并用 sweep=<id> 选择"
            % (path, len(data))
        )
    return next(iter(data.values()))


# ---------------------------------------------------------------------------
# 插值与阻抗
# ---------------------------------------------------------------------------
def interp_complex_log(
    f_new: Sequence[float], f_old: Sequence[float], c_old: Sequence[complex]
) -> np.ndarray:
    """在 log f 上对复数做实部/虚部分别线性插值（对 ±180° 回绕安全）。"""
    f_new = np.asarray(f_new, dtype=float)
    f_old = np.asarray(f_old, dtype=float)
    c_old = np.asarray(c_old, dtype=complex)
    if len(f_old) < 2:
        raise ValueError("插值源至少需要 2 个点")
    order = np.argsort(f_old)
    f_old, c_old = f_old[order], c_old[order]
    tol = 1e-9
    if f_new.min() < f_old.min() * (1.0 - tol) or f_new.max() > f_old.max() * (1.0 + tol):
        raise ValueError(
            "频率范围不匹配：目标 %.6g..%.6g Hz，源 %.6g..%.6g Hz"
            % (f_new.min(), f_new.max(), f_old.min(), f_old.max())
        )
    lf = np.log(np.clip(f_new, f_old.min(), f_old.max()))
    lo = np.log(f_old)
    return np.interp(lf, lo, c_old.real) + 1j * np.interp(lf, lo, c_old.imag)


SHUNT_TOPOLOGIES = frozenset({"rc_lowpass", "rl_highpass"})


def is_shunt_topology(topology: str) -> bool:
    return str(topology).strip().lower() in SHUNT_TOPOLOGIES


def _shunt_element(freq: np.ndarray, std: dict) -> np.ndarray:
    """Return the shunt element of rc_lowpass / rl_highpass."""
    f = np.asarray(freq, dtype=float)
    w = 2.0 * math.pi * f
    topology = std["topology"]
    if topology == "rc_lowpass":
        return 1.0 / (1j * w * float(std["C"]))
    if topology == "rl_highpass":
        return 1j * w * float(std["L"])
    raise ValueError("不是 shunt topology：%r" % topology)


def _parallel(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        return (a * b) / (a + b)


def impedance(freq: Sequence[float], std: dict) -> np.ndarray:
    """Return the series branch impedance of a standard.

    For series topologies this is the familiar R + jwL + 1/(jwC).  For the
    shunt topologies it returns the input impedance seen with the output node
    unloaded: R + Zshunt.  It is used for metadata/diagnostics; the actual
    transfer and Zin inversion use the topology-aware helpers below.
    """
    f = np.asarray(freq, dtype=float)
    w = 2.0 * math.pi * f
    topology = std.get("topology", "resistor")
    if topology == "rc_lowpass":
        return float(std["R"]) + 1.0 / (1j * w * float(std["C"]))
    if topology == "rl_highpass":
        return float(std["R"]) + 1j * w * float(std["L"])
    z = np.full(f.shape, float(std["R"]), dtype=complex)
    if std.get("L") is not None:
        z = z + 1j * w * float(std["L"])
    if std.get("C") is not None:
        z = z + 1.0 / (1j * w * float(std["C"]))
    return z


def standard_zin_from_h(
    freq: Sequence[float], h: Sequence[complex], std: dict
) -> np.ndarray:
    """Invert the known standard network to obtain the input-network Zin.

    series:      H = Zin / (Zseries + Zin)
    rc_lowpass:  H = Zp / (R + Zp), Zp = Zin || Zc
    rl_highpass: H = Zp / (R + Zp), Zp = Zin || Zl
    """
    f = np.asarray(freq, dtype=float)
    hs = np.asarray(h, dtype=complex)
    topology = std.get("topology", "resistor")
    if is_shunt_topology(topology):
        zsh = _shunt_element(f, std)
        r = float(std["R"])
        with np.errstate(divide="ignore", invalid="ignore"):
            zp = hs * r / (1.0 - hs)
            return (zp * zsh) / (zsh - zp)
    z = impedance(f, std)
    with np.errstate(divide="ignore", invalid="ignore"):
        return hs * z / (1.0 - hs)


def reference_transfer(
    freq: Sequence[float], std: dict, r_ref: float
) -> np.ndarray:
    """Known standard transfer with an ideal source and R_ref load.

    series:      H = R_ref / (Zseries + R_ref)
    rc_lowpass:  H = (Zc || R_ref) / (R + Zc || R_ref)
    rl_highpass: H = (Zl || R_ref) / (R + Zl || R_ref)
    """
    f = np.asarray(freq, dtype=float)
    topology = std.get("topology", "resistor")
    if is_shunt_topology(topology):
        zsh = _shunt_element(f, std)
        zp = _parallel(zsh, float(r_ref))
        return zp / (float(std["R"]) + zp)
    z = impedance(f, std)
    return float(r_ref) / (z + float(r_ref))


def resolve_sweep(source: str, histories: Dict[str, dict]) -> dict:
    """把 `sweep=...` 解析成 sweep；既支持文件路径，也支持 history 里的 id。"""
    if os.path.isfile(source):
        return load_curve_file(source)
    if source in histories:
        return histories[source]
    import difflib

    close = difflib.get_close_matches(source, list(histories), n=5, cutoff=0.5)
    hint = ("；相近的 id：%s" % ", ".join(close)) if close else ""
    raise KeyError(
        "找不到 sweep %r（--history 里共 %d 条）%s" % (source, len(histories), hint)
    )


# ---------------------------------------------------------------------------
# 建表
# ---------------------------------------------------------------------------
def build_rows(
    cal_freqs: np.ndarray,
    cal_coeffs: np.ndarray,
    histories: Dict[str, dict],
    standards: Sequence[dict],
) -> List[dict]:
    """对每只标准件求 H/Zin，返回按 level 升序排好的行。"""
    if len(cal_freqs) < 2:
        raise ValueError("短接表频点太少")
    rows = []
    seen = set()
    for std in standards:
        label = std["label"]
        if label in seen:
            raise ValueError("重复的标准件 label：%s" % label)
        seen.add(label)
        sweep = resolve_sweep(std["sweep"], histories)

        # 1) 短接系数插值到该 sweep 的频点；2) H = raw * coeff
        coeff_s = interp_complex_log(sweep["freq"], cal_freqs, cal_coeffs)
        h_s = sweep["h_raw"] * coeff_s

        # 3) 把 H 插值到短接表的公共频点
        h_t = interp_complex_log(cal_freqs, sweep["freq"], h_s)
        if np.any(np.abs(h_t) >= 1.0):
            print(
                "WARNING: %s 有 %d 个频点 |H| >= 1（max=%.4f）。若标准件不是近短路，"
                "这通常说明短接表与 sweep 不是同一次 session；行仍会保留，"
                "请结合校验结果判断。"
                % (label, int(np.sum(np.abs(h_t) >= 1.0)), float(np.max(np.abs(h_t)))),
                file=sys.stderr,
            )

        z_dut = impedance(cal_freqs, std)
        denom = 1.0 - h_t
        with np.errstate(divide="ignore", invalid="ignore"):
            zin = standard_zin_from_h(cal_freqs, h_t, std)
        bad = (np.abs(denom) < 1e-6) | (~np.isfinite(zin))
        if np.any(bad):
            print(
                "WARNING: %s 有 %d 个频点 Zin 反解病态（1-H 接近 0 或 shunt "
                "并联节点接近开路）；这些点用相邻频点插值补齐。"
                % (label, int(np.sum(bad))),
                file=sys.stderr,
            )
        if np.any(bad):
            good = ~bad
            if np.sum(good) < 2:
                raise ValueError("%s: 几乎所有频点都落在病态区，无法建行" % label)
            lf = np.log(cal_freqs)
            lg = lf[good]
            zin[bad] = np.interp(lf[bad], lg, zin.real[good]) + 1j * np.interp(
                lf[bad], lg, zin.imag[good]
            )
        if not np.all(np.isfinite(zin)):
            raise ValueError("%s: Zin 出现非有限值" % label)

        rows.append(
            {
                **std,
                "freq": sweep["freq"],
                "H": h_t,
                "Z": z_dut,
                "Zin": zin,
                "level": float(np.median(np.abs(h_t))),
                "n_points": int(len(sweep["freq"])),
            }
        )
    rows.sort(key=lambda item: item["level"])
    return rows


def _r_ref_from_rows(rows: Sequence[dict], r_ref_standard: str) -> Tuple[float, str]:
    for row in rows:
        if row["label"].strip().lower() == r_ref_standard.strip().lower():
            mask = row["freq"] < 100.0
            if not np.any(mask):
                mask = np.zeros_like(row["freq"], dtype=bool)
                mask[: min(5, len(mask))] = True
            value = float(np.median(row["Zin"][mask].real))
            if np.isfinite(value) and value > 0:
                return value, "auto:%s LF median" % row["label"]
    return DEFAULT_R_REF, "default"


def build_payload(
    cal_freqs: np.ndarray,
    rows: Sequence[dict],
    r_ref: float,
    r_ref_source: str,
    cal_meta: dict,
    cal_path: str,
    histories: Sequence[str],
    base_table: Optional[dict],
    note: str,
) -> dict:
    n = len(cal_freqs)
    base_table = base_table or {}
    shunt_flags = {is_shunt_topology(row.get("topology", "")) for row in rows}
    if len(shunt_flags) > 1:
        table_kind = "topology_aware"
    elif shunt_flags == {True}:
        table_kind = "shunt"
    else:
        table_kind = "series"
    l_re = base_table.get("L_re") or [0.0] * n
    l_im = base_table.get("L_im") or [0.0] * n
    if len(l_re) != n or len(l_im) != n:
        l_re, l_im = [0.0] * n, [0.0] * n
    labels = [row["label"] for row in rows]

    meta_standards = []
    for row in rows:
        meta_standards.append(
            {
                "label": row["label"],
                "topology": row["topology"],
                "R_ohm": float(row["R"]),
                "L_h": float(row["L"]) if row.get("L") is not None else None,
                "C_f": float(row["C"]) if row.get("C") is not None else None,
                "sweep": row["sweep"],
                "n_points": int(row["n_points"]),
                "note": row.get("note", ""),
            }
        )

    return {
        "format": 3,
        "freqs": [float(x) for x in cal_freqs],
        "levels": [float(row["level"]) for row in rows],
        "level_f": [[float(v) for v in np.abs(row["H"])] for row in rows],
        "h_re": [[float(v) for v in row["H"].real] for row in rows],
        "h_im": [[float(v) for v in row["H"].imag] for row in rows],
        "zin_re": [[float(v) for v in row["Zin"].real] for row in rows],
        "zin_im": [[float(v) for v in row["Zin"].imag] for row in rows],
        "L_re": [float(v) for v in l_re],
        "L_im": [float(v) for v in l_im],
        "R_ref": float(r_ref),
        "rlc": base_table.get("rlc", {}),
        "meta": {
            "rows": labels,
            "table_kind": table_kind,
            "note": note,
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "tools/build_hplane.py",
            "calibration": {"path": str(cal_path), "meta": cal_meta},
            "histories": [str(p) for p in histories],
            "standards": meta_standards,
            "r_ref": {"value": float(r_ref), "source": r_ref_source},
        },
    }


# ---------------------------------------------------------------------------
# 校验（与 input_network.zin_at/deembed_complex 同一套 IDW 逻辑）
# ---------------------------------------------------------------------------
def _table_arrays(payload: dict):
    f = np.asarray(payload["freqs"], dtype=float)
    h = np.asarray(payload["h_re"], dtype=float) + 1j * np.asarray(payload["h_im"], dtype=float)
    z = np.asarray(payload["zin_re"], dtype=float) + 1j * np.asarray(payload["zin_im"], dtype=float)
    return f, h, z


def _interp_row(row: np.ndarray, freq: float, f_grid: np.ndarray) -> complex:
    lf = math.log(freq)
    lg = np.log(f_grid)
    return complex(
        np.interp(lf, lg, row.real),
        np.interp(lf, lg, row.imag),
    )


def _zin_at(freq: float, h: complex, f_grid: np.ndarray, h_rows: np.ndarray, z_rows: np.ndarray) -> complex:
    hs = np.asarray([_interp_row(row, freq, f_grid) for row in h_rows])
    zs = np.asarray([_interp_row(row, freq, f_grid) for row in z_rows])
    d2 = np.abs(h - hs) ** 2
    w = 1.0 / (d2 + 1e-6)
    return complex(np.sum(w * zs) / np.sum(w))


def _deembed(h: complex, freq: float, payload: dict, f_grid: np.ndarray, h_rows: np.ndarray, z_rows: np.ndarray) -> complex:
    if abs(h) > 0.999:
        return h
    if abs(h) < 1e-5:
        return 0j
    zin = _zin_at(freq, h, f_grid, h_rows, z_rows)
    if abs(zin) < 1e-12:
        return 0j
    z_dut = zin * (1.0 - h) / h
    return payload["R_ref"] / (z_dut + payload["R_ref"])


def validate_payload(payload: dict, rows: Sequence[dict]) -> List[dict]:
    """用建好的表反解同一批标准件，和理论 H_disp 对比。

    series 行：用 series 子表校验 H_disp；
    shunt 行：没有统一的 series Z_dut，校验表插值出来的 Zin 与已知 Zin。
    topology-aware 表会先按 topology group 取子表，避免 series/shunt 的
    H→Zin 样本互相污染。
    这是 in-sample 自洽检查；它不能代替独立标准件/域外测试。
    """
    f_grid, h_rows, z_rows = _table_arrays(payload)
    series_idx = [
        i for i, row in enumerate(rows)
        if not is_shunt_topology(row.get("topology", ""))
    ]
    shunt_idx = [
        i for i, row in enumerate(rows)
        if is_shunt_topology(row.get("topology", ""))
    ]
    series_arrays = (
        f_grid,
        h_rows[series_idx] if series_idx else np.empty((0, len(f_grid))),
        z_rows[series_idx] if series_idx else np.empty((0, len(f_grid))),
    )
    shunt_arrays = (
        f_grid,
        h_rows[shunt_idx] if shunt_idx else np.empty((0, len(f_grid))),
        z_rows[shunt_idx] if shunt_idx else np.empty((0, len(f_grid))),
    )

    out = []
    for row in rows:
        if is_shunt_topology(row.get("topology", "")):
            _, hs, zs = shunt_arrays
            zin_est = np.asarray(
                [
                    _zin_at(f, h, f_grid, hs, zs)
                    for h, f in zip(row["H"], f_grid)
                ]
            )
            db = 20.0 * np.log10(np.abs(zin_est) + 1e-30) - 20.0 * np.log10(
                np.abs(row["Zin"]) + 1e-30
            )
            dphi = (
                (
                    np.degrees(np.angle(zin_est))
                    - np.degrees(np.angle(row["Zin"]))
                    + 180.0
                )
                % 360.0
            ) - 180.0
        else:
            _, hs, zs = series_arrays
            h_disp = np.asarray(
                [
                    _deembed(h, f, payload, f_grid, hs, zs)
                    for h, f in zip(row["H"], f_grid)
                ]
            )
            h_model = reference_transfer(f_grid, row, payload["R_ref"])
            db = 20.0 * np.log10(np.abs(h_disp) + 1e-30) - 20.0 * np.log10(
                np.abs(h_model) + 1e-30
            )
            dphi = (
                (
                    np.degrees(np.angle(h_disp))
                    - np.degrees(np.angle(h_model))
                    + 180.0
                )
                % 360.0
            ) - 180.0
        out.append(
            {
                "label": row["label"],
                "db_rms": float(np.sqrt(np.mean(db**2))),
                "db_max": float(np.max(np.abs(db))),
                "phase_rms": float(np.sqrt(np.mean(dphi**2))),
                "phase_max": float(np.max(np.abs(dphi))),
            }
        )
    return out


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def write_json_atomic(payload: dict, path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def list_sweeps(histories: Dict[str, dict]) -> None:
    print("%-28s %5s  %10s  %10s  %s" % ("sweep_id", "n", "f_min", "f_max", "mode"))
    for key in sorted(histories):
        sweep = histories[key]
        print(
            "%-28s %5d  %10.2f  %10.2f  %s"
            % (
                key,
                len(sweep["freq"]),
                float(sweep["freq"][0]),
                float(sweep["freq"][-1]),
                sweep.get("mode", ""),
            )
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="由短接校准表 + 标准件扫频 + 万用表参数重建 H 平面表",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python3 tools/build_hplane.py \\\n"
            "    --history data/history.csv \\\n"
            "    --standard \"label=97k2,topology=resistor,R=97.2k,sweep=20261027-214036-429028\" \\\n"
            "    --standard \"label=rc11k6,topology=series_rc,R=11.6k,C=10.26n,sweep=20261028-034254-893800\" \\\n"
            "    --standard \"label=rc_lp_3k3_10n,topology=rc_lowpass,R=3.3k,C=10n,sweep=...\" \\\n"
            "    --standard \"label=rl_hp_2k2_68m,topology=rl_highpass,R=2.2k,L=68m,sweep=...\" \\\n"
            "    --output data/input_network.built.json"
        ),
    )
    parser.add_argument(
        "--history",
        action="append",
        default=[],
        metavar="CSV",
        help="扫频长表/宽表；可重复传入",
    )
    parser.add_argument(
        "--standard",
        action="append",
        default=[],
        metavar="SPEC",
        help="标准件：label=...,topology=resistor|series_rc|series_rl|series_rlc|rc_lowpass|rl_highpass,R=...,L=...,C=...,sweep=...（可重复）",
    )
    parser.add_argument(
        "--standards-json",
        default=None,
        help="标准件 JSON（列表或 {\"standards\":[...]}）",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 JSON")
    parser.add_argument(
        "--base-table",
        default=None,
        help="可选：旧 H 平面表，用于继承 rlc/L_re/L_im 元数据",
    )
    parser.add_argument(
        "--r-ref",
        default="auto",
        help="显示参考电阻；auto=按 97k2 标准件低频 Zin 估计（默认）",
    )
    parser.add_argument(
        "--r-ref-standard",
        default=DEFAULT_R_REF_STANDARD,
        help="估计 R_ref 用的标准件 label（默认 97k2）",
    )
    parser.add_argument("--note", default="", help="写入 meta.note 的备注")
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="跳过建表后的 in-sample 自洽校验",
    )
    parser.add_argument(
        "--list-sweeps",
        action="store_true",
        help="只列出 --history 中的 sweep id、点数、频率范围和 mode",
    )
    args = parser.parse_args(argv)

    try:
        histories = load_history(args.history)
        if args.list_sweeps:
            list_sweeps(histories)
            return 0

        standards: List[dict] = []
        if args.standards_json:
            standards.extend(load_standards_json(args.standards_json))
        standards.extend(parse_standard_spec(spec) for spec in args.standard)
        if not standards:
            parser.error("至少需要一个 --standard 或 --standards-json")
        if not histories:
            # 允许所有 sweep 都指向独立文件
            pass

        cal_freqs, cal_coeffs, cal_meta = load_short_table(str(DEFAULT_CAL), "direct")
        rows = build_rows(cal_freqs, cal_coeffs, histories, standards)

        if str(args.r_ref).strip().lower() == "auto":
            r_ref, r_ref_source = _r_ref_from_rows(rows, args.r_ref_standard)
        else:
            r_ref = parse_value(args.r_ref, "R")
            r_ref_source = "argument"

        base = None
        base_path = args.base_table or (args.output if os.path.isfile(args.output) else None)
        if base_path:
            try:
                with open(base_path, "r", encoding="utf-8-sig") as fh:
                    base = json.load(fh)
            except Exception as exc:  # noqa: BLE001
                print("WARNING: 读取 base table %s 失败：%s" % (base_path, exc), file=sys.stderr)
                base = None

        note = args.note or (
            "H-plane table built from %d standards with %d calibration points"
            % (len(rows), len(cal_freqs))
        )
        payload = build_payload(
            cal_freqs,
            rows,
            r_ref,
            r_ref_source,
            cal_meta,
            str(DEFAULT_CAL),
            args.history,
            base,
            note,
        )

        if not args.no_validate:
            validation = validate_payload(payload, rows)
            print("\nIn-sample validation (table vs known Z):")
            print(
                "%-16s %8s %8s %10s %10s"
                % ("label", "dB rms", "dB max", "phi rms", "phi max")
            )
            for item in validation:
                print(
                    "%-16s %8.3f %8.3f %9.3f° %9.3f°"
                    % (
                        item["label"],
                        item["db_rms"],
                        item["db_max"],
                        item["phase_rms"],
                        item["phase_max"],
                    )
                )
            worst_db = max(item["db_max"] for item in validation)
            worst_phase = max(item["phase_max"] for item in validation)
            if worst_db > 3.0 or worst_phase > 20.0:
                print(
                    "\nWARNING: 校验最差 %.2f dB / %.2f°。若短接表不是这批 sweep 的同一次"
                    " session，换表或重新采标准件；不要用调参掩盖数据配对问题。"
                    % (worst_db, worst_phase),
                    file=sys.stderr,
                )

        write_json_atomic(payload, args.output)
        print(
            "\nsaved %s\n  rows=%d  freqs=%d  R_ref=%.3f (%s)"
            % (
                args.output,
                len(rows),
                len(cal_freqs),
                r_ref,
                r_ref_source,
            )
        )
        print("  calibration: %s (created %s)" % (DEFAULT_CAL, cal_meta.get("created", "?")))
        print("  row order: " + ", ".join(row["label"] for row in rows))
        return 0
    except (ValueError, KeyError, FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
