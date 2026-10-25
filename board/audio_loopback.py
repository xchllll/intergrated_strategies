#!/usr/bin/env python3
"""Official PYNQ audio loopback measurement for minimum acceptance.

This path uses the BaseOverlay audio controller and ADAU1761 driver that ship
with PYNQ. It verifies the physical HP+MIC -> LINE IN path without depending on
an FPGA overlay that may not have been rebuilt yet.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

import numpy as np

DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_AMPLITUDE = 0.15
PRELUDE_SECONDS = 0.08
GAP_SECONDS = 0.03
SILENCE_SECONDS = 0.10


def signed24(values: np.ndarray) -> np.ndarray:
    """Decode the right-justified signed 24-bit samples used by PYNQ audio."""
    low = values.astype(np.int64) & 0xFFFFFF
    return np.where(low < 0x800000, low, low - 0x1000000).astype(np.int32)


def _tone_codes(
    frame_count: int,
    sample_rate: int,
    frequency_hz: float,
    amplitude: float,
) -> np.ndarray:
    time_axis = np.arange(frame_count, dtype=np.float64) / sample_rate
    tone = np.sin(2.0 * np.pi * frequency_hz * time_axis)
    return np.rint(tone * ((1 << 23) - 1) * amplitude).astype(np.int32)


def _preamble_codes(sample_rate: int, amplitude: float) -> np.ndarray:
    frame_count = int(PRELUDE_SECONDS * sample_rate)
    time_axis = np.arange(frame_count, dtype=np.float64) / sample_rate
    phase = 2.0 * np.pi * (
        3000.0 * time_axis
        + 0.5 * 6000.0 * time_axis ** 2 / PRELUDE_SECONDS
    )
    return np.rint(
        np.sin(phase) * ((1 << 23) - 1) * amplitude
    ).astype(np.int32)


def analyze_tone(
    left: np.ndarray,
    sample_rate: int,
    frequency_hz: float,
    measure_seconds: float = 0.5,
) -> dict:
    """Measure amplitude, frequency and I/Q from a captured left channel."""
    signal = signed24(np.asarray(left, dtype=np.int32)).astype(np.float64)
    if signal.size < int(0.1 * sample_rate):
        raise ValueError("采回的数据不足 0.1 秒")

    # Find the first sustained active run after the record buffer's initial
    # silence. This makes capture startup latency harmless.
    block = max(16, int(0.002 * sample_rate))
    block_count = signal.size // block
    block_rms = np.sqrt(
        np.mean(signal[: block_count * block].reshape(block_count, block) ** 2, axis=1)
    )
    noise_floor = max(float(np.median(block_rms[:20])), 1.0)
    threshold = max(noise_floor * 12.0, 20_000.0)
    active = block_rms > threshold
    onset_block = None
    for index in range(max(0, active.size - 2)):
        if active[index] and active[index + 1] and active[index + 2]:
            onset_block = index
            break
    if onset_block is None:
        raise RuntimeError("没有检测到音频激励起点")
    onset = onset_block * block

    measure_start = onset + int(
        (PRELUDE_SECONDS + GAP_SECONDS) * sample_rate
    )
    measure_count = int(measure_seconds * sample_rate)
    measure_end = measure_start + measure_count
    if measure_end > signal.size:
        measure_count = signal.size - measure_start
    if measure_count < int(0.1 * sample_rate):
        raise RuntimeError("音频激励存在，但有效测量窗不足")

    segment = signal[measure_start:measure_end].copy()
    dc = float(np.mean(segment))
    segment -= dc
    rms = float(np.sqrt(np.mean(segment ** 2)))
    peak_amplitude = math.sqrt(2.0) * rms
    if rms <= 0.0:
        raise RuntimeError("测量窗 RMS 为 0")

    padded_size = 1 << 20
    spectrum = np.abs(
        np.fft.rfft(segment * np.hanning(segment.size), padded_size)
    )
    frequencies = np.fft.rfftfreq(padded_size, 1.0 / sample_rate)
    search = np.flatnonzero(
        np.abs(frequencies - frequency_hz) <= max(50.0, frequency_hz * 0.05)
    )
    if search.size == 0:
        raise RuntimeError("频点超出可分析范围")
    peak_index = int(search[np.argmax(spectrum[search])])
    measured_frequency = float(frequencies[peak_index])
    peak_bin_ratio = float(
        spectrum[peak_index] / max(np.median(spectrum[1:]), 1.0)
    )

    sample_index = np.arange(segment.size, dtype=np.float64)
    iq = np.sum(
        segment
        * np.exp(
            -2j
            * np.pi
            * measured_frequency
            * sample_index
            / sample_rate
        )
    )
    measured_iq_magnitude = float(abs(iq))
    if measured_iq_magnitude <= 0.0:
        raise RuntimeError("I/Q 为 0")

    phase = float(math.atan2(iq.imag, iq.real))
    acc_scale = peak_amplitude * segment.size / 2.0
    acc_i = int(round(acc_scale * math.cos(phase)))
    acc_q = int(round(acc_scale * math.sin(phase)))

    return {
        "acc_i": acc_i,
        "acc_q": acc_q,
        "window_n": int(segment.size),
        "sample_rate_hz": sample_rate,
        "requested_frequency_hz": float(frequency_hz),
        "measured_frequency_hz": measured_frequency,
        "frequency_error_hz": measured_frequency - frequency_hz,
        "peak_bin_ratio": peak_bin_ratio,
        "dc_code": dc,
        "ac_rms_code": rms,
        "amplitude_code": peak_amplitude,
        "amplitude_full_scale": peak_amplitude / float(1 << 23),
        "phase_deg": math.degrees(phase),
        "raw_iq_magnitude": measured_iq_magnitude,
        "onset_sample": int(onset),
        "measure_start_sample": int(measure_start),
        "nonzero": bool(acc_i or acc_q),
    }


@dataclass
class AudioLoopback:
    """Reusable official-audio loopback stimulus/capture driver."""

    sample_rate: int = DEFAULT_SAMPLE_RATE
    amplitude: float = DEFAULT_AMPLITUDE
    volume: int = 57
    _base: object | None = None
    _audio: object | None = None

    def initialize(self) -> None:
        if self._audio is not None:
            return
        from pynq.overlays.base import BaseOverlay

        self._base = BaseOverlay("base.bit")
        audio = self._base.audio
        audio.configure(self.sample_rate)
        audio.select_line_in()
        audio.set_volume(self.volume)
        self._audio = audio

    def capture(
        self,
        frequency_hz: float,
        record_seconds: float = 1.0,
        play_seconds: float = 1.5,
        measure_seconds: float = 0.5,
    ) -> dict:
        self.initialize()
        assert self._audio is not None
        audio = self._audio

        play_frames = int(play_seconds * self.sample_rate)
        playback = _tone_codes(
            play_frames,
            self.sample_rate,
            frequency_hz,
            self.amplitude,
        )
        onset = int(SILENCE_SECONDS * self.sample_rate)
        preamble = _preamble_codes(self.sample_rate, self.amplitude)
        playback[:onset] = 0
        playback[onset:onset + preamble.size] = preamble
        stereo = np.empty(play_frames * 2, dtype=np.int32)
        stereo[0::2] = playback
        stereo[1::2] = playback

        record_frames = int(record_seconds * self.sample_rate)
        recorded = np.zeros(record_frames * 2, dtype=np.int32)
        play_pointer = audio._ffi.cast(
            "unsigned int*", audio._ffi.from_buffer(stereo)
        )
        record_pointer = audio._ffi.cast(
            "unsigned int*", audio._ffi.from_buffer(recorded)
        )
        errors: list[str] = []

        def do_record() -> None:
            try:
                audio._libaudio.record(
                    audio.mmio.length,
                    record_pointer,
                    record_frames,
                    audio.uio_index,
                    audio.iic_index,
                )
            except Exception as exc:
                errors.append("%s: %s" % (type(exc).__name__, exc))

        def do_play() -> None:
            try:
                audio._libaudio.play(
                    audio.mmio.length,
                    play_pointer,
                    play_frames,
                    audio.volume,
                    audio.uio_index,
                    audio.iic_index,
                )
            except Exception as exc:
                errors.append("%s: %s" % (type(exc).__name__, exc))

        started = time.monotonic()
        record_thread = threading.Thread(target=do_record)
        play_thread = threading.Thread(target=do_play)
        record_thread.start()
        time.sleep(0.05)
        play_thread.start()
        record_thread.join()
        play_thread.join()
        if errors:
            raise RuntimeError("音频闭环失败：" + "; ".join(errors))

        result = analyze_tone(
            recorded[0::2],
            self.sample_rate,
            frequency_hz,
            measure_seconds,
        )
        result["capture_seconds"] = time.monotonic() - started
        return result

    def measure_repeated(
        self,
        frequency_hz: float,
        repeats: int = 3,
    ) -> dict:
        trials = [self.capture(frequency_hz) for _ in range(repeats)]
        valid = [
            trial
            for trial in trials
            if trial["peak_bin_ratio"] >= 50.0
            and abs(trial["frequency_error_hz"]) <= max(30.0, frequency_hz * 0.02)
        ]
        if not valid:
            raise RuntimeError("连续多次采集均未检测到有效 1 kHz 交流信号")

        amplitudes = np.asarray(
            [trial["amplitude_code"] for trial in valid], dtype=np.float64
        )
        amplitude_median = float(np.median(amplitudes))
        amplitude_spread = float(
            (amplitudes.max() - amplitudes.min()) / max(amplitude_median, 1.0)
        )
        if amplitude_spread > 0.35:
            raise RuntimeError(
                "回环幅值不稳定：离散度 %.1f%%" % (amplitude_spread * 100.0)
            )

        representative = max(valid, key=lambda trial: trial["peak_bin_ratio"])
        phases = np.deg2rad([trial["phase_deg"] for trial in valid])
        mean_phase = float(np.angle(np.mean(np.exp(1j * phases))))
        acc_scale = amplitude_median * representative["window_n"] / 2.0
        measured_frequency_median = float(
            np.median([trial["measured_frequency_hz"] for trial in valid])
        )
        peak_bin_ratio_median = float(
            np.median([trial["peak_bin_ratio"] for trial in valid])
        )
        return {
            "backend": "pynq_official_audio_loopback",
            "sample_rate_hz": self.sample_rate,
            "requested_frequency_hz": float(frequency_hz),
            "trials": trials,
            "valid_trials": len(valid),
            "amplitude_code_median": amplitude_median,
            "amplitude_code": amplitude_median,
            "amplitude_spread_ratio": amplitude_spread,
            "acc_i": int(round(acc_scale * math.cos(mean_phase))),
            "acc_q": int(round(acc_scale * math.sin(mean_phase))),
            "phase_deg": math.degrees(mean_phase),
            "window_n": representative["window_n"],
            "measured_frequency_hz": measured_frequency_median,
            "frequency_error_hz": measured_frequency_median - frequency_hz,
            "peak_bin_ratio": peak_bin_ratio_median,
            "nonzero": True,
        }
