# -*- coding: utf-8 -*-
"""Config-driven Google Colab CLI bridge: pre-flight GPU allocation, live streaming, and verified sync."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from voice_studio import PROJECT_ROOT
from voice_studio.config.models import PipelineConfig
from voice_studio.core.security import safe_extract_zip, sha256_file, validate_safe_id
from voice_studio.core.state import format_job_status, save_json_atomic

SESSION_LINE_RE = re.compile(
    r"^\[([^\]]+)\]\s+(\S+)\s+\|\s+Hardware:\s*([^|]+)\|\s+Shape:\s*([^|]+)\|\s+Variant:\s*([^|]+)(?:\|\s+Status:\s*(.+))?$"
)
GPU_TIER_MAP = {
    "auto": "L4",
    "l4": "L4",
    "a100": "A100",
    "t4": "T4",
    "g4": "G4",
    "h100": "H100",
    "cpu": "CPU",
    "none": "CPU",
}
SINGLE_SHAPE_ACCELERATORS = {"L4", "V5E1", "V6E1"}


def resolve_colab_cli_binary(base_dir: Path = PROJECT_ROOT, explicit_bin: str = "") -> str:
    """Finds Google Colab CLI executable cleanly from explicit path, local/parent venv, or PATH."""
    candidates = [explicit_bin] if explicit_bin else []
    candidates.extend([
        str(base_dir / ".venv-colab-mcp" / "bin" / "colab"),
        str(base_dir.parent / ".venv-colab-mcp" / "bin" / "colab"),
        str(Path(sys.executable).parent / "colab"),
        shutil.which("colab") or "",
    ])
    for cand in candidates:
        if cand and Path(cand).is_file():
            return str(Path(cand).resolve())
    return ""


def _resolve_companion_python(colab_bin: str) -> str:
    py_cand = Path(colab_bin).parent / "python"
    return str(py_cand) if py_cand.is_file() else sys.executable


def _spawn_terminal_monitor(live_log: Path, job_id: str) -> None:
    """Optionally opens a separate desktop terminal window tailing live_terminal.log until the job finishes."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    if os.environ.get("VOICE_STUDIO_OPEN_TERMINAL", "0").strip().lower() not in ("1", "true", "yes"):
        return
    tail_cmd = f"tail -n +1 -f {str(live_log)!r}"
    for term_bin, args in (
        ("foot", ["foot", "--title", f"Voice Studio Live: {job_id}", "sh", "-c", tail_cmd]),
        ("kitty", ["kitty", "--title", f"Voice Studio Live: {job_id}", "sh", "-c", tail_cmd]),
        ("alacritty", ["alacritty", "-t", f"Voice Studio Live: {job_id}", "-e", "sh", "-c", tail_cmd]),
        ("konsole", ["konsole", "-e", "sh", "-c", tail_cmd]),
        ("gnome-terminal", ["gnome-terminal", "--", "sh", "-c", tail_cmd]),
        ("xterm", ["xterm", "-T", f"Voice Studio Live: {job_id}", "-e", "sh", "-c", tail_cmd]),
    ):
        if shutil.which(term_bin):
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            break


def normalize_gpu_request(gpu_tier: str, high_ram: bool) -> Tuple[str, bool]:
    """Maps studio gpu_tier + high_ram config into colab-cli hardware label and --high-mem flag."""
    hw = GPU_TIER_MAP.get((gpu_tier or "l4").strip().lower(), "L4")
    use_high_mem = bool(high_ram) and hw not in SINGLE_SHAPE_ACCELERATORS
    return hw, use_high_mem


def parse_colab_sessions_output(raw_text: str) -> List[Dict[str, str]]:
    """Parses `colab sessions` / `colab status` stdout lines into structured session records."""
    sessions: List[Dict[str, str]] = []
    for raw_ln in (raw_text or "").splitlines():
        ln = raw_ln.strip()
        m = SESSION_LINE_RE.match(ln)
        if not m:
            continue
        sessions.append({
            "name": m.group(1).strip(),
            "endpoint": m.group(2).strip(),
            "hardware": m.group(3).strip().upper(),
            "shape": m.group(4).strip().upper(),
            "variant": m.group(5).strip().upper(),
            "status": (m.group(6) or "").strip(),
        })
    return sessions


