#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫频标定：自己启动扫频、多次平均后填写校准表。

工作流（板端终端执行）：

    python3 board/calibrate_path.py --state direct --ref-amp 1.0 --repeat 5
    python3 board/calibrate_path.py --state buffer --ref-amp 1.0 --repeat 5

做的事：
  1. 加载 PL 设计并配置 codec（与网页端完全相同的路径）
  2. 按网页端一致的 240 点对数扫频（20 Hz ~ 20 kHz），重复 --repeat 次
  3. 逐频点做**复数平均**，据此算 mag_gain / phase_offset，写入校准表

重要约定：
  * 本脚本扫出来的数据**不会**写进 data/history.csv。
    history.csv 只记录网页端「开始扫频测量」发起的扫频（含未校准与已校准两套值）。
  * 标定期间不要同时在网页端点击扫频，两者会争用 PL 与 AXI 寄存器。
  * 标定前确认夹具处于对应状态：direct=短接，buffer=缓冲(电压跟随)。

校准表为 v2 格式：{format, meta, rows}；每条记录含复数系数 h_re/h_im 与
便于人读的 mag_gain/phase_offset_deg，另存 ref_amp / dac_volume / repeat 等元数据。
"""
import argparse
import math
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "python_model"))
sys.path.insert(0, os.path.join(ROOT, "board"))

# PYNQ 3.1 需要在 import pynq 之前设置 XILINX_XRT，否则空设备列表会被缓存
os.environ.setdefault("XILINX_XRT", "/usr")

import calibration as cal  # noqa: E402

DEFAULT_TABLE = cal.CAL_TABLE_DEFAULT_PATH
DEFAULT_HISTORY = os.path.join(ROOT, "data", "history.csv")

# 与 dashboard/app.py 的 SWEEP_POINTS / _log_spaced_freqs 保持一致
SWEEP_POINTS = 240
SWEEP_STOP_HZ = 20000.0
SWEEP_START_FLOOR_HZ = 20.0
# 单轮扫频的默认超时（秒）。超过即中止并抛 TimeoutError。
SWEEP_TIMEOUT_S = 120.0


# ---------------------------------------------------------------------------
# 扫频
# ---------------------------------------------------------------------------
def ensure_pynq_interpreter():
    """当前解释器没有 pynq 时，自动切换到 PYNQ 的 venv 解释器重跑。"""
    try:
        import pynq  # noqa: F401
        return
    except ImportError:
        pass
    venv = "/usr/local/share/pynq-venv/bin/python3"
    if os.path.isfile(venv) and os.path.realpath(sys.executable) != os.path.realpath(venv):
        print("当前解释器没有 pynq，切换到 %s 重新执行" % venv)
        os.execv(venv, [venv] + sys.argv)


def log_spaced_freqs(start_hz, stop_hz, count):
    if start_hz <= 0 or stop_hz <= start_hz or count < 2:
        raise ValueError("扫频范围或点数无效")
    ratio = (stop_hz / start_hz) ** (1.0 / (count - 1))
    return [start_hz * ratio ** index for index in range(count)]


def load_design():
    """加载自定义设计并配置 codec（与网页端 _ensure_rtl_design 等价）。"""
    import acceptance

    info = acceptance.configure_codec()
    loaded = acceptance.load_design(acceptance.ensure_bitstream(), 1e8)
    return info, loaded


def sweep_once(freqs, periods, timeout_s=SWEEP_TIMEOUT_S, verbose=True, label=""):
    """按给定频点扫一遍，返回 [(freq, mag, phase), ...]（未校准）。

    timeout_s > 0 时，单轮累计用时超过该值立即抛 TimeoutError（每测完一个
    频点检查一次，所以不会等到整轮跑完才发现）。
    """
    import ps_ctrl

    out = []
    t0 = time.monotonic()
    for index, freq in enumerate(freqs):
        acc_i, acc_q, window_n = ps_ctrl.measure_once(freq, periods=periods)
        mag, phase = cal.calc_mag_phase(acc_i, acc_q, window_n)
        out.append((freq, mag, phase))
        elapsed = time.monotonic() - t0
        if timeout_s and elapsed > timeout_s:
            raise TimeoutError(
                "%s扫频超过 %.0f s 仍未结束（已完成 %d/%d 点，最后一点 %.1f Hz），已中止"
                % (label, timeout_s, index + 1, len(freqs), freq))
        if verbose and (index + 1) % 60 == 0:
            print("    ... %d/%d 点，已用 %.1f s" % (index + 1, len(freqs), elapsed))
    return out


def complex_average(sweeps):
    """把多次扫频的结果逐频点做复数平均。

    返回每项：(freq, mag_avg, phase_avg, coherence, mag_spread_pct)
      coherence     = |mean(H)| / mean(|H|)，1 表示完全一致，接近 0 表示相位乱跳
      mag_spread_pct= 幅值标准差 / 均值
    """
    n = len(sweeps[0])
    result = []
    for k in range(n):
        freq = sweeps[0][k][0]
        re = im = 0.0
        mags = []
        for sweep in sweeps:
            _f, m, p = sweep[k]
            re += m * math.cos(math.radians(p))
            im += m * math.sin(math.radians(p))
            mags.append(m)
        re /= len(sweeps)
        im /= len(sweeps)
        mag_avg = math.hypot(re, im)
        phase_avg = math.degrees(math.atan2(im, re))
        mean_mag = sum(mags) / len(mags)
        coherence = mag_avg / mean_mag if mean_mag > 0 else 0.0
        spread = (statistics.pstdev(mags) / mean_mag * 100.0) if mean_mag > 0 else 999.0
        result.append((freq, mag_avg, phase_avg, coherence, spread))
    return result


# ---------------------------------------------------------------------------
# 校准表
# ---------------------------------------------------------------------------
def load_table(path):
    table = {}
    if not os.path.isfile(path):
        return table, {}
    cal.load_calibration_table(path)
    for row in cal.CAL_TABLE:
        if isinstance(row, dict) and "freq" in row:
            table[round(float(row["freq"]), 6)] = row
    return table, dict(cal.CAL_META or {})


def main():
    parser = argparse.ArgumentParser(
        description="扫频标定：自己启动扫频、多次平均后填写校准表")
    parser.add_argument("--state", choices=(cal.CAL_PATH_DIRECT, cal.CAL_PATH_BUFFER),
                        help="本次标定的路径：direct 或 buffer")
    parser.add_argument("--ref-amp", type=float, default=1.0,
                        help="DUT 输入端已知幅值（默认 1.0，做相对标定）")
    parser.add_argument("--repeat", type=int, default=3,
                        help="扫频次数，逐频点复数平均（默认 3）")
    parser.add_argument("--periods", type=int, default=10,
                        help="每个频点的积分周期数（默认 10，与网页端一致）")
    parser.add_argument("--points", type=int, default=SWEEP_POINTS,
                        help="扫频频点数（默认 240，须与网页端一致）")
    parser.add_argument("--sweep-timeout", type=float, default=SWEEP_TIMEOUT_S,
                        help="单轮扫频的超时秒数，超过即中止（默认 %.0f，0=不限制）"
                        % SWEEP_TIMEOUT_S)
    parser.add_argument("--start-hz", type=float, default=None,
                        help="扫频起点（默认与网页端相同：max(20, SAFE_MIN_FREQ_HZ)）")
    parser.add_argument("--stop-hz", type=float, default=SWEEP_STOP_HZ,
                        help="扫频终点（默认 20000）")
    parser.add_argument("--table", default=DEFAULT_TABLE,
                        help="校准表 JSON（默认 data/calibration_table.json）")
    parser.add_argument("--min-mag", type=float, default=cal.MIN_VALID_MAG,
                        help="低于该幅值(ADC码)视为无信号，跳过（默认 %.0f）"
                        % cal.MIN_VALID_MAG)
    parser.add_argument("--drop-db", type=float, default=40.0,
                        help="低于本表中位数该 dB 数的点判为弱信号并跳过（默认 40）")
    parser.add_argument("--min-coherence", type=float, default=0.0,
                        help="复数一致性低于该值的频点跳过（0 = 不启用）")
    parser.add_argument("--dac-volume", type=lambda s: int(s, 0), default=0x10,
                        help="标定时 DAC 数字音量寄存器 0x402B 的值（默认 0x10）")
    parser.add_argument("--note", default="", help="写进校准表元数据的备注")
    parser.add_argument("--no-load", action="store_true",
                        help="跳过 PL/codec 加载（设计已由网页端加载好时使用）")
    parser.add_argument("--clear-history", action="store_true",
                        help="仅删除 data/history.csv 后退出（与标定流程无关）")
    args = parser.parse_args()

    if args.clear_history:
        if os.path.isfile(DEFAULT_HISTORY):
            os.remove(DEFAULT_HISTORY)
            print("已清除历史数据：%s" % DEFAULT_HISTORY)
        else:
            print("历史文件不存在，无需清除：%s" % DEFAULT_HISTORY)
        return 0

    if not args.state:
        parser.error("--state 必填（direct 或 buffer）")
    if args.ref_amp <= 0:
        parser.error("--ref-amp 必须为正数")
    if args.repeat < 1:
        parser.error("--repeat 必须 >= 1")

    ensure_pynq_interpreter()

    # ---- 1. 加载设计 ----
    if not args.no_load:
        print("[1/4] 加载 PL 设计并配置 codec ...")
        t0 = time.monotonic()
        info, loaded = load_design()
        print("      完成 %.1fs | %s | codec: line_in=%s dac_unmuted=%s"
              % (time.monotonic() - t0,
                 os.path.basename(str(loaded.get("loaded_path", ""))),
                 info.get("line_in_selected"), info.get("dac_unmuted")))
    else:
        print("[1/4] 跳过设计加载（--no-load）")

    import ps_ctrl

    start_hz = args.start_hz
    if start_hz is None:
        start_hz = max(SWEEP_START_FLOOR_HZ, ps_ctrl.SAFE_MIN_FREQ_HZ)
    freqs = log_spaced_freqs(start_hz, args.stop_hz, args.points)
    print("[2/4] 扫频 %d 点 x %d 次（%.2f Hz ~ %.0f Hz, periods=%d）"
          % (len(freqs), args.repeat, freqs[0], freqs[-1], args.periods))
    print("      注意：本次数据不会写入 history.csv")

    sweeps = []
    for k in range(args.repeat):
        tag = "第 %d/%d 轮" % (k + 1, args.repeat)
        print("    [扫频] %s 开始 ..." % tag)
        t0 = time.monotonic()
        try:
            sweep = sweep_once(freqs, args.periods, timeout_s=args.sweep_timeout,
                               label=tag)
        except TimeoutError as exc:
            raise SystemExit("[扫频] 超时中止：%s" % exc)
        elapsed = time.monotonic() - t0
        sweeps.append(sweep)
        print("    [扫频] %s 完成：用时 %.1f s（%d 点，平均 %.1f ms/点）"
              % (tag, elapsed, len(freqs), elapsed / len(freqs) * 1000.0))

    # ---- 2. 复数平均 ----
    print("[3/4] 复数平均 ...")
    averaged = complex_average(sweeps)
    cohs = [a[3] for a in averaged]
    spreads = [a[4] for a in averaged]
    print("      一致性 coherence: median %.4f, min %.4f"
          % (statistics.median(cohs), min(cohs)))
    print("      幅值离散度: median %.2f%%, max %.2f%%"
          % (statistics.median(spreads), max(spreads)))
    worst = sorted(averaged, key=lambda a: a[3])[:5]
    print("      一致性最差的频点: " +
          ", ".join("%.0fHz(%.3f)" % (a[0], a[3]) for a in worst))

    # ---- 3. 门限 + 写表 ----
    cand = {}
    dropped_weak = dropped_coh = 0
    for freq, mag_avg, phase_avg, coherence, _spread in averaged:
        if not cal.phase_is_valid(mag_avg, args.min_mag):
            dropped_weak += 1
            continue
        if args.min_coherence > 0 and coherence < args.min_coherence:
            dropped_coh += 1
            continue
        cand[freq] = (mag_avg, phase_avg)

    if not cand:
        raise SystemExit("没有可用的频点（检查 --min-mag / --min-coherence）")

    med = statistics.median([v[0] for v in cand.values()])
    floor = med * 10.0 ** (-args.drop_db / 20.0)
    points = {}
    for freq, (mag_avg, phase_avg) in cand.items():
        if mag_avg < floor:
            dropped_weak += 1
            continue
        points[freq] = (mag_avg, phase_avg, args.ref_amp / mag_avg)

    table, _old_meta = load_table(args.table)
    for freq in sorted(points):
        _mag, phase_avg, gain = points[freq]
        key = round(float(freq), 6)
        row = table.setdefault(key, {"freq": float(freq)})
        row[args.state] = cal.make_cal_entry(gain, -phase_avg)

    cal.CAL_TABLE = [table[key] for key in sorted(table)]
    # 元数据描述"最近一次标定动作"，整体替换，避免混入旧表的 sweep_id 等字段
    cal.CAL_META = {
        "format": cal.CAL_FORMAT_VERSION,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ref_amp": args.ref_amp,
        "dac_volume": args.dac_volume,
        "state": args.state,
        "repeat": args.repeat,
        "periods": args.periods,
        "freq_points": args.points,
        "source": "calibrate_path.py self-sweep",
        "note": args.note,
    }
    cal.save_calibration_table(args.table)

    print("[4/4] 已写入 %s：%s 路径 %d 个频点"
          % (args.table, args.state, len(points)))
    print("      丢弃：弱信号 %d，一致性不足 %d；平均 %d 次扫频"
          % (dropped_weak, dropped_coh, args.repeat))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
