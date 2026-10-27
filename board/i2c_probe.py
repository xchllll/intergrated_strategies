#!/usr/bin/env python3
"""探测 PYNQ 镜像中 I2C1 走 MIO 还是 EMIO（ADAU1761 @ 0x3B）。

用法（PYNQ 板端，在仓库根目录执行）:
    python3 board/i2c_probe.py                 # 只测官方 base.bit
    python3 board/i2c_probe.py top.xsa         # 先测 base.bit，再测自定义 overlay

输出:
    stdout 打印 JSON，同时写入 data/i2c_probe_result.json。

结论判定（针对 ADAU1761 0x3B 的 ACK）:
    both_visible    base 与 custom 都 ACK    -> EMIO 方案可行，PYNQ 镜像的 I2C1 走 EMIO
    base_only       base ACK、custom 未 ACK   -> base 用的是 MIO，PYNQ 镜像的 I2C1 走 MIO
    neither_visible 两者都看不到 0x3B
    custom_only     base 未 ACK、custom ACK

注意:
    - 未提供 XSA 参数时 custom 为 null，conclusion 按 custom 未 ACK 参与判定。
    - fcntl.ioctl 设置从机地址成功不代表总线上真的收到 ACK；
      只有回读 0x4002 成功（pll_0x4002 非空）才是总线级确认。
"""

from __future__ import annotations

import fcntl
import glob
import json
import os
import sys
import time
from pathlib import Path

BOARD_DIR = Path(__file__).resolve().parent
ROOT_DIR = BOARD_DIR.parent
DEFAULT_OUTPUT = ROOT_DIR / "data" / "i2c_probe_result.json"

I2C_SLAVE = 0x0703          # linux/i2c-dev.h -> I2C_SLAVE
ADAU1761_ADDR = 0x3B        # ADAU1761 7-bit 地址（0x76 >> 1）
ADAU1761_PLL_CTRL = 0x4002  # PLL 控制寄存器，bit0 = 1 表示锁定
EREMOTEIO = 121             # Remote I/O error


def scan_bus(dev: str) -> dict:
    """对单个 /dev/i2c-N 尝试选择 ADAU1761 地址，ACK 时回读 PLL 状态。"""
    result = {"bus": dev, "addr_0x3B": "ERROR"}
    fd = os.open(dev, os.O_RDWR)
    try:
        try:
            fcntl.ioctl(fd, I2C_SLAVE, ADAU1761_ADDR)
            result["addr_0x3B"] = "ACK"
        except OSError as exc:
            if exc.errno == EREMOTEIO:
                result["addr_0x3B"] = "NACK"
            else:
                result["errno"] = exc.errno
                result["strerror"] = exc.strerror
            return result
        # 0x3B 有 ACK：回读 PLL 控制寄存器 0x4002（PLL 锁定判定）
        try:
            os.write(
                fd,
                bytes([(ADAU1761_PLL_CTRL >> 8) & 0xFF, ADAU1761_PLL_CTRL & 0xFF]),
            )
            raw = os.read(fd, 1)
            value = raw[0]
            result["pll_0x4002"] = "0x%02X" % value
            result["pll_locked"] = bool(value & 0x01)
        except OSError as exc:
            result["pll_0x4002"] = None
            result["pll_locked"] = None
            result["pll_read_error"] = exc.errno
    finally:
        os.close(fd)
    return result


def probe(overlay_path: str | None = None) -> dict:
    """加载 overlay（None = 官方 base.bit）并返回 I2C 扫描结果。"""
    if overlay_path is None:
        from pynq.overlays.base import BaseOverlay

        BaseOverlay("base.bit")
    else:
        from pynq import Overlay

        Overlay(overlay_path)
    time.sleep(0.5)  # 等待 I2C 设备节点就绪
    devices = sorted(glob.glob("/dev/i2c-*"))
    scan = {dev: scan_bus(dev) for dev in devices}
    acks = [entry for entry in scan.values() if entry["addr_0x3B"] == "ACK"]
    return {
        "devices": devices,
        "scan": scan,
        "pll_0x4002": acks[0].get("pll_0x4002") if acks else None,
        "pll_locked": acks[0].get("pll_locked") if acks else None,
    }


def any_ack(result: dict | None) -> bool:
    if result is None:
        return False
    return any(entry.get("addr_0x3B") == "ACK" for entry in result["scan"].values())


def decide_conclusion(base: dict, custom: dict | None) -> str:
    base_ack = any_ack(base)
    custom_ack = any_ack(custom)
    if base_ack and custom_ack:
        return "both_visible"    # EMIO 方案可行，PYNQ 镜像的 I2C1 走 EMIO
    if base_ack:
        return "base_only"       # base 用 MIO，PYNQ 镜像的 I2C1 走 MIO
    if custom_ack:
        return "custom_only"     # 只有自定义 overlay 暴露 I2C1（EMIO）
    return "neither_visible"     # 两者都看不到 0x3B


def main() -> int:
    base = probe(None)  # 官方 base.bit
    custom = None
    if len(sys.argv) > 1:
        custom = probe(sys.argv[1])

    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base": base,
        "custom": custom,
        "conclusion": decide_conclusion(base, custom),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(text, end="")
    DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT.write_text(text, encoding="utf-8")
    print("报告已写入：%s" % DEFAULT_OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