def check_colab_cli_auth(colab_bin: str = "", base_dir: Path = PROJECT_ROOT) -> Dict[str, Any]:
    """Checks non-interactively whether google-colab-cli already has stored OAuth, .env, or ADC credentials."""
    exe = colab_bin or resolve_colab_cli_binary(base_dir)
    if not exe:
        return {"installed": False, "signed_in": False, "binary": "", "detail": "colab CLI not installed"}
    adc_path = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
    if adc_path.is_file():
        return {"installed": True, "signed_in": True, "binary": exe, "auth_mode": "adc"}
    py_exe = _resolve_companion_python(exe)
    env_tok = os.environ.get("COLAB_CLI_OAUTH_TOKEN_JSON", "").strip()
    probe_code = (
        "import os, keyring\n"
        "tok = keyring.get_password('google-colab-cli', 'oauth_token')\n"
        "if not tok and os.environ.get('COLAB_CLI_OAUTH_TOKEN_JSON'):\n"
        "    tok = os.environ['COLAB_CLI_OAUTH_TOKEN_JSON']\n"
        "    keyring.set_password('google-colab-cli', 'oauth_token', tok)\n"
        "print('OK' if tok else 'NONE')\n"
    )
    env_copy = dict(os.environ)
    if env_tok:
        env_copy["COLAB_CLI_OAUTH_TOKEN_JSON"] = env_tok
    proc = subprocess.run([py_exe, "-c", probe_code], capture_output=True, text=True, timeout=15.0, env=env_copy)
    signed = proc.returncode == 0 and proc.stdout.strip().endswith("OK")
    return {
        "installed": True,
        "signed_in": signed,
        "binary": exe,
        "auth_mode": "oauth2" if signed else "unauthenticated",
        "detail": "" if signed else (proc.stderr.strip() or "Run `studio.py mcp --login --mode colab_cli` or `.venv-colab-mcp/bin/colab usage` to authorize."),
    }


