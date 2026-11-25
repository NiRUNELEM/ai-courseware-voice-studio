# -*- coding: utf-8 -*-
"""Chunked file upload & download helpers over google-colab-cli for bundles > 12 MB."""

from __future__ import annotations

import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

CHUNK_BYTES = 12 * 1024 * 1024  # Stays safely under Jupyter /api/contents proxy limits


def chunked_upload_file(bridge: Any, local_path: Path, remote_path: str) -> None:
    """Uploads any size local file to Colab by splitting >12MB payloads into binary chunks."""
    size = local_path.stat().st_size
    if size <= CHUNK_BYTES:
        proc = bridge._run_cli(["upload", "-s", bridge.session_name, str(local_path), remote_path], timeout=300.0)
        if proc.returncode != 0:
            raise RuntimeError(f"colab upload failed ({remote_path}): {(proc.stderr or proc.stdout).strip()}")
        return

    parts_remote: List[str] = []
    with tempfile.TemporaryDirectory(prefix="colab_up_") as tmp:
        with open(local_path, "rb") as src_f:
            idx = 0
            while True:
                buf = src_f.read(CHUNK_BYTES)
                if not buf:
                    break
                part_local = Path(tmp) / f"part_{idx:03d}.bin"
                part_local.write_bytes(buf)
                part_remote = f"{remote_path}.part_{idx:03d}"
                proc = bridge._run_cli(["upload", "-s", bridge.session_name, str(part_local), part_remote], timeout=300.0)
                if proc.returncode != 0:
                    raise RuntimeError(f"colab chunk upload failed ({part_remote}): {(proc.stderr or proc.stdout).strip()}")
                parts_remote.append(part_remote)
                idx += 1

    stitch_code = (
        "import pathlib, shutil\n"
        f"dst = pathlib.Path({remote_path!r})\n"
        "dst.parent.mkdir(parents=True, exist_ok=True)\n"
        "with open(dst, 'wb') as out_f:\n"
        f"    for rp in {parts_remote!r}:\n"
        "        pp = pathlib.Path(rp)\n"
        "        with open(pp, 'rb') as pf:\n"
        "            shutil.copyfileobj(pf, out_f)\n"
        "        pp.unlink()\n"
        f"assert dst.stat().st_size == {size}, f'Size mismatch {{dst.stat().st_size}} != {size}'\n"
        "print(f'[colab-cli] Reassembled remote upload: {dst} ({dst.stat().st_size} bytes)')\n"
    )
    rc = bridge.stream_exec(stitch_code, timeout=180.0)
    if rc != 0:
        raise RuntimeError(f"Remote chunk reassembly failed for {remote_path} (exit={rc})")


