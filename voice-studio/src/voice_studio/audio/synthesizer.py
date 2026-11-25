# -*- coding: utf-8 -*-
"""Clean extensible VoiceSynthesizer for Stage 1 Edge-TTS, Stage 2 RVC v2, and Dry-Run."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from voice_studio.audio.mastering import apply_clean_speech_mastering, concatenate_wav_items
from voice_studio.audio.stage1_tts import (
    estimate_speech_duration,
    generate_dry_run_audio,
    read_wav_duration_safe,
    synthesize_with_edge_tts,
)
from voice_studio.audio.stage2_rvc import convert_stage1_to_rvc_batch, synthesize_edge_rvc_item
from voice_studio.config.models import VoiceProfile
from voice_studio.config.voices import build_voice_profile, profile_to_rvc_dict
from voice_studio.content.spacing import add_thai_clause_spacing


class VoiceSynthesizer:
    """Orchestrates Stage 1 Edge-TTS -> optional Stage 2 RVC v2 -> Stage 3 Noise Gate mastering."""

    def __init__(
        self,
        profile: Optional[VoiceProfile] = None,
        dry_run: bool = False,
        stage1_workers: int = 4,
        cpu_workers: Optional[int] = None,
    ) -> None:
        self.profile = profile or build_voice_profile({"profile": "gawr_gura"})
        self.dry_run = dry_run
        self.stage1_workers = stage1_workers
        self.cpu_workers = cpu_workers

    def synthesize_item(self, text: str, output_path: str, duration_hint_text: str = "") -> float:
        """Synthesizes a single narration segment to mono mastered PCM WAV (16/24-bit)."""
        s1, s2, m = self.profile.stage1, self.profile.stage2, self.profile.mastering
        if self.dry_run:
            est = estimate_speech_duration(duration_hint_text or text)
            generate_dry_run_audio(output_path, est, sample_rate=m.sample_rate)
            return est
        if s2.enabled and s2.backend == "rvc_v2":
            if str(s1.backend or "edge_tts").lower() != "edge_tts":
                from voice_studio.audio.stage1_hf_tts import synthesize_with_hf_tts

                stage1_tmp = f"{output_path}.stage1.wav"
                try:
                    synthesize_with_hf_tts(
                        text,
                        stage1_tmp,
                        backend=str(s1.backend),
                        model_repo=str(getattr(s1, "hf_model_repo", "kunato/wayu-kokoro-thai-v1")),
                        speaker=str(getattr(s1, "hf_speaker", "f_mid_warm")),
                        speed=float(getattr(s1, "hf_speed", 0.96)),
                        sample_rate=int(s1.sample_rate or m.sample_rate),
                        target_lufs=float(m.target_lufs),
                        noise_gate_threshold=float(m.noise_gate_threshold),
                    )
                    convert_stage1_to_rvc_batch(
                        [{"stage1_wav": stage1_tmp, "out_wav": output_path}],
                        profile=self.profile,
                        cpu_workers=self.cpu_workers,
                        resume=False,
                    )
                    return float(read_wav_duration_safe(output_path) or 0.0)
                finally:
                    if os.path.exists(stage1_tmp):
                        os.remove(stage1_tmp)
            return synthesize_edge_rvc_item(
                text=text,
                out_wav=output_path,
                voice_id=self.profile.voice_id,
                edge_voice=s1.voice,
                edge_rate=s1.rate,
                edge_pitch=s1.pitch,
                rvc_cfg=profile_to_rvc_dict(self.profile),
            )
        raw_path = f"{output_path}.raw.wav"
        try:
            if str(s1.backend or "edge_tts").lower() != "edge_tts":
                from voice_studio.audio.stage1_hf_tts import synthesize_with_hf_tts

                synthesize_with_hf_tts(
                    text,
                    raw_path,
                    backend=str(s1.backend),
                    model_repo=str(getattr(s1, "hf_model_repo", "kunato/wayu-kokoro-thai-v1")),
                    speaker=str(getattr(s1, "hf_speaker", "f_mid_warm")),
                    speed=float(getattr(s1, "hf_speed", 0.96)),
                    sample_rate=int(m.sample_rate),
                    target_lufs=float(m.target_lufs),
                    noise_gate_threshold=float(m.noise_gate_threshold),
                )
            else:
                synthesize_with_edge_tts(
                    text=text,
                    output_wav=raw_path,
                    voice=s1.voice,
                    rate=s1.rate,
                    pitch=s1.pitch,
                    sample_rate=m.sample_rate,
                )
            return apply_clean_speech_mastering(
                raw_path,
                output_path,
                sample_rate=m.sample_rate,
                target_lufs=m.target_lufs,
                denoise=m.denoise,
                noise_gate=m.noise_gate,
                noise_gate_threshold=m.noise_gate_threshold,
                bit_depth=m.bit_depth,
                resampler_precision=m.resampler_precision,
                declick=m.declick,
                denoise_nf_db=m.denoise_nf_db,
                denoise_nr_db=m.denoise_nr_db,
            )
        finally:
            if os.path.exists(raw_path):
                os.remove(raw_path)

    def concatenate_slide_audio(self, item_wavs: List[str], output_slide_wav: str, silence_sec: float = 0.40) -> float:
        """Concatenates item WAVs with silence padding and target LUFS normalization."""
        return concatenate_wav_items(
            item_wavs,
            output_slide_wav,
            silence_sec=silence_sec,
            default_sample_rate=int(self.profile.mastering.sample_rate),
            target_lufs=float(self.profile.mastering.target_lufs),
            bit_depth=int(self.profile.mastering.bit_depth),
        )

    def synthesize_manifest(
        self,
        slides_json_path: str,
        output_dir: str,
        only_slides: Optional[Set[int]] = None,
        resume: bool = True,
    ) -> Dict[str, Any]:
        """Synthesizes an entire slide/script JSON manifest into output_dir/audio_slides_wav/."""
        slides = json.loads(Path(slides_json_path).read_text(encoding="utf-8"))
        if isinstance(slides, dict):
            slides = list(slides.get("slides") or [])
        s1_dir = Path(output_dir) / "audio_stage1"
        raw_dir = Path(output_dir) / "audio_items_raw"
        slides_dir = Path(output_dir) / "audio_slides_wav"
        for d in (s1_dir, raw_dir, slides_dir):
            d.mkdir(parents=True, exist_ok=True)

        use_spacing = bool(self.profile.stage1.clause_spacing)
        tasks: List[Dict[str, str]] = []
        slide_texts: Dict[int, List[str]] = {}
        for idx, sl in enumerate(slides, start=1):
            s_no = int(sl.get("slide_no", idx))
            if only_slides is not None and s_no not in only_slides:
                continue
            raw_items = list(sl.get("tts_items") or sl.get("item_scripts") or [sl.get("script", "")])
            items = [add_thai_clause_spacing(str(t)) if use_spacing else str(t).strip() for t in raw_items]
            slide_texts[s_no] = items
            for j, txt in enumerate(items):
                item_name = f"slide_{s_no:02d}_item_{j:02d}.wav"
                s1_wav = str(s1_dir / item_name)
                if read_wav_duration_safe(s1_wav) is None or not resume:
                    if self.dry_run:
                        self.synthesize_item(txt, s1_wav, duration_hint_text=txt)
                    elif str(self.profile.stage1.backend or "edge_tts").lower() != "edge_tts":
                        from voice_studio.audio.stage1_hf_tts import synthesize_with_hf_tts

                        synthesize_with_hf_tts(
                            txt,
                            s1_wav,
                            backend=str(self.profile.stage1.backend),
                            model_repo=str(getattr(self.profile.stage1, "hf_model_repo", "kunato/wayu-kokoro-thai-v1")),
                            speaker=str(getattr(self.profile.stage1, "hf_speaker", "f_mid_warm")),
                            speed=float(getattr(self.profile.stage1, "hf_speed", 0.96)),
                            sample_rate=int(self.profile.stage1.sample_rate or self.profile.mastering.sample_rate),
                            target_lufs=float(self.profile.mastering.target_lufs),
                            noise_gate_threshold=float(self.profile.mastering.noise_gate_threshold),
                        )
                    else:
                        synthesize_with_edge_tts(
                            txt,
                            s1_wav,
                            voice=self.profile.stage1.voice,
                            rate=self.profile.stage1.rate,
                            pitch=self.profile.stage1.pitch,
                            sample_rate=self.profile.stage1.sample_rate,
                            pacing_sec=self.profile.stage1.pacing_sec,
                        )
                tasks.append({"stage1_wav": s1_wav, "out_wav": str(raw_dir / item_name)})

        if not self.dry_run and self.profile.stage2.enabled and self.profile.stage2.backend == "rvc_v2" and tasks:
            convert_stage1_to_rvc_batch(tasks, profile=self.profile, cpu_workers=self.cpu_workers, resume=resume)

        manifest_slides: List[Dict[str, Any]] = []
        silence_sec = float(self.profile.mastering.inter_item_silence)
        for idx, sl in enumerate(slides, start=1):
            s_no = int(sl.get("slide_no", idx))
            if only_slides is not None and s_no not in only_slides:
                continue
            items = slide_texts[s_no]
            item_paths = []
            for j in range(len(items)):
                s1_p = str(s1_dir / f"slide_{s_no:02d}_item_{j:02d}.wav")
                out_p = str(raw_dir / f"slide_{s_no:02d}_item_{j:02d}.wav")
                if not self.dry_run and read_wav_duration_safe(out_p) is not None:
                    item_paths.append(out_p)
                else:
                    if os.path.isfile(out_p) and read_wav_duration_safe(out_p) is None:
                        os.remove(out_p)
                    item_paths.append(s1_p)
            slide_wav = str(slides_dir / f"slide_{s_no:02d}.wav")
            dur = self.concatenate_slide_audio(item_paths, slide_wav, silence_sec=silence_sec)
            manifest_slides.append({"slide_no": s_no, "total_duration_sec": dur, "audio_file": f"audio_slides_wav/slide_{s_no:02d}.wav"})

        manifest_doc = {
            "voice_id": self.profile.voice_id,
            "attribution": self.profile.attribution,
            "slides": manifest_slides,
        }
        (Path(output_dir) / "voice_manifest.json").write_text(json.dumps(manifest_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest_doc