def _build_remote_runner_script(job_id: str, remote_root: str, resume_job: bool = True) -> str:
    """Builds Python script piped to `colab exec` to verify GPU, install deps, run studio job, and bundle ZIP."""
    return f"""
import json, os, platform, shutil, subprocess, sys, time, zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
t0 = time.monotonic()
job_id = {job_id!r}
ws = Path({remote_root!r}) / job_id / "work"
if not {bool(resume_job)!r}:
    shutil.rmtree(ws / "output" / job_id, ignore_errors=True)
shutil.rmtree(ws / "src", ignore_errors=True)
ws.mkdir(parents=True, exist_ok=True)
bundle_zip = Path({remote_root!r}) / job_id / "remote_bundle.zip"
rvc_cache = Path("/content/voice_studio_rvc")
rvc_cache.mkdir(parents=True, exist_ok=True)

def _extract_safe(zp: Path, dst: Path) -> None:
    root = dst.resolve()
    with zipfile.ZipFile(zp, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = (root / info.filename).resolve()
            target.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src_f, open(target, "wb") as out_f:
                shutil.copyfileobj(src_f, out_f)

_extract_safe(bundle_zip, ws)

gpu_name, vram_gb, cuda_ok = "CPU", 0.0, False
try:
    import torch
    cuda_ok = bool(torch.cuda.is_available())
    if cuda_ok:
        props = torch.cuda.get_device_properties(0)
        gpu_name = str(props.name)
        vram_gb = round(props.total_memory / (1024 ** 3), 2)
except Exception as exc:
    print(f"[colab-cli][WARN] torch probe note: {{exc}}", flush=True)

ram_gb = round((os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / (1024 ** 3), 2)
os.environ.setdefault("OMP_NUM_THREADS", str(os.cpu_count() or 2))
os.environ.setdefault("MKL_NUM_THREADS", str(os.cpu_count() or 2))
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
print(f"[colab-runtime] GPU={{gpu_name}} | VRAM={{vram_gb}} GB | RAM={{ram_gb}} GB | CPU={{os.cpu_count()}} | CUDA={{cuda_ok}}", flush=True)

required_pkgs = [
    ("edge_tts", "edge-tts"), ("soundfile", "soundfile"), ("scipy", "scipy"), ("soxr", "soxr"),
    ("pyloudnorm", "pyloudnorm"), ("faster_whisper", "faster-whisper"), ("transformers", "transformers"),
    ("huggingface_hub", "huggingface_hub"), ("faiss", "faiss-cpu"), ("einops", "einops"),
    ("yaml", "PyYAML"), ("PIL", "pillow"), ("pptx", "python-pptx"), ("pypdf", "pypdf"), ("librosa", "librosa"),
]
if cuda_ok:
    required_pkgs.append(("onnxruntime", "onnxruntime-gpu"))
missing = []
for mod_name, pip_name in required_pkgs:
    try:
        __import__(mod_name)
    except ImportError:
        missing.append(pip_name)
if missing:
    print(f"[colab-setup] Installing missing runtime packages: {{', '.join(missing)}}", flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *missing], check=True)

os.chdir(str(ws))
for m_k in [k for k in list(sys.modules) if k == "voice_studio" or k.startswith("voice_studio.")]:
    del sys.modules[m_k]
import importlib
importlib.invalidate_caches()
if str(ws / "src") not in sys.path:
    sys.path.insert(0, str(ws / "src"))
from voice_studio.config.loader import load_pipeline_config
from voice_studio.core.security import sha256_file
from voice_studio.core.state import save_json_atomic
from voice_studio.pipeline.orchestrator import PipelineOrchestrator

spec_path = ws / "job_spec.json"
overrides = json.loads(spec_path.read_text(encoding="utf-8"))
overrides.setdefault("runtime", {{}})["target"] = "local"
overrides["runtime"]["rvc_assets_root"] = str(rvc_cache)
overrides.setdefault("job", {{}})["id"] = job_id
overrides["job"]["output_dir"] = "output"
overrides["job"]["resume"] = True
overrides.setdefault("output", {{}})["dir"] = "output"
cfg = load_pipeline_config(overrides=overrides, base_dir=ws)
try:
    state = PipelineOrchestrator(cfg, base_dir=ws).run()
except Exception as exc:
    import traceback
    err_payload = {{"job_id": job_id, "error": str(exc), "traceback": traceback.format_exc()}}
    print("__COLAB_ERROR_JSON__=" + json.dumps(err_payload, ensure_ascii=False), flush=True)
    raise

out_job_dir = ws / "output" / job_id
runtime_report = {{
    "job_id": job_id,
    "gpu_name": gpu_name,
    "vram_gb": vram_gb,
    "ram_gb": ram_gb,
    "cpu_count": os.cpu_count(),
    "cuda_available": cuda_ok,
    "platform": platform.platform(),
    "elapsed_sec": round(time.monotonic() - t0, 2),
}}
save_json_atomic(out_job_dir / "colab_runtime_report.json", runtime_report)

skip_top = {{"audio_stage1", "audio_items_raw", "video_segments_1080p", "audio_slides_wav", "audio_slides_mp3", "logs"}}
archive_path = Path({remote_root!r}) / job_id / "deliverables_bundle.zip"
with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for fp in sorted(out_job_dir.rglob("*")):
        rel = fp.relative_to(out_job_dir)
        if fp.is_file() and fp.suffix != ".tmp" and rel.parts[0] not in skip_top and "_deliverables_" not in fp.name:
            zf.write(fp, arcname=rel.as_posix())

state_p = out_job_dir / "status.json"
final_st = json.loads(state_p.read_text(encoding="utf-8")) if state_p.is_file() else dict(state)
final_st["archive_sha256"] = sha256_file(archive_path)
final_st["colab_runtime"] = runtime_report
save_json_atomic(state_p, final_st)
print(f"[colab-done] archive={{archive_path}} | sha256={{final_st['archive_sha256']}} | elapsed={{runtime_report['elapsed_sec']}}s", flush=True)
print("__COLAB_STATUS__=OK", flush=True)
""".strip()


