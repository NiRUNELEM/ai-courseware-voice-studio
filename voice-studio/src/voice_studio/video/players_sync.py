# -*- coding: utf-8 -*-
"""Synchronizes HTML5 Studio Video Player & Voice Audition Player templates cleanly for any job."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from voice_studio.video.players_audition import render_audition_player_html
from voice_studio.video.players_video import render_video_player_html


def sync_job_players(
    out_dir: Path,
    title: str,
    slides_payload: List[Dict[str, Any]],
    output_prefix: str = "studio",
    fps: int = 60,
) -> List[Path]:
    """Writes standalone HTML5 video & audition players inside out_dir for any job."""
    total = len(slides_payload)
    mp4_name = f"{output_prefix}_1080p.mp4"
    master_mp3 = f"{output_prefix}_complete.mp3"
    master_wav = f"{output_prefix}_complete.wav"
    v_path = out_dir / "video_player.html"
    a_path = out_dir / "voice_audition.html"
    out_dir.mkdir(parents=True, exist_ok=True)
    v_path.write_text(
        render_video_player_html(total, title, mp4_name, slides_payload, fps=fps),
        encoding="utf-8",
    )
    a_path.write_text(
        render_audition_player_html(total, title, master_mp3, master_wav, slides_payload),
        encoding="utf-8",
    )
    return [v_path, a_path]
