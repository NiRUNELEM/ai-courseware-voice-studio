# -*- coding: utf-8 -*-
"""Model inventory and SHA-256 verification against models/manifest.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from voice_studio import PROJECT_ROOT
from voice_studio.core.security import sha256_file


def verify_local_models(
    base_dir: Path = PROJECT_ROOT,
    verify_checksums: bool = True,
) -> Dict[str, Any]:
    """Verifies model weights, Applio runtime assets, and reference waveforms listed in models/manifest.json."""
    eff_base = base_dir
    manifest_path = eff_base / "models" / "manifest.json"
    if not manifest_path.is_file() and (base_dir / "voice-studio" / "models" / "manifest.json").is_file():
        eff_base = base_dir / "voice-studio"
        manifest_path = eff_base / "models" / "manifest.json"
    if not manifest_path.is_file():
        return {"passed": False, "error": f"Missing {manifest_path}", "entries": [], "missing": ["models/manifest.json"]}

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = list(data.get("models") or [])
    results: List[Dict[str, Any]] = []
    missing: List[str] = []
    mismatched: List[str] = []

    for item in entries:
        rel_path = str(item["path"])
        fp = eff_base / rel_path
        exists = fp.is_file()
        size_ok = exists and fp.stat().st_size == int(item.get("size_bytes", -1))
        sha_ok = True
        actual_sha = ""
        if exists and verify_checksums and item.get("sha256"):
            actual_sha = sha256_file(fp)
            sha_ok = actual_sha == str(item["sha256"])
        if not exists:
            missing.append(rel_path)
        elif not size_ok or not sha_ok:
            mismatched.append(rel_path)
        results.append({
            "id": item.get("id", fp.name),
            "voice": item.get("voice", ""),
            "path": rel_path,
            "exists": exists,
            "size_bytes": fp.stat().st_size if exists else 0,
            "expected_bytes": int(item.get("size_bytes", 0)),
            "sha256_ok": sha_ok,
            "actual_sha256": actual_sha,
            "license": item.get("license", ""),
        })

    passed = bool(entries) and not missing and not mismatched
    return {
        "passed": passed,
        "manifest": str(manifest_path),
        "total": len(results),
        "missing": missing,
        "mismatched": mismatched,
        "entries": results,
    }


def format_models_report(report: Dict[str, Any]) -> str:
    """Formats a concise human-readable model verification summary."""
    lines = [
        f"Models Manifest : {report.get('manifest', '-')}",
        f"Status          : {'PASSED (COMPLETE)' if report.get('passed') else 'INCOMPLETE / MISMATCH'}",
    ]
    for e in report.get("entries", []):
        mb = e["size_bytes"] / (1024 * 1024)
        tag = "OK" if (e["exists"] and e["size_bytes"] == e["expected_bytes"] and e["sha256_ok"]) else "MISSING/BAD"
        lines.append(f"  [{tag:<11}] {e['id']:<22} {mb:7.2f} MB | {e['path']} ({e['license']})")
    return "\n".join(lines)
