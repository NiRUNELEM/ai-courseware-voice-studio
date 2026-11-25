# -*- coding: utf-8 -*-
"""Generic Hardware-Accelerated Video & Deliverable Packager Orchestrator."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from PIL import Image, ImageDraw, ImageFont

from voice_studio.video.compositor import (
    DEFAULT_FPS,
    SLIDE_H,
    SLIDE_W,
    detect_best_video_encoder,
    encode_slide_segment,
    package_deliverables,
    prepare_stage_buffers,
    write_dry_run_preview,
)
from voice_studio.video.players_sync import sync_job_players


def _load_first_font(candidates: List[str], fc_pattern: str, size: int) -> Any:
    """Loads the first valid TrueType font from explicit paths or fontconfig."""
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError as exc:
                print(f"[render] Font load fallback for {path}: {exc}")
    fc_bin = shutil.which("fc-match")
    if fc_bin:
        res = subprocess.run([fc_bin, "-f", "%{file}", fc_pattern], capture_output=True, text=True, check=False)
        matched = res.stdout.strip()
        if matched and os.path.isfile(matched):
            try:
                return ImageFont.truetype(matched, size=size)
            except OSError as exc:
                print(f"[render] Font load fallback for {matched}: {exc}")
    return ImageFont.load_default()


def _resolve_bilingual_fonts(size: int, bold: bool = False) -> tuple[Any, Any]:
    """Resolves paired (thai_font, latin_font) from bundled assets or system fonts."""
    assets_fonts = Path(__file__).resolve().parents[3] / "assets" / "fonts"
    weight = "Bold" if bold else "Regular"
    thai_font = _load_first_font(
        [
            str(assets_fonts / f"NotoSansThai-{weight}.ttf"),
            f"/usr/share/fonts/noto/NotoSansThai-{weight}.ttf",
            f"/usr/share/fonts/truetype/noto/NotoSansThai-{weight}.ttf",
        ],
        f"Noto Sans Thai:style={weight}" if bold else "sans:lang=th",
        size,
    )
    latin_font = _load_first_font(
        [
            str(assets_fonts / f"NotoSans-{weight}.ttf"),
            f"/usr/share/fonts/noto/NotoSans-{weight}.ttf",
            f"/usr/share/fonts/truetype/noto/NotoSans-{weight}.ttf",
            f"/usr/share/fonts/TTF/DejaVuSans{'-Bold' if bold else ''}.ttf",
            f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        ],
        f"Noto Sans:style={weight}" if bold else "sans:lang=en",
        size,
    )
    return thai_font, latin_font


def _split_script_runs(text: str) -> List[tuple[bool, str]]:
    """Splits text into (is_thai, substring) runs so Thai and Latin glyphs never turn into tofu boxes."""
    if not text:
        return []
    runs: List[tuple[bool, str]] = []
    cur_thai = "\u0e00" <= text[0] <= "\u0e7f"
    buf: List[str] = [text[0]]
    for ch in text[1:]:
        is_th = "\u0e00" <= ch <= "\u0e7f"
        if is_th == cur_thai:
            buf.append(ch)
        else:
            runs.append((cur_thai, "".join(buf)))
            cur_thai = is_th
            buf = [ch]
    runs.append((cur_thai, "".join(buf)))
    return runs


def _measure_bilingual_width(draw: ImageDraw.ImageDraw, text: str, thai_font: Any, latin_font: Any) -> float:
    return sum(draw.textlength(seg, font=thai_font if is_th else latin_font) for is_th, seg in _split_script_runs(text))


def _draw_bilingual_text(
    draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, fill: tuple[int, int, int], fonts: tuple[Any, Any]
) -> None:
    thai_font, latin_font = fonts
    x, y = xy
    for is_th, seg in _split_script_runs(text):
        font = thai_font if is_th else latin_font
        draw.text((x, y), seg, fill=fill, font=font)
        x += draw.textlength(seg, font=font)


def _wrap_text(txt: str, width: int = 56, draw: Optional[ImageDraw.ImageDraw] = None, fonts: Optional[tuple[Any, Any]] = None, max_px: int = 1600) -> List[str]:
    words = (txt or "").split()
    lines: List[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip()
        fits = (
            _measure_bilingual_width(draw, cand, fonts[0], fonts[1]) <= max_px
            if (draw is not None and fonts is not None)
            else len(cand) <= width
        )
        if fits or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:8]


def _generate_fallback_slide_png(dst_path: Path, slide_no: int, title: str, bullets: Optional[List[str]] = None) -> None:
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (SLIDE_W, SLIDE_H), color=(15, 23, 42))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([72, 72, SLIDE_W - 72, SLIDE_H - 72], radius=24, fill=(30, 41, 59), outline=(56, 189, 248), width=3)
    f_badge = _resolve_bilingual_fonts(30, bold=True)
    f_title = _resolve_bilingual_fonts(48, bold=True)
    f_body = _resolve_bilingual_fonts(32, bold=False)
    _draw_bilingual_text(draw, (130, 128), f"SEGMENT {slide_no:02d}", (56, 189, 248), f_badge)
    y = 200
    for ln in _wrap_text(title, width=48, draw=draw, fonts=f_title, max_px=1640)[:3]:
        _draw_bilingual_text(draw, (130, y), ln, (248, 250, 252), f_title)
        y += 66
    y += 28
    for b in (bullets or [])[:4]:
        for idx, bl in enumerate(_wrap_text(str(b), width=62, draw=draw, fonts=f_body, max_px=1600)[:2]):
            prefix = "• " if idx == 0 else "  "
            _draw_bilingual_text(draw, (140, y), f"{prefix}{bl}", (203, 213, 225), f_body)
            y += 46
        y += 14
    img.save(dst_path)


def _prepare_pptx_slides(pptx_path: str, out_dir: Path) -> str:
    """Exports slides from a PPTX deck via LibreOffice+pdftoppm or python-pptx text cards when needed."""
    src = Path(pptx_path)
    if not src.is_file():
        return ""
    out_dir.mkdir(parents=True, exist_ok=True)
    if list(out_dir.glob("slide_*.png")):
        return str(out_dir)
    soffice, pdftoppm = shutil.which("soffice"), shutil.which("pdftoppm")
    if soffice and pdftoppm:
        pdf_path = out_dir / f"{src.stem}.pdf"
        res = subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(src)], capture_output=True, text=True, check=False)
        if res.returncode == 0 and pdf_path.is_file():
            subprocess.run([pdftoppm, "-png", "-r", "150", str(pdf_path), str(out_dir / "_page")], capture_output=True, text=True, check=False)
            for idx, page_png in enumerate(sorted(out_dir.glob("_page-*.png")), start=1):
                page_png.rename(out_dir / f"slide_{idx:02d}.png")
            if list(out_dir.glob("slide_*.png")):
                return str(out_dir)
    try:
        from pptx import Presentation

        prs = Presentation(str(src))
        for idx, sl in enumerate(prs.slides, start=1):
            texts = [sh.text_frame.text.strip() for sh in sl.shapes if getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip()]
            s_title = texts[0] if texts else f"Slide {idx}"
            _generate_fallback_slide_png(out_dir / f"slide_{idx:02d}.png", idx, s_title, bullets=texts[1:5])
        return str(out_dir)
    except Exception as exc:
        print(f"[render] PPTX slide fallback skipped ({src.name}): {exc}")
        return ""


def render_job_video(
    job_dir: Path,
    prepared_slides: List[Dict[str, Any]],
    timeline: Dict[str, Any],
    slides_png_dir: str = "",
    pptx_path: str = "",
    dry_run: bool = False,
    workers: int = 0,
    title: str = "Voice Studio Production",
    output_prefix: str = "studio",
    output_formats: Iterable[str] = ("wav", "mp3", "mp4"),
    fps: int = DEFAULT_FPS,
    aac_bitrate: str = "192k",
    mp3_bitrate: str = "192k",
    nvenc_preset: str = "p4",
    nvenc_cq: int = 21,
    bit_depth: int = 16,
) -> Dict[str, Any]:
    """Renders requested WAV/MP3/1080p MP4 deliverables & HTML5 players cleanly under job_dir/deliverables."""
    t0 = time.perf_counter()
    deliv_dir = job_dir / "deliverables"
    segs_dir = job_dir / "video_segments_1080p"
    temp_dir = job_dir / "logs" / "ffmpeg"
    slides_dst_dir = deliv_dir / "slides_png"
    audio_wav_dir = job_dir / "audio_slides_wav"
    audio_mp3_dir = job_dir / "audio_slides_mp3"
    for d in (deliv_dir, segs_dir, temp_dir, slides_dst_dir):
        d.mkdir(parents=True, exist_ok=True)

    fmts = {str(f).lower() for f in output_formats}
    want_mp4 = "mp4" in fmts
    effective_png_dir = slides_png_dir
    if (not effective_png_dir or not Path(effective_png_dir).is_dir()) and pptx_path:
        effective_png_dir = _prepare_pptx_slides(pptx_path, job_dir / "work" / "pptx_png")
    slide_numbers = [int(sl["slide_no"]) for sl in prepared_slides]
    for sl in prepared_slides:
        s_no = int(sl["slide_no"])
        dst_png = slides_dst_dir / f"slide_{s_no:02d}.png"
        src_png = Path(effective_png_dir) / f"slide_{s_no:02d}.png" if effective_png_dir else None
        if src_png and src_png.is_file():
            shutil.copy2(src_png, dst_png)
        elif not dst_png.is_file():
            _generate_fallback_slide_png(
                dst_png,
                s_no,
                str(sl.get("raw_title") or sl.get("title") or f"Segment {s_no}"),
                bullets=list(sl.get("bilingual_item_scripts") or []),
            )

    has_ffmpeg = shutil.which("ffmpeg") is not None
    is_dry_run = dry_run or not has_ffmpeg
    rendered_segments: List[str] = []
    enc_cfg: Dict[str, Any] = {"codec": "audio-only" if not want_mp4 else "dry-run", "workers": min(4, os.cpu_count() or 2)}

    if want_mp4:
        if not is_dry_run:
            enc_cfg = detect_best_video_encoder()
        n_workers = max(1, workers or int(enc_cfg.get("workers", 2)))

        def _render_slide(sl: Dict[str, Any]) -> Optional[tuple[int, str]]:
            s_no = int(sl["slide_no"])
            t_info = timeline.get(str(s_no), {})
            dur = float(t_info.get("total_duration_sec", 0.0))
            if dur <= 0.0:
                return None
            items = list(t_info.get("items", []))
            base_img = Image.open(slides_dst_dir / f"slide_{s_no:02d}.png").convert("RGB")
            if base_img.size != (SLIDE_W, SLIDE_H):
                base_img = base_img.resize((SLIDE_W, SLIDE_H), Image.Resampling.LANCZOS)
            apply_prog = (s_no != 1) and len(items) > 0
            buffers = prepare_stage_buffers(base_img, items, apply_prog)
            if is_dry_run:
                write_dry_run_preview(s_no, len(slide_numbers), apply_prog, base_img, items, buffers, str(deliv_dir))
                return None
            seg_mp4 = str(segs_dir / f"seg_{s_no:02d}.mp4")
            s_wav = str(audio_wav_dir / f"slide_{s_no:02d}.wav")
            encode_slide_segment(
                s_no,
                s_wav,
                seg_mp4,
                str(temp_dir),
                max(1, int(round(dur * fps))),
                apply_prog,
                items,
                buffers,
                encoder_cfg=enc_cfg,
                fps=fps,
                aac_bitrate=aac_bitrate,
                nvenc_preset=nvenc_preset,
                nvenc_cq=nvenc_cq,
            )
            return (s_no, seg_mp4)

        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            results = [r for r in pool.map(_render_slide, prepared_slides) if r is not None]
        rendered_segments = [p for _, p in sorted(results, key=lambda x: x[0])]

    payload = [
        {
            "slide_no": int(sl["slide_no"]),
            "raw_title": sl.get("raw_title", sl.get("title", "")),
            "phonetic_title": sl.get("title", ""),
            "script": sl.get("script", ""),
            "spoken_phonetic": sl.get("spoken_phonetic", ""),
            "raw_items": sl.get("bilingual_item_scripts", []),
            "total_duration_sec": float(timeline.get(str(sl["slide_no"]), {}).get("total_duration_sec", 0.0)),
            "global_start_sec": float(timeline.get(str(sl["slide_no"]), {}).get("global_start_sec", 0.0)),
            "global_end_sec": float(timeline.get(str(sl["slide_no"]), {}).get("global_end_sec", 0.0)),
            "slide_png": f"slides_png/slide_{int(sl['slide_no']):02d}.png",
            "audio_wav": f"audio_slides_wav/slide_{int(sl['slide_no']):02d}.wav",
            "audio_mp3": f"audio_slides_mp3/slide_{int(sl['slide_no']):02d}.mp3",
            "num_reveals": int(timeline.get(str(sl["slide_no"]), {}).get("num_reveals", 0)),
            "alignment_strategy": str(timeline.get(str(sl["slide_no"]), {}).get("alignment_strategy", "static_no_reveals")),
            "audio_items": list(timeline.get(str(sl["slide_no"]), {}).get("audio_items", [])),
        }
        for sl in prepared_slides
    ]
    player_paths = sync_job_players(deliv_dir, title=title, slides_payload=payload, output_prefix=output_prefix, fps=fps)

    # Copy transcript files into deliverables if ASR step ran
    tr_dir = job_dir / "transcript"
    if tr_dir.is_dir():
        for ext in ("txt", "srt", "json"):
            src_tr = tr_dir / f"transcript.{ext}"
            if src_tr.is_file():
                shutil.copy2(src_tr, deliv_dir / src_tr.name)

    artifacts: Dict[str, Any] = {
        "deliverables_dir": str(deliv_dir),
        "codec": enc_cfg.get("codec"),
        "players": [str(p) for p in player_paths],
        "elapsed_sec": round(time.perf_counter() - t0, 2),
    }
    if not is_dry_run:
        zip_path = str(job_dir / f"{job_dir.name}_deliverables.zip")
        packaged = package_deliverables(
            output_prefix=output_prefix,
            temp_dir=str(temp_dir),
            out_dir=str(deliv_dir),
            slides_dir=str(slides_dst_dir),
            audio_wav_dir=str(audio_wav_dir),
            audio_mp3_dir=str(audio_mp3_dir) if audio_mp3_dir.is_dir() else None,
            slide_numbers=slide_numbers,
            rendered_segments=rendered_segments,
            output_formats=fmts,
            zip_out_path=zip_path,
            mp3_bitrate=mp3_bitrate,
            bit_depth=bit_depth,
        )
        artifacts.update(packaged)
    return artifacts
