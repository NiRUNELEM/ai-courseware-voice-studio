# -*- coding: utf-8 -*-
"""End-to-end integration tests for Google Colab HTTP Agent, Drive Sync Bridge & Colab Notebook."""

from __future__ import annotations

import ast
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from voice_studio.audio.rvc_assets import ensure_rvc_model_assets  # noqa: E402
from voice_studio.colab import (  # noqa: E402
    ColabClient,
    LocalDriveSyncBridge,
    run_colab_drive_worker,
    start_agent_server,
)
from voice_studio.config import load_pipeline_config, profile_to_rvc_dict  # noqa: E402
from voice_studio.pipeline import PipelineOrchestrator  # noqa: E402


class TestColabBridges(unittest.TestCase):
    def test_01_colab_http_agent_auth_submit_poll_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            agent_work = tmp_path / "agent_work"
            local_out = tmp_path / "local_out"
            token = "test-secret-colab-token-123"
            server, _ = start_agent_server(host="127.0.0.1", port=0, token=token, work_root=agent_work)
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                base_url = f"http://127.0.0.1:{port}"
                req = urllib.request.Request(f"{base_url}/api/v1/health", method="GET")
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req, timeout=5)
                self.assertEqual(ctx.exception.code, 401)

                cfg = load_pipeline_config(
                    overrides={
                        "job": {"id": "colab_agent_e2e", "output_dir": str(local_out), "dry_run": True},
                        "output": {"dir": str(local_out)},
                        "runtime": {"target": "colab_agent"},
                        "voice": "gawr_gura",
                        "input": {"path": "input/sample_neutral.json"},
                        "colab": {"agent_url": base_url, "agent_token": token, "poll_interval_sec": 0.2, "timeout_sec": 30.0},
                    },
                    base_dir=ROOT,
                )
                client = ColabClient(cfg, base_dir=ROOT)
                self.assertTrue(client.ping()["ok"])
                self.assertIsNone(client.fetch_status("nonexistent_job_404"))

                state = PipelineOrchestrator(cfg, base_dir=ROOT).run()
                self.assertEqual(state["status"], "completed")
                job_dir = local_out / "colab_agent_e2e"
                self.assertTrue((job_dir / "deliverables_bundle.zip").is_file())
                self.assertTrue((job_dir / "deliverables/video_player.html").is_file())

                logs_full = client.fetch_logs("colab_agent_e2e", offset=0)
                self.assertIn("[STEP -> prepare]", logs_full["chunk"])
                self.assertGreater(logs_full["offset"], 0)
                self.assertEqual(client.fetch_logs("colab_agent_e2e", offset=logs_full["offset"])["chunk"], "")

                exec_res = client.exec_command("echo COLAB_LIVE_EXEC_OK")
                self.assertEqual(exec_res["returncode"], 0)
                self.assertIn("COLAB_LIVE_EXEC_OK", exec_res["stdout"])

                ext_md = tmp_path / "external_script.md"
                ext_md.write_text("# Ext Segment\nสวัสดีค่ะ ส่งงานข้ามเครื่องไปคอลแลบ\n", encoding="utf-8")
                ext_png_dir = tmp_path / "ext_pngs"
                ext_png_dir.mkdir()
                sentinel_png = ext_png_dir / "slide_01.png"
                Image.new("RGB", (64, 64), color=(12, 34, 56)).save(sentinel_png)

                cfg_ext = load_pipeline_config(
                    overrides={
                        "job": {"id": "colab_custom_ext", "output_dir": str(local_out), "dry_run": True},
                        "output": {"dir": str(local_out)},
                        "voice": "direct_tts",
                        "input": {"path": str(ext_md), "slides_png_dir": str(ext_png_dir)},
                        "runtime": {"target": "colab_agent", "voice_python": "/nonexistent/python", "rvc_assets_root": "/nonexistent/rvc"},
                        "colab": {"agent_url": base_url, "agent_token": token, "poll_interval_sec": 0.2, "timeout_sec": 30.0},
                    },
                    base_dir=ROOT,
                )
                self.assertEqual(PipelineOrchestrator(cfg_ext, base_dir=ROOT).run()["status"], "completed")
                self.assertEqual((local_out / "colab_custom_ext/deliverables/slides_png/slide_01.png").read_bytes(), sentinel_png.read_bytes())
                remote_spec = json.loads((agent_work / "colab_custom_ext/job_spec.json").read_text(encoding="utf-8"))
                self.assertEqual(remote_spec["runtime"]["voice_python"], "")
                self.assertEqual(remote_spec["runtime"]["rvc_assets_root"], "")
            finally:
                server.shutdown()
                server.server_close()

    def test_02_colab_drive_sync_bridge_and_sha256_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            drive_mount = tmp_path / "gdrive_mount"
            local_out = tmp_path / "local_out"
            cfg = load_pipeline_config(
                overrides={
                    "job": {"id": "drive_e2e_job", "output_dir": str(local_out), "dry_run": True},
                    "output": {"dir": str(local_out)},
                    "runtime": {"target": "colab_drive"},
                    "voice": "direct_tts",
                    "input": {"path": "input/sample_neutral.json"},
                    "colab": {"local_mount_dir": str(drive_mount), "root_folder_name": "studio_jobs"},
                },
                base_dir=ROOT,
            )
            client = ColabClient(cfg, base_dir=ROOT)
            job_dir = local_out / "drive_e2e_job"
            job_dir.mkdir(parents=True)
            self.assertEqual(client.submit_job(job_dir, "drive_e2e_job")["status"], "queued_drive")
            self.assertEqual(run_colab_drive_worker(drive_mount, root_folder_name="studio_jobs"), ["drive_e2e_job"])

            client.download_outputs(job_dir, "drive_e2e_job")
            vm = json.loads((job_dir / "voice_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(vm["voice_id"], "direct_tts")

            st_file = drive_mount / "studio_jobs/drive_e2e_job/status.json"
            bad_st = json.loads(st_file.read_text(encoding="utf-8"))
            bad_st["archive_sha256"] = "0" * 64
            st_file.write_text(json.dumps(bad_st), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                client.download_outputs(job_dir, "drive_e2e_job")

    def test_03_custom_local_rvc_staging_for_remote_colab(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ext_assets = tmp_path / "ext_rvc"
            ext_assets.mkdir()
            pth_file = ext_assets / "custom_speaker.pth"
            idx_file = ext_assets / "custom_speaker.index"
            pth_file.write_bytes(b"PTH_WEIGHTS_SENTINEL")
            idx_file.write_bytes(b"INDEX_VECTOR_SENTINEL")

            local_out = tmp_path / "local_out"
            agent_work = tmp_path / "agent_work"
            token = "yaml-voice-token-789"
            server, _ = start_agent_server(host="127.0.0.1", port=0, token=token, work_root=agent_work)
            port = server.server_address[1]
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                cfg = load_pipeline_config(
                    overrides={
                        "job": {"id": "custom_rvc_agent", "output_dir": str(local_out), "dry_run": True},
                        "output": {"dir": str(local_out)},
                        "runtime": {"target": "colab_agent"},
                        "voice": {
                            "profile": "custom_rvc",
                            "rvc": {"enabled": True, "source_type": "local", "local_pth": str(pth_file), "local_index": str(idx_file), "pitch_shift": 3},
                        },
                        "input": {"path": "input/sample_neutral.json"},
                        "colab": {"agent_url": f"http://127.0.0.1:{port}", "agent_token": token, "poll_interval_sec": 0.2, "timeout_sec": 30.0},
                    },
                    base_dir=ROOT,
                )
                self.assertEqual(PipelineOrchestrator(cfg, base_dir=ROOT).run()["status"], "completed")
                remote_ws = agent_work / "custom_rvc_agent"
                remote_spec = json.loads((remote_ws / "job_spec.json").read_text(encoding="utf-8"))
                self.assertEqual(remote_spec["voice"]["rvc"]["local_pth"], "bundle_inputs/voice/custom_speaker.pth")
                self.assertEqual((remote_ws / "bundle_inputs/voice/custom_speaker.pth").read_bytes(), b"PTH_WEIGHTS_SENTINEL")

                remote_cfg = load_pipeline_config(overrides=remote_spec, base_dir=remote_ws)
                assert remote_cfg.voice_profile is not None
                rpth, ridx = ensure_rvc_model_assets(rvc_cfg=profile_to_rvc_dict(remote_cfg.voice_profile), base_dir=remote_ws)
                self.assertEqual(Path(rpth).read_bytes(), b"PTH_WEIGHTS_SENTINEL")
                self.assertEqual(Path(ridx).read_bytes(), b"INDEX_VECTOR_SENTINEL")
            finally:
                server.shutdown()
                server.server_close()

    def test_05_gdrive_cli_bridge_roundtrip_and_logs(self) -> None:
        from voice_studio.colab.gdrive_cli import GdriveCliBridge

        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            store = tmp_p / "drive_store"
            store.mkdir()
            fake_cli = tmp_p / "gdrive"
            fake_cli.write_text(
                "#!/usr/bin/env python3\n"
                "import json, shutil, sys\n"
                "from pathlib import Path\n"
                f"ROOT = Path({str(store)!r})\n"
                "IDX = ROOT / '_idx.json'\n"
                "idx = json.loads(IDX.read_text()) if IDX.is_file() else {'next': 1, 'nodes': {'root': {'id': 'root', 'name': 'root', 'parent': None, 'mimeType': 'application/vnd.google-apps.folder'}}}\n"
                "def save(): IDX.write_text(json.dumps(idx))\n"
                "args = [a for a in sys.argv[1:] if a != '--json']\n"
                "if args[:2] == ['readonly', 'ls']:\n"
                "  p = args[2] if len(args) > 2 and not args[2].startswith('-') else 'root'\n"
                "  print(json.dumps([v for k, v in idx['nodes'].items() if v['parent'] == p]))\n"
                "elif args[:2] == ['readonly', 'search']:\n"
                "  flag = '--name-exact' if '--name-exact' in args else '--name-contains'\n"
                "  q = args[args.index(flag)+1]\n"
                "  pid = args[args.index('--parent-id')+1] if '--parent-id' in args else None\n"
                "  print(json.dumps([v for v in idx['nodes'].values() if v['name'] == q and (pid is None or v['parent'] == pid)]))\n"
                "elif args[:2] == ['mutate', 'mkdir']:\n"
                "  name = args[2]; p = args[args.index('--parent')+1] if '--parent' in args else 'root'\n"
                "  fid = f'f{idx[\"next\"]}'; idx['next'] += 1\n"
                "  idx['nodes'][fid] = {'id': fid, 'name': name, 'parent': p, 'mimeType': 'application/vnd.google-apps.folder'}\n"
                "  save(); print(json.dumps({'id': fid}))\n"
                "elif args[:2] == ['mutate', 'upload']:\n"
                "  src = Path(args[2]); p = args[args.index('--parent')+1]\n"
                "  fid = f'f{idx[\"next\"]}'; idx['next'] += 1\n"
                "  shutil.copy2(src, ROOT / fid)\n"
                "  idx['nodes'][fid] = {'id': fid, 'name': src.name, 'parent': p, 'mimeType': 'application/octet-stream'}\n"
                "  save(); print(json.dumps({'id': fid}))\n"
                "elif args[:2] == ['mutate', 'update']:\n"
                "  shutil.copy2(args[3], ROOT / args[2]); print(json.dumps({'id': args[2]}))\n"
                "elif args[:2] == ['readonly', 'download']:\n"
                "  shutil.copy2(ROOT / args[2], args[args.index('--out')+1])\n",
                encoding="utf-8",
            )
            fake_cli.chmod(0o755)
            bridge = GdriveCliBridge(root_folder_name="VoiceStudioColab", gdrive_bin=str(fake_cli))
            bundle_p = tmp_p / "bundle.zip"
            bundle_p.write_bytes(b"PK-TEST")
            work_p = tmp_p / "work"
            work_p.mkdir()
            st = bridge.submit_job("gdrive_test", bundle_p, {"job": {"id": "gdrive_test"}}, tmp_dir=work_p)
            self.assertEqual(st["status"], "queued_drive")
            self.assertEqual(bridge.read_status("gdrive_test")["status"], "queued_drive")
            self.assertEqual(bridge.read_logs("gdrive_test")["chunk"], "")

    def test_04_colab_notebook_ast_and_studio_py_detection(self) -> None:
        nb = json.loads((ROOT / "colab/voice_studio_colab.ipynb").read_text(encoding="utf-8"))
        self.assertEqual(nb.get("nbformat"), 4)
        cells = nb.get("cells", [])
        self.assertGreaterEqual(len(cells), 7)

        cleaned_cells = []
        for idx, cell in enumerate(cells):
            if cell.get("cell_type") == "code":
                cleaned = "".join(
                    f"{ln[: len(ln) - len(ln.lstrip())]}pass\n" if ln.lstrip().startswith("!") else ln
                    for ln in "".join(cell.get("source", [])).splitlines(keepends=True)
                )
                ast.parse(cleaned, filename=f"cell_{idx}")
                cleaned_cells.append(cleaned)

        cell1_src = cleaned_cells[0]
        self.assertIn("studio.py", "".join(cells[1]["source"]))
        tree = ast.parse(cell1_src)
        upload_if = next(n for n in tree.body if isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "USE_GOOGLE_DRIVE")
        compiled = compile(ast.Module(body=[upload_if], type_ignores=[]), "<cell1>", "exec")

        upload_calls = 0

        class _FakeFiles:
            @staticmethod
            def upload() -> dict[str, bytes]:
                nonlocal upload_calls
                upload_calls += 1
                return {}

        fake_google = types.ModuleType("google")
        fake_colab = types.ModuleType("google.colab")
        fake_colab.files = _FakeFiles  # type: ignore[attr-defined]
        fake_google.colab = fake_colab  # type: ignore[attr-defined]
        old_g, old_c = sys.modules.get("google"), sys.modules.get("google.colab")
        sys.modules["google"] = fake_google
        sys.modules["google.colab"] = fake_colab
        try:
            with tempfile.TemporaryDirectory() as tmp:
                ws_ready = Path(tmp) / "ready"
                ws_ready.mkdir()
                (ws_ready / "studio.py").write_text("# studio\n", encoding="utf-8")
                exec(compiled, {"os": os, "shutil": shutil, "USE_GOOGLE_DRIVE": False, "WORK_ROOT": str(ws_ready), "BUNDLE_ZIP": "missing.zip", "DRIVE_BUNDLE_PATH": ""})
                self.assertEqual(upload_calls, 0)

                ws_empty = Path(tmp) / "empty"
                ws_empty.mkdir()
                exec(compiled, {"os": os, "shutil": shutil, "USE_GOOGLE_DRIVE": False, "WORK_ROOT": str(ws_empty), "BUNDLE_ZIP": "missing.zip", "DRIVE_BUNDLE_PATH": ""})
                self.assertEqual(upload_calls, 1)
        finally:
            sys.modules.pop("google", None) if old_g is None else sys.modules.__setitem__("google", old_g)
            sys.modules.pop("google.colab", None) if old_c is None else sys.modules.__setitem__("google.colab", old_c)

    def test_06_colab_cli_config_driven_disconnect_and_recreate(self) -> None:
        from voice_studio.colab import ColabCliBridge

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            log_file = t / "cli_calls.log"
            fake_bin_dir = t / "bin"
            fake_bin_dir.mkdir()
            fake_py = fake_bin_dir / "python"
            fake_py.symlink_to(sys.executable)

            fake_colab = fake_bin_dir / "colab"
            script_lines = [
                f"#!{sys.executable}",
                "import hashlib, json, os, sys, zipfile",
                "from pathlib import Path",
                f"LOG = Path({str(log_file)!r})",
                f"STATE_FILE = Path({str(t / 'session.json')!r})",
                "args = sys.argv[1:]",
                "with open(LOG, 'a', encoding='utf-8') as f: f.write(' '.join(args) + '\\n')",
                "cmd = args[0] if args else ''",
                "if cmd == 'sessions':",
                "    st = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {'hw': 'CPU', 'shape': 'STANDARD'}",
                "    print(f\"[voice-studio-pro] ep-old-1 | Hardware: {st['hw']} | Shape: {st['shape']} | Variant: DEFAULT | Status: IDLE\")",
                "elif cmd == 'stop':",
                "    STATE_FILE.unlink(missing_ok=True); print('[colab] Session terminated.')",
                "elif cmd == 'new':",
                "    hw = args[args.index('--gpu') + 1] if '--gpu' in args else 'CPU'",
                "    shape = 'HIGH_RAM' if ('--high-mem' in args or hw == 'L4') else 'STANDARD'",
                "    STATE_FILE.write_text(json.dumps({'hw': hw, 'shape': shape})); print('[colab] Session READY.')",
                "elif cmd == 'url':",
                "    print('https://colab.research.google.com/notebooks/empty.ipynb?dbu=%2Ftun%2Fm%2Fep-l4')",
                "elif cmd == 'upload':",
                "    dst = Path(args[-1].replace('/content/', str(LOG.parent / 'remote_content') + '/'))",
                "    dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(Path(args[-2]).read_bytes())",
                "elif cmd == 'exec':",
                "    code = sys.stdin.read()",
                "    if '_extract_safe' in code:",
                "        rc_root = LOG.parent / 'remote_content'",
                "        j_out = rc_root / 'voice_studio_jobs/cli_test/work/output/cli_test'",
                "        j_out.mkdir(parents=True, exist_ok=True); (j_out / 'deliverables').mkdir(exist_ok=True)",
                "        (j_out / 'deliverables/test.txt').write_text('OK', encoding='utf-8')",
                "        arch = rc_root / 'voice_studio_jobs/cli_test/deliverables_bundle.zip'",
                "        with zipfile.ZipFile(arch, 'w') as zf: zf.write(j_out / 'deliverables/test.txt', 'deliverables/test.txt')",
                "        sha = hashlib.sha256(arch.read_bytes()).hexdigest()",
                "        (j_out / 'status.json').write_text(json.dumps({'job_id':'cli_test','status':'completed','progress_pct':100.0,'steps':{},'archive_sha256':sha}))",
                "        print('[colab-runtime] GPU=NVIDIA L4 | VRAM=22.5 GB'); print('__COLAB_STATUS__=OK')",
                "    elif '__CLI_RC__' in code:",
                "        print('CUDA=True'); print('__CLI_RC__=0')",
                "elif cmd == 'download':",
                "    src = Path(args[-2].replace('/content/', str(LOG.parent / 'remote_content') + '/'))",
                "    Path(args[-1]).parent.mkdir(parents=True, exist_ok=True); Path(args[-1]).write_bytes(src.read_bytes())",
            ]
            fake_colab.write_text("\n".join(script_lines) + "\n", encoding="utf-8")
            fake_colab.chmod(0o755)
            # Provide ADC credential file in temporary HOME so check_colab_cli_auth passes cleanly
            adc_dir = t / ".config/gcloud"
            adc_dir.mkdir(parents=True)
            (adc_dir / "application_default_credentials.json").write_text("{}", encoding="utf-8")

            old_home = os.environ.get("HOME")
            os.environ["HOME"] = str(t)
            try:
                cfg = load_pipeline_config(
                    overrides={
                        "job": {"id": "cli_test", "output_dir": str(t / "out"), "dry_run": True},
                        "output": {"dir": str(t / "out")},
                        "runtime": {"target": "colab_cli", "gpu_tier": "l4", "high_ram": True},
                        "colab": {"cli_binary": str(fake_colab), "session_name": "voice-studio-pro"},
                        "input": {"path": "input/sample_neutral.json"},
                    },
                    base_dir=ROOT,
                )
                state = PipelineOrchestrator(cfg, base_dir=ROOT).run()
                self.assertEqual(state["status"], "completed")
                calls = log_file.read_text(encoding="utf-8")
                self.assertIn("stop -s voice-studio-pro", calls)
                self.assertIn("new -s voice-studio-pro --gpu L4", calls)
                self.assertTrue((t / "out/cli_test/deliverables/test.txt").is_file())
                exec_res = ColabClient(cfg, base_dir=ROOT).exec_command("nvidia-smi")
                self.assertEqual(exec_res["returncode"], 0)
            finally:
                if old_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = old_home

    def test_07_colab_cli_provision_timeout_recovery(self) -> None:
        import subprocess
        from voice_studio.colab.chunk_io import provision_colab_session

        class _FakeBridge:
            session_name = "voice-studio-pro"

            def _run_cli(self, cmd: list[str], timeout: float = 420.0):  # noqa: ARG002
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=1.0)

            def list_sessions(self) -> list[dict[str, str]]:
                return [{"name": "voice-studio-pro", "endpoint": "ep-l4", "hardware": "L4", "shape": "HIGH_RAM", "status": "IDLE"}]

            def _matches_config(self, rec: dict[str, str], desired_hw: str, high_ram: bool) -> bool:  # noqa: ARG002
                return rec.get("hardware") == desired_hw

        rec = provision_colab_session(_FakeBridge(), ["new", "-s", "voice-studio-pro", "--gpu", "L4"], "L4", True, poll_sleep_sec=0.0)
        self.assertEqual(rec["endpoint"], "ep-l4")
        self.assertEqual(rec["hardware"], "L4")

    def test_08_sentinel_ok_survives_trailing_connection_lost(self) -> None:
        from voice_studio.colab.chunk_io import stream_popen_with_sentinel

        class _FakeProc:
            def __init__(self) -> None:
                self.terminated = False
                self.stdout = iter(["work line\n", "__COLAB_STATUS__=OK\n", "Connection was lost.\n"])

            def poll(self) -> int | None:
                return None

            def terminate(self) -> None:
                self.terminated = True

            def wait(self, timeout: float = 60.0) -> int:  # noqa: ARG002
                return 1

        proc = _FakeProc()
        lines: list[str] = []
        rc = stream_popen_with_sentinel(proc, line_callback=lines.append)
        self.assertEqual(rc, 0)
        self.assertTrue(proc.terminated)
        self.assertIn("__COLAB_STATUS__=OK", lines)

    def test_09_colab_provision_auto_fallback_on_503(self) -> None:
        import subprocess
        from voice_studio.colab.chunk_io import provision_colab_session

        class _Fake503Bridge:
            session_name = "voice-studio-pro"
            seen_cmds: list[list[str]] = []

            def _run_cli(self, args: list[str], timeout: float = 120.0) -> subprocess.CompletedProcess[str]:  # noqa: ARG002
                self.seen_cmds.append(list(args))
                if "L4" in args:
                    return subprocess.CompletedProcess(args, 1, "", "ColabRequestError: Service Unavailable (503)")
                return subprocess.CompletedProcess(args, 0, "Session created on T4", "")

            def list_sessions(self) -> list[dict[str, str]]:
                if any("T4" in c for c in self.seen_cmds):
                    return [{"name": "voice-studio-pro", "hardware": "T4", "shape": "HIGH_RAM"}]
                return []

            def _matches_config(self, row: dict[str, str], desired_hw: str, high_ram: bool) -> bool:  # noqa: ARG002
                return row.get("hardware") == desired_hw

        bridge = _Fake503Bridge()
        rec = provision_colab_session(bridge, ["new", "-s", "voice-studio-pro", "--gpu", "L4"], "L4", True, poll_sleep_sec=0.0)
        self.assertEqual(rec["hardware"], "T4")
        self.assertEqual(len(bridge.seen_cmds), 2)
        self.assertIn("T4", bridge.seen_cmds[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
