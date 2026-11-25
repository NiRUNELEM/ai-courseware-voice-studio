# -*- coding: utf-8 -*-
"""Authenticated HTTP Colab Worker Agent: receives jobs, reports status/GPU info, and serves verified outputs."""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import zipfile
from urllib.parse import parse_qs, urlparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from voice_studio.config.loader import load_pipeline_config
from voice_studio.core.runtime import probe_runtime
from voice_studio.core.security import (
    constant_time_token_match,
    safe_extract_zip,
    sha256_file,
    validate_safe_id,
)
from voice_studio.core.state import list_jobs, save_json_atomic
from voice_studio.pipeline.orchestrator import PipelineOrchestrator

MAX_AGENT_UPLOAD_BYTES = 256 * 1024 * 1024


class ColabWorkerState:
    """Thread-safe workspace and job tracker for the Colab HTTP Worker Agent."""

    def __init__(self, work_root: Path, token: str) -> None:
        self.work_root = work_root.resolve()
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.lock = threading.Lock()
        self.threads: Dict[str, threading.Thread] = {}

    def job_workspace(self, job_id: str) -> Path:
        safe_id = validate_safe_id(job_id, label="job_id")
        return self.work_root / safe_id

    def launch_job(self, job_id: str, bundle_bytes: bytes, spec_override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Extracts uploaded bundle, writes job_spec.json, and launches orchestrator in background thread."""
        ws = self.job_workspace(job_id)
        ws.mkdir(parents=True, exist_ok=True)
        bundle_zip = ws / "bundle.zip"
        bundle_zip.write_bytes(bundle_bytes)
        safe_extract_zip(bundle_zip, ws)

        spec_file = ws / "job_spec.json"
        if spec_override:
            save_json_atomic(spec_file, spec_override)
        elif not spec_file.is_file():
            raise ValueError("Uploaded bundle missing job_spec.json")

        out_job_dir = ws / "output" / job_id
        out_job_dir.mkdir(parents=True, exist_ok=True)
        status_path = out_job_dir / "status.json"
        if status_path.is_file():
            initial_status = json.loads(status_path.read_text(encoding="utf-8"))
            initial_status["status"] = "queued"
            initial_status["error"] = None
        else:
            initial_status = {"job_id": job_id, "status": "queued", "progress_pct": 0.0, "steps": {}, "error": None}
        save_json_atomic(status_path, initial_status)

        def _worker() -> None:
            log_path = out_job_dir / "logs" / "live_terminal.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                overrides = json.loads(spec_file.read_text(encoding="utf-8"))
                overrides.setdefault("runtime", {})["target"] = "local"
                overrides.setdefault("job", {})["id"] = job_id
                overrides.setdefault("job", {})["output_dir"] = "output"
                overrides.setdefault("output", {})["dir"] = "output"
                cfg = load_pipeline_config(overrides=overrides, base_dir=ws)
                PipelineOrchestrator(cfg, base_dir=ws).run()

                archive_path = ws / "deliverables_bundle.zip"
                with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for fp in sorted(out_job_dir.rglob("*")):
                        if fp.is_file() and fp.suffix != ".tmp":
                            zf.write(fp, arcname=fp.relative_to(out_job_dir).as_posix())
            except Exception as exc:
                with open(log_path, "a", encoding="utf-8") as lf:
                    lf.write(f"\n[colab-agent][ERROR] {exc}\n")
                err_status = {"job_id": job_id, "status": "failed", "progress_pct": 0.0, "error": str(exc)}
                if status_path.is_file():
                    try:
                        err_status = json.loads(status_path.read_text(encoding="utf-8"))
                        err_status["status"] = "failed"
                        err_status["error"] = str(exc)
                    except (json.JSONDecodeError, OSError):
                        err_status["error"] = str(exc)
                save_json_atomic(status_path, err_status)

        t = threading.Thread(target=_worker, name=f"colab-job-{job_id}", daemon=True)
        with self.lock:
            self.threads[job_id] = t
        t.start()
        return initial_status

    def read_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        ws = self.job_workspace(job_id)
        status_path = ws / "output" / job_id / "status.json"
        if not status_path.is_file():
            return None
        return json.loads(status_path.read_text(encoding="utf-8"))

    def read_job_logs(self, job_id: str, offset: int = 0) -> Dict[str, Any]:
        ws = self.job_workspace(job_id)
        log_path = ws / "output" / job_id / "logs" / "live_terminal.log"
        if not log_path.is_file():
            return {"job_id": job_id, "offset": 0, "chunk": ""}
        raw = log_path.read_bytes()
        safe_off = max(0, min(int(offset), len(raw)))
        return {
            "job_id": job_id,
            "offset": len(raw),
            "chunk": raw[safe_off:].decode("utf-8", errors="replace"),
        }

    def exec_command(self, command: str, cwd: str = "", timeout_sec: float = 120.0) -> Dict[str, Any]:
        clean_cmd = (command or "").strip()
        if not clean_cmd:
            raise ValueError("Command must not be empty.")
        run_cwd = self.work_root
        if cwd:
            cand = (self.work_root / cwd).resolve()
            if not cand.is_relative_to(self.work_root):
                raise PermissionError(f"cwd escapes worker root: {cwd}")
            if cand.is_dir():
                run_cwd = cand
        t0 = time.monotonic()
        proc = subprocess.run(
            clean_cmd,
            shell=True,
            cwd=str(run_cwd),
            capture_output=True,
            text=True,
            timeout=max(1.0, min(float(timeout_sec), 600.0)),
        )
        return {
            "command": clean_cmd,
            "cwd": str(run_cwd),
            "returncode": proc.returncode,
            "stdout": proc.stdout[-32000:],
            "stderr": proc.stderr[-16000:],
            "elapsed_sec": round(time.monotonic() - t0, 2),
        }

    def archive_path(self, job_id: str) -> Path:
        return self.job_workspace(job_id) / "deliverables_bundle.zip"


def _make_handler(worker_state: ColabWorkerState) -> type[BaseHTTPRequestHandler]:
    class _AgentHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[colab-agent] {self.address_string()} - {fmt % args}")

        def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _check_auth(self) -> bool:
            auth_hdr = self.headers.get("Authorization", "")
            token = auth_hdr[7:].strip() if auth_hdr.startswith("Bearer ") else ""
            if not constant_time_token_match(token, worker_state.token):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "Unauthorized"})
                return False
            return True

        def do_GET(self) -> None:
            if not self._check_auth():
                return
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/api/v1/health":
                self._send_json(HTTPStatus.OK, {"ok": True, "runtime": probe_runtime()})
                return
            if path == "/api/v1/jobs":
                items = []
                for sub in sorted(worker_state.work_root.iterdir()):
                    if sub.is_dir():
                        items.extend(list_jobs(sub / "output"))
                self._send_json(HTTPStatus.OK, {"jobs": items})
                return
            parts = [p for p in path.split("/") if p]
            if len(parts) == 5 and parts[:3] == ["api", "v1", "jobs"] and parts[4] == "status":
                try:
                    st = worker_state.read_job_status(parts[3])
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                if st is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Job not found"})
                    return
                self._send_json(HTTPStatus.OK, st)
                return
            if len(parts) == 5 and parts[:3] == ["api", "v1", "jobs"] and parts[4] == "logs":
                try:
                    off = int((query.get("offset") or ["0"])[0])
                    payload = worker_state.read_job_logs(validate_safe_id(parts[3], label="job_id"), offset=off)
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                self._send_json(HTTPStatus.OK, payload)
                return
            if len(parts) == 5 and parts[:3] == ["api", "v1", "jobs"] and parts[4] == "download":
                try:
                    job_id = validate_safe_id(parts[3], label="job_id")
                except ValueError as exc:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                    return
                st = worker_state.read_job_status(job_id)
                if st is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Job not found"})
                    return
                arch = worker_state.archive_path(job_id)
                if not arch.is_file():
                    self._send_json(HTTPStatus.CONFLICT, {"error": f"Archive not ready (status={st.get('status')})"})
                    return
                digest = sha256_file(arch)
                size = arch.stat().st_size
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(size))
                self.send_header("X-Content-SHA256", digest)
                self.end_headers()
                with open(arch, "rb") as f:
                    shutil.copyfileobj(f, self.wfile)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})

        def do_POST(self) -> None:
            if not self._check_auth():
                return
            path = self.path.split("?", 1)[0]
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_AGENT_UPLOAD_BYTES:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length"})
                    return
                raw_body = self.rfile.read(length)
                data = json.loads(raw_body.decode("utf-8"))
                if path == "/api/v1/exec":
                    res = worker_state.exec_command(
                        command=str(data.get("cmd") or data.get("command") or ""),
                        cwd=str(data.get("cwd") or ""),
                        timeout_sec=float(data.get("timeout_sec") or 120.0),
                    )
                    self._send_json(HTTPStatus.OK, res)
                    return
                if path != "/api/v1/jobs":
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown endpoint"})
                    return
                job_id = validate_safe_id(str(data["job_id"]), label="job_id")
                bundle_bytes = base64.b64decode(data["bundle_b64"], validate=True)
                spec_override = data.get("job_spec")
                st = worker_state.launch_job(job_id, bundle_bytes, spec_override=spec_override)
                self._send_json(HTTPStatus.ACCEPTED, st)
            except Exception as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    return _AgentHandler


def start_agent_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str = "",
    work_root: Optional[Path] = None,
) -> tuple[ThreadingHTTPServer, ColabWorkerState]:
    """Creates and returns a configured Colab HTTP Worker server instance."""
    resolved_token = token or os.environ.get("COLAB_AGENT_TOKEN") or secrets.token_urlsafe(24)
    if len(resolved_token) < 16:
        print("[WARN][security] Colab agent bearer token is shorter than 16 characters; use a strong random token.")
    if host == "0.0.0.0":
        print("[NOTICE][security] Agent bound to 0.0.0.0; expose ONLY through an encrypted tunnel with Bearer auth.")
    root = work_root or Path(os.environ.get("COLAB_AGENT_WORKDIR", "/tmp/voice_studio_colab_agent"))
    state = ColabWorkerState(root, resolved_token)
    server = ThreadingHTTPServer((host, port), _make_handler(state))
    return server, state
