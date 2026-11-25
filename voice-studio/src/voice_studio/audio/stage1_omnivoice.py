"""Stage 1 OmniVoice-Thai (Qwen3-0.6B) zero-shot voice cloning backend."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
from typing import Any, List, Optional, Tuple

import numpy as np
import soundfile as sf

from voice_studio.audio.mastering import condition_stage1_pre_rvc_wav

_OV_LOCK = threading.Lock()
_OV_CACHE: dict[str, Any] = {}
DEFAULT_OMNIVOICE_REPO = "hotdogs/omnivoice-thai"
DEFAULT_REF_TEXT = None


def _resolve_ref_audio() -> str:
    here = Path(__file__).resolve()
    candidates = [
        here.parent / "assets" / "gawr_gura_ref.wav",
        here.parents[3] / "tools" / "omnivoice_thai_studio" / "assets" / "gawr_gura_ref.wav",
        Path("/content/f5_gura_ref_exact.wav"),
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    raise FileNotFoundError(f"OmniVoice reference WAV not found in {candidates}")


def _load_omnivoice(model_repo: str) -> Any:
    repo = (model_repo or DEFAULT_OMNIVOICE_REPO).strip()
    with _OV_LOCK:
        if repo in _OV_CACHE:
            return _OV_CACHE[repo]
        try:
            import torch
            from omnivoice import OmniVoice
        except ImportError:
            import subprocess, sys
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "omnivoice"])
            import torch
            from omnivoice import OmniVoice
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if device.startswith("cuda") else torch.float32
        model = OmniVoice.from_pretrained(repo, device_map=device, dtype=dtype, load_asr=True)
        _OV_CACHE[repo] = model
        return model


def _apply_vocos_polish(wav: np.ndarray) -> np.ndarray:
    """Applies charactr/vocos-mel-24khz mel-resynthesis to strip DAC codec reverb/boxiness."""
    import torch

    with _OV_LOCK:
        vocos = _OV_CACHE.get("__vocos__")
        if vocos is None:
            try:
                from vocos import Vocos
            except ImportError:
                import subprocess, sys
                subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "vocos"])
                from vocos import Vocos
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            vocos = Vocos.from_pretrained("charactr/vocos-mel-24khz").to(device)
            vocos.eval()
            _OV_CACHE["__vocos__"] = vocos
    device = next(vocos.parameters()).device
    with torch.no_grad():
        y_t = torch.from_numpy(wav.astype(np.float32)).unsqueeze(0).to(device)
        feats = vocos.feature_extractor(y_t)
        return vocos.decode(feats).squeeze().detach().cpu().numpy().astype(np.float32)


def synthesize_with_omnivoice_thai(
    text: str,
    output_wav: str,
    chunks: List[Tuple[str, float]],
    *,
    model_repo: str = DEFAULT_OMNIVOICE_REPO,
    ref_audio: Optional[str] = None,
    ref_text: Optional[str] = DEFAULT_REF_TEXT,
    sample_rate: int = 48000,
    target_lufs: float = -18.0,
    noise_gate_threshold: float = 0.005,
    use_vocos: bool = True,
) -> float:
    """Synthesizes Thai+English speech using hotdogs/omnivoice-thai + Vocos dry reconstruction."""
    model = _load_omnivoice(model_repo)
    ref_wav = ref_audio or _resolve_ref_audio()
    out_path = Path(output_wav)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pieces: List[np.ndarray] = []
    for idx, (seg_txt, pause_after) in enumerate(chunks):
        if not seg_txt.strip():
            continue
        with _OV_LOCK:
            kwargs = {"text": seg_txt.strip(), "ref_audio": ref_wav}
            if ref_text:
                kwargs["ref_text"] = ref_text
            audio = model.generate(**kwargs)
        seg_wav = np.asarray(audio[0], dtype=np.float32).squeeze()
        if seg_wav.size > 0:
            if use_vocos:
                seg_wav = _apply_vocos_polish(seg_wav)
            pieces.append(seg_wav)
            if pause_after > 0.0 and idx < len(chunks) - 1:
                pieces.append(np.zeros(int(round(pause_after * 24000)), dtype=np.float32))

    wav = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
    if wav.size == 0:
        raise RuntimeError(f"OmniVoice-Thai synthesized zero samples for text={text!r}")

    fd, raw_tmp = tempfile.mkstemp(prefix="omnivoice_", suffix=".wav", dir=str(out_path.parent))
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
