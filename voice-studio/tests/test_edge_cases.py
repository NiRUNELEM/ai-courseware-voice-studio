# -*- coding: utf-8 -*-
"""Regression tests for hybrid Colab staging, Drive traversal protection, resume resilience & CLI overrides."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from voice_studio.audio.qa_gate import validate_audio_deliverables  # noqa: E402
from voice_studio.audio.synthesizer import VoiceSynthesizer  # noqa: E402
from voice_studio.cli import main as cli_main  # noqa: E402
from voice_studio.colab import ColabClient, LocalDriveSyncBridge, run_colab_drive_worker  # noqa: E402
from voice_studio.colab.bundle import create_remote_job_bundle  # noqa: E402
from voice_studio.config import load_pipeline_config  # noqa: E402
from voice_studio.core.security import safe_extract_zip  # noqa: E402
from voice_studio.pipeline import PipelineOrchestrator  # noqa: E402


class TestEdgeCasesAndRegressions(unittest.TestCase):
    def test_01_hybrid_step_continuation_stages_custom_pngs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            local_pngs = t / "host_pngs"
            local_pngs.mkdir()
            Image.new("RGB", (1920, 1080), color=(220, 20, 40)).save(local_pngs / "slide_01.png")
            md = t / "script.md"
            md.write_text("# Segment 1\nสวัสดีค่ะ ทดสอบส่งต่อสไลด์ไปคอลแลบ\n", encoding="utf-8")

            local_out = t / "local_out"
            cfg_local = load_pipeline_config(
                overrides={
                    "job": {"id": "hybrid_png", "output_dir": str(local_out), "dry_run": True},
                    "output": {"dir": str(local_out)},
                    "input": {"path": str(md), "slides_png_dir": str(local_pngs)},
                    "steps": ["prepare", "tts"],
                },
                base_dir=ROOT,
            )
            PipelineOrchestrator(cfg_local, base_dir=ROOT).run()

            cfg_remote = load_pipeline_config(
                overrides={
                    "job": {"id": "hybrid_png", "output_dir": str(local_out), "dry_run": True},
                    "output": {"dir": str(local_out)},
                    "runtime": {"target": "colab_agent"},
                    "input": {"path": str(md), "slides_png_dir": str(local_pngs)},
                    "steps": ["convert", "qa", "align", "render"],
                },
                base_dir=ROOT,
            )
            spec_r, staged_r = ColabClient(cfg_remote, base_dir=ROOT)._prepare_remote_spec_and_files(
                local_out / "hybrid_png", "hybrid_png"
            )
            remote_ws = t / "remote_ws"
            zpath = t / "remote_bundle.zip"
            create_remote_job_bundle(zpath, spec_r, root_dir=ROOT, staged_files=staged_r)
            safe_extract_zip(zpath, remote_ws)

            shutil.rmtree(local_pngs)
            spec_r["runtime"]["target"] = "local"
            cfg_exec = load_pipeline_config(overrides=spec_r, base_dir=remote_ws)
            st = PipelineOrchestrator(cfg_exec, base_dir=remote_ws).run()
            self.assertEqual(st["status"], "completed")
            rendered_png = remote_ws / "output/hybrid_png/deliverables/slides_png/slide_01.png"
            self.assertEqual(Image.open(rendered_png).getpixel((100, 100)), (220, 20, 40))

    def test_02_cli_voice_switch_and_external_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            job_id = "_test_voice_switch_tmp"
            job_dir = ROOT / "output" / job_id
            try:
                custom_cfg = t / "custom.yaml"
                custom_cfg.write_text(
                    f"job:\n  id: {job_id}\n  dry_run: true\n"
                    "voice:\n"
                    "  profile: my_alt\n"
                    "  tts_voice: th-TH-PremwadeeNeural\n"
                    "  rvc:\n    enabled: true\n    repo_id: Org/Model\n"
                    "input:\n  path: input/sample_neutral.json\n",
                    encoding="utf-8",
                )
                self.assertEqual(cli_main(["run", "--config", str(custom_cfg), "--steps", "prepare,tts"]), 0)
                self.assertEqual(cli_main(["run", "--job", job_id, "--voice", "direct_tts", "--steps", "convert"]), 0)
                st = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
                self.assertEqual(st["voice_id"], "direct_tts")
            finally:
                shutil.rmtree(job_dir, ignore_errors=True)

            # External project config dir resolves ../inputs & ../output deterministically
            ext_proj = t / "external_proj"
            (ext_proj / "jobs").mkdir(parents=True)
            (ext_proj / "inputs").mkdir(parents=True)
            (ext_proj / "inputs" / "demo.json").write_text(
                json.dumps({"title": "External Demo", "slides": [{"slide_no": 1, "title": "Intro", "items": ["ทดสอบค่ะ"]}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            ext_cfg = ext_proj / "jobs" / "job.yaml"
            ext_cfg.write_text(
                'job:\n  id: ext_s1\n  output_dir: "../out"\n  dry_run: true\n'
                'input:\n  path: "../inputs/demo.json"\n  title: "บทที่ 10 ทดสอบ"\n'
                'output:\n  dir: "../out"\n',
                encoding="utf-8",
            )
            self.assertEqual(
                cli_main(["run", "--config", str(ext_cfg), "--slides", "1", "--steps", "prepare"]),
                0,
            )
            prep = json.loads((ext_proj / "out/ext_s1/work/prepared_slides.json").read_text(encoding="utf-8"))
            self.assertEqual(len(prep["slides"]), 1)
            self.assertIn("บทที่ 10", prep["meta"]["title"])

    def test_03_remote_staging_disambiguates_same_named_external_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            d1, d2 = t / "d1", t / "d2"
            d1.mkdir()
            d2.mkdir()
            (d1 / "dup.dat").write_bytes(b"FILE_ONE")
            (d2 / "dup.dat").write_bytes(b"FILE_TWO")
            ext_vocab = t / "custom_vocab.json"
            ext_vocab.write_text((ROOT / "src/voice_studio/content/phonetic_vocab.json").read_text(encoding="utf-8"), encoding="utf-8")

            cfg = load_pipeline_config(
                overrides={
                    "job": {"id": "coll_test", "output_dir": str(t / "out"), "dry_run": True},
                    "output": {"dir": str(t / "out")},
                    "runtime": {"target": "colab_agent"},
                    "phonetic_vocab_path": str(ext_vocab),
                    "voice": {
                        "profile": "coll_voice",
                        "rvc": {"enabled": True, "local_pth": str(d1 / "dup.dat"), "local_index": str(d2 / "dup.dat")},
                    },
                    "input": {"path": "input/sample_neutral.json"},
                },
                base_dir=ROOT,
            )
            job_dir = t / "out/coll_test"
            job_dir.mkdir(parents=True)
            spec, staged = ColabClient(cfg, base_dir=ROOT)._prepare_remote_spec_and_files(job_dir, "coll_test")
            self.assertEqual(spec["phonetic_vocab_path"], "bundle_inputs/phonetic_vocab.json")
            pth_arc = spec["voice"]["rvc"]["local_pth"]
            idx_arc = spec["voice"]["rvc"]["local_index"]
            self.assertNotEqual(pth_arc, idx_arc)

            zpath = t / "bundle.zip"
            create_remote_job_bundle(zpath, spec, root_dir=ROOT, staged_files=staged)
            with zipfile.ZipFile(zpath, "r") as zf:
                self.assertEqual(zf.read(pth_arc), b"FILE_ONE")
                self.assertEqual(zf.read(idx_arc), b"FILE_TWO")
                self.assertIn("bundle_inputs/phonetic_vocab.json", zf.namelist())

    def test_04_corrupt_resume_empty_input_and_drive_resilience(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            items_dir = t / "resume_job/audio_items_raw"
            items_dir.mkdir(parents=True)
            (items_dir / "slide_01_item_00.wav").write_bytes(b"NOT_A_RIFF_WAV_" * 100)
            synth = VoiceSynthesizer(dry_run=True)
            synth.synthesize_manifest(str(ROOT / "input/sample_neutral.json"), str(t / "resume_job"), resume=True)
            gate = validate_audio_deliverables(str(t / "resume_job"), expected_slides=2)
            self.assertEqual(gate.exit_code, 0, f"QA failed: {gate.violations}")

            empty_json = t / "empty.json"
            empty_json.write_text('{"title": "Empty", "slides": []}', encoding="utf-8")
            cfg_empty = load_pipeline_config(
                overrides={
                    "job": {"id": "empty_fail", "output_dir": str(t / "out"), "dry_run": True},
                    "output": {"dir": str(t / "out")},
                    "input": {"path": str(empty_json)},
                },
                base_dir=ROOT,
            )
            with self.assertRaises(ValueError):
                PipelineOrchestrator(cfg_empty, base_dir=ROOT).run()

            drive_mount = t / "drive_mount"
            bridge = LocalDriveSyncBridge(drive_mount, root_folder_name="jobs")
            bad_bundle = t / "bad.zip"
            create_remote_job_bundle(bad_bundle, {"voice": {"profile": "gawr_gura"}}, root_dir=ROOT)
            bridge.submit_job("job_01_bad", bad_bundle, {
                "job": {"id": "job_01_bad", "output_dir": "output", "dry_run": True},
                "input": {"path": "nonexistent_script.md"},
            })

            cfg_ok = load_pipeline_config(
                overrides={
                    "job": {"id": "job_02_ok", "output_dir": str(t / "out"), "dry_run": True},
                    "output": {"dir": str(t / "out")},
                    "runtime": {"target": "colab_drive"},
                    "input": {"path": "input/sample_neutral.json"},
                    "colab": {"local_mount_dir": str(drive_mount), "root_folder_name": "jobs"},
                },
                base_dir=ROOT,
            )
            ok_dir = t / "out/job_02_ok"
            ok_dir.mkdir(parents=True)
            ColabClient(cfg_ok, base_dir=ROOT).submit_job(ok_dir, "job_02_ok")

            processed = run_colab_drive_worker(drive_mount, root_folder_name="jobs")
            self.assertEqual(processed, ["job_02_ok"])
            self.assertEqual(bridge.read_status("job_01_bad")["status"], "failed")
            self.assertEqual(bridge.read_status("job_02_ok")["status"], "completed")

            # Verify DriveApiBridge rejects path traversal filenames from remote Drive API
            import types
            from voice_studio.colab.drive_bridge import DriveApiBridge

            fake_gapi = types.ModuleType("googleapiclient")
            fake_http = types.ModuleType("googleapiclient.http")
            fake_http.MediaIoBaseDownload = object  # type: ignore[attr-defined]
            fake_gapi.http = fake_http  # type: ignore[attr-defined]
            sys.modules["googleapiclient"] = fake_gapi
            sys.modules["googleapiclient.http"] = fake_http

            class _FakeFilesRes:
                def list(self, **_kw):
                    class _Exec:
                        def execute(self):
                            return {"files": [{"id": "f1", "name": "../../escape_pwn.txt", "mimeType": "text/plain"}]}
                    return _Exec()

            class _FakeService:
                def files(self):
                    return _FakeFilesRes()

            try:
                api_bridge = DriveApiBridge(_FakeService(), root_folder_name="jobs")
                api_bridge._folder_cache[("jobs", None)] = "r1"
                api_bridge._folder_cache[("j1", "r1")] = "j1_id"
                api_bridge._folder_cache[("output", "j1_id")] = "o1_id"
                with self.assertRaises(PermissionError):
                    api_bridge.download_outputs("j1", t / "safe_dl")
            finally:
                sys.modules.pop("googleapiclient.http", None)
                sys.modules.pop("googleapiclient", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
