# -*- coding: utf-8 -*-
"""3-Tier Pre-Render Audio Quality Validation Gate (File Integrity, Duration Budget, Acoustics)."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from voice_studio.audio.mastering import measure_wav_acoustics


@dataclass
class AudioGateResult:
    exit_code: int
    tier1_files_passed: bool
    tier2_duration_passed: bool
    tier3_acoustics_passed: bool
    violations: List[str] = field(default_factory=list)
    measured_metrics: Dict[int, Dict[str, Any]] = field(default_factory=dict)


def compute_audio_metrics(wav_path: str) -> Dict[str, float]:
    """Measures duration, peak dBFS, noise floor dBFS, SNR (dB), and BS.1770 LUFS."""
    return measure_wav_acoustics(wav_path)


def audit_phonetics_zero_english(phonetic_json_path: str) -> Tuple[bool, List[str]]:
    """Audits phonetic JSON to ensure ZERO Latin characters remain in item_scripts."""
    if not os.path.exists(phonetic_json_path):
        return False, [f"Phonetic JSON not found: {phonetic_json_path}"]

    with open(phonetic_json_path, "r", encoding="utf-8") as f:
        slides = json.load(f)
    if isinstance(slides, dict):
        slides = slides.get("slides", [])

    eng_tokens: List[str] = []
    for s in slides:
        items = s.get("item_scripts") or [s.get("script", s.get("narration", ""))]
        for itm in items:
            matches = re.findall(r"[a-zA-Z]+", str(itm))
            if matches:
                eng_tokens.extend(matches)
    return (len(eng_tokens) == 0, eng_tokens)


def validate_audio_deliverables(
    job_dir: str,
    phonetic_json_path: str = "",
    timeline_json_path: str = "",
    expected_slides: int = 0,
    duration_tolerance: float = 1.0,
    min_snr: float = 28.0,
    target_lufs: float = -18.0,
    lufs_tolerance: float = 1.5,
    enforce_zero_latin: bool = False,
) -> AudioGateResult:
    """Validates job audio slides across file integrity, duration budgets, and SNR/LUFS acoustics."""
    violations: List[str] = []
    measured_metrics: Dict[int, Dict[str, Any]] = {}
    t1_passed, t2_passed, t3_passed = True, True, True

    audio_slides_dir = os.path.join(job_dir, "audio_slides_wav")
    if not os.path.isdir(audio_slides_dir):
        violations.append(f"Tier 1: Missing audio_slides_wav directory at {audio_slides_dir}")
        return AudioGateResult(1, False, False, False, violations, measured_metrics)

    budgets: Dict[int, float] = {}
    tl_cand = timeline_json_path or os.path.join(job_dir, "timeline_manifest.json")
    if os.path.isfile(tl_cand):
        try:
            tm_data = json.loads(open(tl_cand, "r", encoding="utf-8").read())
            slides_map = tm_data.get("slides", tm_data) if isinstance(tm_data, dict) else {}
            if isinstance(slides_map, dict):
                for k, v in slides_map.items():
                    if str(k).isdigit() and isinstance(v, dict) and "total_duration_sec" in v:
                        budgets[int(k)] = float(v["total_duration_sec"])
            elif isinstance(tm_data, list):
                for it in tm_data:
                    budgets[int(it["slide_no"])] = float(it["total_duration_sec"])
        except Exception as exc:
            violations.append(f"Tier 2: Error loading timeline budgets ({tl_cand}): {exc}")

    voice_manifest_path = os.path.join(job_dir, "voice_manifest.json")
    if not budgets and os.path.isfile(voice_manifest_path):
        try:
            vm_data = json.loads(open(voice_manifest_path, "r", encoding="utf-8").read())
            slides_list = vm_data.get("slides", vm_data) if isinstance(vm_data, dict) else vm_data
            for s in slides_list:
                budgets[int(s["slide_no"])] = float(s.get("total_duration_sec", s.get("duration_sec", 0.0)))
        except Exception as err:
            violations.append(f"Tier 2: Failed loading voice_manifest budgets: {err}")

    slide_numbers: List[int] = []
    if phonetic_json_path and os.path.isfile(phonetic_json_path):
        raw_p = json.loads(open(phonetic_json_path, "r", encoding="utf-8").read())
        p_slides = raw_p.get("slides", []) if isinstance(raw_p, dict) else raw_p
        if isinstance(p_slides, list) and p_slides:
            slide_numbers = [int(s.get("slide_no", i)) for i, s in enumerate(p_slides, 1)]
    if not slide_numbers and expected_slides > 0:
        slide_numbers = list(range(1, expected_slides + 1))
    if not slide_numbers:
        wav_files = sorted(fn for fn in os.listdir(audio_slides_dir) if fn.startswith("slide_") and fn.endswith(".wav"))
        slide_numbers = [int(fn.replace("slide_", "").replace(".wav", "")) for fn in wav_files if fn[6:-4].isdigit()]
    if not slide_numbers:
        violations.append("Tier 1: No slide WAV files found to validate.")
        return AudioGateResult(1, False, False, False, violations, measured_metrics)

    for s_no in slide_numbers:
        wav_file = os.path.join(audio_slides_dir, f"slide_{s_no:02d}.wav")
        if not os.path.isfile(wav_file) or os.path.getsize(wav_file) <= 44:
            violations.append(f"Tier 1: Missing or empty WAV file for Segment {s_no}: {wav_file}")
            t1_passed = False
            continue
        metrics = measure_wav_acoustics(wav_file)
        measured_metrics[s_no] = metrics
        actual_dur = metrics["duration_sec"]
        if actual_dur <= 0.0:
            violations.append(f"Tier 1: Invalid zero duration for Segment {s_no}")
            t1_passed = False
            continue
        if s_no in budgets and budgets[s_no] > 0:
            budget_dur = budgets[s_no]
            dev = abs(actual_dur - budget_dur) / budget_dur
            if dev > duration_tolerance:
                violations.append(
                    f"Tier 2: Segment {s_no} duration {actual_dur:.2f}s deviates by {dev*100:.1f}% "
                    f"from budget {budget_dur:.2f}s (tolerance: {duration_tolerance*100:.0f}%)"
                )
                t2_passed = False
        if metrics["snr_db"] < min_snr:
            violations.append(f"Tier 3: Segment {s_no} SNR {metrics['snr_db']:.1f} dB < {min_snr:.1f} dB")
            t3_passed = False
        if abs(metrics["loudness_lufs"] - target_lufs) > lufs_tolerance:
            violations.append(
                f"Tier 3: Segment {s_no} Loudness {metrics['loudness_lufs']:.1f} LUFS outside {target_lufs} +- {lufs_tolerance}"
            )
            t3_passed = False

    if enforce_zero_latin and phonetic_json_path:
        zero_eng_pass, eng_tokens = audit_phonetics_zero_english(phonetic_json_path)
        if not zero_eng_pass:
            violations.append(f"Tier 3: Phonetic file contains {len(eng_tokens)} uncleaned Latin tokens: {eng_tokens[:8]}")
            t3_passed = False

    exit_code = 0 if (t1_passed and t2_passed and t3_passed and not violations) else 1
    return AudioGateResult(exit_code, t1_passed, t2_passed, t3_passed, violations, measured_metrics)


def print_gate_report(label: str, result: AudioGateResult) -> None:
    """Prints formatted QA summary table and pass/fail status."""
    print(f"=== AUDIO QUALITY VALIDATION GATE [{label}] ===")
    print(f"  Tier 1 (WAV Files Integrity): {'PASS' if result.tier1_files_passed else 'FAIL'}")
    print(f"  Tier 2 (Duration Budgets)   : {'PASS' if result.tier2_duration_passed else 'FAIL'}")
    print(f"  Tier 3 (Acoustics SNR/LUFS) : {'PASS' if result.tier3_acoustics_passed else 'FAIL'}")
    if result.measured_metrics:
        durs = [m["duration_sec"] for m in result.measured_metrics.values()]
        snrs = [m["snr_db"] for m in result.measured_metrics.values()]
        lufs = [m["loudness_lufs"] for m in result.measured_metrics.values()]
        peaks = [m.get("peak_dbfs", -100.0) for m in result.measured_metrics.values()]
        print(
            f"  Measured ({len(durs)} segments): Total={sum(durs):.1f}s | "
            f"Min SNR={min(snrs):.1f}dB | LUFS=[{min(lufs):.1f}, {max(lufs):.1f}] | Max Peak={max(peaks):.2f}dBFS"
        )
    if result.violations:
        for v in result.violations[:12]:
            print(f"  [VIOLATION] {v}")
    print(f"  OVERALL GATE RESULT: {'PASS (EXIT 0)' if result.exit_code == 0 else 'FAIL (EXIT 1)'}")
