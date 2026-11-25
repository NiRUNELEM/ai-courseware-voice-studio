# -*- coding: utf-8 -*-
"""Self-contained Google Colab bundle packager and remote job packager."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from voice_studio import PROJECT_ROOT

TOOL_CORE_FILES: List[Path] = [
    Path("THIRD_PARTY_LICENSES.md"),
    Path("pyproject.toml"),
    Path("studio.py"),
    Path("requirements.txt"),
    Path("configs/studio.yaml"),
    Path("configs/colab_pro.yaml"),
    Path("configs/mcp_config.example.json"),
    Path("configs/custom_voice.example.yaml"),
    Path("README.md"),
    Path("licenses/GawrGura-RVC_README.md"),
    Path("licenses/GawrGura-RVC_metadata.json"),
    Path("licenses/Applio_LICENSE.txt"),
    Path("licenses/edge-tts_LICENSE.txt"),
    Path("licenses/Whisper_MIT_NOTICE.md"),
    Path("colab/voice_studio_colab.ipynb"),
    Path("input/README.md"),
    Path("input/sample_neutral.json"),
    Path("input/schema/input_script.schema.json"),
    Path("input/templates/script_template.json"),
    Path("input/templates/script_template.md"),
    Path("input/templates/script_template.txt"),
    Path("input/templates/subtitles_template.srt"),
    Path("input/templates/custom_vocab.example.json"),
    Path("src/voice_studio/content/phonetic_vocab.json"),
    Path("assets/fonts/NotoSans-Regular.ttf"),
    Path("assets/fonts/NotoSans-Bold.ttf"),
    Path("assets/fonts/NotoSansThai-Regular.ttf"),
    Path("assets/fonts/NotoSansThai-Bold.ttf"),
]

TOOL_OPTIONAL_FILES: List[Path] = [
    Path("models/manifest.json"),
    Path("models/README.md"),
]


def _collect_include_paths(root_dir: Path, extra_includes: Optional[List[str]] = None) -> List[Path]:
    out: List[Path] = []
    for rel in extra_includes or []:
        p = (root_dir / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
        if not p.is_relative_to(root_dir.resolve()):
            raise ValueError(f"Include path must stay inside workspace: {rel}")
        if p.is_file():
            out.append(p.relative_to(root_dir.resolve()))
        elif p.is_dir():
            for fp in sorted(p.rglob("*")):
                if fp.is_file() and "__pycache__" not in fp.parts and "node_modules" not in fp.parts:
                    out.append(fp.relative_to(root_dir.resolve()))
        else:
            raise FileNotFoundError(f"Include path not found: {rel}")
    return out


def collect_bundle_paths(root_dir: Path = PROJECT_ROOT, include_paths: Optional[List[str]] = None) -> List[Path]:
    """Collects core Voice Studio runtime files plus any optional user-specified input/project paths."""
    files_to_pack: List[Path] = []
    seen = set()
    for rel_path in TOOL_CORE_FILES:
        abs_path = root_dir / rel_path
        if not abs_path.is_file():
            raise FileNotFoundError(f"Missing required core studio file: {rel_path}")
        if rel_path not in seen:
            seen.add(rel_path)
            files_to_pack.append(rel_path)

    for rel_path in TOOL_OPTIONAL_FILES:
        if (root_dir / rel_path).is_file() and rel_path not in seen:
            seen.add(rel_path)
            files_to_pack.append(rel_path)

    src_pkg = root_dir / "src" / "voice_studio"
    for py_file in sorted(src_pkg.rglob("*.py")):
        rel = py_file.relative_to(root_dir)
        if rel not in seen:
            seen.add(rel)
            files_to_pack.append(rel)

    for rel in _collect_include_paths(root_dir, include_paths):
        if rel not in seen:
            seen.add(rel)
            files_to_pack.append(rel)
    return files_to_pack


def build_zip_bundle(output_zip: Path, rel_paths: List[Path], root_dir: Path = PROJECT_ROOT) -> Tuple[int, int, int]:
    """Writes ZIP archive using POSIX paths and verifies ZIP CRC integrity."""
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    raw_bytes = 0
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel_path in rel_paths:
            abs_path = root_dir / rel_path
            raw_bytes += abs_path.stat().st_size
            zf.write(abs_path, arcname=rel_path.as_posix())
    with zipfile.ZipFile(output_zip, "r") as zf:
        corrupt = zf.testzip()
        if corrupt is not None:
            raise RuntimeError(f"ZIP CRC failure on member: {corrupt}")
        member_count = len(zf.namelist())
    return member_count, raw_bytes, output_zip.stat().st_size


def create_remote_job_bundle(
    output_zip: Path,
    job_spec: Dict[str, object],
    root_dir: Path = PROJECT_ROOT,
    extra_paths: List[Path] | None = None,
    staged_files: List[Tuple[Path, str]] | None = None,
) -> Path:
    """Packages minimal tool code, configs, licenses, and input assets for fast Colab upload."""
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    rel_items: List[Path] = []
    for base_rel in [
        Path("pyproject.toml"),
        Path("studio.py"),
        Path("requirements.txt"),
        Path("THIRD_PARTY_LICENSES.md"),
        Path("src/voice_studio/content/phonetic_vocab.json"),
    ]:
        if (root_dir / base_rel).is_file():
            rel_items.append(base_rel)
    for folder in ("src/voice_studio", "configs", "licenses", "assets"):
        p_dir = root_dir / folder
        if p_dir.is_dir():
            for fp in sorted(p_dir.rglob("*")):
                if fp.is_file() and "__pycache__" not in fp.parts:
                    rel_items.append(fp.relative_to(root_dir))
    if extra_paths:
        for ep in extra_paths:
            if ep.is_file():
                rel_items.append(ep.relative_to(root_dir) if ep.is_relative_to(root_dir) else ep)

    seen = set()
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("job_spec.json", json.dumps(job_spec, ensure_ascii=False, indent=2))
        if staged_files:
            for abs_p, arc_name in staged_files:
                clean_arc = arc_name.replace("\\", "/").lstrip("/")
                if clean_arc not in seen and abs_p.is_file():
                    seen.add(clean_arc)
                    zf.write(abs_p, arcname=clean_arc)
        for item in rel_items:
            abs_p = item if item.is_absolute() else (root_dir / item)
            arc = (
                item.relative_to(root_dir).as_posix()
                if item.is_absolute() and item.is_relative_to(root_dir)
                else (item.name if item.is_absolute() else item.as_posix())
            )
            if arc not in seen and abs_p.is_file():
                seen.add(arc)
                zf.write(abs_p, arcname=arc)
    return output_zip


def build_production_bundle(root_dir: Path = PROJECT_ROOT, include_paths: Optional[List[str]] = None) -> int:
    """Builds and CRC-verifies exports/voice_studio_colab_bundle.zip."""
    rel_paths = collect_bundle_paths(root_dir=root_dir, include_paths=include_paths)
    out_zip = root_dir / "exports/voice_studio_colab_bundle.zip"
    member_count, raw_bytes, zip_bytes = build_zip_bundle(out_zip, rel_paths, root_dir=root_dir)
    print(
        f"[BUNDLE OK] Built {out_zip.relative_to(root_dir)} | files={member_count} | "
        f"raw={raw_bytes / (1024 * 1024):.2f} MB | zip={zip_bytes / (1024 * 1024):.2f} MB"
    )
    return 0