class ColabCliBridge:
    """Controls config-driven Colab GPU runtime provisioning and live execution via google-colab-cli."""

    def __init__(self, cfg: PipelineConfig, base_dir: Path = PROJECT_ROOT) -> None:
        self.cfg = cfg
        self.base_dir = base_dir
        self.session_name = validate_safe_id(cfg.colab.session_name or "voice-studio-pro", label="session_name")
        self.colab_bin = resolve_colab_cli_binary(base_dir, explicit_bin=cfg.colab.cli_binary)
        if not self.colab_bin:
            raise RuntimeError("google-colab-cli (`colab`) not found. Install it into `.venv-colab-mcp` first.")
        self.remote_root = "/content/voice_studio_jobs"

    def _run_cli(self, args: List[str], timeout: float = 120.0, stdin_text: Optional[str] = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.colab_bin, *args],
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _require_auth(self) -> Dict[str, Any]:
        auth = check_colab_cli_auth(self.colab_bin, self.base_dir)
        if not auth["signed_in"]:
            raise RuntimeError(f"Colab CLI not authenticated: {auth['detail']}")
        return auth

    def list_sessions(self) -> List[Dict[str, str]]:
        self._require_auth()
        proc = self._run_cli(["sessions"], timeout=30.0)
        if proc.returncode != 0:
            raise RuntimeError(f"colab sessions failed: {(proc.stderr or proc.stdout).strip()}")
        return parse_colab_sessions_output(proc.stdout)

    def stop_session(self, name: str) -> None:
        proc = self._run_cli(["stop", "-s", name], timeout=60.0)
        if proc.returncode != 0:
            raise RuntimeError(f"colab stop -s {name} failed: {(proc.stderr or proc.stdout).strip()}")

    def unassign_endpoint(self, endpoint: str) -> None:
        """Releases an orphaned server-side Colab runtime whose name is '?' in `colab sessions`."""
        py_exe = _resolve_companion_python(self.colab_bin)
        code = "import sys; from colab_cli.common import state; state.client.unassign(sys.argv[1])"
        proc = subprocess.run([py_exe, "-c", code, endpoint], capture_output=True, text=True, timeout=30.0)
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to unassign Colab endpoint {endpoint}: {(proc.stderr or proc.stdout).strip()}")

    def _matches_config(self, rec: Dict[str, str], desired_hw: str, desired_high_mem: bool) -> bool:
        hw = rec.get("hardware", "").upper()
        if hw != desired_hw:
            return False
        if desired_high_mem and hw not in ("L4", "A100") and "HIGH" not in rec.get("shape", "").upper():
            return False
        return True

    def ensure_runtime(self, force_recreate: bool = False) -> Dict[str, Any]:
        """Disconnects mismatched/stale runtimes and provisions GPU tier + High-RAM from config before work."""
        desired_hw, desired_high_mem = normalize_gpu_request(self.cfg.runtime.gpu_tier, self.cfg.runtime.high_ram)
        print(f"[colab-cli] Pre-flight runtime config: session={self.session_name} | target_hw={desired_hw} | high_ram={self.cfg.runtime.high_ram}")
        active = self.list_sessions()
        reused: Optional[Dict[str, str]] = None

        for rec in active:
            if rec["name"] == self.session_name and not force_recreate and self._matches_config(rec, desired_hw, desired_high_mem):
                reused = rec
                continue
            if self.cfg.colab.disconnect_mismatched or rec["name"] == self.session_name:
                if rec["name"] != "?":
                    print(f"[colab-cli] Disconnecting stale/mismatched session '{rec['name']}' ({rec['hardware']} / {rec['shape']})...")
                    self.stop_session(rec["name"])
                else:
                    print(f"[colab-cli] Releasing orphaned runtime {rec['endpoint']} ({rec['hardware']} / {rec['shape']})...")
                    self.unassign_endpoint(rec["endpoint"])

        if reused is None:
            cmd = ["new", "-s", self.session_name]
            if desired_hw != "CPU":
                cmd.extend(["--gpu", desired_hw])
            if desired_high_mem:
                cmd.append("--high-mem")
            print(f"[colab-cli] Provisioning fresh runtime: {' '.join(cmd)}")
            from voice_studio.colab.chunk_io import provision_colab_session

            reused = provision_colab_session(
                self,
                cmd,
                desired_hw,
                bool(self.cfg.runtime.high_ram),
                fallback_tiers=list(self.cfg.colab.gpu_fallback_tiers) or None,
            )

        url_proc = self._run_cli(["url", "-s", self.session_name], timeout=20.0)
        attach_url = url_proc.stdout.strip() if url_proc.returncode == 0 else ""
        if attach_url:
            print(f"[colab-cli] Attach URL: {attach_url}")
        return {"ok": True, "mode": "colab_cli", "session": self.session_name, "runtime": reused, "url": attach_url}

    def ping(self, ensure: bool = False) -> Dict[str, Any]:
        """Checks auth and active session state, optionally provisioning matching GPU tier."""
        if ensure:
            return self.ensure_runtime()
        auth = check_colab_cli_auth(self.colab_bin, self.base_dir)
        desired_hw, _ = normalize_gpu_request(self.cfg.runtime.gpu_tier, self.cfg.runtime.high_ram)
        sessions = self.list_sessions() if auth["signed_in"] else []
        cur = next((s for s in sessions if s["name"] == self.session_name), None)
        return {
            "ok": bool(auth["signed_in"]),
            "mode": "colab_cli",
            "session": self.session_name,
            "desired_hardware": desired_hw,
            "desired_high_ram": bool(self.cfg.runtime.high_ram),
            "auth": auth,
            "active_session": cur,
            "all_sessions": sessions,
        }

    def stream_exec(
        self,
        code: str,
        timeout: float = 3600.0,
        line_callback: Optional[Callable[[str], None]] = None,
    ) -> int:
        """Executes Python code in the provisioned Colab session and streams output live with WS retry."""
        from voice_studio.colab.chunk_io import run_colab_exec_with_retry

        return run_colab_exec_with_retry(
            self.colab_bin,
            self.session_name,
            code,
            timeout=timeout,
            line_callback=line_callback,
        )

    def exec_command(self, command: str, cwd: str = "", timeout_sec: float = 120.0) -> Dict[str, Any]:
        """Runs a single shell command inside the provisioned Colab VM via colab-cli."""
        clean_cmd = (command or "").strip()
        if not clean_cmd or "\x00" in clean_cmd:
            raise ValueError("Command must be non-empty and free of NUL bytes.")
        self.ensure_runtime()
        t0 = time.monotonic()
        wrapped = (
            "import subprocess, sys\n"
            f"p = subprocess.run({clean_cmd!r}, shell=True, cwd={cwd or '/content'!r}, text=True, capture_output=True)\n"
            "if p.stdout: sys.stdout.write(p.stdout)\n"
            "if p.stderr: sys.stderr.write(p.stderr)\n"
            "print('__CLI_RC__=' + str(p.returncode))\n"
        )
        lines: List[str] = []
        rc_exec = self.stream_exec(wrapped, timeout=timeout_sec, line_callback=lines.append)
        cmd_rc = rc_exec
        out_lines: List[str] = []
        for ln in lines:
            if ln.startswith("__CLI_RC__="):
                cmd_rc = int(ln.partition("=")[2].strip())
            else:
                out_lines.append(ln)
        return {"returncode": cmd_rc, "stdout": "\n".join(out_lines), "stderr": "", "elapsed_sec": round(time.monotonic() - t0, 2)}

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        from voice_studio.colab.chunk_io import chunked_upload_file

        chunked_upload_file(self, local_path, remote_path)

    def download_file(self, remote_path: str, local_path: Path) -> None:
        from voice_studio.colab.chunk_io import chunked_download_file

        chunked_download_file(self, remote_path, local_path)

    def run_remote_job(self, job_dir: Path, job_id: str, bundle_zip: Path, state: Dict[str, Any]) -> Dict[str, Any]:
        """Provisions matching GPU VM, uploads job bundle, streams Live Terminal, and downloads outputs."""
        safe_id = validate_safe_id(job_id, label="job_id")
        rt_info = self.ensure_runtime()
        save_json_atomic(job_dir / "status.json", {**state, "status": "running", "colab_runtime": rt_info.get("runtime")})

        remote_job_dir = f"{self.remote_root}/{safe_id}"
        remote_bundle = f"{remote_job_dir}/remote_bundle.zip"
        mkdir_rc = self.stream_exec(f"import pathlib\npathlib.Path({remote_job_dir!r}).mkdir(parents=True, exist_ok=True)\n", timeout=60.0)
        if mkdir_rc != 0:
            raise RuntimeError(f"Failed to create remote dir {remote_job_dir} (exit {mkdir_rc})")

        print(f"[colab-cli] Uploading job bundle ({bundle_zip.stat().st_size} bytes) -> {remote_bundle}")
        self.upload_file(bundle_zip, remote_bundle)
        time.sleep(1.0)

        live_log = job_dir / "logs" / "live_terminal.log"
        live_log.parent.mkdir(parents=True, exist_ok=True)
        live_log.touch(exist_ok=True)
        _spawn_terminal_monitor(live_log, safe_id)
        captured_lines: List[str] = []

        def _emit(line: str) -> None:
            print(f"[colab-live] | {line}", flush=True)
            captured_lines.append(line)
            with open(live_log, "a", encoding="utf-8") as lf:
                lf.write(line + "\n")

        script = _build_remote_runner_script(safe_id, self.remote_root, resume_job=bool(self.cfg.resume))
        rc = self.stream_exec(script, timeout=float(self.cfg.colab.timeout_sec), line_callback=_emit)
        if rc != 0 or "__COLAB_STATUS__=OK" not in captured_lines:
            err_msg = f"Colab remote execution failed (exit={rc}); see {live_log}"
            for ln in reversed(captured_lines):
                if ln.startswith("__COLAB_ERROR_JSON__="):
                    try:
                        err_data = json.loads(ln.partition("=")[2])
                        err_msg = f"Colab remote error in {safe_id}: {err_data.get('error')} (see {live_log})"
                    except json.JSONDecodeError:
                        err_msg = f"Colab remote error in {safe_id}: {ln}"
                    break
            save_json_atomic(job_dir / "status.json", {**state, "status": "failed", "error": err_msg})
            raise RuntimeError(err_msg)

        status_local = job_dir / "status.json"
        self.download_file(f"{remote_job_dir}/work/output/{safe_id}/status.json", status_local)
        arch_local = job_dir / "deliverables_bundle.zip"
        self.download_file(f"{remote_job_dir}/deliverables_bundle.zip", arch_local)
        remote_status = json.loads(status_local.read_text(encoding="utf-8"))
        expected_sha = str(remote_status.get("archive_sha256") or "")
        actual_sha = sha256_file(arch_local)
        if expected_sha and actual_sha != expected_sha:
            raise RuntimeError(f"Colab archive SHA-256 mismatch: {actual_sha} != {expected_sha}")
        from datetime import datetime, timezone

        ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        ts_arch = job_dir / f"{safe_id}_deliverables_{ts_tag}.zip"
        shutil.copy2(arch_local, ts_arch)
        remote_status["timestamped_archive"] = str(ts_arch)
        safe_extract_zip(arch_local, job_dir)
        save_json_atomic(status_local, remote_status)
        print("\n" + format_job_status(remote_status))
        return remote_status
