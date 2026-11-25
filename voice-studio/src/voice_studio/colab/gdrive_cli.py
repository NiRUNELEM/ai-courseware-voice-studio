# -*- coding: utf-8 -*-
"""Google Drive bridge powered by Antigravity's signed-in gdrive CLI plugin."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from voice_studio.core.security import safe_extract_zip, safe_resolve_under, validate_safe_id
from voice_studio.core.state import save_json_atomic


def resolve_gdrive_cli_binary() -> str:
    """Returns gdrive plugin CLI executable path from PATH, AGY plugin sandbox, or env."""
    import os

    cand = os.environ.get("GDRIVE_CLI_BINARY") or shutil.which("gdrive") or ""
    if cand and Path(cand).is_file():
        return str(Path(cand).resolve())
    for p in sorted(Path("/tmp").glob("agy-plugin-cli-*/gdrive"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_file():
            return str(p)
    return ""


class GdriveCliBridge:
    """Google Drive bridge backed by Antigravity's signed-in gdrive plugin CLI."""

    def __init__(self, root_folder_name: str = "VoiceStudioColab", gdrive_bin: str = "") -> None:
        self.root_folder_name = root_folder_name
        self.gdrive_bin = gdrive_bin or resolve_gdrive_cli_binary()
        if not self.gdrive_bin:
            raise RuntimeError("gdrive CLI executable is not on PATH; sign in to Google Drive plugin or configure OAuth.")
        self._folder_cache: Dict[tuple[str, Optional[str]], str] = {}

    def _run(self, args: List[str], timeout: float = 90.0) -> str:
        proc = subprocess.run([self.gdrive_bin, *args], capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"gdrive {' '.join(args[:2])} failed: {msg}")
        return proc.stdout

    def _list_folder(self, parent_id: Optional[str] = None, max_items: int = 100) -> List[Dict[str, Any]]:
        args = ["readonly", "ls"]
        if parent_id:
            args.append(parent_id)
        args.extend(["--max", str(max_items), "--json"])
        raw = self._run(args).strip()
        if not raw:
            return []
        data = json.loads(raw)
        return list(data) if isinstance(data, list) else list(data.get("files") or [])

    def _find_child(self, name: str, parent_id: Optional[str] = None, folder_only: bool = False) -> Optional[Dict[str, Any]]:
        if parent_id:
            children = self._list_folder(parent_id=parent_id, max_items=200)
            matches = [
                it
                for it in children
                if str(it.get("name")) == name
                and (not folder_only or it.get("mimeType") == "application/vnd.google-apps.folder")
            ]
            if matches:
                matches.sort(key=lambda it: str(it.get("createdTime") or it.get("modifiedTime") or ""))
                return matches[0]
        args = ["readonly", "search", "--name-exact", name, "--max", "50", "--json"]
        if parent_id:
            args.extend(["--parent-id", parent_id])
        if folder_only:
            args.extend(["--type", "folder"])
        raw = self._run(args).strip()
        items = json.loads(raw) if raw else []
        matches = [it for it in (items if isinstance(items, list) else []) if str(it.get("name")) == name]
        matches.sort(key=lambda it: str(it.get("createdTime") or it.get("modifiedTime") or ""))
        return matches[0] if matches else None

    def _ensure_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        key = (name, parent_id)
        if key in self._folder_cache:
            return self._folder_cache[key]
        existing = self._find_child(name, parent_id=parent_id, folder_only=True)
        if existing:
            fid = str(existing["id"])
            self._folder_cache[key] = fid
            return fid
        args = ["mutate", "mkdir", name]
        if parent_id:
            args.extend(["--parent", parent_id])
        out = self._run(args).strip()
        fid = ""
        try:
            fid = str(json.loads(out)["id"])
        except (ValueError, KeyError, TypeError):
            m = re.search(r"ID:\s*([A-Za-z0-9_-]+)", out) or re.search(r"\b([A-Za-z0-9_-]{25,})\b", out)
            if m:
                fid = m.group(1)
        if not fid:
            found_after = self._find_child(name, parent_id=parent_id, folder_only=True)
            if not found_after:
                raise RuntimeError(f"Unable to resolve created Drive folder ID for {name!r}: {out}")
            fid = str(found_after["id"])
        self._folder_cache[key] = fid
        return fid

    def _upload_or_replace(self, local_path: Path, folder_id: str, remote_name: str) -> str:
        stage_file = local_path
        tmp_dir: Optional[Path] = None
        if local_path.name != remote_name:
            tmp_dir = Path(tempfile.mkdtemp(prefix="gdrive_stage_"))
            stage_file = tmp_dir / remote_name
            shutil.copy2(local_path, stage_file)
        try:
            existing = self._find_child(remote_name, parent_id=folder_id)
            if existing:
                fid = str(existing["id"])
                self._run(["mutate", "update", fid, str(stage_file)], timeout=300.0)
                return fid
            out = self._run(["mutate", "upload", str(stage_file), "--parent", folder_id], timeout=300.0).strip()
            try:
                return str(json.loads(out)["id"])
            except (ValueError, KeyError, TypeError):
                found_after = self._find_child(remote_name, parent_id=folder_id)
                if found_after:
                    return str(found_after["id"])
                m = re.search(r"ID:\s*([A-Za-z0-9_-]+)", out) or re.search(r"\b([A-Za-z0-9_-]{25,})\b", out)
                if m:
                    return m.group(1)
                raise RuntimeError(f"Unable to parse uploaded file ID from: {out}")
        finally:
            if tmp_dir and tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _job_folders(self, job_id: str) -> tuple[str, str, str]:
        safe_id = validate_safe_id(job_id, label="job_id")
        root_id = self._ensure_folder(self.root_folder_name)
        job_fid = self._ensure_folder(safe_id, parent_id=root_id)
        in_fid = self._ensure_folder("input", parent_id=job_fid)
        out_fid = self._ensure_folder("output", parent_id=job_fid)
        return job_fid, in_fid, out_fid

    def submit_job(self, job_id: str, bundle_zip: Path, job_spec: Dict[str, Any], tmp_dir: Path) -> Dict[str, Any]:
        safe_id = validate_safe_id(job_id, label="job_id")
        job_fid, in_fid, _ = self._job_folders(safe_id)
        spec_tmp = tmp_dir / "job_spec.json"
        save_json_atomic(spec_tmp, job_spec)
        status = {"job_id": safe_id, "status": "queued_drive", "progress_pct": 0.0, "steps": {}, "error": None}
        status_tmp = tmp_dir / "status.json"
        save_json_atomic(status_tmp, status)
        self._upload_or_replace(bundle_zip, in_fid, "bundle.zip")
        self._upload_or_replace(spec_tmp, in_fid, "job_spec.json")
        self._upload_or_replace(status_tmp, job_fid, "status.json")
        return status

    def _download_child_bytes(self, name: str, parent_id: str) -> Optional[bytes]:
        item = self._find_child(name, parent_id=parent_id)
        if not item:
            return None
        with tempfile.TemporaryDirectory() as tmp:
            dst = Path(tmp) / name
            self._run(["readonly", "download", str(item["id"]), "--out", str(dst)], timeout=300.0)
            return dst.read_bytes()

    def read_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        safe_id = validate_safe_id(job_id, label="job_id")
        root_id = self._ensure_folder(self.root_folder_name)
        job_item = self._find_child(safe_id, parent_id=root_id, folder_only=True)
        if not job_item:
            return None
        raw = self._download_child_bytes("status.json", str(job_item["id"]))
        return json.loads(raw.decode("utf-8")) if raw else None

    def read_logs(self, job_id: str, offset: int = 0) -> Dict[str, Any]:
        safe_id = validate_safe_id(job_id, label="job_id")
        root_id = self._ensure_folder(self.root_folder_name)
        job_item = self._find_child(safe_id, parent_id=root_id, folder_only=True)
        if not job_item:
            return {"job_id": safe_id, "offset": 0, "chunk": ""}
        out_item = self._find_child("output", parent_id=str(job_item["id"]), folder_only=True)
        raw = self._download_child_bytes("live_terminal.log", str(out_item["id"])) if out_item else None
        if not raw:
            return {"job_id": safe_id, "offset": 0, "chunk": ""}
        safe_off = max(0, min(int(offset), len(raw)))
        return {"job_id": safe_id, "offset": len(raw), "chunk": raw[safe_off:].decode("utf-8", errors="replace")}

    def download_outputs(self, job_id: str, local_job_dir: Path) -> List[Path]:
        _, _, out_fid = self._job_folders(job_id)
        downloaded: List[Path] = []
        for fmeta in self._list_folder(parent_id=out_fid):
            if fmeta.get("mimeType") == "application/vnd.google-apps.folder":
                continue
            dst = safe_resolve_under(local_job_dir, str(fmeta["name"]))
            dst.parent.mkdir(parents=True, exist_ok=True)
            self._run(["readonly", "download", str(fmeta["id"]), "--out", str(dst)], timeout=300.0)
            downloaded.append(dst)
        arch = local_job_dir / "deliverables_bundle.zip"
        if arch.is_file():
            safe_extract_zip(arch, local_job_dir)
        return downloaded

    def resolve_folder_id(self, folder_name_or_id: str) -> str:
        """Resolves a Drive folder ID directly or finds/creates a named subfolder under studio root."""
        spec = (folder_name_or_id or "").strip()
        if not spec or spec == self.root_folder_name:
            return self._ensure_folder(self.root_folder_name)
        if "/" in spec:
            cur_id: Optional[str] = None
            for part in [p.strip() for p in spec.split("/") if p.strip()]:
                cur_id = self._ensure_folder(part, parent_id=cur_id)
            assert cur_id is not None
            return cur_id
        if re.fullmatch(r"[A-Za-z0-9_-]{18,}", spec):
            return spec
        root_id = self._ensure_folder(self.root_folder_name)
        return self._ensure_folder(spec, parent_id=root_id)

    def replace_folder_files(
        self,
        folder_name_or_id: str,
        file_paths: List[Path],
        trash_prefixes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Uploads/updates target files into a Drive folder and moves obsolete prefixed files to trash."""
        folder_id = self.resolve_folder_id(folder_name_or_id)
        existing = self._list_folder(parent_id=folder_id, max_items=200)
        new_names = {p.name for p in file_paths if p.is_file()}
        trashed: List[str] = []
        if trash_prefixes:
            for item in existing:
                name = str(item.get("name") or "")
                if name not in new_names and any(name.startswith(pref) for pref in trash_prefixes):
                    self._run(["mutate", "trash", str(item["id"])], timeout=60.0)
                    trashed.append(name)
        uploaded: Dict[str, str] = {}
        for fp in file_paths:
            if fp.is_file():
                uploaded[fp.name] = self._upload_or_replace(fp, folder_id, fp.name)
        return {"folder_id": folder_id, "uploaded": uploaded, "trashed": trashed}

    def clean_studio_folder(self, keep_names: Optional[List[str]] = None) -> List[str]:
        """Moves stale temporary job folders/archives inside root_folder_name to Drive trash."""
        root_id = self._ensure_folder(self.root_folder_name)
        keep = set(keep_names or ("voice_studio_colab.ipynb", "voice_studio_colab_bundle.zip"))
        trashed: List[str] = []
        for item in self._list_folder(parent_id=root_id, max_items=200):
            name = str(item.get("name") or "")
            if name and name not in keep:
                self._run(["mutate", "trash", str(item["id"])], timeout=60.0)
                trashed.append(name)
        return trashed
