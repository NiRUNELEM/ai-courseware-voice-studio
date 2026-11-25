# -*- coding: utf-8 -*-
"""FFmpeg 16/24-bit PCM conversion, SoXR resampling, BS.1770 LUFS normalization, Noise Gate & metrics."""

from __future__ import annotations

import array
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from typing import Dict, List


def create_silence_pcm(duration_sec: float, sample_rate: int = 24000, sampwidth: int = 2) -> bytes:
    """Generates mono zero-PCM byte stream for inter-item pauses at 16-bit or 24-bit depth."""
    frames = max(0, int(round(duration_sec * sample_rate)))
    return b"\x00" * (frames * sampwidth)


def _decode_pcm_mono(frames: bytes, channels: int, sampwidth: int) -> List[float]:
    if sampwidth == 2:
        total = len(frames) // 2
        raw = struct.unpack(f"<{total}h", frames)
        if channels <= 1:
            return [s / 32768.0 for s in raw]
        n = total // channels
        return [sum(raw[i * channels : (i + 1) * channels]) / (channels * 32768.0) for i in range(n)]
    if sampwidth == 3:
        total = len(frames) // 3
        inv = 1.0 / 8388608.0
        vals = [int.from_bytes(frames[i * 3 : (i + 1) * 3], "little", signed=True) * inv for i in range(total)]
        if channels <= 1:
            return vals
        n = total // channels
        return [sum(vals[i * channels : (i + 1) * channels]) / float(channels) for i in range(n)]
    n_frames = len(frames) // max(1, channels * max(1, sampwidth))
    return [0.0] * n_frames


def _soft_limit_samples(norm_samples: List[float], ceiling: float = 0.794) -> List[float]:
    """Applies smooth tanh soft-knee limiting above 88% ceiling so peaks never hard-clip."""
    if ceiling <= 0.0:
        return norm_samples
    knee = ceiling * 0.88
    head = ceiling - knee
    out: List[float] = []
    for s in norm_samples:
        a = abs(s)
        if a <= knee:
            out.append(s)
        else:
            excess = (a - knee) / max(1e-6, head)
            shaped = knee + head * math.tanh(excess)
            out.append(math.copysign(min(ceiling, shaped), s))
    return out


def _encode_pcm_samples(norm_samples: List[float], sampwidth: int, peak_ceiling: float = 0.794) -> bytes:
    safe_samples = _soft_limit_samples(norm_samples, ceiling=peak_ceiling)
    if sampwidth == 3:
        max_v = 8220835  # -0.18 dBFS digital safety clamp
        out = bytearray()
        for s in safe_samples:
            clamped = max(-max_v, min(max_v, int(round(s * 8388608.0))))
            out.extend(int(clamped).to_bytes(3, "little", signed=True))
        return bytes(out)
    arr = array.array("h", (max(-32112, min(32112, int(round(s * 32768.0)))) for s in safe_samples))
    if sys.byteorder != "little":
        arr.byteswap()
    return arr.tobytes()


def _clamp_digital_silence_floor(pcm_bytes: bytes, sampwidth: int = 2, floor_code: int = 16) -> bytes:
    """Zeroes tiny post-gate residual quantization noise on 16-bit/24-bit PCM samples."""
    if not pcm_bytes or sampwidth not in (2, 3):
        return pcm_bytes
    if sampwidth == 2:
        total = len(pcm_bytes) // 2
        raw = list(struct.unpack(f"<{total}h", pcm_bytes[: total * 2]))
        clamped = [0 if abs(s) < floor_code else s for s in raw]
        arr = array.array("h", clamped)
        if sys.byteorder != "little":
            arr.byteswap()
        return arr.tobytes()
    total = len(pcm_bytes) // 3
    floor_24 = floor_code * 256
    out = bytearray()
    for i in range(total):
        val = int.from_bytes(pcm_bytes[i * 3 : (i + 1) * 3], "little", signed=True)
        out.extend((0 if abs(val) < floor_24 else val).to_bytes(3, "little", signed=True))
    return bytes(out)


