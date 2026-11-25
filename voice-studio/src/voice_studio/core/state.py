# -*- coding: utf-8 -*-
"""Atomic job state tracking, step progress recording, and CLI status reporting."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from voice_studio.config.models import ORDERED_STEPS
from voice_studio.core.security import validate_safe_id


def _dict_signature(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def compute_pipeline_signatures(
    content_summary: Dict[str, Any],
    voice_profile: Any,
) -> Dict[str, str]:
    """Computes deterministic content, Stage 1 TTS, and Stage 2 RVC+mastering cache signatures."""
    if voice_profile is None:
        return {"content": _dict_signature(content_summary), "tts": "", "convert": ""}
    s1 = voice_profile.stage1
    s2 = voice_profile.stage2
    m = voice_profile.mastering
    tts_payload = {
        "backend": s1.backend,
        "voice": s1.voice,
        "hf_model_repo": getattr(s1, "hf_model_repo", ""),
        "hf_speaker": getattr(s1, "hf_speaker", ""),
        "hf_speed": float(getattr(s1, "hf_speed", 0.96)),
        "rate": s1.rate,
        "pitch": s1.pitch,
        "text_mode": s1.text_mode,
        "clause_spacing": s1.clause_spacing,
    }
    convert_payload = {
        "voice_id": voice_profile.voice_id,
        "stage2": s2.__dict__,
        "mastering": m.__dict__,
    }
    return {
        "content": _dict_signature(content_summary),
        "tts": _dict_signature(tts_payload),
        "convert": _dict_signature(convert_payload),
    }


def _reset_step_record(rec: Dict[str, Any], message: str) -> None:
    rec["status"] = "pending"
    rec["progress"] = 0.0
    rec["started_at"] = None
    rec["finished_at"] = None
    rec["duration_sec"] = None
    rec["message"] = message
    rec["needs_rerun"] = True


def purge_step_artifacts(job_dir: Path, from_step: str) -> None:
    """Removes stale intermediate outputs at or downstream of from_step so resume never reuses old runs."""
    if from_step not in ORDERED_STEPS:
        return
    idx = ORDERED_STEPS.index(from_step)
    if idx <= ORDERED_STEPS.index("transcribe"):
        shutil.rmtree(job_dir / "transcript", ignore_errors=True)
    if idx <= ORDERED_STEPS.index("tts"):
        shutil.rmtree(job_dir / "audio_stage1", ignore_errors=True)
    if idx <= ORDERED_STEPS.index("convert"):
        for d in ("audio_items_raw", "audio_slides_wav", "audio_slides_mp3"):
            shutil.rmtree(job_dir / d, ignore_errors=True)
        vm = job_dir / "voice_manifest.json"
        if vm.is_file():
            vm.unlink()
    if idx <= ORDERED_STEPS.index("align"):
        tl = job_dir / "timeline_manifest.json"
        if tl.is_file():
            tl.unlink()
    if idx <= ORDERED_STEPS.index("render"):
        shutil.rmtree(job_dir / "video_segments_1080p", ignore_errors=True)
        shutil.rmtree(job_dir / "deliverables", ignore_errors=True)


def invalidate_steps_from(
    job_dir: Path,
    state: Dict[str, Any],
    from_step: str,
    reason: str,
    inclusive: bool = True,
    purge_artifacts: bool = True,
) -> Dict[str, Any]:
    """Marks steps from `from_step` onward as pending across ORDERED_STEPS."""
    if from_step not in ORDERED_STEPS:
        return state
    start_idx = ORDERED_STEPS.index(from_step) + (0 if inclusive else 1)
    if start_idx >= len(ORDERED_STEPS):
        return state
    steps_map = state.setdefault("steps", {})
    for s_name in ORDERED_STEPS[start_idx:]:
        rec = steps_map.setdefault(s_name, {})
        if rec.get("status") not in ("skipped", None) or s_name in (state.get("requested_steps") or []):
            _reset_step_record(rec, reason)
    if purge_artifacts:
        purge_step_artifacts(job_dir, ORDERED_STEPS[start_idx])
    req = state.get("requested_steps") or list(ORDERED_STEPS)
    done_weight = sum(float(steps_map.get(s, {}).get("progress", 0.0)) for s in req)
    state["progress_pct"] = round(min(100.0, 100.0 * done_weight / max(1, len(req))), 1)
    if not all(steps_map.get(s, {}).get("status") == "completed" for s in req):
        state["status"] = "initialized"
    state["error"] = None
    return state


def utc_now_iso() -> str:
    """Returns current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_job_id(content_label: str, voice_id: str) -> str:
    """Builds a deterministic filesystem-safe job identifier."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    raw = f"{content_label}_{voice_id}_{stamp}"
    safe = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in raw)
    return validate_safe_id(safe, label="job_id")


def save_json_atomic(path: Path, payload: Any) -> None:
    """Writes JSON atomically via same-directory temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


