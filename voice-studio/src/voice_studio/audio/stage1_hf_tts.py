# -*- coding: utf-8 -*-
"""Stage 1 HuggingFace Bilingual Thai+English Neural TTS Backend (Wayu Kokoro Thai v1 ONNX)."""

from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import tempfile
import threading
import types
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf

from voice_studio import PROJECT_ROOT
from voice_studio.audio.mastering import condition_stage1_pre_rvc_wav
from voice_studio.audio.stage1_tts import estimate_speech_duration, generate_dry_run_audio, read_wav_duration_safe

DEFAULT_HF_TTS_REPO = "kunato/wayu-kokoro-thai-v1"
DEFAULT_HF_SPEAKER = "f_mid_warm"
DEFAULT_HF_SPEED = 0.96

KOKORO_THAI_SPEAKERS: Dict[str, int] = {
    "f_teen_bright": 0,
    "spk00_f_teen_bright": 0,
    "f_young_bright": 1,
    "spk01_f_young_bright": 1,
    "f_young_clear": 2,
    "spk02_f_young_clear": 2,
    "f_young_warm": 3,
    "spk03_f_young_warm": 3,
    "f_mid_clear": 4,
    "spk04_f_mid_clear": 4,
    "f_mid_warm": 5,
    "spk05_f_mid_warm": 5,
    "f_elderly_soft": 6,
    "spk06_f_elderly_soft": 6,
    "f_elderly_low": 7,
    "spk07_f_elderly_low": 7,
    "m_teen_bright": 8,
    "spk08_m_teen_bright": 8,
    "m_young_clear": 9,
    "spk09_m_young_clear": 9,
    "m_mid_warm": 10,
    "spk10_m_mid_warm": 10,
    "m_elderly_deep": 11,
    "spk11_m_elderly_deep": 11,
}

_REQUIRED_REPO_FILES: Tuple[str, ...] = (
    "assets/kokoro_config.json",
    "data/seeds/roster.json",
    "kokoro_thai/__init__.py",
    "kokoro_thai/g2p.py",
    "kokoro_thai/name_gate.py",
    "kokoro_thai/normalize.py",
    "kokoro_thai/onnx_infer.py",
    "kokoro_thai/vocab.py",
    "kokoro_thai/word_level.py",
    "kokoro_thai/data/lexicon_pinned.tsv",
    "kokoro_thai/data/name_respell.tsv",
    "onnx/onnx_manifest.json",
    "onnx/prosody_fp32.onnx",
    "onnx/curves_fp32.onnx",
    "onnx/decoder_fp32.onnx",
    "onnx/source_params.npz",
    "onnx/styles.npz",
)

_SETUP_LOCK = threading.Lock()
_SYNTH_LOCK = threading.Lock()
_RUNTIME_READY = False
_ENGINE_CACHE: Dict[str, Tuple[Any, Dict[int, np.ndarray], Any]] = {}


