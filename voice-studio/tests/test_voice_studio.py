# -*- coding: utf-8 -*-
"""Comprehensive tests for Voice & Media Studio config, ASR transcription, TTS/RVC, mastering QA & security."""

from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from voice_studio.audio.mastering import (  # noqa: E402
    apply_clean_speech_mastering,
    concatenate_wav_items,
    condition_stage1_pre_rvc_wav,
    measure_wav_acoustics,
)
from voice_studio.audio.stage2_rvc import condition_rvc_input_tensor  # noqa: E402
from voice_studio.audio.qa_gate import validate_audio_deliverables  # noqa: E402
from voice_studio.audio.rvc_assets import ensure_rvc_model_assets  # noqa: E402
from voice_studio.audio.stage1_tts import generate_dry_run_audio  # noqa: E402
from voice_studio.audio.synthesizer import VoiceSynthesizer  # noqa: E402
from voice_studio.audio.transcribe import transcribe_audio  # noqa: E402
from voice_studio.cli import main as cli_main  # noqa: E402
from voice_studio.config import (  # noqa: E402
    DEFAULT_SCRIPT_STEPS,
    ORDERED_STEPS,
    load_pipeline_config,
    profile_to_rvc_dict,
    resolve_step_range,
    resolve_voice_profile,
)
from voice_studio.content import add_thai_clause_spacing  # noqa: E402
from voice_studio.core import (  # noqa: E402
    is_non_loopback_http,
    safe_extract_zip,
    safe_resolve_under,
    validate_safe_id,
)
from voice_studio.pipeline import PipelineOrchestrator  # noqa: E402


