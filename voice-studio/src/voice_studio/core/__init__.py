# -*- coding: utf-8 -*-
"""Core utilities for runtime checks, security guards, model verification, and job state."""

from voice_studio.core.models_manager import format_models_report, verify_local_models
from voice_studio.core.runtime import (
    has_module,
    probe_ffmpeg,
    probe_gpu_in_current_python,
    probe_runtime,
    resolve_rvc_assets_base,
    resolve_voice_python,
)
from voice_studio.core.security import (
    collect_checksums,
    constant_time_token_match,
    is_non_loopback_http,
    redact_secret,
    safe_extract_zip,
    safe_resolve_under,
    sha256_file,
    validate_safe_id,
)
from voice_studio.core.state import (
    compute_pipeline_signatures,
    default_job_id,
    format_job_status,
    invalidate_steps_from,
    list_jobs,
    load_or_init_job_state,
    purge_step_artifacts,
    save_json_atomic,
    utc_now_iso,
    update_step_state,
)

__all__ = [
    "collect_checksums",
    "compute_pipeline_signatures",
    "constant_time_token_match",
    "default_job_id",
    "format_job_status",
    "format_models_report",
    "has_module",
    "invalidate_steps_from",
    "is_non_loopback_http",
    "list_jobs",
    "load_or_init_job_state",
    "probe_ffmpeg",
    "probe_gpu_in_current_python",
    "probe_runtime",
    "purge_step_artifacts",
    "redact_secret",
    "resolve_rvc_assets_base",
    "resolve_voice_python",
    "safe_extract_zip",
    "safe_resolve_under",
    "save_json_atomic",
    "sha256_file",
    "utc_now_iso",
    "update_step_state",
    "validate_safe_id",
    "verify_local_models",
]
