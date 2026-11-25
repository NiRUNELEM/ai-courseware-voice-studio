# -*- coding: utf-8 -*-
"""Stage 1 TTS engine: Microsoft Edge-TTS (configurable voice/rate/pitch) and Dry-Run generator."""

from __future__ import annotations

import asyncio
import math
import os
import random
import re
import ssl
import struct
import tempfile
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from voice_studio.audio.mastering import condition_stage1_pre_rvc_wav, convert_to_pcm_wav

EDGE_DEFAULT_VOICE = "th-TH-PremwadeeNeural"
EDGE_DEFAULT_RATE = "-6%"
EDGE_DEFAULT_PITCH = "+0Hz"
EDGE_DEFAULT_PACING_SEC = 0.85
EDGE_HQ_OUTPUT_FORMAT = "audio-24khz-96kbitrate-mono-mp3"
_EDGE_TTS_SEMAPHORE = threading.BoundedSemaphore(1)
_EDGE_COOLDOWN_LOCK = threading.Lock()
_EDGE_COOLDOWN_UNTIL = 0.0
_EDGE_LAST_CALL = 0.0


def estimate_speech_duration(text: str, speed: float = 0.90) -> float:
    """Estimates natural Thai speech duration in seconds from character count."""
    char_count = len((text or "").replace(" ", ""))
    return round(max(1.2, char_count / (11.5 * speed)), 3)


