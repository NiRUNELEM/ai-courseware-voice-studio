#!/usr/bin/env python3
"""Verification suite for Standalone Voice & Media Studio Integrity, Input System, Models & Bundle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voice_studio.audio.mastering import apply_clean_speech_mastering, measure_wav_acoustics  # noqa: E402
from voice_studio.cli import main as cli_main  # noqa: E402
from voice_studio.content.validator import validate_input_source  # noqa: E402
from voice_studio.core.models_manager import verify_local_models  # noqa: E402
from voice_studio.video.compositor import detect_best_video_encoder  # noqa: E402
from voice_studio.video.renderer import _generate_fallback_slide_png  # noqa: E402


class TestToolIntegrityAndInputSystem(unittest.TestCase):
    def test_01_strict_decoupling_zero_courseware_or_alternative_leakage(self) -> None:
        self.assertTrue((ROOT / "studio.py").is_file())
        self.assertTrue((ROOT / "requirements.txt").is_file())
        self.assertTrue((ROOT / "configs/studio.yaml").is_file())
        self.assertTrue((ROOT / "configs/colab_pro.yaml").is_file())
        self.assertFalse((ROOT / "requirements-voice.txt").exists())
        self.assertFalse((ROOT / "requirements-colab.txt").exists())
        self.assertFalse((ROOT / "courseware_cli.py").exists())

        forbidden_pat = re.compile(
            r"chapter_10_authentication|chapter_11_authorization|feiyu|seedvc|f5_thai|thonburian|courseware|ep10_sid|Bun\.password|ElysiaJS",
            re.I,
        )
        for scan_root in (ROOT / "src/voice_studio", ROOT / "configs", ROOT / "input"):
            for fp in scan_root.rglob("*"):
                if fp.is_file() and fp.suffix in (".py", ".yaml", ".json", ".md", ".txt", ".srt"):
                    hits = forbidden_pat.findall(fp.read_text(encoding="utf-8"))
                    self.assertEqual(hits, [], f"Leak detected in {fp}: {hits}")

    def test_02_complete_input_system_templates_validation_and_thai_render(self) -> None:
        for req in (
            "input/README.md",
            "input/sample_neutral.json",
            "input/schema/input_script.schema.json",
            "input/templates/script_template.json",
            "input/templates/script_template.md",
            "input/templates/script_template.txt",
            "input/templates/subtitles_template.srt",
            "input/templates/custom_vocab.example.json",
        ):
            self.assertTrue((ROOT / req).is_file(), f"Missing input asset: {req}")

        for sample in (
            "input/sample_neutral.json",
            "input/templates/script_template.json",
            "input/templates/script_template.md",
            "input/templates/script_template.txt",
            "input/templates/subtitles_template.srt",
        ):
            rep = validate_input_source(sample, base_dir=ROOT)
            self.assertTrue(rep["valid"], f"Validation failed for {sample}: {rep['errors']}")
            self.assertGreaterEqual(rep["segment_count"], 1)

        self.assertEqual(cli_main(["input", "check", "input/sample_neutral.json"]), 0)
        created_rel = "_tmp_test_init.srt"
        try:
            self.assertEqual(cli_main(["input", "init", created_rel, "--format", "srt"]), 0)
            self.assertTrue((ROOT / "input" / created_rel).is_file())
        finally:
            (ROOT / "input" / created_rel).unlink(missing_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            png_out = Path(tmp) / "slide_thai.png"
            _generate_fallback_slide_png(png_out, 1, "ทดสอบสไลด์ภาษาไทยและ ดาต้าเบส", bullets=["รายการทดสอบที่ 1"])
            self.assertTrue(png_out.is_file())
            self.assertEqual(Image.open(png_out).size, (1920, 1080))

    def test_03_colab_bundle_zip_and_essential_models_verified(self) -> None:
        model_rep = verify_local_models(ROOT, verify_checksums=False)
        self.assertTrue(model_rep["passed"], f"Incomplete models/: {model_rep}")
        model_ids = {e["id"] for e in model_rep["entries"]}
        self.assertEqual(model_ids, {"gawr_gura_pth", "gawr_gura_index", "gawr_gura_metadata", "applio_contentvec", "applio_rmvpe"})

        self.assertEqual(cli_main(["bundle"]), 0)
        zip_path = ROOT / "exports/voice_studio_colab_bundle.zip"
        self.assertTrue(zip_path.is_file())
        with zipfile.ZipFile(zip_path, "r") as zf:
            self.assertIsNone(zf.testzip())
            names = set(zf.namelist())
            for req in (
                "studio.py",
                "README.md",
                "requirements.txt",
                "configs/studio.yaml",
                "configs/colab_pro.yaml",
                "models/manifest.json",
                "colab/voice_studio_colab.ipynb",
                "input/sample_neutral.json",
                "input/templates/subtitles_template.srt",
                "THIRD_PARTY_LICENSES.md",
            ):
                self.assertIn(req, names, f"Bundle missing {req}")
            for member in zf.namelist():
                disk_path = ROOT / member
                self.assertTrue(disk_path.is_file())
                self.assertEqual(hashlib.sha256(zf.read(member)).hexdigest(), hashlib.sha256(disk_path.read_bytes()).hexdigest())

    def test_04_mastering_acoustics_and_video_encoder_probe(self) -> None:
        if shutil.which("ffmpeg"):
            with tempfile.TemporaryDirectory() as tmp:
                import math
                import random

                raw_wav = os.path.join(tmp, "raw_40k.wav")
                master_wav = os.path.join(tmp, "master_40k.wav")
                sr = 40000
                rng = random.Random(42)
                frames = bytearray()
                for i in range(int(sr * 3.0)):
                    in_speech = (i % sr) < int(sr * 0.60)
                    val = 8200.0 * math.sin(2.0 * math.pi * 440.0 * i / float(sr)) if in_speech else max(-600, min(600, int(rng.gauss(0.0, 180.0))))
                    frames.extend(struct.pack("<h", int(val)))
                with wave.open(raw_wav, "wb") as wf:
                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr); wf.writeframes(bytes(frames))
                dur = apply_clean_speech_mastering(raw_wav, master_wav, sample_rate=40000, target_lufs=-16.0)
                self.assertGreater(dur, 2.8)
                metrics = measure_wav_acoustics(master_wav)
                self.assertGreaterEqual(metrics["snr_db"], 55.0)
                self.assertAlmostEqual(metrics["loudness_lufs"], -16.0, delta=0.5)
        self.assertIn("codec", detect_best_video_encoder())

    def test_05_resolve_path_clean_and_mcp_stdout_isolation(self) -> None:
        import io
        from voice_studio.config.loader import resolve_repo_path
        from voice_studio.mcp_server import serve_mcp_stdio

        self.assertEqual(resolve_repo_path("voice-studio/output/demo", ROOT), (ROOT / "output/demo").resolve())
        self.assertEqual(cli_main(["input", "formats"]), 0)

        with tempfile.TemporaryDirectory() as tmp:
            req = json.dumps({
                "jsonrpc": "2.0", "id": 7, "method": "tools/call",
                "params": {"name": "studio_run", "arguments": {"input_path": "input/sample_neutral.json", "dry_run": True, "job_id": "_mcp_iso", "steps": ["prepare"]}},
            }) + "\n"
            t = Path(tmp)
            fake_in = io.BytesIO(req.encode("utf-8"))
            fake_out = io.BytesIO()
            t_in = io.TextIOWrapper(fake_in, encoding="utf-8")
            t_out = io.TextIOWrapper(fake_out, encoding="utf-8")
            err_buf = io.StringIO()
            old_in, old_out, old_err = sys.stdin, sys.stdout, sys.stderr
            try:
                sys.stdin, sys.stdout, sys.stderr = t_in, t_out, err_buf  # type: ignore[assignment]
                self.assertEqual(serve_mcp_stdio(base_dir=ROOT), 0)
                sys.stdout.flush()
            finally:
                sys.stdin, sys.stdout, sys.stderr = old_in, old_out, old_err
                shutil.rmtree(ROOT / "output/_mcp_iso", ignore_errors=True)
            lines = [ln for ln in fake_out.getvalue().decode("utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(lines), 1)
            parsed = json.loads(lines[0])
            self.assertEqual(parsed["id"], 7)
            self.assertFalse(parsed["result"]["isError"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