def _compute_lufs_gain_db(
    pcm_bytes: bytes, target_lufs: float, sampwidth: int, headroom_db: float = -1.8
) -> float:
    """Computes BS.1770 integrated gain in dB with headroom guard so peaks never crunch."""
    samples = _decode_pcm_mono(pcm_bytes, channels=1, sampwidth=sampwidth)
    if not samples:
        return 0.0
    mean_sq = sum(s * s for s in samples) / float(len(samples))
    if mean_sq <= 1e-12:
        return 0.0
    target_mean_sq = 10.0 ** ((target_lufs + 0.691) / 10.0)
    gain = math.sqrt(target_mean_sq / mean_sq)
    gain_db = 20.0 * math.log10(max(1e-6, gain))
    peak = max((abs(s) for s in samples), default=0.0)
    if peak > 1e-6:
        peak_db = 20.0 * math.log10(peak)
        gain_db = min(gain_db, float(headroom_db) - peak_db)
    return max(-16.0, min(14.0, gain_db))


def _run_ffmpeg_norm_gate_limiter(
    src_audio: str,
    dst_wav: str,
    pre_filters: List[str],
    post_filters: List[str],
    sample_rate: int,
    target_lufs: float,
    bit_depth: int,
    clamp_floor: bool = False,
    headroom_db: float = -1.8,
) -> float:
    """Shared two-pass FFmpeg float32 filter -> LUFS gain -> gate/limiter -> PCM WAV chain (zero clip)."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg binary not found in PATH; required for WAV conditioning.")
    dst_parent = os.path.dirname(os.path.abspath(dst_wav))
    os.makedirs(dst_parent, exist_ok=True)
    pcm_codec = "pcm_s24le" if int(bit_depth) == 24 else "pcm_s16le"
    with tempfile.TemporaryDirectory() as tmp_dir:
        filtered_wav = os.path.join(tmp_dir, "filtered.wav")
        proc1 = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", src_audio, "-af", ",".join(pre_filters),
                "-ac", "1", "-ar", str(sample_rate), "-c:a", pcm_codec, filtered_wav,
            ],
            capture_output=True, text=True,
        )
        if proc1.returncode != 0:
            raise RuntimeError(f"ffmpeg pre-filter failed for {src_audio}: {proc1.stderr.strip()}")
        with wave.open(filtered_wav, "rb") as wf_in:
            pcm = wf_in.readframes(wf_in.getnframes())
            in_sw = wf_in.getsampwidth()
        gain_db = _compute_lufs_gain_db(pcm, target_lufs=target_lufs, sampwidth=in_sw, headroom_db=headroom_db)
        float_chain = [f"volume={gain_db:.4f}dB", *post_filters]
        proc2 = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", filtered_wav, "-af", ",".join(float_chain),
                "-ac", "1", "-ar", str(sample_rate), "-c:a", pcm_codec, dst_wav,
            ],
            capture_output=True, text=True,
        )
        if proc2.returncode != 0:
            raise RuntimeError(f"ffmpeg gate/limiter failed for {src_audio}: {proc2.stderr.strip()}")
    if clamp_floor:
        with wave.open(dst_wav, "rb") as wf_r:
            sr_r, sw_r, ch_r = wf_r.getframerate(), wf_r.getsampwidth(), wf_r.getnchannels()
            raw_r = wf_r.readframes(wf_r.getnframes())
        cleaned_pcm = _clamp_digital_silence_floor(raw_r, sampwidth=sw_r, floor_code=16)
        with wave.open(dst_wav, "wb") as wf_w:
            wf_w.setnchannels(ch_r)
            wf_w.setsampwidth(sw_r)
            wf_w.setframerate(sr_r)
            wf_w.writeframes(cleaned_pcm)
    with wave.open(dst_wav, "rb") as wf_out:
        frames, rate = wf_out.getnframes(), wf_out.getframerate()
        if frames <= 0 or rate <= 0:
            raise RuntimeError(f"Conditioned WAV is empty: {dst_wav}")
        return frames / float(rate)


def condition_stage1_pre_rvc_wav(
    src_audio: str,
    dst_wav: str,
    sample_rate: int = 40000,
    target_lufs: float = -18.0,
    noise_gate_threshold: float = 0.008,
    bit_depth: int = 24,
    highpass_hz: int = 85,
    lowpass_hz: int = 11500,
    denoise: bool = True,
) -> float:
    """Conditions Stage 1 TTS audio BEFORE RVC: 24-bit SoXR-33 + configurable highpass/lowpass/denoise + gate + LUFS."""
    thresh = max(0.0005, min(0.2, float(noise_gate_threshold)))
    gate_filter = f"agate=threshold={thresh}:range=0.0003:ratio=8.0:attack=6:release=110:knee=3.0:detection=rms"
    pre_filters = ["aresample=resampler=soxr:precision=33"]
    if int(highpass_hz) > 0:
        pre_filters.append(f"highpass=f={int(highpass_hz)}")
    if int(lowpass_hz) > 0:
        pre_filters.append(f"lowpass=f={int(lowpass_hz)}")
    if denoise:
        pre_filters.append("afftdn=nf=-55:nr=5:nt=w")
    pre_filters.extend([
        "acompressor=threshold=-16dB:ratio=1.5:attack=15:release=140:knee=6.0",
        gate_filter,
    ])
    post_filters = [
        "alimiter=limit=-3.5dB:attack=12:release=180:level=false",
    ]
    return _run_ffmpeg_norm_gate_limiter(
        src_audio=src_audio,
        dst_wav=dst_wav,
        pre_filters=pre_filters,
        post_filters=post_filters,
        sample_rate=sample_rate,
        target_lufs=min(-18.0, float(target_lufs)),
        bit_depth=bit_depth,
        clamp_floor=True,
    )


def convert_to_pcm_wav(
    src_audio: str,
    dst_wav: str,
    sample_rate: int = 24000,
    bit_depth: int = 16,
    clean_pre_model: bool = True,
) -> float:
    """Converts any audio into clean pre-model gated/normalized mono PCM WAV and returns duration."""
    if clean_pre_model:
        return condition_stage1_pre_rvc_wav(
            src_audio=src_audio,
            dst_wav=dst_wav,
            sample_rate=sample_rate,
            bit_depth=bit_depth,
        )
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg binary not found in PATH; required for WAV normalization.")
    dst_parent = os.path.dirname(os.path.abspath(dst_wav))
    os.makedirs(dst_parent, exist_ok=True)
    s_fmt = "s32" if int(bit_depth) == 24 else "s16"
    codec = "pcm_s24le" if int(bit_depth) == 24 else "pcm_s16le"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", src_audio, "-ac", "1", "-ar", str(sample_rate), "-c:a", codec, dst_wav]
    if s_fmt == "s16":
        cmd.extend(["-sample_fmt", "s16"])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed for {src_audio}: {proc.stderr.strip()}")
    with wave.open(dst_wav, "rb") as wf:
        frames, rate = wf.getnframes(), wf.getframerate()
        if frames <= 0 or rate <= 0:
            raise RuntimeError(f"Converted WAV is empty: {dst_wav}")
        return frames / float(rate)


def normalize_pcm16_lufs(
    pcm_bytes: bytes, target_lufs: float = -18.0, sampwidth: int = 2, peak_ceiling: float = 0.794
) -> bytes:
    """Normalizes 16-bit or 24-bit mono PCM bytes to target BS.1770 loudness with soft-knee limiting."""
    target_mean_sq = 10.0 ** ((target_lufs + 0.691) / 10.0)
    samples = _decode_pcm_mono(pcm_bytes, channels=1, sampwidth=sampwidth)
    if not samples:
        return pcm_bytes
    mean_sq = sum(s * s for s in samples) / float(len(samples))
    if mean_sq <= 1e-12:
        return pcm_bytes
    gain = math.sqrt(target_mean_sq / mean_sq)
    return _encode_pcm_samples([s * gain for s in samples], sampwidth=sampwidth, peak_ceiling=peak_ceiling)


def measure_wav_acoustics(wav_path: str) -> Dict[str, float]:
    """Measures duration, sample rate, bit depth, peak dBFS, noise floor dBFS, SNR (dB), and LUFS."""
    if not os.path.exists(wav_path) or os.path.getsize(wav_path) <= 44:
        return {
            "duration_sec": 0.0, "sample_rate": 0, "bit_depth": 0,
            "peak_dbfs": -100.0, "noise_floor_dbfs": -100.0, "snr_db": 0.0, "loudness_lufs": -100.0,
        }

    with wave.open(wav_path, "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        channels = w.getnchannels()
        sampwidth = w.getsampwidth()
        if n == 0 or sr == 0:
            return {
                "duration_sec": 0.0, "sample_rate": sr, "bit_depth": sampwidth * 8,
                "peak_dbfs": -100.0, "noise_floor_dbfs": -100.0, "snr_db": 0.0, "loudness_lufs": -100.0,
            }
        frames = w.readframes(n)
        duration_sec = n / float(sr)

    norm_samples = _decode_pcm_mono(frames, channels=channels, sampwidth=sampwidth)
    peak_amp = max((abs(s) for s in norm_samples), default=0.0)
    peak_dbfs = round(20.0 * math.log10(max(1e-7, peak_amp)), 2)
    mean_sq = sum(s**2 for s in norm_samples) / max(1, len(norm_samples))
    loudness_lufs = round(-0.691 + 10.0 * math.log10(max(1e-12, mean_sq)), 2)

    block_size = max(1, int(sr * 0.05))
    num_blocks = len(norm_samples) // block_size
    if num_blocks > 0:
        blocks_rms = [
            math.sqrt(sum(x**2 for x in norm_samples[b * block_size : (b + 1) * block_size]) / float(block_size))
            for b in range(num_blocks)
        ]
        sorted_blocks = sorted(blocks_rms)
        floor_min = 1.0 / (8388608.0 if sampwidth == 3 else 32768.0)
        noise_floor = max(floor_min, sorted_blocks[int(len(sorted_blocks) * 0.05)])
        median_rms = sorted_blocks[int(len(sorted_blocks) * 0.50)]
        active = [b for b in blocks_rms if b > median_rms]
        speech_rms = math.sqrt(sum(b**2 for b in active) / len(active)) if active else median_rms
        snr_db = round(20.0 * math.log10(max(floor_min, speech_rms) / noise_floor), 2)
        noise_floor_dbfs = round(20.0 * math.log10(max(1e-7, noise_floor)), 2)
    else:
        snr_db = 30.0
        noise_floor_dbfs = -60.0

    return {
        "duration_sec": round(duration_sec, 3),
        "sample_rate": sr,
        "bit_depth": sampwidth * 8,
        "peak_dbfs": peak_dbfs,
        "noise_floor_dbfs": noise_floor_dbfs,
        "snr_db": snr_db,
        "loudness_lufs": loudness_lufs,
    }


EQ_PRESETS: Dict[str, List[Dict[str, float]]] = {
    "anime_female": [
        {"f": 280.0, "q": 1.4, "g": -2.0},
        {"f": 2400.0, "q": 1.6, "g": 1.2},
        {"f": 6000.0, "q": 2.0, "g": -2.4},
        {"f": 7600.0, "q": 2.2, "g": -2.0},
    ],
    "warm_male": [
        {"f": 180.0, "q": 1.2, "g": 1.0},
        {"f": 320.0, "q": 1.4, "g": -1.5},
        {"f": 3000.0, "q": 1.6, "g": 1.2},
        {"f": 6800.0, "q": 2.0, "g": -1.5},
    ],
    "neutral": [
        {"f": 250.0, "q": 1.4, "g": -1.0},
        {"f": 6500.0, "q": 2.0, "g": -1.2},
    ],
    "off": [],
}


def resolve_eq_filters(eq_preset: str = "anime_female", eq_bands: List[Dict[str, float]] | None = None) -> List[str]:
    """Builds FFmpeg equalizer filter strings from custom eq_bands or named eq_preset."""
    bands = list(eq_bands) if eq_bands else EQ_PRESETS.get(str(eq_preset or "anime_female").lower(), EQ_PRESETS["anime_female"])
    out: List[str] = []
    for b in bands:
        freq = max(20.0, min(20000.0, float(b.get("f", 1000.0))))
        q_val = max(0.1, min(10.0, float(b.get("q", 1.5))))
        gain = max(-18.0, min(18.0, float(b.get("g", 0.0))))
        out.append(f"equalizer=f={freq:g}:t=q:w={q_val:g}:g={gain:g}")
    return out


def apply_clean_speech_mastering(
    src_wav: str,
    dst_wav: str,
    sample_rate: int = 24000,
    target_lufs: float = -18.0,
    denoise: bool = True,
    noise_gate: bool = True,
    noise_gate_threshold: float = 0.007,
    bit_depth: int = 16,
    resampler_precision: int = 20,
    declick: bool = False,
    denoise_nf_db: int = -48,
    denoise_nr_db: int = 8,
    highpass_hz: int = 105,
    lowpass_hz: int = 0,
    eq_preset: str = "anime_female",
    eq_bands: List[Dict[str, float]] | None = None,
    comp_threshold_db: float = -16.0,
    comp_ratio: float = 1.5,
    gate_ratio: float = 8.0,
    gate_release_ms: float = 110.0,
    limiter_db: float = -1.8,
) -> float:
    """Applies configurable SoXR resampling + EQ + denoise + clip-free LUFS + gate + limiter."""
    prec = max(16, min(33, int(resampler_precision)))
    lp_hz = int(lowpass_hz) if int(lowpass_hz) > 0 else (17500 if int(sample_rate) >= 48000 else 15500)
    hp_hz = max(0, int(highpass_hz))
    lim_db = max(-12.0, min(-0.1, float(limiter_db)))

    pre_filters = [f"aresample=resampler=soxr:precision={prec}"]
    if declick:
        pre_filters.append("adeclick")
    if hp_hz > 0:
        pre_filters.append(f"highpass=f={hp_hz}")
    pre_filters.append(f"lowpass=f={lp_hz}")
    pre_filters.extend(resolve_eq_filters(eq_preset=eq_preset, eq_bands=eq_bands))
    if denoise:
        pre_filters.append(f"afftdn=nf={int(denoise_nf_db)}:nr={int(denoise_nr_db)}:nt=w")
    if float(comp_ratio) > 1.0:
        pre_filters.append(
            f"acompressor=threshold={float(comp_threshold_db):g}dB:ratio={float(comp_ratio):g}:attack=15:release=160:knee=6.0"
        )

    post_filters = []
    if noise_gate:
        thresh = max(0.0005, min(0.2, float(noise_gate_threshold)))
        g_ratio = max(1.5, min(20.0, float(gate_ratio)))
        g_rel = max(20.0, min(500.0, float(gate_release_ms)))
        post_filters.append(f"agate=threshold={thresh}:range=0.0003:ratio={g_ratio:g}:attack=6:release={g_rel:g}:knee=3.0:detection=rms")
    post_filters.append(f"alimiter=limit={lim_db:g}dB:attack=12:release=180:level=false")

    return _run_ffmpeg_norm_gate_limiter(
        src_audio=src_wav,
        dst_wav=dst_wav,
        pre_filters=pre_filters,
        post_filters=post_filters,
        sample_rate=sample_rate,
        target_lufs=target_lufs,
        bit_depth=bit_depth,
        clamp_floor=bool(noise_gate),
        headroom_db=lim_db,
    )


def concatenate_wav_items(
    item_wavs: List[str],
    output_slide_wav: str,
    silence_sec: float = 0.40,
    default_sample_rate: int = 24000,
    target_lufs: float = -18.0,
    bit_depth: int = 16,
) -> float:
    """Concatenates 16/24-bit item WAVs with silence padding and BS.1770 LUFS normalization."""
    os.makedirs(os.path.dirname(os.path.abspath(output_slide_wav)), exist_ok=True)
    sr = int(default_sample_rate)
    sw = 3 if int(bit_depth) == 24 else 2
    silence_bytes = b""
    silence_frames = 0
    raw_parts: List[bytes] = []
    total_frames = 0
    for idx, item_path in enumerate(item_wavs):
        with wave.open(item_path, "rb") as in_w:
            item_sr, item_sw = in_w.getframerate(), in_w.getsampwidth()
            if idx == 0:
                if item_sr > 0:
                    sr = item_sr
                if item_sw in (2, 3):
                    sw = item_sw
                silence_bytes = create_silence_pcm(silence_sec, sample_rate=sr, sampwidth=sw)
                silence_frames = int(round(silence_sec * sr))
            pcm = in_w.readframes(in_w.getnframes())
        raw_parts.append(pcm)
        total_frames += len(pcm) // sw
        if idx < len(item_wavs) - 1:
            raw_parts.append(silence_bytes)
            total_frames += silence_frames
    normalized_pcm = normalize_pcm16_lufs(b"".join(raw_parts), target_lufs=target_lufs, sampwidth=sw)
    with wave.open(output_slide_wav, "wb") as out_w:
        out_w.setnchannels(1)
        out_w.setsampwidth(sw)
        out_w.setframerate(sr)
        out_w.writeframes(normalized_pcm)
    return round(total_frames / float(sr), 3)
