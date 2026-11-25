# -*- coding: utf-8 -*-
"""Google Colab bridge modules: HTTP worker agent, signed-in Drive sync, client, and bundle packager."""

from voice_studio.colab.agent import ColabWorkerState, start_agent_server
from voice_studio.colab.bundle import (
    build_production_bundle,
    build_zip_bundle,
    collect_bundle_paths,
    create_remote_job_bundle,
)
from voice_studio.colab.client import ColabClient
from voice_studio.colab.colab_cli_bridge import (
    ColabCliBridge,
    check_colab_cli_auth,
    normalize_gpu_request,
    parse_colab_sessions_output,
    resolve_colab_cli_binary,
)
from voice_studio.colab.drive_bridge import (
    DriveApiBridge,
    LocalDriveSyncBridge,
    authenticate_google_drive,
    run_colab_drive_worker,
)
from voice_studio.colab.gdrive_cli import GdriveCliBridge, resolve_gdrive_cli_binary

__all__ = [
    "ColabCliBridge",
    "ColabClient",
    "ColabWorkerState",
    "DriveApiBridge",
    "GdriveCliBridge",
    "LocalDriveSyncBridge",
    "authenticate_google_drive",
    "build_production_bundle",
    "build_zip_bundle",
    "check_colab_cli_auth",
    "collect_bundle_paths",
    "create_remote_job_bundle",
    "normalize_gpu_request",
    "parse_colab_sessions_output",
    "resolve_colab_cli_binary",
    "resolve_gdrive_cli_binary",
    "run_colab_drive_worker",
    "start_agent_server",
]
