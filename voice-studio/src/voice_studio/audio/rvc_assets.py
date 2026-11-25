# -*- coding: utf-8 -*-
"""RVC v2 model asset resolution, Hugging Face download, ZIP extraction, and Applio/Torch runtime setup."""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from voice_studio.core.runtime import resolve_rvc_assets_base
from voice_studio.core.security import safe_resolve_under

DEFAULT_APPLIO_REPO = "https://github.com/IAHispano/Applio.git"
APPLIO_RESOURCES_BASE = "https://huggingface.co/IAHispano/Applio/resolve/main/Resources"
APPLIO_REQUIRED_ASSETS = (
    ("predictors/rmvpe.pt", "rvc/models/predictors/rmvpe.pt"),
    ("embedders/contentvec/pytorch_model.bin", "rvc/models/embedders/contentvec/pytorch_model.bin"),
    ("embedders/contentvec/config.json", "rvc/models/embedders/contentvec/config.json"),
)


def default_rvc_dirs(subdir_name: str = "gawr_gura", assets_root: str = "") -> Tuple[str, str]:
    """Resolves local model cache and Applio checkout directories without polluting repo root."""
    base_dir = resolve_rvc_assets_base(assets_root)
    clean_sub = (subdir_name or "custom_rvc").strip().replace("\\", "/").strip("/")
    if not clean_sub or ".." in clean_sub:
        clean_sub = "custom_rvc"
    return os.path.join(base_dir, clean_sub), os.path.join(base_dir, "Applio")


def ensure_applio_repo(applio_dir: str) -> str:
    """Clones Applio shallowly if not already present on disk and ensures RMVPE + ContentVec weights exist."""
    import urllib.request

    os.makedirs(os.path.dirname(os.path.abspath(applio_dir)), exist_ok=True)
    if not os.path.exists(os.path.join(applio_dir, "rvc", "infer", "infer.py")):
        subprocess.run(["git", "clone", "--depth", "1", DEFAULT_APPLIO_REPO, applio_dir], check=True)
    for remote_rel, local_rel in APPLIO_REQUIRED_ASSETS:
        dst = os.path.join(applio_dir, local_rel)
        if not (os.path.isfile(dst) and os.path.getsize(dst) > 0):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            url = f"{APPLIO_RESOURCES_BASE}/{remote_rel}"
            tmp_dst = f"{dst}.tmp"
            urllib.request.urlretrieve(url, tmp_dst)
            os.replace(tmp_dst, dst)
    return applio_dir


