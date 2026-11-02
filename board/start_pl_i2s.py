#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-shot PL/I2S bring-up for the persistent-PL dashboard mode.

Run this once after every board power-on, before starting the dashboard:

    sudo bash board/start_pl_i2s.sh

Boot.py has already loaded the BaseOverlay (base.bit) and configured the
ADAU1761 for its 10 MHz PLL path.  This script only:

  1. loads the custom XSA (which resets PL and stops the base I2S),
  2. sets FCLK0 to 100 MHz and calls configure_codec_direct() for the custom
     25 MHz MCLK / 48.828 kHz path (this happens inside load_design),
  3. performs one dummy measurement so the first ps_start releases the gated
     custom I2S TX and leaves it running for the dashboard.

It does NOT call acceptance.configure_codec() and does NOT load base.bit again.
The dashboard must NOT load the overlay or configure the codec again.

A marker is written to /tmp/pl_i2s_ready.json.  The dashboard only needs to
read that marker and attach to the already-running AXI GPIOs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "board"))
sys.path.insert(0, str(PROJECT / "python_model"))

MARKER = Path("/tmp/pl_i2s_ready.json")
XSA = PROJECT / "board" / "design_1_wrapper.xsa"
BIT = PROJECT / "board" / "design_1_wrapper.bit"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _marker_valid() -> bool:
    try:
        info = json.loads(MARKER.read_text(encoding="utf-8"))
        return (
            bool(info.get("ok"))
            and info.get("xsa_sha256") == _sha256(XSA)
            and info.get("bit_sha256") == _sha256(BIT)
        )
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent PL/I2S bring-up")
    parser.add_argument("--force", action="store_true",
                        help="reload PL even if the marker is still valid")
    args = parser.parse_args()

    if not args.force and _marker_valid():
        print("[pl] already initialized; marker is valid:", MARKER)
        print("[pl] use --force to reload the bitstream")
        return 0

    # Root is required for /dev/mem, /dev/i2c-1 and FPGA manager.
    if os.geteuid() != 0:
        print("[pl] must run as root: sudo bash board/start_pl_i2s.sh", file=sys.stderr)
        return 2

    if not XSA.is_file():
        print("[pl] missing XSA:", XSA, file=sys.stderr)
        return 1

    # Any bring-up/reload starts from an unknown frame state.  Remove the old
    # marker first; only write a new one after I2S has actually restarted.
    try:
        MARKER.unlink()
    except FileNotFoundError:
        pass

    import acceptance  # noqa: E402
    import ps_ctrl  # noqa: E402

    # Boot.py's BaseOverlay has already loaded base.bit and configured the
    # Codec.  Do not reload base.bit here: load_design() replaces PL with the
    # custom XSA, sets FCLK0=100 MHz, and then calls configure_codec_direct()
    # for the custom 25 MHz MCLK / 48.828 kHz path.
    print("[pl] loading custom XSA and programming PL ...", flush=True)
    loaded = acceptance.load_design(acceptance.ensure_bitstream(), 1e8)
    print("[pl] loaded:", loaded.get("loaded_path"),
          "FCLK0=%.3f MHz" % loaded.get("clock_actual_mhz", -1), flush=True)

    # The first ps_start releases i2s_tx.enable and latches i2s_started in the
    # PL.  After measure_once() returns, ps_start is low but I2S keeps running.
    print("[pl] starting I2S with one dummy measurement ...", flush=True)
    acc_i, acc_q, n = ps_ctrl.measure_once(1000.0, periods=10)
    print("[pl] dummy measurement done: N=%d acc=(%d,%d)" % (n, acc_i, acc_q),
          flush=True)

    # Let the codec see a few frames after the dummy measurement before the
    # dashboard starts using the persistent stream.
    time.sleep(0.05)

    marker = {
        "ok": True,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "xsa": str(XSA),
        "xsa_sha256": _sha256(XSA),
        "bit": str(BIT),
        "bit_sha256": _sha256(BIT),
        "loaded_path": loaded.get("loaded_path"),
        "dummy_n": n,
    }
    MARKER.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    print("[pl] persistent PL/I2S ready:", MARKER, flush=True)
    print("[pl] now start dashboard without loading PL again", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
