# -*- coding: utf-8 -*-
"""Modular video composition, reveal alignment, HTML5 player, and job renderer package."""

from voice_studio.video.alignment import (
    generate_job_timeline,
    get_audio_duration,
    resolve_alignment,
    validate_timeline,
)
from voice_studio.video.compositor import (
    DEFAULT_FPS,
    SLIDE_H,
    SLIDE_W,
    detect_best_video_encoder,
    encode_slide_segment,
    package_deliverables,
    prepare_stage_buffers,
)
from voice_studio.video.manifest import detect_total_slides, load_timeline_manifest, read_wav_duration
from voice_studio.video.players_audition import render_audition_player_html
from voice_studio.video.players_sync import sync_job_players
from voice_studio.video.players_video import render_video_player_html
from voice_studio.video.renderer import render_job_video
from voice_studio.video.reveals import (
    extract_presentation_hierarchy,
    extract_slide_hierarchy,
    save_presentation_reveals,
)

__all__ = [
    "DEFAULT_FPS",
    "SLIDE_H",
    "SLIDE_W",
    "detect_best_video_encoder",
    "detect_total_slides",
    "encode_slide_segment",
    "extract_presentation_hierarchy",
    "extract_slide_hierarchy",
    "generate_job_timeline",
    "get_audio_duration",
    "load_timeline_manifest",
    "package_deliverables",
    "prepare_stage_buffers",
    "read_wav_duration",
    "render_audition_player_html",
    "render_job_video",
    "render_video_player_html",
    "resolve_alignment",
    "save_presentation_reveals",
    "sync_job_players",
    "validate_timeline",
]