def extract_rvc_model_zip(zip_path: str, target_dir: str, pth_name: str, idx_name: str) -> None:
    """Safely extracts .pth, optional .index, and metadata.json from an RVC model archive."""
    target_path = Path(target_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            lower = os.path.basename(info.filename).lower()
            if lower.endswith(".pth"):
                out_name = pth_name
            elif lower.endswith(".index") and idx_name:
                out_name = idx_name
            elif lower == "metadata.json":
                out_name = "metadata.json"
            else:
                continue
            dst_path = safe_resolve_under(target_path, out_name)
            with zf.open(info, "r") as src_f, open(dst_path, "wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f)


def _resolve_optional_local(raw_path: str, base_dir: Optional[Path]) -> str:
    cleaned = (raw_path or "").strip()
    if not cleaned:
        return ""
    p = Path(cleaned)
    if not p.is_absolute() and base_dir is not None:
        return str((base_dir / p).resolve())
    return str(p)


def ensure_rvc_model_assets(
    voice_id: str = "gawr_gura",
    model_dir: Optional[str] = None,
    rvc_cfg: Optional[Dict[str, Any]] = None,
    assets_root: str = "",
    base_dir: Optional[Path] = None,
) -> Tuple[str, str]:
    """Resolves or downloads RVC .pth and optional .index weights from local paths or Hugging Face Hub."""
    cfg = dict(rvc_cfg or {})
    local_pth = _resolve_optional_local(str(cfg.get("local_pth") or ""), base_dir)
    local_idx = _resolve_optional_local(str(cfg.get("local_index") or ""), base_dir)
    if local_pth:
        if not os.path.isfile(local_pth):
            raise FileNotFoundError(f"Configured local_pth not found: {local_pth}")
        if local_idx and not os.path.isfile(local_idx):
            raise FileNotFoundError(f"Configured local_index not found: {local_idx}")
        return local_pth, local_idx

    subdir = str(cfg.get("default_subdir") or voice_id)
    default_dir, _ = default_rvc_dirs(subdir, assets_root=assets_root)
    target_dir = model_dir or default_dir
    os.makedirs(target_dir, exist_ok=True)

    pth_name = str(cfg.get("pth_filename") or "model.pth")
    idx_name = str(cfg["index_filename"]).strip() if cfg.get("index_filename") is not None else "model.index"

    pth_path = os.path.join(target_dir, pth_name)
    idx_path = os.path.join(target_dir, idx_name) if idx_name else ""
    if os.path.exists(pth_path) and (not idx_path or os.path.exists(idx_path)):
        return pth_path, idx_path

    if str(cfg.get("source_type", "huggingface")).lower() == "local":
        raise FileNotFoundError(f"Missing local RVC weights: pth={pth_path}" + (f", index={idx_path}" if idx_path else ""))

    from huggingface_hub import hf_hub_download

    repo_id = str(cfg.get("repo_id") or "").strip()
    if not repo_id:
        raise ValueError(f"Missing repo_id for RVC voice {voice_id!r} and no local_pth provided.")
    zip_filename = str(cfg.get("zip_filename") or "")
    if zip_filename:
        dl_dir = os.path.join(target_dir, "_hf_dl")
        os.makedirs(dl_dir, exist_ok=True)
        zip_path = hf_hub_download(repo_id=repo_id, filename=zip_filename, local_dir=dl_dir)
        extract_rvc_model_zip(zip_path, target_dir, pth_name, idx_name)
        if not os.path.exists(pth_path) or (idx_path and not os.path.exists(idx_path)):
            raise FileNotFoundError(f"Missing {pth_path} or {idx_path} after extracting {zip_filename}")
        return pth_path, idx_path

    if not os.path.exists(pth_path):
        hf_hub_download(repo_id=repo_id, filename=pth_name, local_dir=target_dir)
    if idx_path and not os.path.exists(idx_path):
        hf_hub_download(repo_id=repo_id, filename=idx_name, local_dir=target_dir)
    return pth_path, idx_path


def configure_torch_runtime(config: Any = None, rvc_fp32: bool = False) -> Dict[str, Any]:
    """Configures PyTorch/Applio GPU device cleanly (supports full FP32 on Colab Pro L4/A100)."""
    info: Dict[str, Any] = {"cuda": False, "device_name": "cpu", "vram_gb": 0.0, "cc": (0, 0)}
    try:
        import torch

        if torch.cuda.is_available():
            cc = torch.cuda.get_device_capability(0)
            props = torch.cuda.get_device_properties(0)
            vram_gb = round(props.total_memory / float(1024**3), 1)
            info.update({"cuda": True, "device_name": props.name, "vram_gb": vram_gb, "cc": cc})
            torch.backends.cudnn.benchmark = True
            if cc >= (8, 0):
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
                torch.set_float32_matmul_precision("high")
            elif cc < (7, 0):
                try:
                    _ = float((torch.ones(1, device="cuda") + 1.0)[0].item())
                except Exception as exc:
                    print(f"[RVC] GPU CC {cc} unsupported by current torch CUDA build ({exc}); using CPU.")
                    if config is not None:
                        config.device = "cpu"
                        config.is_half = False
                    info["cuda"] = False
                    info["device_name"] = "cpu"
                    return info
            if config is not None:
                if rvc_fp32:
                    config.is_half = False
                if vram_gb >= 14.0:
                    config.x_pad, config.x_query, config.x_center, config.x_max = (3, 10, 60, 65)
    except Exception as exc:
        print(f"[RVC] GPU runtime probe notice: {exc}")
    return info
