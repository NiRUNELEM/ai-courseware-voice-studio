# -*- coding: utf-8 -*-
"""Google Drive OAuth2 Sign-In Bridge and Mounted Drive Sync Bridge for Colab Execution."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from voice_studio.core.security import safe_extract_zip, safe_resolve_under, sha256_file, validate_safe_id
from voice_studio.core.state import save_json_atomic

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def authenticate_google_drive(client_secrets_path: Path, token_path: Path) -> Any:
    """Runs or refreshes Google OAuth2 Desktop Sign-In and stores tokens with strict permissions."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Google Drive OAuth2 libraries are not installed. Install requirements.txt: "
            "pip install -r requirements.txt"
        ) from exc

    creds = None
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), DRIVE_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not client_secrets_path.is_file():
                raise FileNotFoundError(
                    f"Missing Google OAuth client secrets file: {client_secrets_path}. "
                    "Download OAuth Desktop Client JSON from Google Cloud Console and save it there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_path), DRIVE_SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(token_path.parent, 0o700)
            os.chmod(token_path, 0o600)
        except OSError as exc:
            print(f"[colab][security] Unable to tighten token permissions on {token_path}: {exc}")
        token_path.write_text(creds.to_json(), encoding="utf-8")
        try:
            os.chmod(token_path, 0o600)
        except OSError as exc:
            print(f"[colab][security] Unable to chmod 0600 on {token_path}: {exc}")
    return build("drive", "v3", credentials=creds)


class LocalDriveSyncBridge:
    """Shared-folder Drive bridge (for mounted Google Drive, rclone mount, or local testing)."""

    def __init__(self, mount_dir: Path, root_folder_name: str = "voice_studio_jobs") -> None:
        self.base = Path(mount_dir) / root_folder_name
        self.base.mkdir(parents=True, exist_ok=True)

    def submit_job(self, job_id: str, bundle_zip: Path, job_spec: Dict[str, Any]) -> Dict[str, Any]:
        safe_id = validate_safe_id(job_id, label="job_id")
        job_root = self.base / safe_id
        in_dir = job_root / "input"
        out_dir = job_root / "output"
        in_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundle_zip, in_dir / "bundle.zip")
        save_json_atomic(in_dir / "job_spec.json", job_spec)
        status = {"job_id": safe_id, "status": "queued_drive", "progress_pct": 0.0, "steps": {}, "error": None}
        save_json_atomic(job_root / "status.json", status)
        return status

    def read_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        safe_id = validate_safe_id(job_id, label="job_id")
        st_path = self.base / safe_id / "status.json"
        if not st_path.is_file():
            return None
        return json.loads(st_path.read_text(encoding="utf-8"))

    def download_outputs(self, job_id: str, local_job_dir: Path) -> List[Path]:
        safe_id = validate_safe_id(job_id, label="job_id")
        remote_out = self.base / safe_id / "output"
        if not remote_out.is_dir():
            raise FileNotFoundError(f"Remote Drive output directory not found: {remote_out}")
        downloaded: List[Path] = []
        for fp in sorted(remote_out.rglob("*")):
            if not fp.is_file():
                continue
            rel = fp.relative_to(remote_out)
            dst = safe_resolve_under(local_job_dir, rel.as_posix())
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(fp, dst)
            downloaded.append(dst)
        arch = local_job_dir / "deliverables_bundle.zip"
        if arch.is_file():
            safe_extract_zip(arch, local_job_dir)
        return downloaded

    def read_logs(self, job_id: str, offset: int = 0) -> Dict[str, Any]:
        safe_id = validate_safe_id(job_id, label="job_id")
        log_path = self.base / safe_id / "output" / "live_terminal.log"
        if not log_path.is_file():
            return {"job_id": safe_id, "offset": 0, "chunk": ""}
        raw = log_path.read_bytes()
        safe_off = max(0, min(int(offset), len(raw)))
        return {"job_id": safe_id, "offset": len(raw), "chunk": raw[safe_off:].decode("utf-8", errors="replace")}


