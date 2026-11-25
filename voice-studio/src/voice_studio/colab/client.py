# -*- coding: utf-8 -*-
"""Unified Colab Client: connects to Colab (agent or signed-in Drive), tracks live status, and downloads outputs."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from voice_studio import PROJECT_ROOT
from voice_studio.colab.bundle import create_remote_job_bundle
from voice_studio.colab.colab_cli_bridge import ColabCliBridge
from voice_studio.colab.drive_bridge import (
    DriveApiBridge,
    LocalDriveSyncBridge,
    authenticate_google_drive,
)
from voice_studio.colab.gdrive_cli import GdriveCliBridge, resolve_gdrive_cli_binary
from voice_studio.config.loader import resolve_repo_path
from voice_studio.config.models import PipelineConfig
from voice_studio.core.security import is_non_loopback_http, safe_extract_zip, sha256_file
from voice_studio.core.state import format_job_status, save_json_atomic
from voice_studio.pipeline.orchestrator import _serialize_job_spec


class ColabClient:
    """Controls Colab job submission, status monitoring, and verified local artifact download."""

    def __init__(self, cfg: PipelineConfig, base_dir: Path = PROJECT_ROOT) -> None:
        self.cfg = cfg
        self.base_dir = base_dir
        self.colab_cfg = cfg.colab

    def _effective_target(self) -> str:
        if self.cfg.target in ("colab_cli", "colab_agent", "colab_drive"):
            return self.cfg.target
        if self.colab_cfg.agent_url:
            return "colab_agent"
        if self.colab_cfg.mode in ("colab_cli", "colab_agent", "colab_drive"):
            return self.colab_cfg.mode
        return "colab_drive"

    def _agent_request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> tuple[int, bytes, Any]:
        url = self.colab_cfg.agent_url.rstrip("/")
        if not url:
            raise ValueError("colab.agent_url is empty; set --colab-url or COLAB_AGENT_URL.")
        if not self.colab_cfg.agent_token:
            raise ValueError("colab.agent_token is empty; set --colab-token or COLAB_AGENT_TOKEN.")
        if is_non_loopback_http(url):
            print(f"[WARN][security] Sending Bearer token over plaintext HTTP ({url}); prefer HTTPS tunnel URL.")
        full_url = f"{url}{path}"
        headers = {"Authorization": f"Bearer {self.colab_cfg.agent_token}"}
        data_bytes = None
        if payload is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(full_url, data=data_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.colab_cfg.request_timeout_sec) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Colab agent HTTP {err.code} on {path}: {body}") from err

    def _drive_bridge(self) -> DriveApiBridge | GdriveCliBridge | LocalDriveSyncBridge:
        if self.colab_cfg.local_mount_dir:
            mount = resolve_repo_path(self.colab_cfg.local_mount_dir, self.base_dir)
            return LocalDriveSyncBridge(mount, root_folder_name=self.colab_cfg.root_folder_name)
        secrets = resolve_repo_path(self.colab_cfg.client_secrets_path, self.base_dir)
        token_p = resolve_repo_path(self.colab_cfg.token_path, self.base_dir)
        if token_p.is_file() or secrets.is_file() or not resolve_gdrive_cli_binary():
            service = authenticate_google_drive(secrets, token_p)
            return DriveApiBridge(service, root_folder_name=self.colab_cfg.root_folder_name)
        return GdriveCliBridge(root_folder_name=self.colab_cfg.root_folder_name)

    def _cli_bridge(self) -> ColabCliBridge:
        import importlib
        import voice_studio.colab.colab_cli_bridge as _cli_mod

        importlib.reload(_cli_mod)
        return _cli_mod.ColabCliBridge(self.cfg, base_dir=self.base_dir)

    def ping(self, ensure: bool = False) -> Dict[str, Any]:
        """Verifies connectivity and inspects remote Colab GPU/CPU runtime."""
        eff = self._effective_target()
        if eff == "colab_cli":
            return self._cli_bridge().ping(ensure=ensure)
        if eff == "colab_agent":
            _, body, _ = self._agent_request("GET", "/api/v1/health")
            return json.loads(body.decode("utf-8"))
        bridge = self._drive_bridge()
        return {"ok": True, "mode": "colab_drive", "bridge": type(bridge).__name__}

    def stop_session(self, session_name: str = "") -> Dict[str, Any]:
        """Stops the active Colab CLI session to immediately stop Compute Unit consumption."""
        eff = self._effective_target()
        if eff != "colab_cli":
            return {"ok": False, "mode": eff, "detail": "stop_session is only supported for colab_cli target"}
        bridge = self._cli_bridge()
        target_name = session_name.strip() or bridge.session_name
        bridge.stop_session(target_name)
        return {"ok": True, "mode": "colab_cli", "stopped_session": target_name}

    def _stage_path(self, raw_path: str, bundle_subpath: str, staged: List[Tuple[Path, str]]) -> str:
        resolved = resolve_repo_path(raw_path, self.base_dir)
        if not resolved.is_file():
            return raw_path
        res_abs = resolved.resolve()
        base_abs = self.base_dir.resolve()
        if res_abs.is_relative_to(base_abs):
            rel = res_abs.relative_to(base_abs).as_posix()
            if not any(arc == rel and p.resolve() == res_abs for p, arc in staged):
                staged.append((resolved, rel))
            return rel
        existing = {arc: p.resolve() for p, arc in staged}
        target_arc = bundle_subpath.replace("\\", "/").lstrip("/")
        if target_arc in existing and existing[target_arc] != res_abs:
            cand_p = Path(target_arc)
            idx = 2
            while True:
                alt = (cand_p.parent / f"{cand_p.stem}_{idx}{cand_p.suffix}").as_posix()
                if alt not in existing or existing[alt] == res_abs:
                    target_arc = alt
                    break
                idx += 1
        if target_arc not in existing:
            staged.append((resolved, target_arc))
        return target_arc

    def _prepare_remote_spec_and_files(self, job_dir: Path, job_id: str) -> tuple[Dict[str, Any], List[Tuple[Path, str]]]:
        spec = _serialize_job_spec(self.cfg, for_remote=True)
        staged: List[Tuple[Path, str]] = []
        input_d = spec["input"]

        if self.cfg.content.path:
            in_path = resolve_repo_path(self.cfg.content.path, self.base_dir)
            if in_path.is_file():
                input_d["path"] = self._stage_path(self.cfg.content.path, f"bundle_inputs/{in_path.name}", staged)
                if in_path.suffix.lower() == ".json":
                    try:
                        doc = json.loads(in_path.read_text(encoding="utf-8"))
                        if isinstance(doc, dict):
                            for key in ("slides_png_dir", "pptx_path", "timeline_json", "reveal_manifest", "extra_vocab_path"):
                                if doc.get(key) and not input_d.get(key):
                                    input_d[key] = str(doc[key])
                    except (json.JSONDecodeError, OSError) as exc:
                        print(f"[colab] Input JSON inspection notice: {exc}")

        for key, subname in (
            ("pptx_path", "bundle_inputs/deck.pptx"),
            ("timeline_json", "bundle_inputs/timeline.json"),
            ("reveal_manifest", "bundle_inputs/reveals.json"),
            ("extra_vocab_path", "bundle_inputs/extra_vocab.json"),
        ):
            if input_d.get(key):
                input_d[key] = self._stage_path(str(input_d[key]), subname, staged)

        png_dir_str = str(input_d.get("slides_png_dir") or "")
        if png_dir_str:
            png_dir = resolve_repo_path(png_dir_str, self.base_dir)
            if png_dir.is_dir():
                slide_filter = set(self.cfg.content.slides) if self.cfg.content.slides else None
                for png in sorted(png_dir.glob("slide_*.png")):
                    stem_parts = png.stem.split("_")
                    num = int(stem_parts[1]) if len(stem_parts) > 1 and stem_parts[1].isdigit() else 0
                    if slide_filter is None or num in slide_filter:
                        staged.append((png, f"bundle_inputs/slides_png/{png.name}"))
                input_d["slides_png_dir"] = "bundle_inputs/slides_png"

        # Stage custom local RVC weights cleanly if specified
        rvc_d = (spec.get("voice") or {}).get("rvc") or {}
        for field in ("local_pth", "local_index"):
            raw_v = str(rvc_d.get(field) or "").strip()
            if raw_v:
                rvc_d[field] = self._stage_path(raw_v, f"bundle_inputs/voice/{Path(raw_v).name}", staged)

        if self.cfg.phonetic_vocab_path and self.cfg.phonetic_vocab_path != "src/voice_studio/content/phonetic_vocab.json":
            spec["phonetic_vocab_path"] = self._stage_path(
                self.cfg.phonetic_vocab_path, "bundle_inputs/phonetic_vocab.json", staged
            )

        # Carry over existing local artifacts (always Stage 1 pre-stage; Stage 2+ only on intermediate step resume)
        carry_meta = [
            Path("status.json"),
            Path("transcript/script_from_audio.json"),
            Path("work/prepared_slides.json"),
            Path("work/slides_phonetic.json"),
            Path("work/job_meta.json"),
            Path("work/stage1_manifest.json"),
        ]
        carry_dirs = ["audio_stage1"]
        if self.cfg.resume and self.cfg.steps and self.cfg.steps[0] not in ("transcribe", "prepare", "tts"):
            carry_meta.append(Path("voice_manifest.json"))
            carry_dirs.extend(["audio_items_raw", "audio_slides_wav"])
        for rel_p in carry_meta:
            fp = job_dir / rel_p
            if fp.is_file():
                staged.append((fp, f"output/{job_id}/{rel_p.as_posix()}"))
        for folder in carry_dirs:
            f_dir = job_dir / folder
            if f_dir.is_dir():
                for wav in sorted(f_dir.glob("*.wav")):
                    staged.append((wav, f"output/{job_id}/{folder}/{wav.name}"))
        return spec, staged

    def _build_staged_bundle(self, job_dir: Path, job_id: str) -> Tuple[Dict[str, Any], Path]:
        spec, staged = self._prepare_remote_spec_and_files(job_dir, job_id)
        bundle_zip = job_dir / "work" / "remote_bundle.zip"
        create_remote_job_bundle(bundle_zip, spec, root_dir=self.base_dir, staged_files=staged)
        return spec, bundle_zip

    def submit_job(self, job_dir: Path, job_id: str) -> Dict[str, Any]:
        """Bundles config + code + input assets and submits job to Colab."""
        spec, bundle_zip = self._build_staged_bundle(job_dir, job_id)
        eff = self._effective_target()
        if eff == "colab_cli":
            bridge = self._cli_bridge()
            bridge.ensure_runtime()
            remote_bundle = f"{bridge.remote_root}/{job_id}/remote_bundle.zip"
            bridge.stream_exec(f"import pathlib\npathlib.Path('{bridge.remote_root}/{job_id}').mkdir(parents=True, exist_ok=True)\n", timeout=60.0)
            bridge.upload_file(bundle_zip, remote_bundle)
            return {"job_id": job_id, "status": "submitted", "mode": "colab_cli", "remote_bundle": remote_bundle}
        if eff == "colab_agent":
            payload = {
                "job_id": job_id,
                "job_spec": spec,
                "bundle_b64": base64.b64encode(bundle_zip.read_bytes()).decode("ascii"),
            }
            _, body, _ = self._agent_request("POST", "/api/v1/jobs", payload=payload)
            return json.loads(body.decode("utf-8"))

        bridge = self._drive_bridge()
        if isinstance(bridge, (DriveApiBridge, GdriveCliBridge)):
            return bridge.submit_job(job_id, bundle_zip, spec, tmp_dir=job_dir / "work")
        return bridge.submit_job(job_id, bundle_zip, spec)

    def fetch_logs(self, job_id: str, offset: int = 0) -> Dict[str, Any]:
        """Fetches incremental Live Terminal output chunk for a remote Colab job."""
        eff = self._effective_target()
        if eff == "colab_cli":
            log_p = resolve_repo_path(self.cfg.output_root, self.base_dir) / job_id / "logs" / "live_terminal.log"
            if not log_p.is_file():
                return {"job_id": job_id, "offset": 0, "chunk": ""}
            raw = log_p.read_bytes()
            safe_off = max(0, min(int(offset), len(raw)))
            return {"job_id": job_id, "offset": len(raw), "chunk": raw[safe_off:].decode("utf-8", errors="replace")}
        if eff == "colab_agent":
            try:
                _, body, _ = self._agent_request("GET", f"/api/v1/jobs/{job_id}/logs?offset={int(offset)}")
                return json.loads(body.decode("utf-8"))
            except RuntimeError as exc:
                if "HTTP 404" in str(exc):
                    return {"job_id": job_id, "offset": offset, "chunk": ""}
                raise
        return self._drive_bridge().read_logs(job_id, offset=offset)

    def exec_command(self, command: str, cwd: str = "", timeout_sec: float = 120.0) -> Dict[str, Any]:
        """Executes a command directly on the connected Colab CLI or HTTP Agent worker."""
        eff = self._effective_target()
        if eff == "colab_cli":
            return self._cli_bridge().exec_command(command=command, cwd=cwd, timeout_sec=timeout_sec)
        if eff != "colab_agent":
            raise RuntimeError("Interactive remote command execution requires colab_cli or colab_agent target.")
        _, body, _ = self._agent_request(
            "POST",
            "/api/v1/exec",
            payload={"cmd": command, "cwd": cwd, "timeout_sec": timeout_sec},
        )
        return json.loads(body.decode("utf-8"))

    def run_interactive_shell(self) -> int:
        """Opens a live interactive command shell connected to remote Colab worker."""
        info = self.ping()
        rt = dict(info.get("runtime") or {})
        print(f"[colab-shell] Connected ({self._effective_target()}) | GPU={rt.get('device_name', 'drive')} | VRAM={rt.get('vram_gb', 0)} GB")
        print("[colab-shell] Type any shell command, 'status <job_id>', 'logs <job_id>', or 'exit'.")
        while True:
            try:
                line = input("colab> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("")
                return 0
            if not line or line in ("exit", "quit"):
                return 0
            if line.startswith("status "):
                st = self.fetch_status(line[7:].strip())
                print(format_job_status(st) if st else "Job not found.")
                continue
            if line.startswith("logs "):
                res = self.fetch_logs(line[5:].strip(), offset=0)
                print(res.get("chunk") or "(no logs yet)")
                continue
            out = self.exec_command(line)
            if out.get("stdout"):
                print(out["stdout"], end="" if out["stdout"].endswith("\n") else "\n")
            if out.get("stderr"):
                print(f"[stderr] {out['stderr']}", end="" if out["stderr"].endswith("\n") else "\n")
            print(f"[exit={out.get('returncode')} | {out.get('elapsed_sec')}s]")

    def fetch_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves current remote status JSON from Colab."""
        eff = self._effective_target()
        if eff == "colab_cli":
            st_p = resolve_repo_path(self.cfg.output_root, self.base_dir) / job_id / "status.json"
            return json.loads(st_p.read_text(encoding="utf-8")) if st_p.is_file() else None
        if eff == "colab_agent":
            try:
                _, body, _ = self._agent_request("GET", f"/api/v1/jobs/{job_id}/status")
                return json.loads(body.decode("utf-8"))
            except RuntimeError as exc:
                if "HTTP 404" in str(exc):
                    return None
                raise
        return self._drive_bridge().read_status(job_id)

    def download_outputs(self, job_dir: Path, job_id: str) -> Path:
        """Downloads completed artifacts from Colab into local job_dir and verifies SHA-256."""
        eff = self._effective_target()
        if eff == "colab_cli":
            bridge = self._cli_bridge()
            remote_job = f"{bridge.remote_root}/{job_id}"
            st_p = job_dir / "status.json"
            bridge.download_file(f"{remote_job}/work/output/{job_id}/status.json", st_p)
            arch = job_dir / "deliverables_bundle.zip"
            bridge.download_file(f"{remote_job}/deliverables_bundle.zip", arch)
            remote_st = json.loads(st_p.read_text(encoding="utf-8"))
            expected_sha = str(remote_st.get("archive_sha256") or "")
            if expected_sha and sha256_file(arch) != expected_sha:
                raise RuntimeError("Colab CLI archive SHA-256 mismatch")
            safe_extract_zip(arch, job_dir)
            save_json_atomic(st_p, remote_st)
            return arch
        if eff == "colab_agent":
            _, body, headers = self._agent_request("GET", f"/api/v1/jobs/{job_id}/download")
            arch = job_dir / "deliverables_bundle.zip"
            arch.write_bytes(body)
            expected_sha = headers.get("X-Content-SHA256", "")
            actual_sha = sha256_file(arch)
            if expected_sha and actual_sha != expected_sha:
                raise RuntimeError(f"Checksum mismatch on downloaded Colab archive: {actual_sha} != {expected_sha}")
            safe_extract_zip(arch, job_dir)
            return arch

        bridge = self._drive_bridge()
        remote_st = bridge.read_status(job_id) or {}
        downloaded = bridge.download_outputs(job_id, job_dir)
        if not downloaded:
            raise RuntimeError(f"No output files found in Drive for job {job_id}")
        arch = job_dir / "deliverables_bundle.zip"
        expected_sha = str(remote_st.get("archive_sha256") or "")
        if arch.is_file() and expected_sha:
            actual_sha = sha256_file(arch)
            if actual_sha != expected_sha:
                raise RuntimeError(f"Drive archive SHA-256 mismatch: {actual_sha} != {expected_sha}")
        return arch if arch.is_file() else downloaded[0]

    def run_remote_job(self, job_dir: Path, job_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        """Executes Submit -> Live Terminal Stream -> Download -> Verify workflow against Colab."""
        target = self._effective_target()
        if target == "colab_cli":
            _, bundle_zip = self._build_staged_bundle(job_dir, job_id)
            return self._cli_bridge().run_remote_job(job_dir=job_dir, job_id=job_id, bundle_zip=bundle_zip, state=state)
        print(f"[colab] Connecting ({target}) and submitting job {job_id}...")
        ping_info = self.ping()
        rt = dict(ping_info.get("runtime") or {})
        if rt:
            print(f"[colab-live] Remote Runtime: device={rt.get('device_name')} | VRAM={rt.get('vram_gb')} GB | CPU={rt.get('cpu_count')}")
        remote_st = self.submit_job(job_dir, job_id)
        save_json_atomic(job_dir / "status.json", {**state, "status": remote_st.get("status", "submitted")})

        if target == "colab_drive" and not self.colab_cfg.local_mount_dir:
            print(
                f"[colab-drive] Job queued in Drive '{self.colab_cfg.root_folder_name}/{job_id}'. "
                f"Ensure Colab worker ('drive-worker' or Colab MCP) is running; streaming status..."
            )

        deadline = time.monotonic() + float(self.colab_cfg.timeout_sec)
        last_key = ""
        log_offset = 0
        while time.monotonic() < deadline:
            log_res = self.fetch_logs(job_id, offset=log_offset)
            chunk = str(log_res.get("chunk") or "")
            if chunk:
                log_offset = int(log_res.get("offset") or log_offset)
                for ln in chunk.splitlines():
                    print(f"[colab-live] | {ln}", flush=True)

            cur = self.fetch_status(job_id)
            if cur:
                save_json_atomic(job_dir / "status.json", cur)
                key = f"{cur.get('status')}:{cur.get('current_step') or '-'}:{cur.get('progress_pct', 0.0)}%"
                if key != last_key:
                    print(f"[colab] {job_id} -> {key}", flush=True)
                    last_key = key
                if cur.get("status") == "completed":
                    tail_res = self.fetch_logs(job_id, offset=log_offset)
                    if tail_res.get("chunk"):
                        for ln in str(tail_res["chunk"]).splitlines():
                            print(f"[colab-live] | {ln}", flush=True)
                    print(f"[colab] Job {job_id} completed! Downloading outputs to {job_dir}...")
                    self.download_outputs(job_dir, job_id)
                    print("\n" + format_job_status(cur))
                    return cur
                if cur.get("status") == "failed":
                    raise RuntimeError(f"Remote Colab job {job_id} failed: {cur.get('error')}")
            time.sleep(float(self.colab_cfg.poll_interval_sec))

        raise TimeoutError(f"Timed out waiting for Colab job {job_id} after {self.colab_cfg.timeout_sec}s")
