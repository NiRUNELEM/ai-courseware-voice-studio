# -*- coding: utf-8 -*-
"""Security guards: traversal protection, hardened zip extraction, URL scheme checks & constant-time auth."""

from __future__ import annotations

import hashlib
import hmac
import re
import shutil
import stat
import zipfile
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
DEFAULT_MAX_ZIP_BYTES = 16 * 1024 * 1024 * 1024
DEFAULT_MAX_ZIP_FILES = 10000


def validate_safe_id(raw_id: str, label: str = "identifier") -> str:
    """Rejects path separators, null bytes, and parent traversal tokens in IDs."""
    cleaned = (raw_id or "").strip()
    if "\x00" in cleaned or ".." in cleaned or "/" in cleaned or "\\" in cleaned or not SAFE_ID_RE.match(cleaned):
        raise ValueError(f"Unsafe {label}: {raw_id!r}")
    return cleaned


def safe_resolve_under(base_dir: Path, raw_rel_path: str) -> Path:
    """Pre-strip path traversal and sibling-prefix guard (LEARN-001)."""
    if not raw_rel_path or "\x00" in raw_rel_path:
        raise PermissionError("Invalid or null-byte path rejected.")
    normalized_raw = raw_rel_path.replace("\\", "/")
    if ".." in normalized_raw.split("/"):
        raise PermissionError(f"Path traversal rejected: {raw_rel_path!r}")
    stripped = normalized_raw.lstrip("/")
    if not stripped:
        raise PermissionError(f"Empty relative path rejected: {raw_rel_path!r}")
    root_resolved = base_dir.resolve()
    target_resolved = (root_resolved / stripped).resolve()
    try:
        target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PermissionError(f"Path escapes workspace boundary: {raw_rel_path!r}") from exc
    return target_resolved


def safe_extract_zip(
    zip_path: Path,
    target_dir: Path,
    max_total_bytes: int = DEFAULT_MAX_ZIP_BYTES,
    max_files: int = DEFAULT_MAX_ZIP_FILES,
) -> int:
    """Extracts ZIP entries into target_dir while blocking zip-slip, symlinks, and zip bombs."""
    target_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0
    total_written = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        if len(infos) > max_files:
            raise PermissionError(f"ZIP exceeds max file limit ({len(infos)} > {max_files})")
        declared_total = sum(max(0, int(info.file_size)) for info in infos)
        if declared_total > max_total_bytes:
            raise PermissionError(f"ZIP exceeds max uncompressed size ({declared_total} > {max_total_bytes})")

        for info in infos:
            mode = (info.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                raise PermissionError(f"ZIP symlink entry rejected: {info.filename!r}")
            dst_path = safe_resolve_under(target_dir, info.filename)
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src_f, open(dst_path, "wb") as dst_f:
                while True:
                    chunk = src_f.read(1024 * 1024)
                    if not chunk:
                        break
                    total_written += len(chunk)
                    if total_written > max_total_bytes:
                        raise PermissionError(f"ZIP stream exceeded max uncompressed limit ({max_total_bytes})")
                    dst_f.write(chunk)
            extracted += 1
    return extracted


def is_non_loopback_http(url: str) -> bool:
    """Returns True if url uses plaintext http:// to a non-loopback remote host."""
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "http" and host not in ("127.0.0.1", "localhost", "::1", "")


def sha256_file(path: Path) -> str:
    """Computes streaming SHA-256 hex digest for a file."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def collect_checksums(root_dir: Path) -> Dict[str, str]:
    """Builds a relative POSIX path -> SHA-256 checksum map for all files under root_dir."""
    checksums: Dict[str, str] = {}
    if not root_dir.is_dir():
        return checksums
    for fp in sorted(root_dir.rglob("*")):
        if fp.is_file():
            rel = fp.relative_to(root_dir).as_posix()
            checksums[rel] = sha256_file(fp)
    return checksums


def constant_time_token_match(provided: str, expected: str) -> bool:
    """Compares bearer tokens in constant time."""
    if not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def redact_secret(secret: str) -> str:
    """Masks tokens/keys before printing logs."""
    if not secret:
        return "<unset>"
    if len(secret) <= 8:
        return "***"
    return f"{secret[:3]}...{secret[-3:]}"