class TestVoiceStudioCore(unittest.TestCase):
    def test_01_voice_profiles_and_unified_studio_config(self) -> None:
        gura = resolve_voice_profile("gawr_gura")
        self.assertEqual(gura.stage1.voice, "th-TH-PremwadeeNeural")
        self.assertTrue(gura.stage2.enabled)
        self.assertEqual(gura.stage2.repo_id, "KeroroK66/GawrGura")
        self.assertEqual((gura.stage2.pitch_shift, gura.stage2.index_rate, gura.stage2.protect, gura.stage2.volume_envelope), (2, 0.10, 0.55, 0.75))

        direct = resolve_voice_profile("direct_tts")
        self.assertFalse(direct.stage2.enabled)
        self.assertEqual(direct.stage2.backend, "none")

        with tempfile.TemporaryDirectory() as tmp:
            custom_cfg = Path(tmp) / "custom_job.yaml"
            custom_cfg.write_text(
                "voice:\n"
                "  profile: my_alt_rvc\n"
                "  display_name: Custom Alt Voice\n"
                "  tts_voice: th-TH-NiwatNeural\n"
                "  rvc:\n"
                "    enabled: true\n"
                "    repo_id: CustomOrg/AltModel\n"
                "    pitch_shift: -1\n",
                encoding="utf-8",
            )
            loaded = load_pipeline_config(custom_cfg, base_dir=ROOT)
            self.assertEqual(loaded.voice, "my_alt_rvc")
            assert loaded.voice_profile is not None
            self.assertEqual(loaded.voice_profile.stage1.voice, "th-TH-NiwatNeural")
            self.assertEqual(loaded.voice_profile.stage2.repo_id, "CustomOrg/AltModel")
            self.assertEqual(loaded.voice_profile.stage2.pitch_shift, -1)

        with self.assertRaises(ValueError):
            resolve_voice_profile("non_existent_voice")

    def test_02_thai_clause_spacing_and_custom_vocab_srt_support(self) -> None:
        spaced = add_thai_clause_spacing("สวัสดีค่ะผู้เรียนทุกท่าน ในบทเรียนนี้ประกอบด้วย การยืนยันตัวตนค่ะ")
        self.assertIn("สวัสดีค่ะ ผู้เรียนทุกท่าน,", spaced)
        self.assertIn("ประกอบด้วย,", spaced)

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            srt_p = t / "sample.srt"
            srt_p.write_text("1\n00:00:00,500 --> 00:00:03,000\nทดสอบ Docker และ Database ค่ะ\n", encoding="utf-8")
            j_p = t / "custom.json"
            j_p.write_text(
                json.dumps({
                    "title": "Inline Vocab",
                    "phonetic_vocab": {"words": {"BrandX": "แบรนด์ เอ็กซ์"}},
                    "slides": [{"slide_no": 1, "title": "Intro", "items": ["ทดสอบ BrandX กับ Database ค่ะ"]}],
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            from voice_studio.content.loader import parse_script_input, preview_prepared_content

            _, srt_slides = parse_script_input(srt_p, base_dir=ROOT)
            self.assertEqual(len(srt_slides), 1)
            self.assertAlmostEqual(srt_slides[0]["duration_est_sec"], 2.5)
            prev = preview_prepared_content(str(j_p), base_dir=ROOT)
            self.assertEqual(prev["warnings"], [])
            self.assertIn("แบรนด์ เอ็กซ์", prev["segments"][0]["items"][0]["phonetic"])
            self.assertIn("ดาต้าเบส", prev["segments"][0]["items"][0]["phonetic"])

    def test_03_hardened_security_guards_and_step_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "workspace"
            base.mkdir()
            for bad_rel in ("/../secret.txt", "../workspace_evil/pwn.txt", "ok\x00bad", "/"):
                with self.assertRaises(PermissionError):
                    safe_resolve_under(base, bad_rel)
            with self.assertRaises(ValueError):
                validate_safe_id("../bad_job")

            bad_zip = Path(tmp) / "evil.zip"
            with zipfile.ZipFile(bad_zip, "w") as zf:
                zf.writestr("../../escape.txt", "pwn")
            with self.assertRaises(PermissionError):
                safe_extract_zip(bad_zip, base / "extract1")

            symlink_zip = Path(tmp) / "symlink.zip"
            zi = zipfile.ZipInfo("link_out")
            zi.create_system = 3
            zi.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(symlink_zip, "w") as zf:
                zf.writestr(zi, "/etc/passwd")
            with self.assertRaises(PermissionError):
                safe_extract_zip(symlink_zip, base / "extract2")

            bomb_zip = Path(tmp) / "limit.zip"
            with zipfile.ZipFile(bomb_zip, "w") as zf:
                zf.writestr("big.txt", b"A" * 2048)
            with self.assertRaises(PermissionError):
                safe_extract_zip(bomb_zip, base / "extract3", max_total_bytes=1024)

        self.assertTrue(is_non_loopback_http("http://example.com:8765"))
        self.assertFalse(is_non_loopback_http("https://example.com:8765"))
        self.assertFalse(is_non_loopback_http("http://127.0.0.1:8765"))

        self.assertEqual(resolve_step_range(from_step="tts", to_step="qa"), ["tts", "convert", "qa"])
        with self.assertRaises(ValueError):
            resolve_step_range(from_step="render", to_step="tts")
        with self.assertRaises(ValueError):
            resolve_step_range(steps=["unknown_step"])

    def test_04_neutral_single_pass_and_audio_only_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_pipeline_config(
                overrides={
                    "job": {"id": "neutral_e2e_dry", "output_dir": tmp, "dry_run": True},
                    "output": {"dir": tmp, "formats": ["wav", "mp3"]},
                    "input": {"path": "input/sample_neutral.json"},
                },
                base_dir=ROOT,
            )
            state = PipelineOrchestrator(cfg, base_dir=ROOT).run()
            self.assertEqual(state["status"], "completed")
            for s in DEFAULT_SCRIPT_STEPS:
                self.assertEqual(state["steps"][s]["status"], "completed")

            job_dir = Path(tmp) / "neutral_e2e_dry"
            self.assertTrue((job_dir / "audio_stage1/slide_01_item_00.wav").is_file())
            self.assertTrue((job_dir / "audio_slides_wav/slide_01.wav").is_file())
            self.assertTrue((job_dir / "voice_manifest.json").is_file())
            self.assertTrue((job_dir / "timeline_manifest.json").is_file())
            self.assertTrue((job_dir / "deliverables/voice_audition.html").is_file())

    def test_05_whisper_asr_transcription_and_end_to_end_redub_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            media_wav = t / "sample_recording.wav"
            generate_dry_run_audio(str(media_wav), 2.4, sample_rate=16000)

            tr_dir = t / "standalone_tr"
            res = transcribe_audio(media_wav, tr_dir, dry_run=True)
            self.assertEqual(res["backend"], "dry_run")
            for k in ("txt_path", "srt_path", "json_path", "script_path"):
                self.assertTrue(Path(res[k]).is_file())
            self.assertIn("-->", Path(res["srt_path"]).read_text(encoding="utf-8"))

            cfg = load_pipeline_config(
                overrides={
                    "job": {"id": "asr_to_gura_dry", "output_dir": tmp, "dry_run": True},
                    "output": {"dir": tmp},
                    "input": {"path": str(media_wav)},
                },
                base_dir=ROOT,
            )
            self.assertEqual(cfg.steps, list(ORDERED_STEPS))
            st = PipelineOrchestrator(cfg, base_dir=ROOT).run()
            self.assertEqual(st["status"], "completed")
            self.assertTrue((Path(tmp) / "asr_to_gura_dry/transcript/transcript.srt").is_file())
            self.assertTrue((Path(tmp) / "asr_to_gura_dry/deliverables/transcript.srt").is_file())

    def test_06_step_switching_voice_invalidation_and_rvc_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            cfg1 = load_pipeline_config(
                overrides={
                    "job": {"id": "switch_repro", "output_dir": tmp, "dry_run": True},
                    "output": {"dir": tmp},
                    "voice": "gawr_gura",
                    "input": {"path": "input/sample_neutral.json"},
                },
                base_dir=ROOT,
            )
            PipelineOrchestrator(cfg1, base_dir=ROOT).run()
            job_dir = t / "switch_repro"
            s1_wav = job_dir / "audio_stage1/slide_01_item_00.wav"
            raw_wav = job_dir / "audio_items_raw/slide_01_item_00.wav"
            with wave.open(str(raw_wav), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(40000)
                wf.writeframes(b"\x01\x00" * 40000)

            s1_mtime = s1_wav.stat().st_mtime_ns
            # Switch Stage 2 to direct_tts (keeps Stage 1 WAV, purges Stage 2 raw_wav, invalidates downstream)
            cfg2 = load_pipeline_config(
                overrides={
                    "job": {"id": "switch_repro", "output_dir": tmp, "dry_run": True},
                    "output": {"dir": tmp},
                    "voice": "direct_tts",
                    "input": {"path": "input/sample_neutral.json"},
                    "steps": ["convert"],
                },
                base_dir=ROOT,
            )
            st2 = PipelineOrchestrator(cfg2, base_dir=ROOT).run()
            self.assertEqual(s1_wav.stat().st_mtime_ns, s1_mtime)
            with wave.open(str(raw_wav), "rb") as wf:
                self.assertGreater(wf.getnframes() / float(wf.getframerate()), 5.0)
            self.assertEqual(st2["steps"]["qa"]["status"], "pending")
            self.assertEqual(st2["steps"]["render"]["status"], "pending")

            # Custom RVC config without index_filename must NOT assume model.index
            cfg_no_idx = load_pipeline_config(
                overrides={
                    "voice": {
                        "profile": "no_idx",
                        "rvc": {"enabled": True, "source_type": "local", "pth_filename": "c.pth", "index_filename": ""},
                    }
                },
                base_dir=ROOT,
            )
            with self.assertRaises(FileNotFoundError) as ctx1:
                ensure_rvc_model_assets(rvc_cfg=profile_to_rvc_dict(cfg_no_idx.voice_profile), assets_root=tmp)
            self.assertNotIn("model.index", str(ctx1.exception))

            self.assertEqual(cli_main(["doctor", "--voice-python", sys.executable]), 0)
            self.assertEqual(cli_main(["voices", "gawr_gura"]), 0)

    def test_07_qa_gate_and_synthesizer_and_acoustics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            deliv = Path(tmp) / "job_qa"
            (deliv / "audio_slides_wav").mkdir(parents=True)
            raw_p = deliv / "raw_01.wav"
            wav_p = deliv / "audio_slides_wav/slide_01.wav"
            generate_dry_run_audio(str(raw_p), 6.0, sample_rate=40000)
            concatenate_wav_items([str(raw_p)], str(wav_p), default_sample_rate=40000, target_lufs=-18.0)
            (deliv / "voice_manifest.json").write_text(
                json.dumps({"slides": [{"slide_no": 1, "total_duration_sec": 6.0}]}), encoding="utf-8"
            )
            phon_p = deliv / "phon.json"
            phon_p.write_text(json.dumps([{"slide_no": 1, "item_scripts": ["สวัสดีค่ะ ทดสอบระบบ"]}]), encoding="utf-8")
            res = validate_audio_deliverables(str(deliv), str(phon_p), expected_slides=1, duration_tolerance=0.25)
            self.assertEqual(res.exit_code, 0, f"QA failed: {res.violations}")
            metrics = measure_wav_acoustics(str(wav_p))
            self.assertIn("peak_dbfs", metrics)
            self.assertAlmostEqual(metrics["loudness_lufs"], -18.0, delta=1.0)

            synth = VoiceSynthesizer(dry_run=True)
            out_s = synth.synthesize_manifest(str(ROOT / "input/sample_neutral.json"), str(Path(tmp) / "synth_out"))
            self.assertEqual(len(out_s["slides"]), 2)

    def test_08_colab_pro_config_and_24bit_soxr_mastering(self) -> None:
        local_cfg = load_pipeline_config(ROOT / "configs/studio.yaml", base_dir=ROOT)
        pro_cfg = load_pipeline_config(ROOT / "configs/colab_pro.yaml", base_dir=ROOT)

        self.assertEqual(local_cfg.target, "local")
        assert local_cfg.voice_profile is not None
        self.assertFalse(local_cfg.voice_profile.stage2.rvc_fp32)
        self.assertEqual(local_cfg.voice_profile.mastering.sample_rate, 40000)
        self.assertEqual(local_cfg.voice_profile.mastering.bit_depth, 16)
        self.assertEqual(local_cfg.transcribe.model, "openai/whisper-small")
        self.assertEqual((local_cfg.output.nvenc_preset, local_cfg.output.nvenc_cq), ("p4", 21))

        self.assertEqual(pro_cfg.runtime.gpu_tier, "l4")
        self.assertTrue(pro_cfg.runtime.high_ram)
        assert pro_cfg.voice_profile is not None
        self.assertTrue(pro_cfg.voice_profile.stage2.rvc_fp32)
        self.assertTrue(pro_cfg.voice_profile.stage2.intermediate_float)
        self.assertEqual(pro_cfg.voice_profile.mastering.sample_rate, 48000)
        self.assertEqual(pro_cfg.voice_profile.mastering.bit_depth, 24)
        self.assertEqual(pro_cfg.voice_profile.mastering.resampler_precision, 33)
        self.assertTrue(pro_cfg.voice_profile.mastering.declick)
        self.assertEqual(pro_cfg.voice_profile.mastering.denoise_nf_db, -50)
        self.assertEqual(pro_cfg.voice_profile.mastering.denoise_nr_db, 10)
        self.assertAlmostEqual(pro_cfg.voice_profile.mastering.noise_gate_threshold, 0.005)
        self.assertEqual((pro_cfg.output.mp3_bitrate, pro_cfg.output.aac_bitrate), ("320k", "320k"))
        self.assertEqual((pro_cfg.output.nvenc_preset, pro_cfg.output.nvenc_cq), ("p7", 18))
        self.assertEqual(pro_cfg.transcribe.backend, "faster_whisper")
        self.assertEqual(pro_cfg.transcribe.model, "openai/whisper-large-v3-turbo")
        self.assertEqual(pro_cfg.transcribe.beam_size, 5)
        self.assertTrue(pro_cfg.transcribe.vad_filter)

        with tempfile.TemporaryDirectory() as tmp:
            import math, random, shutil, struct

            if shutil.which("ffmpeg"):
                raw_p = Path(tmp) / "raw_40k.wav"
                master_24 = Path(tmp) / "master_48k_24b.wav"
                joined_24 = Path(tmp) / "joined_48k_24b.wav"
                sr = 40000
                rng = random.Random(7)
                frames = bytearray()
                for i in range(int(sr * 2.5)):
                    in_speech = (i % sr) < int(sr * 0.6)
                    val = 8000.0 * math.sin(2.0 * math.pi * 330.0 * i / float(sr)) if in_speech else max(-400, min(400, int(rng.gauss(0.0, 120.0))))
                    frames.extend(struct.pack("<h", int(val)))
                with wave.open(str(raw_p), "wb") as wf:
                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr); wf.writeframes(bytes(frames))

                dur = apply_clean_speech_mastering(
                    str(raw_p),
                    str(master_24),
                    sample_rate=48000,
                    target_lufs=-16.0,
                    denoise=True,
                    noise_gate=True,
                    noise_gate_threshold=0.005,
                    bit_depth=24,
                    resampler_precision=33,
                    declick=True,
                    denoise_nf_db=-50,
                    denoise_nr_db=10,
                )
                self.assertGreater(dur, 2.3)
                metrics = measure_wav_acoustics(str(master_24))
                self.assertEqual(metrics["bit_depth"], 24)
                self.assertEqual(metrics["sample_rate"], 48000)
                self.assertGreaterEqual(metrics["snr_db"], 55.0)
                self.assertAlmostEqual(metrics["loudness_lufs"], -16.0, delta=0.8)

                j_dur = concatenate_wav_items([str(master_24)], str(joined_24), default_sample_rate=48000, target_lufs=-16.0, bit_depth=24)
                self.assertGreater(j_dur, 2.3)
                with wave.open(str(joined_24), "rb") as wf:
                    self.assertEqual((wf.getsampwidth(), wf.getframerate()), (3, 48000))

    def test_09_mcp_stdio_jsonrpc_server(self) -> None:
        from voice_studio.mcp_server import handle_jsonrpc_request

        init_resp = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, base_dir=ROOT)
        assert init_resp is not None
        self.assertEqual(init_resp["result"]["serverInfo"]["name"], "voice-studio")

        list_resp = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, base_dir=ROOT)
        assert list_resp is not None
        tool_names = {t["name"] for t in list_resp["result"]["tools"]}
        self.assertIn("studio_run", tool_names)
        self.assertIn("studio_transcribe", tool_names)
        self.assertIn("studio_colab_download", tool_names)

        call_resp = handle_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "studio_voices", "arguments": {"voice_id": "gawr_gura"}}},
            base_dir=ROOT,
        )
        assert call_resp is not None
        self.assertFalse(call_resp["result"]["isError"])
        payload = json.loads(call_resp["result"]["content"][0]["text"])
        self.assertEqual(payload["voice_id"], "gawr_gura")

    def test_10_database_phonetic_natural_thai_and_loanwords(self) -> None:
        from voice_studio.content.phonetic import clean_script_text

        self.assertEqual(clean_script_text("ทดสอบ ดาต้าเบส ค่ะ"), "ทดสอบ ดาต้าเบส ค่ะ")
        self.assertEqual(clean_script_text("ทดสอบ เดย์-ทะ-เบส ค่ะ"), "ทดสอบ ดาต้าเบส ค่ะ")
        self.assertEqual(clean_script_text("ทดสอบ เด-ทะ-เบส ค่ะ"), "ทดสอบ ดาต้าเบส ค่ะ")
        self.assertEqual(clean_script_text("ทดสอบ ดาตาเบส ค่ะ"), "ทดสอบ ดาต้าเบส ค่ะ")
        self.assertEqual(clean_script_text("ทดสอบ database ค่ะ"), "ทดสอบ ดาต้าเบส ค่ะ")
        self.assertEqual(clean_script_text("Role-Based Access Control"), "โรล เบสด์ แอ็กเซส คอนโทรล")
        self.assertEqual(clean_script_text("Query SAML IDOR"), "คิวรี่ แซมเมิล ไอดอร์")

    def test_11_mcp_first_run_auth_config_and_content_length_framing(self) -> None:
        from voice_studio.mcp_server import handle_jsonrpc_request, serve_mcp_stdio
        from voice_studio.mcp_setup import sync_mcp_config, update_studio_config

        prev = handle_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "studio_prepare_preview", "arguments": {"input_or_text": "input/sample_neutral.json"}}},
            base_dir=ROOT,
        )
        assert prev is not None and not prev["result"]["isError"]
        prev_data = json.loads(prev["result"]["content"][0]["text"])
        self.assertEqual(prev_data["warnings"], [])
        self.assertIn("Database", prev_data["segments"][0]["items"][1]["tts"])
        self.assertIn("ดาต้าเบส", prev_data["segments"][0]["items"][1]["phonetic"])

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "configs").mkdir()
            (t / "configs/colab_pro.yaml").write_text("# Header comment preserved\nruntime:\n  target: \"colab_drive\"  # keep comment\n  gpu_tier: \"l4\"\n", encoding="utf-8")
            (t / "studio.py").write_text("# stub\n", encoding="utf-8")

            upd = update_studio_config(profile="colab_pro", updates={"gpu_tier": "a100"}, base_dir=t)
            self.assertEqual(upd["data"]["runtime"]["gpu_tier"], "a100")
            raw_yaml = (t / "configs/colab_pro.yaml").read_text(encoding="utf-8")
            self.assertIn("# keep comment", raw_yaml)

            mcp_cfg_p = t / "mcp_config.json"
            sync_res = sync_mcp_config(mcp_config_path=mcp_cfg_p, base_dir=t)
            self.assertIn("voice-studio", sync_res["servers"])

            login_resp = handle_jsonrpc_request(
                {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "studio_auth_login", "arguments": {"mode": "agent", "colab_url": "https://colab-agent.example"}}},
                base_dir=t,
            )
            assert login_resp is not None and not login_resp["result"]["isError"]
            self.assertTrue((t / ".env").is_file())

        import io

        req_body = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping"}).encode("utf-8")
        framed_in = io.BytesIO(f"Content-Length: {len(req_body)}\r\n\r\n".encode("ascii") + req_body)
        framed_out = io.BytesIO()
        txt_in = io.TextIOWrapper(framed_in, encoding="utf-8")
        txt_out = io.TextIOWrapper(framed_out, encoding="utf-8")
        old_in, old_out = sys.stdin, sys.stdout
        try:
            sys.stdin, sys.stdout = txt_in, txt_out  # type: ignore[assignment]
            self.assertEqual(serve_mcp_stdio(base_dir=ROOT), 0)
            sys.stdout.flush()
            raw_out = framed_out.getvalue()
        finally:
            sys.stdin, sys.stdout = old_in, old_out
        self.assertTrue(raw_out.startswith(b"Content-Length:"))
        self.assertIn(b'"id": 99', raw_out)

    def test_12_pre_rvc_stage1_noise_gate_and_tensor_normalization(self) -> None:
        import math, random, shutil, struct
        import numpy as np

        sr_t = 16000
        t_arr = np.linspace(0.0, 1.0, sr_t, endpoint=False, dtype=np.float32)
        sig = np.where((t_arr >= 0.2) & (t_arr <= 0.7), 0.04 * np.sin(2.0 * math.pi * 300.0 * t_arr), 0.001) + 0.015
        cond = condition_rvc_input_tensor(sig, sr=sr_t)
        self.assertAlmostEqual(float(np.max(np.abs(cond[: int(0.1 * sr_t)]))), 0.0, places=5)
        active = cond[int(0.25 * sr_t) : int(0.65 * sr_t)]
        self.assertAlmostEqual(float(np.sqrt(np.mean(active * active))), 0.085, delta=0.015)

        if shutil.which("ffmpeg"):
            with tempfile.TemporaryDirectory() as tmp:
                raw_wav = Path(tmp) / "raw_stage1.wav"
                clean_wav = Path(tmp) / "clean_pre_rvc.wav"
                sr = 40000
                rng = random.Random(11)
                frames = bytearray()
                for i in range(int(sr * 2.0)):
                    in_speech = 0.25 <= (i / float(sr)) <= 1.25
                    val = 4500.0 * math.sin(2.0 * math.pi * 320.0 * i / float(sr)) if in_speech else max(-500, min(500, int(rng.gauss(0.0, 140.0))))
                    frames.extend(struct.pack("<h", int(val)))
                with wave.open(str(raw_wav), "wb") as wf:
                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr); wf.writeframes(bytes(frames))
                dur = condition_stage1_pre_rvc_wav(str(raw_wav), str(clean_wav), sample_rate=sr, target_lufs=-18.0)
                self.assertGreater(dur, 1.8)
                metrics = measure_wav_acoustics(str(clean_wav))
                self.assertGreaterEqual(metrics["snr_db"], 60.0)
                self.assertAlmostEqual(metrics["loudness_lufs"], -18.0, delta=1.0)

    def test_13_hf_kokoro_thai_profiles_and_dispatch(self) -> None:
        from voice_studio.audio.stage1_hf_tts import _split_hf_sentences, resolve_hf_speaker_id, synthesize_stage1_hf_batch
        from voice_studio.core.state import compute_pipeline_signatures

        self.assertEqual((resolve_hf_speaker_id("f_young_bright"), resolve_hf_speaker_id("f_mid_warm")), (1, 5))
        hf_direct = resolve_voice_profile("hf_female")
        self.assertEqual((hf_direct.stage1.backend, hf_direct.stage1.hf_speaker, hf_direct.stage2.enabled), ("hf_kokoro_thai", "f_young_bright", False))
        gura_hf = resolve_voice_profile("gawr_gura_hf")
        self.assertEqual((gura_hf.stage1.backend, gura_hf.stage1.hf_speaker, gura_hf.stage2.enabled, gura_hf.stage2.pitch_shift), ("hf_kokoro_thai", "f_mid_warm", True, 1))
        self.assertEqual(_split_hf_sentences("สวัสดีค่ะ. นี่คือตัวอย่างเสียงทดสอบระบบสังเคราะห์เสียงภาษาไทยให้เป็นธรรมชาติค่ะ."), [("สวัสดีค่ะ, นี่คือตัวอย่างเสียงทดสอบระบบสังเคราะห์เสียงภาษาไทยให้เป็นธรรมชาติค่ะ", 0.0)])
        long_pair = _split_hf_sentences("ประโยคที่หนึ่งมีความยาวมากกว่าสี่สิบห้าตัวอักษรแน่นอนค่ะ. ประโยคที่สองมีความยาวมากกว่าสี่สิบห้าตัวอักษรเช่นกันค่ะ.")
        self.assertEqual(len(long_pair), 2)
        self.assertGreaterEqual(long_pair[0][1], 0.55)
        ov_cfg = load_pipeline_config(overrides={"voice": {"profile": "hf_female", "hf_speaker": "f_young_clear"}}, base_dir=ROOT)
        assert ov_cfg.voice_profile is not None
        self.assertEqual((ov_cfg.voice_profile.stage2.enabled, ov_cfg.voice_profile.stage1.hf_speaker), (False, "f_young_clear"))
        self.assertNotEqual(compute_pipeline_signatures({"k": "v"}, gura_hf)["tts"], compute_pipeline_signatures({"k": "v"}, ov_cfg.voice_profile)["tts"])
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp) / "hf_dry.wav"
            self.assertGreater(synthesize_stage1_hf_batch([{"wav_path": str(w), "text": "ทดสอบ Cloud Computing ค่ะ"}], dry_run=True)[str(w)], 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