class _NoopCorpusModule(types.ModuleType):
    """Lightweight tltk.corpus stand-in so tltk.nlp loads cleanly without gensim on Python 3.14+."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None


class _ShimSpacyToken:
    """Minimal token record matching the attributes read by misaki.en.G2P."""

    def __init__(self, text: str, tag: str, whitespace: str) -> None:
        self.text = text
        self.tag_ = tag
        self.whitespace_ = whitespace


def _shim_nlp_tokenize(text: str) -> List[_ShimSpacyToken]:
    tokens: List[_ShimSpacyToken] = []
    for match in re.finditer(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?|[^\sA-Za-z0-9]", text):
        word = match.group(0)
        end = match.end()
        ws = " " if end < len(text) and text[end].isspace() else ""
        if word.isupper() or (len(word) > 1 and word[:1].isupper()):
            tag = "NNP"
        elif word.isdigit():
            tag = "CD"
        else:
            tag = "NN"
        tokens.append(_ShimSpacyToken(word, tag, ws))
    return tokens


def _ensure_kokoro_packages() -> None:
    """Ensures ONNX + Thai/English G2P dependencies are installed in the active interpreter."""
    pip_pkgs: List[str] = []
    for mod_name, pkg_spec in (
        ("onnxruntime", "onnxruntime>=1.19.0"),
        ("pythainlp", "pythainlp>=5.0.0"),
        ("nltk", "nltk>=3.8"),
        ("pandas", "pandas>=2.2.0"),
        ("sklearn_crfsuite", "sklearn-crfsuite>=0.5.0"),
        ("regex", "regex>=2024.0"),
        ("addict", "addict>=2.4.0"),
        ("num2words", "num2words>=0.5.13"),
        ("phonemizer", "phonemizer-fork>=3.3.0"),
        ("espeakng_loader", "espeakng-loader>=0.2.4"),
    ):
        try:
            importlib.import_module(mod_name)
        except ImportError:
            pip_pkgs.append(pkg_spec)
    if pip_pkgs:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pip_pkgs], check=True)

    nodeps_pkgs: List[str] = []
    for mod_name, pkg_spec in (("tltk", "tltk==1.11"), ("misaki", "misaki==0.9.4")):
        if importlib.util.find_spec(mod_name) is None:
            nodeps_pkgs.append(pkg_spec)
    if nodeps_pkgs:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--ignore-requires-python", *nodeps_pkgs],
            check=True,
        )


def _configure_frontend_shims() -> None:
    """Configures bundled espeak-ng data/lib and Python 3.14-safe tltk/misaki shims."""
    global _RUNTIME_READY
    with _SETUP_LOCK:
        if _RUNTIME_READY:
            return
        _ensure_kokoro_packages()

        import espeakng_loader
        from phonemizer.backend.espeak.wrapper import EspeakWrapper

        EspeakWrapper.set_library(espeakng_loader.get_library_path())
        EspeakWrapper.set_data_path(espeakng_loader.get_data_path())

        if "tltk.corpus" not in sys.modules:
            try:
                importlib.import_module("tltk.corpus")
            except ImportError:
                sys.modules["tltk.corpus"] = _NoopCorpusModule("tltk.corpus")

        if "spacy" not in sys.modules:
            try:
                sp = importlib.import_module("spacy")
                if not sp.util.is_package("en_core_web_sm"):
                    raise LookupError("Missing en_core_web_sm")
            except (ImportError, LookupError):
                spacy_mod = types.ModuleType("spacy")
                spacy_mod.util = types.SimpleNamespace(is_package=lambda _name: True)  # type: ignore[attr-defined]
                spacy_mod.cli = types.SimpleNamespace(download=lambda _name: None)  # type: ignore[attr-defined]
                spacy_mod.load = lambda _name, *_args, **_kwargs: _shim_nlp_tokenize  # type: ignore[attr-defined]
                sys.modules["spacy"] = spacy_mod

        _RUNTIME_READY = True


def resolve_hf_speaker_id(speaker: str | int) -> int:
    """Resolves a speaker name or numeric index cleanly to a verified Kokoro speaker ID."""
    if isinstance(speaker, int):
        if 0 <= speaker <= 11:
            return speaker
        raise ValueError(f"Unknown HF Kokoro Thai speaker index: {speaker}")
    cleaned = str(speaker or DEFAULT_HF_SPEAKER).strip().lower()
    if cleaned.isdigit() and 0 <= int(cleaned) <= 11:
        return int(cleaned)
    if cleaned in KOKORO_THAI_SPEAKERS:
        return KOKORO_THAI_SPEAKERS[cleaned]
    raise ValueError(f"Unknown HF Kokoro Thai speaker {speaker!r}. Available: {sorted(KOKORO_THAI_SPEAKERS)}")


def _repo_cache_dir(repo_id: str) -> Path:
    safe_name = repo_id.replace("/", "__")
    base = Path(os.environ.get("VOICE_STUDIO_HF_CACHE") or (PROJECT_ROOT / "models" / "hf_tts"))
    return base / safe_name


def ensure_wayu_kokoro_repo(repo_id: str = DEFAULT_HF_TTS_REPO) -> Path:
    """Downloads the compact ONNX serving bundle and frontend code from HuggingFace once."""
    from huggingface_hub import hf_hub_download

    target_dir = _repo_cache_dir(repo_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    missing = [rel for rel in _REQUIRED_REPO_FILES if not (target_dir / rel).is_file()]
    if missing:
        for rel in missing:
            hf_hub_download(repo_id=repo_id, filename=rel, local_dir=str(target_dir))
    return target_dir


def _load_kokoro_engine(repo_id: str = DEFAULT_HF_TTS_REPO, intra_threads: int = 4) -> Tuple[Any, Dict[int, np.ndarray], Any]:
    with _SYNTH_LOCK:
        cached = _ENGINE_CACHE.get(repo_id)
        if cached is not None:
            return cached
        _configure_frontend_shims()
        repo_dir = ensure_wayu_kokoro_repo(repo_id)
        repo_str = str(repo_dir)
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)
        from kokoro_thai.g2p import ThaiG2P
        from kokoro_thai import onnx_infer

        g2p = ThaiG2P()
        student = onnx_infer.load_onnx(repo_dir / "onnx", g2p=g2p, intra_threads=intra_threads, precision="fp32")
        styles = onnx_infer.load_styles(repo_dir / "onnx")
        bundle = (student, styles, onnx_infer)
        _ENGINE_CACHE[repo_id] = bundle
        return bundle


def _clean_hf_segment(seg: str) -> str:
    s = seg.strip().strip(",").strip()
    if s.endswith("..."):
        return s
    return s.rstrip(".").strip()


def _cap_hf_chunk(seg_txt: str, pause_after: float, max_chunk_chars: int) -> List[Tuple[str, float]]:
    """Splits an oversized clause at comma or space boundaries to stay within Kokoro BERT's 512-token limit."""
    text = seg_txt.strip()
    if len(text) <= max_chunk_chars:
        return [(text, pause_after)]

    out: List[Tuple[str, float]] = []
    remaining = text
    while len(remaining) > max_chunk_chars:
        window = remaining[:max_chunk_chars]
        split_at = window.rfind(", ")
        sub_pause = 0.18
        step = 2
        if split_at < max_chunk_chars // 3:
            split_at = window.rfind(" ")
            sub_pause = 0.08
            step = 1
        if split_at <= 0:
            split_at = max_chunk_chars
            sub_pause = 0.05
            step = 0
        head = _clean_hf_segment(remaining[:split_at])
        if head:
            out.append((head, sub_pause))
        remaining = remaining[split_at + step :].strip()
    tail = _clean_hf_segment(remaining)
    if tail:
        out.append((tail, pause_after))
    elif out:
        last_txt, _ = out[-1]
        out[-1] = (last_txt, pause_after)
    return out