def load_or_init_job_state(
    job_dir: Path,
    job_id: str,
    target: str,
    voice_id: str,
    requested_steps: List[str],
    content_summary: Optional[Dict[str, Any]] = None,
    signatures: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Loads existing status.json for resumption or initializes a fresh job state record."""
    status_path = job_dir / "status.json"
    if status_path.is_file():
        state = json.loads(status_path.read_text(encoding="utf-8"))
        prev_voice = state.get("voice_id")
        prev_sigs = dict(state.get("signatures") or {})
        new_sigs = dict(signatures or {})
        state["updated_at"] = utc_now_iso()
        state["target"] = target
        state["voice_id"] = voice_id
        state["requested_steps"] = list(requested_steps)
        if content_summary:
            state["content"] = content_summary
        if new_sigs:
            state["signatures"] = new_sigs

        invalidated_from: Optional[str] = None
        reason = ""
        if prev_sigs.get("content") and new_sigs.get("content") and prev_sigs["content"] != new_sigs["content"]:
            invalidated_from = "prepare"
            reason = "Content input changed"
        elif prev_sigs.get("tts") and new_sigs.get("tts") and prev_sigs["tts"] != new_sigs["tts"]:
            invalidated_from = "tts"
            reason = f"Stage 1 TTS config changed ({prev_voice} -> {voice_id})"
        elif (prev_sigs.get("convert") and new_sigs.get("convert") and prev_sigs["convert"] != new_sigs["convert"]) or (
            prev_voice and prev_voice != voice_id
        ):
            invalidated_from = "convert"
            reason = f"Voice changed ({prev_voice} -> {voice_id})"

        if invalidated_from:
            invalidate_steps_from(job_dir, state, invalidated_from, reason=reason, inclusive=True, purge_artifacts=True)
        else:
            for step_name in requested_steps:
                rec = state.setdefault("steps", {}).setdefault(step_name, {})
                if rec.get("status") == "skipped":
                    rec["status"] = "pending"
        save_json_atomic(status_path, state)
        return state

    steps_map: Dict[str, Dict[str, Any]] = {
        step: {
            "status": "pending" if step in requested_steps else "skipped",
            "started_at": None,
            "finished_at": None,
            "duration_sec": None,
            "progress": 0.0,
            "message": "",
            "metrics": {},
        }
        for step in ORDERED_STEPS
    }
    state = {
        "job_id": job_id,
        "status": "initialized",
        "target": target,
        "voice_id": voice_id,
        "requested_steps": list(requested_steps),
        "current_step": None,
        "progress_pct": 0.0,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "content": content_summary or {},
        "signatures": dict(signatures or {}),
        "steps": steps_map,
        "artifacts": {},
        "checksums": {},
        "error": None,
    }
    save_json_atomic(status_path, state)
    return state


def update_step_state(
    job_dir: Path,
    state: Dict[str, Any],
    step_name: str,
    status: str,
    message: str = "",
    progress: Optional[float] = None,
    metrics: Optional[Dict[str, Any]] = None,
    artifacts: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Updates step record, computes overall progress %, and persists status.json atomically."""
    step_rec = state.setdefault("steps", {}).setdefault(step_name, {})
    now = utc_now_iso()
    if status == "running" and (not step_rec.get("started_at") or (progress is not None and progress <= 0.1)):
        step_rec["started_at"] = now
        step_rec["finished_at"] = None
        step_rec["duration_sec"] = None
    if status in ("completed", "failed"):
        step_rec["finished_at"] = now
        if step_rec.get("started_at"):
            t0 = datetime.fromisoformat(step_rec["started_at"])
            t1 = datetime.fromisoformat(now)
            step_rec["duration_sec"] = round((t1 - t0).total_seconds(), 3)
    step_rec["status"] = status
    if status == "completed":
        step_rec.pop("needs_rerun", None)
    if message:
        step_rec["message"] = message
    if progress is not None:
        step_rec["progress"] = round(float(progress), 3)
    elif status == "completed":
        step_rec["progress"] = 1.0
    if metrics:
        step_rec.setdefault("metrics", {}).update(metrics)
    if artifacts:
        state.setdefault("artifacts", {}).update(artifacts)

    req = state.get("requested_steps") or list(ORDERED_STEPS)
    done_weight = sum(float(state["steps"].get(s, {}).get("progress", 0.0)) for s in req)
    state["progress_pct"] = round(min(100.0, 100.0 * done_weight / max(1, len(req))), 1)
    state["current_step"] = step_name if status == "running" else state.get("current_step")
    state["updated_at"] = now

    if status == "failed":
        state["status"] = "failed"
        state["error"] = error or message
    elif all(state["steps"].get(s, {}).get("status") == "completed" for s in req):
        state["status"] = "completed"
        state["current_step"] = None
        state["error"] = None
    elif status == "running":
        state["status"] = "running"

    save_json_atomic(job_dir / "status.json", state)
    return state


def list_jobs(output_root: Path) -> List[Dict[str, Any]]:
    """Lists all jobs in output_root sorted newest-first."""
    if not output_root.is_dir():
        return []
    records: List[Dict[str, Any]] = []
    for sub in sorted(output_root.iterdir()):
        status_file = sub / "status.json"
        if sub.is_dir() and status_file.is_file():
            try:
                records.append(json.loads(status_file.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError) as exc:
                records.append({"job_id": sub.name, "status": "corrupt", "error": str(exc)})
    records.sort(key=lambda r: str(r.get("updated_at", "")), reverse=True)
    return records


def format_job_status(state: Dict[str, Any]) -> str:
    """Formats human-readable terminal progress report for a job."""
    lines = [
        "=" * 78,
        f"JOB STATUS : {state.get('job_id')} [{str(state.get('status')).upper()}] ({state.get('progress_pct', 0.0)}%)",
        f"Target     : {state.get('target')} | Voice: {state.get('voice_id')}",
        f"Updated At : {state.get('updated_at')}",
        "-" * 78,
    ]
    for step in ORDERED_STEPS:
        rec = (state.get("steps") or {}).get(step, {})
        st = str(rec.get("status", "pending")).upper()
        dur = rec.get("duration_sec")
        dur_str = f" ({dur:.1f}s)" if isinstance(dur, (int, float)) else ""
        msg = rec.get("message") or ""
        lines.append(f"  {step:<12} : {st:<10}{dur_str}  {msg}")
    if state.get("artifacts"):
        lines.append("-" * 78)
        for k, v in state["artifacts"].items():
            lines.append(f"  artifact[{k}] -> {v}")
    if state.get("error"):
        lines.append("-" * 78)
        lines.append(f"  ERROR: {state['error']}")
    lines.append("=" * 78)
    return "\n".join(lines)


def cleanup_intermediate_job_dirs(job_dir: Path) -> List[str]:
    """Removes intermediate pipeline work directories while preserving deliverables/, status.json, logs/, and zip."""
    removed: List[str] = []
    for sub in ("work", "audio_items_raw", "audio_stage1", "video_segments_1080p"):
        cand = job_dir / sub
        if cand.is_dir():
            shutil.rmtree(cand, ignore_errors=True)
            removed.append(sub)
    return removed
