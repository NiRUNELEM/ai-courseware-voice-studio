"""Stage 1 F5-TTS-TH-V2 (Vocos) zero-shot Thai voice cloning backend."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
from typing import Any, List, Optional, Tuple

import numpy as np
import soundfile as sf

from voice_studio.audio.mastering import condition_stage1_pre_rvc_wav

_F5_LOCK = threading.Lock()
_F5_CACHE: dict[str, Any] = {}
DEFAULT_F5_REF_TEXT = "สวัสดีค่ะ บทที่ 11 การกำหนดและตรวจสอบสิทธิ์"


def _resolve_f5_ref_audio() -> str:
    here = Path(__file__).resolve()
    candidates = [
        here.parent / "assets" / "gawr_gura_ref.wav",
        here.parents[3] / "tools" / "omnivoice_thai_studio" / "assets" / "gawr_gura_ref.wav",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    raise FileNotFoundError(f"F5-TTS-TH-V2 reference WAV not found in {candidates}")


def _load_f5_tts(version: str = "v2") -> Any:
    key = (version or "v2").strip().lower()
    with _F5_LOCK:
        if key in _F5_CACHE:
            return _F5_CACHE[key]
        try:
            from f5_tts_th.tts import TTS
        except ImportError:
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "f5-tts-th"])
            from f5_tts_th.tts import TTS
        model = TTS(model=key)
        _F5_CACHE[key] = model
        return model


def synthesize_with_f5_tts_th(
    text: str,
    output_wav: str,
    chunks: List[Tuple[str, float]],
    *,
    ref_audio: Optional[str] = None,
    ref_text: str = DEFAULT_F5_REF_TEXT,
    speed: float = 0.96,
    sample_rate: int = 48000,
    target_lufs: float = -18.0,
    noise_gate_threshold: float = 0.005,
) -> float:
    """Synthesizes dry, crisp Thai speech using VIZINTZOR/F5-TTS-TH-V2 + Vocos."""
    tts = _load_f5_tts("v2")
    ref_wav = ref_audio or _resolve_f5_ref_audio()
    out_path = Path(output_wav)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pieces: List[np.ndarray] = []
    for idx, (seg_txt, pause_after) in enumerate(chunks):
        if not seg_txt.strip():
            continue
        with _F5_LOCK:
            seg_wav = np.asarray(
                tts.infer(ref_audio=ref_wav, ref_text=ref_text, gen_text=seg_txt.strip(), step=32, cfg=2.0, speed=float(speed)),
                dtype=np.float32,
            ).squeeze()
        if seg_wav.size > 0:
            pieces.append(seg_wav)
            if pause_after > 0.0 and idx < len(chunks) - 1:
                pieces.append(np.zeros(int(round(pause_after * 24000)), dtype=np.float32))

    wav = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
    if wav.size == 0:
        raise RuntimeError(f"F5-TTS-TH-V2 synthesized zero samples for text={text!r}")

    fd, raw_tmp = tempfile.mkstemp(prefix="f5_th_", suffix=".wav", dir=str(out_path.parent))
    os.close(fd)
    try:
        sf.write(raw_tmp, wav, 24000, subtype="FLOAT")
        return condition_stage1_pre_rvc_wav(
            raw_tmp,
            str(out_path),
            sample_rate=int(sample_rate),
            target_lufs=float(target_lufs),
            noise_gate_threshold=float(noise_gate_threshold),
        )
    finally:
        if os.path.exists(raw_tmp):
            os.remove(raw_tmp)
