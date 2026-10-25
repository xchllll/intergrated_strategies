#!/usr/bin/env python3
"""Execute one repeatable I/Q acceptance run through remote Jupyter."""

import argparse
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

import requests
import websocket
from jupyter_client.session import Session


PAYLOAD = r'''
import fcntl
import glob
import json
import math
import os
import time
import traceback

result = {"ok": False, "steps": []}
clock_changed = False
requested_clock_hz = 100000000
input_mode = "line"
window_cycles = 2.0
freq_hz = 1000

def emit(step, **values):
    item = {"step": step, **values}
    result["steps"].append(item)
    print(json.dumps(item, ensure_ascii=False, default=str))

try:
    import pynq
    emit("environment", cwd=os.getcwd(), pynq_version=getattr(pynq, "__version__", "unknown"))

    from pynq.overlays.base import BaseOverlay
    base = BaseOverlay("base.bit")
    audio = getattr(base, "audio", None)
    if audio is None:
        from pynq.lib.audio import Audio
        audio = Audio(base)
    audio.configure(48000)
    if hasattr(audio, "set_volume"):
        audio.set_volume(60)
    line_in_selected = input_mode == "line"
    if input_mode == "line" and hasattr(audio, "select_line_in"):
        audio.select_line_in()
    elif input_mode == "mic" and hasattr(audio, "select_microphone"):
        audio.select_microphone()
    emit(
        "codec",
        driver=type(audio).__name__,
        input_mode=input_mode,
        line_in_selected=line_in_selected,
    )

    i2c_fd = os.open("/dev/i2c-1", os.O_RDWR)
    try:
        fcntl.ioctl(i2c_fd, 0x0703, 0x3B)
        # Re-arm the official 10 MHz MCLK -> 49.152 MHz PLL after Audio.configure.
        os.write(i2c_fd, bytes([
            0x40, 0x02, 0x02, 0x71, 0x02, 0x3C, 0x21, 0x03,
        ]))
        volume_register = (60 << 2) | 0x03
        for register, value in (
            ((0x40, 0x22), 0x21),
            ((0x40, 0x24), 0x41),
            ((0x40, 0x29), volume_register),
            ((0x40, 0x30), volume_register),
        ):
            os.write(i2c_fd, bytes([register[0], register[1], value]))
    finally:
        os.close(i2c_fd)
    emit(
        "codec_config",
        pll="official BaseOverlay configuration",
        dac_unmuted=True,
        volume_register=volume_register,
    )

    candidates = [
        os.path.join(os.getcwd(), "project", "design_1_wrapper.xsa"),
        os.path.join(os.getcwd(), "design_1_wrapper.xsa"),
    ]
    candidates += glob.glob("/home/xilinx/**/project/design_1_wrapper.xsa", recursive=True)
    xsa_path = next((path for path in candidates if os.path.isfile(path)), None)
    if not xsa_path:
        raise FileNotFoundError("找不到 design_1_wrapper.xsa")

    from pynq import Overlay
    overlay = Overlay(xsa_path)
    emit("overlay", path=xsa_path)

    from pynq.ps import Clocks
    clock_before = Clocks.fclk0_mhz
    if abs(clock_before - requested_clock_hz / 1_000_000) > 0.01:
        Clocks.fclk0_mhz = requested_clock_hz / 1_000_000
        clock_changed = True
    time.sleep(0.5)
    emit(
        "clock",
        before_mhz=clock_before,
        requested_mhz=requested_clock_hz / 1_000_000,
        actual_mhz=Clocks.fclk0_mhz,
    )

    start_gpio = overlay.axi_gpio_0
    done_gpio = overlay.axi_gpio_1
    i_gpio = overlay.axi_gpio_2
    q_gpio = overlay.axi_gpio_3
    window_n = 200000
    actual_clock_hz = round(Clocks.fclk0_mhz * 1_000_000)
    window_n = round(window_cycles * actual_clock_hz / freq_hz)
    phase_inc = int(round(freq_hz * (2**32) / actual_clock_hz))
    emit(
        "configure",
        frequency_hz=freq_hz,
        phase_inc=phase_inc,
        phase_inc_hex=f"0x{phase_inc:08X}",
        sample_clock_hz=actual_clock_hz,
        window_n=window_n,
    )

    start_gpio.channel1.write(phase_inc, 0xFFFFFFFF)
    done_gpio.channel1.write(window_n, 0xFFFFFFFF)
    trials = []
    for trial in range(1, 6):
        start_gpio.channel2.write(1, 0x1)
        started = time.monotonic()
        try:
            while not done_gpio.channel2.read():
                if time.monotonic() - started > 2.0:
                    raise TimeoutError(f"第 {trial} 次测量 out_done 超时")
                time.sleep(0.001)
            acc_i = (i_gpio.channel2.read() << 32) | i_gpio.channel1.read()
            acc_q = (q_gpio.channel2.read() << 32) | q_gpio.channel1.read()
            if acc_i >= (1 << 47):
                acc_i -= (1 << 48)
            if acc_q >= (1 << 47):
                acc_q -= (1 << 48)
        finally:
            start_gpio.channel2.write(0, 0x1)
            time.sleep(0.01)

        item = {
            "trial": trial,
            "elapsed_seconds": time.monotonic() - started,
            "acc_i": acc_i,
            "acc_q": acc_q,
            "acc_i_hex": f"0x{acc_i & ((1 << 48) - 1):012X}",
            "acc_q_hex": f"0x{acc_q & ((1 << 48) - 1):012X}",
            "magnitude_v": math.hypot(acc_i, acc_q) * 2.0 / (window_n * 32767.0),
            "phase_deg": math.degrees(math.atan2(acc_q, acc_i)),
            "nonzero": bool(acc_i or acc_q),
        }
        trials.append(item)
        emit("trial", **item)

    result["trials"] = trials
    result["valid_trials"] = sum(item["nonzero"] for item in trials)
    result["ok"] = result["valid_trials"] == 5
except Exception as exc:
    result["error"] = f"{type(exc).__name__}: {exc}"
    result["traceback"] = traceback.format_exc()
finally:
    if clock_changed:
        try:
            Clocks.fclk0_mhz = 50
        except Exception as exc:
            result["clock_restore_error"] = f"{type(exc).__name__}: {exc}"
    print("ACCEPTANCE_RESULT=" + json.dumps(result, ensure_ascii=False, default=str))
'''