def _split_hf_sentences(
    clean_text: str,
    min_chunk_chars: int = 45,
    max_chunk_chars: int = 220,
) -> List[Tuple[str, float]]:
    """Splits multi-sentence Thai/bilingual script at major breaks while merging short clauses and capping max length."""
    text = " ".join((clean_text or "").split()).strip()
    if not text:
        return [("", 0.0)]
    parts = re.split(r"(\.\.\.\s+|\.\s+)", text)
    raw_items: List[Tuple[str, float]] = []
    i = 0
    while i < len(parts):
        seg = _clean_hf_segment(parts[i])
        sep = parts[i + 1] if i + 1 < len(parts) else ""
        i += 2
        if not seg:
            continue
        pause_sec = 0.85 if "..." in sep else (0.58 if "." in sep else 0.0)
        raw_items.append((seg, pause_sec))
    if not raw_items:
        return [(_clean_hf_segment(text) or text, 0.0)]

    merged: List[Tuple[str, float]] = []
    idx = 0
    while idx < len(raw_items):
        cur_txt, cur_pause = raw_items[idx]
        if len(cur_txt) < min_chunk_chars:
            if idx + 1 < len(raw_items) and len(cur_txt) + len(raw_items[idx + 1][0]) + 2 <= max_chunk_chars:
                nxt_txt, nxt_pause = raw_items[idx + 1]
                raw_items[idx + 1] = (f"{cur_txt.rstrip(' ,.')}, {nxt_txt.lstrip(' ,')}", nxt_pause)
                idx += 1
                continue
            if merged and len(merged[-1][0]) + len(cur_txt) + 2 <= max_chunk_chars:
                prev_txt, _ = merged[-1]
                merged[-1] = (f"{prev_txt.rstrip(' ,.')}, {cur_txt.lstrip(' ,')}", cur_pause)
                idx += 1
                continue
        merged.extend(_cap_hf_chunk(cur_txt, cur_pause, max_chunk_chars))
        idx += 1
    if merged:
        last_txt, _ = merged[-1]
        merged[-1] = (last_txt, 0.0)
    return merged