def generate_dry_run_audio(filepath: str, duration_sec: float, sample_rate: int = 24000) -> None:
    """Generates synthetic 16-bit mono WAV with natural phrase pauses for dry-run validation."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    num_samples = int(round(duration_sec * sample_rate))
    tile_samples = max(1, int(round(0.60 * sample_rate)))
    half_phrase = int(round(0.225 * sample_rate))
    active_samples = int(round(0.45 * sample_rate))
    tile = bytearray()
    for i in range(tile_samples):
        amp = 8400.0 if i < half_phrase else 7200.0
        val = int(amp * math.sin(2.0 * math.pi * 440.0 * i / float(sample_rate))) if i < active_samples else 0
        tile.extend(struct.pack("<h", val))
    raw_data = (bytes(tile) * ((num_samples // tile_samples) + 1))[: num_samples * 2]
    with wave.open(filepath, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(raw_data)


def _sanitize_tts_text(text: str) -> str:
    cleaned = re.sub(r"[<>]+", " ", text)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _run_coroutine_sync(coro: object) -> None:
    """Runs an async coroutine safely whether or not the current thread already has a running event loop."""
    try:
        asyncio.get_running_loop()
        has_loop = True
    except RuntimeError:
        has_loop = False
    if not has_loop:
        asyncio.run(coro)  # type: ignore[arg-type]
        return
    with ThreadPoolExecutor(max_workers=1) as ex:
        ex.submit(asyncio.run, coro).result()  # type: ignore[arg-type]


async def _save_edge_tts_hq(
    edge_tts_mod: Any,
    text: str,
    raw_mp3: str,
    voice: str,
    rate: str,
    pitch: str,
    output_format: str = EDGE_HQ_OUTPUT_FORMAT,
) -> None:
    """Fetches higher-bitrate 96kbps Edge-TTS MP3 stream over WebSocket, falling back to default Save."""
    try:
        import aiohttp
        import certifi
        from edge_tts import communicate as comm_mod, drm
        from edge_tts.constants import SEC_MS_GEC_VERSION, WSS_HEADERS, WSS_URL

        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        tts_cfg = comm_mod.TTSConfig(voice=voice, rate=rate, volume="+0%", pitch=pitch, boundary="SentenceBoundary")
        chunks = comm_mod.split_text_by_byte_length(comm_mod.escape(comm_mod.remove_incompatible_characters(text)), 4096)
        audio_buf = bytearray()
        timeout = aiohttp.ClientTimeout(total=None, connect=None, sock_connect=10, sock_read=60)
        for chunk in chunks:
            ws_url = (
                f"{WSS_URL}&ConnectionId={comm_mod.connect_id()}"
                f"&Sec-MS-GEC={drm.DRM.generate_sec_ms_gec()}"
                f"&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}"
            )
            async with aiohttp.ClientSession(trust_env=True, timeout=timeout) as session:
                async with session.ws_connect(
                    ws_url, compress=15, headers=drm.DRM.headers_with_muid(WSS_HEADERS), ssl=ssl_ctx
                ) as ws:
                    await ws.send_str(
                        f"X-Timestamp:{comm_mod.date_to_string()}\r\n"
                        "Content-Type:application/json; charset=utf-8\r\n"
                        "Path:speech.config\r\n\r\n"
                        '{"context":{"synthesis":{"audio":{"metadataoptions":{'
                        '"sentenceBoundaryEnabled":"true","wordBoundaryEnabled":"false"'
                        f'}},"outputFormat":"{output_format}"}}}}\r\n'
                    )
                    await ws.send_str(
                        comm_mod.ssml_headers_plus_data(
                            comm_mod.connect_id(), comm_mod.date_to_string(), comm_mod.mkssml(tts_cfg, chunk)
                        )
                    )
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.BINARY and len(msg.data) >= 2:
                            hlen = int.from_bytes(msg.data[:2], "big")
                            if b"Path:audio" in msg.data[2 : 2 + hlen]:
                                audio_buf.extend(msg.data[2 + hlen :])
                        elif msg.type == aiohttp.WSMsgType.TEXT and "Path:turn.end" in msg.data:
                            break
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            break
        if len(audio_buf) > 512:
            with open(raw_mp3, "wb") as f:
                f.write(audio_buf)
            return
    except Exception as exc:
        print(f"[Stage1-TTS] HQ 96kbps stream fallback to standard edge_tts.Communicate: {exc}", flush=True)

    comm = edge_tts_mod.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
    await comm.save(raw_mp3)


def _attempt_single_edge_tts(
    edge_tts_mod: Any,
    text: str,
    raw_mp3: str,
    voice: str,
    rate: str,
    pitch: str,
    pacing_sec: float,
) -> None:
    """Executes one paced Microsoft Edge-TTS synthesis call inside the global rate-limit lock."""
    global _EDGE_LAST_CALL

    with _EDGE_TTS_SEMAPHORE:
        with _EDGE_COOLDOWN_LOCK:
            now = time.monotonic()
            wait_until = max(_EDGE_COOLDOWN_UNTIL, _EDGE_LAST_CALL + max(0.25, float(pacing_sec or 0.25)))
        if wait_until > now:
            time.sleep(wait_until - now)
        _run_coroutine_sync(_save_edge_tts_hq(edge_tts_mod, text, raw_mp3, voice, rate, pitch))
        with _EDGE_COOLDOWN_LOCK:
            _EDGE_LAST_CALL = time.monotonic()


def synthesize_with_edge_tts(
    text: str,
    output_wav: str,
    voice: str = EDGE_DEFAULT_VOICE,
    rate: str = EDGE_DEFAULT_RATE,
    pitch: str = EDGE_DEFAULT_PITCH,
    sample_rate: int = 24000,
    max_retries: int = 8,
    pacing_sec: float = EDGE_DEFAULT_PACING_SEC,
    target_lufs: float = -18.0,
    noise_gate_threshold: float = 0.008,
    clean_pre_model: bool = True,
    fallback_warnings: Optional[List[Dict[str, str]]] = None,
    fallback_voice: str = "th-TH-NiwatNeural",
    fallback_pitch: str = "+22Hz",
    pre_rvc_highpass_hz: int = 85,
    pre_rvc_lowpass_hz: int = 11500,
    pre_rvc_denoise: bool = True,
) -> float:
    """Synthesizes speech with edge-tts, auto-recovering via configurable fallback voice on NoAudioReceived."""
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("edge-tts is not installed. Install requirements.txt or use --dry-run.") from exc

    safe_text = _sanitize_tts_text(text)
    last_err: Optional[Exception] = None
    fallback_candidates: List[tuple[str, str]] = [(voice, pitch)]
    if fallback_voice and fallback_voice != voice:
        fallback_candidates.append((fallback_voice, fallback_pitch))

    with tempfile.TemporaryDirectory() as tmp_dir:
        raw_mp3 = os.path.join(tmp_dir, "edge_raw.mp3")
        global _EDGE_COOLDOWN_UNTIL
        for v_idx, (cand_voice, cand_pitch) in enumerate(fallback_candidates):
            tries = max_retries if v_idx == 0 else 2
            for attempt in range(1, tries + 1):
                try:
                    _attempt_single_edge_tts(edge_tts, safe_text, raw_mp3, cand_voice, rate, cand_pitch, pacing_sec)
                    if v_idx > 0:
                        warn_msg = (
                            f"[WARN][Stage1-TTS] Primary voice '{voice}' failed ({type(last_err).__name__}); "
                            f"recovered '{os.path.basename(output_wav)}' via '{cand_voice}' (pitch={cand_pitch})."
                        )
                        print(warn_msg, flush=True)
                        if fallback_warnings is not None:
                            fallback_warnings.append({
                                "file": os.path.basename(output_wav),
                                "primary_voice": voice,
                                "fallback_voice": cand_voice,
                                "reason": f"{type(last_err).__name__}: {last_err}",
                            })
                    if clean_pre_model:
                        return condition_stage1_pre_rvc_wav(
                            raw_mp3,
                            output_wav,
                            sample_rate=sample_rate,
                            target_lufs=target_lufs,
                            noise_gate_threshold=noise_gate_threshold,
                            bit_depth=24,
                            highpass_hz=pre_rvc_highpass_hz,
                            lowpass_hz=pre_rvc_lowpass_hz,
                            denoise=pre_rvc_denoise,
                        )
                    return convert_to_pcm_wav(raw_mp3, output_wav, sample_rate=sample_rate, bit_depth=24, clean_pre_model=False)
                except Exception as exc:
                    last_err = exc
                    wait_sec = min(12.0, 1.5 * attempt) + random.uniform(0.3, 0.9)
                    with _EDGE_COOLDOWN_LOCK:
                        _EDGE_COOLDOWN_UNTIL = max(_EDGE_COOLDOWN_UNTIL, time.monotonic() + wait_sec)
                    if attempt >= 2 or v_idx > 0:
                        print(
                            f"[WARN][Stage1-TTS retry {attempt}/{tries}] {cand_voice} ({os.path.basename(output_wav)}): "
                            f"{type(exc).__name__}; cooling {wait_sec:.2f}s...",
                            flush=True,
                        )
                    time.sleep(wait_sec)

    raise RuntimeError(f"edge-tts failed across primary + fallback voices for {os.path.basename(output_wav)}: {last_err}")


def read_wav_duration_safe(wav_path: str) -> Optional[float]:
    """Reads WAV duration if file exists and is valid PCM WAV, else returns None."""
    if not os.path.isfile(wav_path) or os.path.getsize(wav_path) <= 1024:
        return None
    try:
        with wave.open(wav_path, "rb") as wf:
            frames, rate = wf.getnframes(), wf.getframerate()
            if frames > 0 and rate > 0:
                return frames / float(rate)
    except (wave.Error, OSError, EOFError) as exc:
        print(f"[Stage1] Unreadable WAV {wav_path}: {exc}")
    return None


def synthesize_stage1_batch(
    tasks: List[Dict[str, str]],
    voice: str = EDGE_DEFAULT_VOICE,
    rate: str = EDGE_DEFAULT_RATE,
    pitch: str = EDGE_DEFAULT_PITCH,
    sample_rate: int = 40000,
    workers: int = 4,
    pacing_sec: float = EDGE_DEFAULT_PACING_SEC,
    target_lufs: float = -18.0,
    noise_gate_threshold: float = 0.008,
    dry_run: bool = False,
    resume: bool = True,
    on_progress: Optional[Callable[[int, int], None]] = None,
    fallback_warnings: Optional[List[Dict[str, str]]] = None,
    clean_pre_model: bool = True,
    fallback_voice: str = "th-TH-NiwatNeural",
    fallback_pitch: str = "+22Hz",
    pre_rvc_highpass_hz: int = 85,
    pre_rvc_lowpass_hz: int = 11500,
    pre_rvc_denoise: bool = True,
) -> Dict[str, float]:
    """Synthesizes Stage 1 WAVs concurrently with pre-model Noise Gate + LUFS normalization."""
    durations: Dict[str, float] = {}
    pending: List[Dict[str, str]] = []
    for task in tasks:
        out_wav = task["out_wav"]
        os.makedirs(os.path.dirname(os.path.abspath(out_wav)), exist_ok=True)
        existing_dur = read_wav_duration_safe(out_wav) if resume else None
        if existing_dur is not None:
            durations[out_wav] = existing_dur
        else:
            pending.append(task)

    total = len(tasks)
    completed = len(durations)
    if on_progress and total > 0:
        on_progress(completed, total)
    if not pending:
        return durations

    if dry_run:
        for task in pending:
            hint = task.get("duration_hint_text") or task["text"]
            est = estimate_speech_duration(hint, speed=0.90)
            generate_dry_run_audio(task["out_wav"], est, sample_rate=sample_rate)
            durations[task["out_wav"]] = est
            completed += 1
            if on_progress:
                on_progress(completed, total)
        return durations

    clean_pitch = "+0Hz" if pitch == "-0Hz" else pitch
    n_workers = max(1, min(int(workers), 4, len(pending)))
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futs = [
            (
                t["out_wav"],
                pool.submit(
                    synthesize_with_edge_tts,
                    text=t["text"],
                    output_wav=t["out_wav"],
                    voice=voice,
                    rate=rate,
                    pitch=clean_pitch,
                    sample_rate=sample_rate,
                    pacing_sec=pacing_sec,
                    target_lufs=target_lufs,
                    noise_gate_threshold=noise_gate_threshold,
                    clean_pre_model=clean_pre_model,
                    fallback_warnings=fallback_warnings,
                    fallback_voice=fallback_voice,
                    fallback_pitch=fallback_pitch,
                    pre_rvc_highpass_hz=pre_rvc_highpass_hz,
                    pre_rvc_lowpass_hz=pre_rvc_lowpass_hz,
                    pre_rvc_denoise=pre_rvc_denoise,
                ),
            )
            for t in pending
        ]
        for out_wav, fut in futs:
            durations[out_wav] = fut.result()
            completed += 1
            if on_progress:
                on_progress(completed, total)
    return durations