def chunked_download_file(bridge: Any, remote_path: str, local_path: Path) -> None:
    """Downloads any size remote file from Colab cleanly using <=12MB chunks when needed."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    probe_code = (
        "import pathlib, shutil\n"
        f"src = pathlib.Path({remote_path!r})\n"
        "sz = src.stat().st_size\n"
        "print(f'__RSIZE__={sz}')\n"
        f"if sz > {CHUNK_BYTES}:\n"
        "    with open(src, 'rb') as f:\n"
        "        idx = 0\n"
        "        while True:\n"
        f"            buf = f.read({CHUNK_BYTES})\n"
        "            if not buf: break\n"
        "            pp = pathlib.Path(f'{src}.dl_{idx:03d}')\n"
        "            pp.write_bytes(buf)\n"
        "            print(f'__RPART__={pp}')\n"
        "            idx += 1\n"
    )
    rc = bridge.stream_exec(probe_code, timeout=300.0, line_callback=lines.append)
    if rc != 0:
        raise RuntimeError(f"Remote download prepare failed ({remote_path})")

    remote_parts = [ln.partition("=")[2].strip() for ln in lines if ln.startswith("__RPART__=")]
    if not remote_parts:
        proc = bridge._run_cli(["download", "-s", bridge.session_name, remote_path, str(local_path)], timeout=300.0)
        if proc.returncode != 0:
            raise RuntimeError(f"colab download failed ({remote_path}): {(proc.stderr or proc.stdout).strip()}")
        return

    with tempfile.TemporaryDirectory(prefix="colab_dn_") as tmp:
        with open(local_path, "wb") as out_f:
            for idx, rpart in enumerate(remote_parts):
                p_loc = Path(tmp) / f"dl_{idx:03d}.bin"
                proc = None
                for attempt in range(3):
                    proc = bridge._run_cli(["download", "-s", bridge.session_name, rpart, str(p_loc)], timeout=300.0)
                    if proc.returncode == 0:
                        break
                    time.sleep(2.0 * (attempt + 1))
                if proc is None or proc.returncode != 0:
                    raise RuntimeError(f"colab chunk download failed ({rpart}): {((proc.stderr or proc.stdout) if proc else '').strip()}")
                out_f.write(p_loc.read_bytes())

    bridge.stream_exec(
        f"import pathlib\nfor p in {remote_parts!r}:\n    pathlib.Path(p).unlink(missing_ok=True)\n",
        timeout=60.0,
    )


def stream_popen_with_sentinel(
    proc: Any,
    line_callback: Optional[Callable[[str], None]] = None,
) -> int:
    """Streams subprocess stdout and unwinds hung WebSockets shortly after completion sentinels."""
    saw_sentinel = False
    sentinel_rc = 0
    timer: Optional[threading.Timer] = None

    def _force_close() -> None:
        if proc.poll() is None:
            proc.terminate()

    try:
        for raw_ln in proc.stdout:
            line = raw_ln.rstrip("\r\n")
            if line_callback is not None:
                line_callback(line)
            else:
                print(f"[colab-live] | {line}", flush=True)
            if "Connection was lost." in line:
                proc.terminate()
                return sentinel_rc if saw_sentinel else 1
            if line == "__COLAB_STATUS__=OK":
                saw_sentinel = True
                sentinel_rc = 0
            elif line.startswith("__CLI_RC__="):
                saw_sentinel = True
                raw_rc = line.partition("=")[2].strip()
                sentinel_rc = int(raw_rc) if raw_rc.lstrip("-").isdigit() else 0
            if saw_sentinel and timer is None:
                timer = threading.Timer(5.0, _force_close)
                timer.daemon = True
                timer.start()
        rc = int(proc.wait(timeout=60.0))
        return sentinel_rc if saw_sentinel else rc
    finally:
        if timer is not None:
            timer.cancel()


def run_colab_exec_with_retry(
    colab_bin: str,
    session_name: str,
    code: str,
    timeout: float = 3600.0,
    line_callback: Optional[Callable[[str], None]] = None,
    max_attempts: int = 4,
) -> int:
    """Runs `colab exec` with automatic reconnect backoff on transient Colab WebSocket drops."""
    last_rc = 1
    for attempt in range(max_attempts):
        seen_error_json = False
        saw_ws_drop = False

        def _hook(ln: str) -> None:
            nonlocal seen_error_json, saw_ws_drop
            if ln.startswith("__COLAB_ERROR_JSON__="):
                seen_error_json = True
            if "Connection was lost." in ln or ("Session '" in ln and "appears to be lost" in ln):
                saw_ws_drop = True
            if line_callback is not None:
                line_callback(ln)
            else:
                print(f"[colab-live] | {ln}", flush=True)

        proc = subprocess.Popen(
            [colab_bin, "exec", "-s", session_name, "--timeout", str(int(timeout))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(code)
        proc.stdin.close()
        last_rc = stream_popen_with_sentinel(proc, line_callback=_hook)
        if last_rc == 0 or seen_error_json:
            return last_rc
        if attempt + 1 < max_attempts and (saw_ws_drop or last_rc != 0):
            delay = 3.0 * (attempt + 1)
            print(
                f"[colab-cli][RETRY] Transient Colab tunnel drop (exit={last_rc}, attempt {attempt + 1}/{max_attempts}); "
                f"reconnecting in {delay:.1f}s...",
                flush=True,
            )
            time.sleep(delay)
            continue
        break
    return last_rc


_GPU_FALLBACK_CHAIN: Dict[str, tuple[str, ...]] = {
    "L4": ("T4", "A100"),
    "A100": ("L4", "T4"),
    "T4": ("L4",),
}


def _is_capacity_503(err_text: str) -> bool:
    low = (err_text or "").lower()
    return any(
        k in low
        for k in ("service unavailable", "503", "cannot currently connect to a gpu", "usage limits")
    )


def _replace_gpu_flag(cmd: List[str], new_gpu: str) -> List[str]:
    out = list(cmd)
    for idx, tok in enumerate(out[:-1]):
        if tok == "--gpu":
            out[idx + 1] = new_gpu
            return out
    return out + ["--gpu", new_gpu]


def provision_colab_session(
    bridge: Any,
    cmd: List[str],
    desired_hw: str,
    high_ram: bool,
    timeout: float = 420.0,
    poll_sleep_sec: float = 5.0,
) -> Dict[str, str]:
    """Provisions a Colab CLI runtime with timeout-recovery polling and automatic GPU fallback on 503."""
    proc: Optional[subprocess.CompletedProcess[str]] = None
    active_hw = desired_hw
    try:
        proc = bridge._run_cli(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(
            f"[colab-cli][WARN] Provisioning command exceeded {timeout:.0f}s; "
            "polling server sessions for recovery...",
            flush=True,
        )
        for idx in range(3):
            recovered = next(
                (
                    r
                    for r in bridge.list_sessions()
                    if r.get("name") == bridge.session_name and bridge._matches_config(r, desired_hw, high_ram)
                ),
                None,
            )
            if recovered is not None:
                print(
                    f"[colab-cli] Recovered provisioned session '{bridge.session_name}' "
                    f"({recovered.get('hardware')} / {recovered.get('shape')}).",
                    flush=True,
                )
                return recovered
            if idx < 2 and poll_sleep_sec > 0:
                time.sleep(poll_sleep_sec)
        try:
            proc = bridge._run_cli(cmd, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"colab {' '.join(cmd)} timed out after {timeout:.0f}s") from exc

    assert proc is not None
    if proc.returncode != 0:
        existing = next(
            (
                r
                for r in bridge.list_sessions()
                if r.get("name") == bridge.session_name and bridge._matches_config(r, desired_hw, high_ram)
            ),
            None,
        )
        if existing is not None:
            return existing
        err_msg = (proc.stderr or proc.stdout).strip()
        if _is_capacity_503(err_msg):
            for fb_hw in _GPU_FALLBACK_CHAIN.get(desired_hw.upper(), ()):
                fb_cmd = _replace_gpu_flag(cmd, fb_hw)
                print(
                    f"[colab-cli][WARN] GPU {desired_hw} unavailable (503); "
                    f"automatically falling back to --gpu {fb_hw}...",
                    flush=True,
                )
                fb_proc = bridge._run_cli(fb_cmd, timeout=timeout)
                if fb_proc.returncode == 0:
                    proc = fb_proc
                    active_hw = fb_hw
                    break
                err_msg = (fb_proc.stderr or fb_proc.stdout).strip()
        if proc.returncode != 0:
            raise RuntimeError(f"colab {' '.join(cmd)} failed: {err_msg}")

    for ln in (proc.stdout or "").splitlines():
        if ln.strip():
            print(f"[colab-cli] {ln.strip()}")
    active_after = bridge.list_sessions()
    return next(
        (r for r in active_after if r.get("name") == bridge.session_name),
        {"name": bridge.session_name, "hardware": active_hw, "shape": "HIGH_RAM" if high_ram else "STANDARD"},
    )