def synthesize_with_hf_tts(
    text: str,
    output_wav: str,
    *,
    backend: str = "hf_kokoro_thai",
    model_repo: str = DEFAULT_HF_TTS_REPO,
    speaker: str = DEFAULT_HF_SPEAKER,
    speed: float = DEFAULT_HF_SPEED,
    sample_rate: int = 24000,
    target_lufs: float = -18.0,
    noise_gate_threshold: float = 0.006,
    seed: int = 1234,
) -> float:
    """Synthesizes bilingual Thai+English speech using the verified HuggingFace TTS model."""
    cleaned_backend = (backend or "hf_kokoro_thai").strip().lower()
    clean_text = " ".join((text or "").split()).strip()
    sentence_chunks = _split_hf_sentences(clean_text)
    if cleaned_backend in ("f5_tts_th", "f5_th", "f5_tts_th_v2"):
        from voice_studio.audio.stage1_f5_tts import synthesize_with_f5_tts_th

        return synthesize_with_f5_tts_th(
            clean_text,
            output_wav,
            sentence_chunks,
            speed=float(speed),
            sample_rate=int(sample_rate),
            target_lufs=float(target_lufs),
            noise_gate_threshold=float(noise_gate_threshold),
        )
    if cleaned_backend in ("omnivoice_thai", "qwen3_omnivoice"):
        from voice_studio.audio.stage1_omnivoice import synthesize_with_omnivoice_thai

        return synthesize_with_omnivoice_thai(
            clean_text,
            output_wav,
            sentence_chunks,
            model_repo=model_repo or "hotdogs/omnivoice-thai",
            sample_rate=int(sample_rate),
            target_lufs=float(target_lufs),
            noise_gate_threshold=float(noise_gate_threshold),
        )
    if cleaned_backend not in ("hf_kokoro_thai", "wayu_kokoro_thai", "kokoro_thai"):
        raise ValueError(f"Unsupported HuggingFace TTS backend {backend!r}; expected 'hf_kokoro_thai' or 'omnivoice_thai'.")

    out_path = Path(output_wav)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    spk_id = resolve_hf_speaker_id(speaker)

    student, styles, onnx_infer = _load_kokoro_engine(model_repo or DEFAULT_HF_TTS_REPO)
    wav_pieces: List[np.ndarray] = []
    for idx, (seg_txt, pause_after) in enumerate(sentence_chunks):
        with _SYNTH_LOCK:
            seg_wav = onnx_infer.synth(student, seg_txt, styles[spk_id], speed=float(speed), seed=int(seed) + idx)
        if seg_wav.size > 0:
            wav_pieces.append(np.asarray(seg_wav, dtype=np.float32))
            if pause_after > 0.0 and idx < len(sentence_chunks) - 1:
                wav_pieces.append(np.zeros(int(round(pause_after * 24000)), dtype=np.float32))
    wav = np.concatenate(wav_pieces) if wav_pieces else np.zeros(0, dtype=np.float32)
    if wav.size == 0:
        raise RuntimeError(f"HuggingFace Kokoro Thai synthesized zero samples for text={clean_text!r}")

    fd, raw_tmp = tempfile.mkstemp(prefix="hf_kokoro_", suffix=".wav", dir=str(out_path.parent))
    os.close(fd)
    try:
        sf.write(raw_tmp, np.asarray(wav, dtype=np.float32), 24000, subtype="FLOAT")
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


def synthesize_stage1_hf_batch(
    tasks: List[Dict[str, Any]],
    *,
    backend: str = "hf_kokoro_thai",
    model_repo: str = DEFAULT_HF_TTS_REPO,
    speaker: str = DEFAULT_HF_SPEAKER,
    speed: float = DEFAULT_HF_SPEED,
    sample_rate: int = 24000,
    target_lufs: float = -18.0,
    noise_gate_threshold: float = 0.006,
    dry_run: bool = False,
    resume: bool = True,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, float]:
    """Batch Stage 1 HuggingFace TTS generator yielding pre-conditioned mono WAVs."""
    total = len(tasks)
    if total == 0:
        return {}
    durations: Dict[str, float] = {}
    for idx, task in enumerate(tasks, 1):
        wav_path = str(task.get("out_wav") or task["wav_path"])
        os.makedirs(os.path.dirname(os.path.abspath(wav_path)), exist_ok=True)
        text = str(task["text"])
        if resume and os.path.isfile(wav_path) and os.path.getsize(wav_path) > 44:
            durations[wav_path] = read_wav_duration_safe(wav_path)
        elif dry_run:
            dur = estimate_speech_duration(str(task.get("duration_hint_text") or text))
            generate_dry_run_audio(wav_path, dur, sample_rate=sample_rate)
            durations[wav_path] = dur
        else:
            durations[wav_path] = synthesize_with_hf_tts(
                text,
                wav_path,
                backend=backend,
                model_repo=model_repo,
                speaker=speaker,
                speed=speed,
                sample_rate=sample_rate,
                target_lufs=target_lufs,
                noise_gate_threshold=noise_gate_threshold,
            )
        if on_progress:
            on_progress(idx, total)
    return durations
