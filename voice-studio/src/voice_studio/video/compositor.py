# -*- coding: utf-8 -*-
"""1080p progressive reveal compositor, NVENC/CPU encoder, and generic deliverable packager."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import tempfile
import zipfile
from typing import Any, Dict, Iterable, List, Optional

from PIL import Image, ImageDraw

SLIDE_W = 1920
SLIDE_H = 1080
TOP_H = 1076
BAR_H = 4
DEFAULT_FPS = 60
TRANSITION_FRAMES = 24
CYAN_RGB = bytes([56, 189, 248])
SLATE_RGB = bytes([30, 41, 59])


def create_contextual_blank(
    base_img: Image.Image,
    bbox: List[int],
    level: int,
    parent_id: Any,
    all_items: List[Dict[str, Any]],
) -> Image.Image:
    """Creates a surface-matched unrevealed card or interior fill without background bleed."""
    x1, y1, x2, y2 = bbox
    w, h = max(1, x2 - x1), max(1, y2 - y1)
    full_crop = base_img.crop((x1, y1, x2, y2))

    if level == 0:
        samples = [
            full_crop.getpixel((min(15, w - 1), min(15, h - 1))),
            full_crop.getpixel((max(0, w - 15), min(15, h - 1))),
            full_crop.getpixel((min(15, w - 1), max(0, h - 15))),
            full_crop.getpixel((max(0, w - 15), max(0, h - 15))),
            full_crop.getpixel((w // 2, min(20, h - 1))),
        ]
        avg_b = sum(0.299 * s[0] + 0.587 * s[1] + 0.114 * s[2] for s in samples) / len(samples)
        blank = full_crop.copy()
        draw = ImageDraw.Draw(blank)
        if avg_b > 180:
            draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=14, fill=(255, 255, 255), outline=(226, 232, 240), width=1)
        elif avg_b < 80:
            draw.rounded_rectangle([0, 0, w - 1, h - 1], radius=12, fill=(15, 23, 42), outline=(30, 41, 59), width=1)
        else:
            draw.rectangle([0, 0, w, h], fill=(int(samples[0][0]), int(samples[0][1]), int(samples[0][2])))
        return blank

    parent_item = None
    if parent_id:
        for p in all_items:
            if p.get("reveal_id") in (parent_id, f"{parent_id}_rev"):
                parent_item = p
                break
    if not parent_item:
        for p in all_items:
            if p.get("level", 0) == 0:
                px1, py1, px2, py2 = p["bbox"]
                if px1 <= x1 + 5 and py1 <= y1 + 5 and px2 >= x2 - 5 and py2 >= y2 - 5:
                    parent_item = p
                    break

    if parent_item:
        pw = max(1, parent_item["bbox"][2] - parent_item["bbox"][0])
        ph = max(1, parent_item["bbox"][3] - parent_item["bbox"][1])
        p_crop = base_img.crop(parent_item["bbox"])
        p_pts = [
            p_crop.getpixel((max(0, pw - 20), max(0, ph - 20))),
            p_crop.getpixel((min(20, pw - 1), max(0, ph - 20))),
            p_crop.getpixel((pw // 2, max(0, ph - 15))),
        ]
        p_avg_b = sum(0.299 * s[0] + 0.587 * s[1] + 0.114 * s[2] for s in p_pts) / len(p_pts)
        fill_col = (255, 255, 255) if p_avg_b > 180 else ((15, 23, 42) if p_avg_b < 80 else (int(p_pts[0][0]), int(p_pts[0][1]), int(p_pts[0][2])))
    else:
        edge_samples = [
            full_crop.getpixel((0, min(5, h - 1))),
            full_crop.getpixel((w - 1, min(5, h - 1))),
            full_crop.getpixel((0, max(0, h - 5))),
            full_crop.getpixel((w - 1, max(0, h - 5))),
        ]
        fill_col = (
            int(sum(s[0] for s in edge_samples) / len(edge_samples)),
            int(sum(s[1] for s in edge_samples) / len(edge_samples)),
            int(sum(s[2] for s in edge_samples) / len(edge_samples)),
        )
    return Image.new("RGB", (w, h), color=fill_col)


def prepare_stage_buffers(
    base_img: Image.Image,
    items: List[Dict[str, Any]],
    apply_progressive: bool,
    transition_frames: int = TRANSITION_FRAMES,
) -> Dict[str, Any]:
    """Pre-renders masked stages and ease-out alpha dissolve byte chunks."""
    t_frames = max(1, int(transition_frames))
    initial_stage = base_img.copy()
    stage_images: List[Image.Image] = []
    if not apply_progressive:
        base_top = base_img.crop((0, 0, SLIDE_W, TOP_H)).tobytes()
        return {
            "initial_stage": initial_stage,
            "stage_images": [],
            "base_top_bytes": base_top,
            "initial_top_bytes": base_top,
            "stage_top_bytes": [],
            "transitions": {},
            "transition_frames": t_frames,
        }

    items.sort(key=lambda x: (x["start_sec"], x.get("level", 0)))
    for it in items:
        bbox = it["bbox"]
        x1, y1 = max(0, bbox[0]), max(0, bbox[1])
        x2, y2 = min(SLIDE_W, bbox[2]), min(SLIDE_H, bbox[3])
        it["x1"], it["y1"], it["x2"], it["y2"] = x1, y1, x2, y2
        it["full_crop"] = base_img.crop((x1, y1, x2, y2))
        it["blank_crop"] = create_contextual_blank(base_img, [x1, y1, x2, y2], it.get("level", 0), it.get("parent_id"), items)

    for u in sorted(items, key=lambda x: x.get("level", 0)):
        initial_stage.paste(u["blank_crop"], (u["x1"], u["y1"]))

    for k in range(len(items)):
        stage_img = base_img.copy()
        for u in sorted(items[k + 1 :], key=lambda x: x.get("level", 0)):
            stage_img.paste(u["blank_crop"], (u["x1"], u["y1"]))
        stage_images.append(stage_img)

    initial_top_bytes = initial_stage.crop((0, 0, SLIDE_W, TOP_H)).tobytes()
    stage_top_bytes = [img.crop((0, 0, SLIDE_W, TOP_H)).tobytes() for img in stage_images]
    transitions: Dict[int, List[bytes]] = {}
    for idx, it in enumerate(items):
        if idx > 0 or it["start_sec"] > 0.05:
            prior = stage_images[idx - 1] if idx > 0 else initial_stage
            chunks: List[bytes] = []
            for rel_f in range(t_frames):
                p = rel_f / float(t_frames)
                alpha = 1.0 - (1.0 - p) ** 2
                blended = Image.blend(it["blank_crop"], it["full_crop"], alpha)
                f_img = prior.copy()
                f_img.paste(blended, (it["x1"], it["y1"]))
                chunks.append(f_img.crop((0, 0, SLIDE_W, TOP_H)).tobytes())
            transitions[idx] = chunks

    return {
        "initial_stage": initial_stage,
        "stage_images": stage_images,
        "base_top_bytes": initial_top_bytes,
        "initial_top_bytes": initial_top_bytes,
        "stage_top_bytes": stage_top_bytes,
        "transitions": transitions,
        "transition_frames": t_frames,
    }


@functools.lru_cache(maxsize=8)
def detect_best_video_encoder(preferred_codec: str = "auto") -> Dict[str, Any]:
    """Probes FFmpeg/GPU support to pick preferred codec (auto/hevc/h264/libx264/libx265) with fallback."""
    cpu_count = os.cpu_count() or 4
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return {"codec": "none", "hardware": False, "workers": max(1, min(4, cpu_count)), "threads_per_worker": 2}

    all_candidates = [
        ("hevc_nvenc", True, ["-c:v", "hevc_nvenc", "-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "5M", "-maxrate", "10M", "-spatial-aq", "1", "-pix_fmt", "yuv420p"]),
        ("h264_nvenc", True, ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-rc", "vbr", "-cq", "21", "-b:v", "6M", "-maxrate", "12M", "-pix_fmt", "yuv420p"]),
        ("libx264", False, ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p"]),
        ("libx265", False, ["-c:v", "libx265", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p"]),
    ]
    pref = (preferred_codec or "auto").strip().lower()
    if pref in ("h264", "avc", "web"):
        order = ("h264_nvenc", "libx264", "hevc_nvenc", "libx265")
    elif pref in ("hevc", "h265"):
        order = ("hevc_nvenc", "libx265", "h264_nvenc", "libx264")
    elif pref in ("hevc_nvenc", "h264_nvenc", "libx264", "libx265"):
        order = (pref, *(c[0] for c in all_candidates if c[0] != pref))
    else:
        order = ("hevc_nvenc", "h264_nvenc", "libx264", "libx265")
    cand_map = {c[0]: c for c in all_candidates}
    candidates = [cand_map[name] for name in order if name in cand_map]
    with tempfile.NamedTemporaryFile(suffix=".mp4") as probe_out:
        for codec, is_hw, codec_args in candidates:
            probe_cmd = [
                ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.05",
                "-frames:v", "2", *codec_args, probe_out.name,
            ]
            res = subprocess.run(probe_cmd, capture_output=True, check=False)
            if res.returncode == 0 and os.path.getsize(probe_out.name) > 0:
                workers = max(2, min(4 if is_hw else 6, cpu_count // 2 or 2))
                threads_per = max(1 if is_hw else 2, cpu_count // workers)
                return {
                    "codec": codec, "hardware": is_hw, "args": codec_args,
                    "workers": workers, "threads_per_worker": threads_per, "cpu_count": cpu_count,
                }
    return {
        "codec": "libx264", "hardware": False,
        "args": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"],
        "workers": max(1, min(4, cpu_count // 2 or 1)), "threads_per_worker": max(2, cpu_count // 2), "cpu_count": cpu_count,
    }


def _override_nvenc_args(args: List[str], nvenc_preset: str, nvenc_cq: int) -> List[str]:
    out = list(args)
    is_nvenc = any("nvenc" in x for x in out)
    for i, tok in enumerate(out[:-1]):
        if tok == "-preset" and is_nvenc:
            out[i + 1] = nvenc_preset
        elif tok == "-cq":
            out[i + 1] = str(nvenc_cq)
        elif tok == "-b:v" and is_nvenc and nvenc_cq <= 19:
            out[i + 1] = "8M"
        elif tok == "-maxrate" and is_nvenc and nvenc_cq <= 19:
            out[i + 1] = "16M"
    return out


def encode_slide_segment(
    s_no: int,
    slide_wav: str,
    seg_mp4: str,
    temp_dir: str,
    total_frames: int,
    apply_progressive: bool,
    items: List[Dict[str, Any]],
    buffers: Dict[str, Any],
    encoder_cfg: Optional[Dict[str, Any]] = None,
    fps: int = DEFAULT_FPS,
    aac_bitrate: str = "192k",
    nvenc_preset: str = "p4",
    nvenc_cq: int = 21,
    progress_bar: bool = True,
) -> None:
    """Streams raw RGB frames with an optional 4px progress bar into ffmpeg."""
    enc = encoder_cfg or detect_best_video_encoder()
    is_hw = bool(enc.get("hardware", False))
    # Cap raw RGB pipe input to 20fps (divides 60fps evenly by 3x) to avoid ~950GB Python pipe bottleneck
    pipe_fps = min(fps, 20) if is_hw else min(fps, 10)
    pipe_frames = total_frames if pipe_fps == fps else max(1, int(round((total_frames / float(fps)) * pipe_fps)))

    v_args = _override_nvenc_args(
        list(enc.get("args", ["-c:v", "hevc_nvenc", "-preset", "p4", "-pix_fmt", "yuv420p"])),
        nvenc_preset=nvenc_preset,
        nvenc_cq=nvenc_cq,
    )
    if not is_hw and "-threads" not in v_args:
        v_args.extend(["-threads", str(enc.get("threads_per_worker", 2))])
    cmd = [
        "ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{SLIDE_W}x{SLIDE_H}", "-r", str(pipe_fps),
        "-i", "pipe:0", "-i", slide_wav,
        *v_args, "-r", str(fps), "-c:a", "aac", "-b:a", aac_bitrate, "-ar", "48000", "-shortest", seg_mp4,
    ]
    log_path = os.path.join(temp_dir, f"ffmpeg_{s_no:02d}.log")
    pipe_error: Optional[BrokenPipeError] = None
    with open(log_path, "w", encoding="utf-8") as ffmpeg_log:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=ffmpeg_log, bufsize=8 * 1024 * 1024)
        transitions = buffers["transitions"]
        t_frames = int(buffers.get("transition_frames", TRANSITION_FRAMES))
        stage_top_bytes = buffers["stage_top_bytes"]
        assert proc.stdin is not None
        last_pw = -1
        cached_bar = (SLATE_RGB * SLIDE_W) * BAR_H
        try:
            for f_idx in range(pipe_frames):
                t = f_idx / float(pipe_fps)
                if not apply_progressive:
                    top_bytes = buffers["base_top_bytes"]
                else:
                    active_idx = -1
                    for idx, it in enumerate(items):
                        if t >= it["start_sec"]:
                            active_idx = idx
                        else:
                            break
                    if active_idx == -1:
                        top_bytes = buffers["initial_top_bytes"]
                    else:
                        trans_idx = int((t - items[active_idx]["start_sec"]) * fps)
                        if 0 <= trans_idx < t_frames and active_idx in transitions:
                            top_bytes = transitions[active_idx][trans_idx]
                        else:
                            top_bytes = stage_top_bytes[active_idx]
                if progress_bar:
                    p_w = min(SLIDE_W, max(0, int(SLIDE_W * (f_idx + 1) / float(pipe_frames))))
                    if p_w != last_pw:
                        cached_bar = (CYAN_RGB * p_w + SLATE_RGB * (SLIDE_W - p_w)) * BAR_H
                        last_pw = p_w
                proc.stdin.write(top_bytes)
                proc.stdin.write(cached_bar)
        except BrokenPipeError as exc:
            pipe_error = exc
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError as close_exc:
                pipe_error = pipe_error or close_exc
        proc.wait()

    if proc.returncode != 0 or pipe_error is not None:
        tail = ""
        if os.path.exists(log_path):
            with open(log_path, "r", encoding="utf-8", errors="replace") as lf:
                tail = "".join(lf.readlines()[-20:]).strip()
        raise RuntimeError(f"ffmpeg encode failed for segment {s_no:02d} (exit={proc.returncode}, pipe={pipe_error}): {tail}")


def write_dry_run_preview(
    s_no: int,
    total_slides: int,
    apply_progressive: bool,
    base_img: Image.Image,
    items: List[Dict[str, Any]],
    buffers: Dict[str, Any],
    out_dir: str,
) -> None:
    """Exports verification PNG frames for representative reveal slides in dry-run mode."""
    verif_dir = os.path.join(out_dir, "verification_frames")
    os.makedirs(verif_dir, exist_ok=True)
    if apply_progressive and (s_no == 2 or s_no == total_slides - 1):
        buffers["initial_stage"].save(os.path.join(verif_dir, f"seg_{s_no:02d}_01_initial_masked.png"))
        stage_images = buffers["stage_images"]
        if stage_images:
            stage_images[-1].save(os.path.join(verif_dir, f"seg_{s_no:02d}_02_final_revealed.png"))
    elif s_no == 1:
        base_img.save(os.path.join(verif_dir, "seg_01_preview.png"))


def package_deliverables(
    output_prefix: str,
    temp_dir: str,
    out_dir: str,
    slides_dir: str,
    audio_wav_dir: str,
    audio_mp3_dir: Optional[str],
    slide_numbers: List[int],
    rendered_segments: List[str],
    output_formats: Iterable[str] = ("wav", "mp3", "mp4"),
    zip_out_path: str = "",
    mp3_bitrate: str = "192k",
    bit_depth: int = 16,
) -> Dict[str, str]:
    """Concatenates requested master WAV/MP3/MP4 files and packages deliverables archive."""
    fmts = {str(f).lower() for f in output_formats}
    artifacts: Dict[str, str] = {}
    has_ffmpeg = shutil.which("ffmpeg") is not None

    if "mp4" in fmts and rendered_segments and has_ffmpeg:
        concat_list = os.path.join(temp_dir, "concat_video.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            for seg in rendered_segments:
                f.write(f"file '{seg}'\n")
        master_mp4 = os.path.join(out_dir, f"{output_prefix}_1080p.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list, "-c", "copy", master_mp4], capture_output=True, check=True)
        artifacts["master_mp4"] = master_mp4

    if ("wav" in fmts or "mp3" in fmts) and has_ffmpeg:
        audio_concat = os.path.join(temp_dir, "concat_audio.txt")
        with open(audio_concat, "w", encoding="utf-8") as f:
            for s_no in slide_numbers:
                w = os.path.join(audio_wav_dir, f"slide_{s_no:02d}.wav")
                if os.path.exists(w):
                    f.write(f"file '{w}'\n")
        if os.path.getsize(audio_concat) > 0:
            master_wav = os.path.join(out_dir, f"{output_prefix}_complete.wav")
            pcm_codec = "pcm_s24le" if int(bit_depth) == 24 else "pcm_s16le"
            subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", audio_concat, "-c:a", pcm_codec, "-ac", "1", master_wav], capture_output=True, check=True)
            if "wav" in fmts:
                artifacts["master_wav"] = master_wav
            if "mp3" in fmts:
                master_mp3 = os.path.join(out_dir, f"{output_prefix}_complete.mp3")
                subprocess.run(["ffmpeg", "-y", "-i", master_wav, "-c:a", "libmp3lame", "-b:a", mp3_bitrate, master_mp3], capture_output=True, check=True)
                artifacts["master_mp3"] = master_mp3
            if "wav" not in fmts and os.path.exists(master_wav):
                os.remove(master_wav)

    if slides_dir and os.path.isdir(slides_dir) and os.path.abspath(slides_dir) != os.path.abspath(os.path.join(out_dir, "slides_png")):
        shutil.copytree(slides_dir, os.path.join(out_dir, "slides_png"), dirs_exist_ok=True)
    if os.path.isdir(audio_wav_dir) and os.path.abspath(audio_wav_dir) != os.path.abspath(os.path.join(out_dir, "audio_slides_wav")):
        shutil.copytree(audio_wav_dir, os.path.join(out_dir, "audio_slides_wav"), dirs_exist_ok=True)
    if audio_mp3_dir and os.path.isdir(audio_mp3_dir) and os.path.abspath(audio_mp3_dir) != os.path.abspath(os.path.join(out_dir, "audio_slides_mp3")):
        shutil.copytree(audio_mp3_dir, os.path.join(out_dir, "audio_slides_mp3"), dirs_exist_ok=True)

    if zip_out_path:
        from datetime import datetime, timezone

        ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base_zip = zip_out_path[:-4] if zip_out_path.endswith(".zip") else zip_out_path
        ts_zip_path = f"{base_zip}_{ts_str}.zip"
        with zipfile.ZipFile(zip_out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(out_dir):
                for fn in sorted(files):
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.relpath(fp, os.path.dirname(out_dir)))
        shutil.copy2(zip_out_path, ts_zip_path)
        artifacts["zip_out"] = zip_out_path
        artifacts["timestamped_zip_out"] = ts_zip_path
    return artifacts