class DriveApiBridge:
    """Google Drive REST v3 bridge powered by OAuth2 sign-in."""

    def __init__(self, service: Any, root_folder_name: str = "voice_studio_jobs") -> None:
        self.service = service
        self.root_folder_name = root_folder_name
        self._folder_cache: Dict[tuple[str, Optional[str]], str] = {}

    @staticmethod
    def _escape_drive_q(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def _ensure_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        cache_key = (name, parent_id)
        if cache_key in self._folder_cache:
            return self._folder_cache[cache_key]
        esc_name = self._escape_drive_q(name)
        q = f"name='{esc_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            q += f" and '{self._escape_drive_q(parent_id)}' in parents"
        res = self.service.files().list(q=q, spaces="drive", fields="files(id, name)").execute()
        files = res.get("files", [])
        if files:
            fid = str(files[0]["id"])
            self._folder_cache[cache_key] = fid
            return fid
        meta: Dict[str, Any] = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            meta["parents"] = [parent_id]
        created = self.service.files().create(body=meta, fields="id").execute()
        fid = str(created["id"])
        self._folder_cache[cache_key] = fid
        return fid

    def _upload_or_replace(self, local_path: Path, folder_id: str, remote_name: str) -> str:
        from googleapiclient.http import MediaFileUpload

        esc_name = self._escape_drive_q(remote_name)
        esc_parent = self._escape_drive_q(folder_id)
        q = f"name='{esc_name}' and '{esc_parent}' in parents and trashed=false"
        res = self.service.files().list(q=q, spaces="drive", fields="files(id)").execute()
        media = MediaFileUpload(str(local_path), resumable=True)
        existing = res.get("files", [])
        if existing:
            fid = str(existing[0]["id"])
            self.service.files().update(fileId=fid, media_body=media).execute()
            return fid
        meta = {"name": remote_name, "parents": [folder_id]}
        created = self.service.files().create(body=meta, media_body=media, fields="id").execute()
        return str(created["id"])

    def submit_job(self, job_id: str, bundle_zip: Path, job_spec: Dict[str, Any], tmp_dir: Path) -> Dict[str, Any]:
        safe_id = validate_safe_id(job_id, label="job_id")
        root_id = self._ensure_folder(self.root_folder_name)
        job_fid = self._ensure_folder(safe_id, parent_id=root_id)
        in_fid = self._ensure_folder("input", parent_id=job_fid)
        self._ensure_folder("output", parent_id=job_fid)

        spec_tmp = tmp_dir / "job_spec.json"
        save_json_atomic(spec_tmp, job_spec)
        status = {"job_id": safe_id, "status": "queued_drive", "progress_pct": 0.0, "steps": {}, "error": None}
        status_tmp = tmp_dir / "status.json"
        save_json_atomic(status_tmp, status)

        self._upload_or_replace(bundle_zip, in_fid, "bundle.zip")
        self._upload_or_replace(spec_tmp, in_fid, "job_spec.json")
        self._upload_or_replace(status_tmp, job_fid, "status.json")
        return status

    def read_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        safe_id = validate_safe_id(job_id, label="job_id")
        root_id = self._ensure_folder(self.root_folder_name)
        job_fid = self._ensure_folder(safe_id, parent_id=root_id)
        q = f"name='status.json' and '{job_fid}' in parents and trashed=false"
        res = self.service.files().list(q=q, spaces="drive", fields="files(id)").execute()
        files = res.get("files", [])
        if not files:
            return None
        content = self.service.files().get_media(fileId=files[0]["id"]).execute()
        return json.loads(content.decode("utf-8"))

    def download_outputs(self, job_id: str, local_job_dir: Path) -> List[Path]:
        from googleapiclient.http import MediaIoBaseDownload

        safe_id = validate_safe_id(job_id, label="job_id")
        root_id = self._ensure_folder(self.root_folder_name)
        job_fid = self._ensure_folder(safe_id, parent_id=root_id)
        out_fid = self._ensure_folder("output", parent_id=job_fid)
        q = f"'{out_fid}' in parents and trashed=false"
        res = self.service.files().list(q=q, spaces="drive", fields="files(id, name, mimeType)").execute()
        downloaded: List[Path] = []
        for fmeta in res.get("files", []):
            if fmeta.get("mimeType") == "application/vnd.google-apps.folder":
                continue
            dst = safe_resolve_under(local_job_dir, str(fmeta["name"]))
            dst.parent.mkdir(parents=True, exist_ok=True)
            req = self.service.files().get_media(fileId=fmeta["id"])
            with open(dst, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, req)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
            downloaded.append(dst)
        arch = local_job_dir / "deliverables_bundle.zip"
        if arch.is_file():
            safe_extract_zip(arch, local_job_dir)
        return downloaded

    def read_logs(self, job_id: str, offset: int = 0) -> Dict[str, Any]:
        safe_id = validate_safe_id(job_id, label="job_id")
        return {"job_id": safe_id, "offset": int(offset), "chunk": ""}


def _process_drive_jobs_once(jobs_root: Path) -> List[str]:
    from voice_studio.config.loader import load_pipeline_config
    from voice_studio.pipeline.orchestrator import PipelineOrchestrator

    processed: List[str] = []
    for job_dir_remote in sorted(jobs_root.iterdir()):
        status_file = job_dir_remote / "status.json"
        in_bundle = job_dir_remote / "input" / "bundle.zip"
        if not (job_dir_remote.is_dir() and status_file.is_file() and in_bundle.is_file()):
            continue
        st = json.loads(status_file.read_text(encoding="utf-8"))
        if st.get("status") not in ("queued_drive", "queued"):
            continue

        job_id = validate_safe_id(str(st.get("job_id") or job_dir_remote.name), label="job_id")
        local_ws = Path("/tmp/voice_studio_drive_work") / job_id
        if local_ws.exists():
            shutil.rmtree(local_ws)
        local_ws.mkdir(parents=True, exist_ok=True)
        safe_extract_zip(in_bundle, local_ws)
        spec_in = job_dir_remote / "input" / "job_spec.json"
        if spec_in.is_file():
            shutil.copy2(spec_in, local_ws / "job_spec.json")

        overrides = json.loads((local_ws / "job_spec.json").read_text(encoding="utf-8"))
        overrides.setdefault("runtime", {})["target"] = "local"
        overrides.setdefault("job", {})["id"] = job_id
        overrides.setdefault("job", {})["output_dir"] = "output"
        overrides.setdefault("output", {})["dir"] = "output"
        save_json_atomic(status_file, {**st, "status": "running", "progress_pct": 5.0})

        try:
            cfg = load_pipeline_config(overrides=overrides, base_dir=local_ws)
            remote_out = job_dir_remote / "output"
            remote_out.mkdir(parents=True, exist_ok=True)
            final_state = PipelineOrchestrator(cfg, base_dir=local_ws, status_mirror_path=status_file).run()
            live_log = local_ws / "output" / job_id / "logs" / "live_terminal.log"
            if live_log.is_file():
                shutil.copy2(live_log, remote_out / "live_terminal.log")
        except Exception as exc:
            save_json_atomic(status_file, {**st, "status": "failed", "error": str(exc)})
            print(f"[colab-drive-worker] Job {job_id} failed: {exc}")
            continue

        local_out = local_ws / "output" / job_id
        remote_out = job_dir_remote / "output"
        remote_out.mkdir(parents=True, exist_ok=True)
        import zipfile

        arch = remote_out / "deliverables_bundle.zip"
        with zipfile.ZipFile(arch, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for fp in sorted(local_out.rglob("*")):
                if fp.is_file() and fp.suffix != ".tmp":
                    zf.write(fp, arcname=fp.relative_to(local_out).as_posix())
        final_state["archive_sha256"] = sha256_file(arch)
        save_json_atomic(status_file, final_state)
        processed.append(job_id)
    return processed


def run_colab_drive_worker(
    drive_mount_dir: Path,
    root_folder_name: str = "voice_studio_jobs",
    watch: bool = False,
    poll_interval_sec: float = 5.0,
    idle_timeout_sec: float = 3600.0,
) -> List[str]:
    """Colab-side worker: picks up queued Drive jobs, streams progress, and exports verified deliverables."""
    jobs_root = Path(drive_mount_dir) / root_folder_name
    if not watch:
        return _process_drive_jobs_once(jobs_root) if jobs_root.is_dir() else []

    processed_all: List[str] = []
    last_active = time.monotonic()
    print(f"[colab-drive-worker] Watching {jobs_root} (poll={poll_interval_sec}s, idle_timeout={idle_timeout_sec}s)...")
    while True:
        batch = _process_drive_jobs_once(jobs_root) if jobs_root.is_dir() else []
        if batch:
            processed_all.extend(batch)
            last_active = time.monotonic()
        elif (time.monotonic() - last_active) >= idle_timeout_sec:
            print("[colab-drive-worker] Idle timeout reached; exiting cleanly.")
            break
        time.sleep(max(0.5, float(poll_interval_sec)))
    return processed_all