def login(session: requests.Session, base_url: str, password: str) -> str:
    page = session.get(base_url + "/login", timeout=10)
    page.raise_for_status()
    match = re.search(r'name="_xsrf" value="([^"]+)"', page.text)
    if not match:
        raise RuntimeError("Jupyter 登录页没有 XSRF token")
    xsrf = match.group(1)
    response = session.post(
        base_url + "/login?next=%2Ftree%3F",
        data={"_xsrf": xsrf, "password": password},
        headers={"X-XSRFToken": xsrf},
        allow_redirects=False,
        timeout=10,
    )
    if response.status_code not in (301, 302, 303):
        raise RuntimeError("Jupyter 登录失败：HTTP %d" % response.status_code)
    return xsrf


def execute(
    base_url: str,
    xsrf: str,
    session: requests.Session,
    clock_hz: int,
    input_mode: str,
    cycles: float,
    freq_hz: float,
) -> dict:
    kernel = session.post(
        base_url + "/api/kernels",
        json={"name": "python3"},
        headers={"X-XSRFToken": xsrf},
        timeout=15,
    )
    kernel.raise_for_status()
    kernel_id = kernel.json()["id"]
    session_id = str(uuid.uuid4())
    ws_url = (
        base_url.replace("http://", "ws://").replace("https://", "wss://")
        + "/api/kernels/%s/channels?session_id=%s" % (kernel_id, session_id)
    )
    cookie = "; ".join("%s=%s" % item for item in session.cookies.items())
    protocol = Session(session=session_id, username="codex")
    request = protocol.msg(
        "execute_request",
        content={
            "code": PAYLOAD.replace(
                "requested_clock_hz = 100000000",
                "requested_clock_hz = %d" % clock_hz,
                1,
            ).replace(
                'input_mode = "line"',
                'input_mode = "%s"' % input_mode,
                1,
            ).replace(
                "window_cycles = 2.0",
                "window_cycles = %r" % cycles,
                1,
            ).replace(
                "freq_hz = 1000",
                "freq_hz = %r" % freq_hz,
                1,
            ),
            "silent": False,
            "store_history": False,
            "user_expressions": {},
            "allow_stdin": False,
            "stop_on_error": True,
        },
    )
    request["channel"] = "shell"
    chunks = []
    cleanup_warning = None
    try:
        ws = websocket.create_connection(
            ws_url,
            header=["Cookie: " + cookie],
            origin=base_url,
            timeout=10,
        )
        try:
            ws.send(json.dumps(request, default=str))
            ws.settimeout(1)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                try:
                    message = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    continue
                content = message.get("content", {})
                msg_type = message.get("header", {}).get("msg_type")
                if msg_type == "stream":
                    chunks.append(content.get("text", ""))
                elif msg_type in ("execute_result", "display_data"):
                    chunks.append(content.get("data", {}).get("text/plain", ""))
                elif msg_type == "error":
                    chunks.append(
                        "%s: %s\n%s"
                        % (
                            content.get("ename"),
                            content.get("evalue"),
                            "\n".join(content.get("traceback", [])),
                        )
                    )
                if "ACCEPTANCE_RESULT=" in "".join(chunks):
                    break
            else:
                raise TimeoutError("远程内核 120 秒内未完成")
        finally:
            ws.close()
    finally:
        try:
            session.delete(
                base_url + "/api/kernels/" + kernel_id,
                headers={"X-XSRFToken": xsrf},
                timeout=30,
            )
        except requests.RequestException as exc:
            cleanup_warning = "临时内核删除失败：%s" % exc

    text = "".join(chunks)
    marker = "ACCEPTANCE_RESULT="
    line = next(
        (item[len(marker):] for item in text.splitlines() if item.startswith(marker)),
        None,
    )
    if line is None:
        raise RuntimeError("远程执行没有返回验收结果：\n" + text)
    result = json.loads(line)
    result["raw_output"] = text
    if cleanup_warning:
        result["cleanup_warning"] = cleanup_warning
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://192.168.2.99:9090")
    parser.add_argument("--password", default="xilinx")
    parser.add_argument("--clock-hz", type=int, default=100_000_000)
    parser.add_argument("--input", choices=("line", "mic"), default="line")
    parser.add_argument("--cycles", type=float, default=2.0)
    parser.add_argument("--freq-hz", type=float, default=1000.0)
    parser.add_argument("--output", type=Path, default=Path("data/acceptance_remote.json"))
    args = parser.parse_args()

    session = requests.Session()
    session.trust_env = False
    xsrf = login(session, args.url.rstrip("/"), args.password)
    result = execute(
        args.url.rstrip("/"),
        xsrf,
        session,
        args.clock_hz,
        args.input,
        args.cycles,
        args.freq_hz,
    )
    result["timestamp"] = datetime.now().astimezone().isoformat()
    result["url"] = args.url
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
