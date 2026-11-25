# -*- coding: utf-8 -*-
"""HTML5 Studio Video Player template renderer and shared CSS/escaping helpers."""

from __future__ import annotations

import html
import json
from typing import Any, Dict, List

SHARED_PLAYER_CSS = """
:root {
  --bg: #0f172a; --surface: #1e293b; --surface-alt: #162032; --border: #334155;
  --cyan: #38bdf8; --cyan-dim: rgba(56, 189, 248, 0.14);
  --green: #34d399; --amber: #fbbf24; --text: #f8fafc; --muted: #94a3b8;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Prompt", "Sarabun", system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); line-height: 1.55; }
header { background: #0b1220; border-bottom: 1px solid var(--border); padding: 16px 24px; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; }
.brand h1 { font-size: 1.15rem; font-weight: 700; color: var(--text); }
.brand p { font-size: 0.85rem; color: var(--muted); margin-top: 2px; }
.badges { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.badge { background: var(--surface); border: 1px solid var(--border); color: var(--cyan); padding: 4px 10px; border-radius: 6px; font-size: 0.78rem; font-weight: 600; }
.btn { background: var(--surface); color: var(--text); border: 1px solid var(--border); padding: 7px 13px; border-radius: 8px; font-size: 0.84rem; font-weight: 600; cursor: pointer; transition: border-color 0.15s, background 0.15s; }
.btn:hover { border-color: var(--cyan); background: #26354d; }
.btn-primary { background: #0284c7; border-color: var(--cyan); color: #ffffff; }
.btn-primary:hover { background: #0369a1; }
input[type="text"], input[type="search"] { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 8px; padding: 7px 12px; font-size: 0.86rem; }
.mono { font-family: "Consolas", ui-monospace, monospace; }
"""


def safe_json_embed(payload: Any) -> str:
    """Safely embeds JSON inside <script> tags without script breakout or HTML comment tricks."""
    raw = json.dumps(payload, ensure_ascii=False)
    return (
        raw.replace("</", "<\\/")
        .replace("<!--", "<\\!--")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def fmt_mmss(seconds: float) -> str:
    """Formats seconds as MM:SS string."""
    sec_int = max(0, int(round(seconds)))
    return f"{sec_int // 60:02d}:{sec_int % 60:02d}"


def render_video_player_html(
    total_slides: int,
    title: str,
    mp4_filename: str,
    slides_payload: List[Dict[str, Any]],
    fps: int = 60,
) -> str:
    """Renders standalone synchronized interactive HTML5 Studio Video Player."""
    total_dur = sum(float(s.get("total_duration_sec", 0.0)) for s in slides_payload)
    esc_title = html.escape(title)
    esc_mp4 = html.escape(mp4_filename)
    slides_json = safe_json_embed(slides_payload)

    return f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc_title} — Video Player ({total_slides} segments)</title>
<style>{SHARED_PLAYER_CSS}
.layout {{ display: grid; grid-template-columns: minmax(0, 1fr) 390px; gap: 18px; padding: 18px 24px; max-width: 1680px; margin: 0 auto; }}
@media (max-width: 1080px) {{ .layout {{ grid-template-columns: 1fr; }} }}
.stage {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }}
.video-wrap {{ position: relative; width: 100%; aspect-ratio: 16 / 9; background: #020617; }}
video, .preview-img {{ width: 100%; height: 100%; object-fit: contain; display: block; }}
.preview-img {{ display: none; }}
.video-wrap.preview-mode video {{ display: none; }}
.video-wrap.preview-mode .preview-img {{ display: block; }}
.toolbar {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 10px; padding: 12px 16px; background: var(--surface-alt); border-top: 1px solid var(--border); }}
.status-bar {{ padding: 10px 16px; font-size: 0.84rem; color: var(--amber); background: rgba(251, 191, 36, 0.08); border-top: 1px solid var(--border); display: none; }}
.details {{ padding: 16px; border-top: 1px solid var(--border); }}
.details h2 {{ font-size: 1.05rem; color: var(--cyan); margin-bottom: 6px; }}
.details .sub {{ font-size: 0.85rem; color: var(--muted); margin-bottom: 12px; }}
.item-list {{ display: grid; gap: 8px; margin-top: 10px; }}
.item-box {{ background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; font-size: 0.86rem; }}
.item-meta {{ color: var(--cyan); font-size: 0.78rem; margin-bottom: 4px; }}
.sidebar {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; display: flex; flex-direction: column; max-height: calc(100vh - 110px); }}
.sidebar-head {{ padding: 12px 14px; border-bottom: 1px solid var(--border); display: grid; gap: 8px; }}
.slide-list {{ overflow-y: auto; padding: 8px; display: grid; gap: 6px; }}
.slide-btn {{ width: 100%; text-align: left; background: var(--surface-alt); border: 1px solid var(--border); border-radius: 8px; padding: 9px 11px; color: var(--text); cursor: pointer; display: grid; gap: 3px; }}
.slide-btn:hover {{ border-color: var(--cyan); }}
.slide-btn.active {{ background: var(--cyan-dim); border-color: var(--cyan); }}
.slide-btn-top {{ display: flex; justify-content: space-between; font-size: 0.78rem; color: var(--cyan); font-weight: 600; }}
.slide-btn-title {{ font-size: 0.85rem; color: var(--text); }}
</style>
</head>
<body>
<header>
  <div class="brand">
    <h1>{esc_title} — Studio Video Player</h1>
    <p>1080p @ {fps}fps | {total_slides} segments (<span class="mono">{esc_mp4}</span>)</p>
  </div>
  <div class="badges">
    <span class="badge">{total_slides} Segments</span>
    <span class="badge mono">{fmt_mmss(total_dur)} ({total_dur:.1f}s)</span>
    <label class="btn" style="display:inline-block;">
      เลือกไฟล์ MP4 จากเครื่อง
      <input type="file" id="mp4FileInput" accept="video/mp4,.mp4" style="display:none;">
    </label>
    <button type="button" class="btn" id="togglePreviewBtn">โหมดภาพสไลด์นิ่ง</button>
  </div>
</header>
<main class="layout">
  <section class="stage">
    <div class="video-wrap" id="videoWrap">
      <video id="masterVideo" controls preload="metadata" poster="slides_png/slide_01.png" src="{esc_mp4}"></video>
      <img id="previewImg" class="preview-img" src="slides_png/slide_01.png" alt="Segment 01 Preview">
    </div>
    <div class="status-bar" id="videoStatus">
      ยังไม่พบไฟล์ <span class="mono">{esc_mp4}</span> ในโฟลเดอร์นี้ (กำลังแสดงภาพสไลด์ PNG แบบโต้ตอบ หรือกดเลือกไฟล์ MP4 จากเครื่องได้ทันที)
    </div>
    <div class="toolbar">
      <div style="display:flex;gap:8px;align-items:center;">
        <button type="button" class="btn" id="prevBtn">ก่อนหน้า</button>
        <button type="button" class="btn btn-primary" id="nextBtn">ถัดไป</button>
        <span id="currentLabel" class="badge mono">01 / {total_slides:02d}</span>
      </div>
      <div style="display:flex;gap:6px;align-items:center;">
        <span style="font-size:0.82rem;color:var(--muted);">ความเร็ว:</span>
        <button type="button" class="btn speed-btn" data-rate="0.75">0.75x</button>
        <button type="button" class="btn speed-btn btn-primary" data-rate="1">1.0x</button>
        <button type="button" class="btn speed-btn" data-rate="1.25">1.25x</button>
        <button type="button" class="btn speed-btn" data-rate="1.5">1.5x</button>
      </div>
    </div>
    <div class="details">
      <h2 id="detailTitle"></h2>
      <div class="sub" id="detailMeta"></div>
      <div id="detailItems" class="item-list"></div>
    </div>
  </section>
  <aside class="sidebar">
    <div class="sidebar-head">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <strong style="font-size:0.95rem;">รายการทั้งหมด ({total_slides} ช่วง)</strong>
        <span class="mono" style="font-size:0.78rem;color:var(--muted);">คลิกเพื่อข้ามช่วงเวลา</span>
      </div>
      <input type="search" id="searchInput" placeholder="ค้นหาชื่อช่วงหรือคำอ่าน...">
    </div>
    <div class="slide-list" id="slideList"></div>
  </aside>
</main>
<script>
const SLIDES = {slides_json};
const video = document.getElementById("masterVideo");
const videoWrap = document.getElementById("videoWrap");
const previewImg = document.getElementById("previewImg");
const videoStatus = document.getElementById("videoStatus");
const slideList = document.getElementById("slideList");
const detailTitle = document.getElementById("detailTitle");
const detailMeta = document.getElementById("detailMeta");
const detailItems = document.getElementById("detailItems");
const currentLabel = document.getElementById("currentLabel");
const searchInput = document.getElementById("searchInput");
let activeIdx = 0; let manualPreview = false; let manualVideoUrl = null;
function safeMediaUrl(u) {{ if (!u) return ""; return /^https?:|^blob:|^[A-Za-z0-9_.\\/\\-]+$/.test(String(u)) ? String(u) : ""; }}
function fmtTime(sec) {{ const s = Math.max(0, Math.floor(sec || 0)); return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); }}
function renderSidebar(filterText = "") {{
  const q = filterText.trim().toLowerCase(); slideList.innerHTML = "";
  SLIDES.forEach((s, idx) => {{
    const hay = `${{s.slide_no}} ${{s.raw_title}} ${{s.phonetic_title}} ${{s.script}}`.toLowerCase();
    if (q && !hay.includes(q)) return;
    const btn = document.createElement("button"); btn.type = "button";
    btn.className = "slide-btn" + (idx === activeIdx ? " active" : ""); btn.dataset.idx = String(idx);
    const topDiv = document.createElement("div"); topDiv.className = "slide-btn-top";
    const leftSp = document.createElement("span"); leftSp.textContent = `Segment ${{String(s.slide_no).padStart(2, "0")}}`;
    const rightSp = document.createElement("span"); rightSp.className = "mono"; rightSp.textContent = `${{fmtTime(s.global_start_sec)}} - ${{fmtTime(s.global_end_sec)}} (${{s.total_duration_sec.toFixed(1)}}s)`;
    topDiv.appendChild(leftSp); topDiv.appendChild(rightSp);
    const titleDiv = document.createElement("div"); titleDiv.className = "slide-btn-title"; titleDiv.textContent = s.raw_title;
    btn.appendChild(topDiv); btn.appendChild(titleDiv);
    btn.addEventListener("click", () => selectSlide(idx, true)); slideList.appendChild(btn);
  }});
}}
function selectSlide(idx, seekVideo) {{
  if (idx < 0 || idx >= SLIDES.length) return;
  activeIdx = idx; const s = SLIDES[idx];
  currentLabel.textContent = `${{String(s.slide_no).padStart(2, "0")}} / ${{String(SLIDES.length).padStart(2, "0")}}`;
  previewImg.src = safeMediaUrl(s.slide_png); previewImg.alt = `Segment ${{s.slide_no}}`;
  detailTitle.textContent = `ช่วงที่ ${{s.slide_no}}: ${{s.raw_title}}`;
  detailMeta.textContent = `ช่วงเวลา ${{fmtTime(s.global_start_sec)}} - ${{fmtTime(s.global_end_sec)}} | ความยาว ${{s.total_duration_sec.toFixed(2)}} วินาที | Reveals: ${{s.num_reveals}} | Strategy: ${{s.alignment_strategy}}`;
  detailItems.innerHTML = "";
  (s.audio_items || []).forEach((it, j) => {{
    const box = document.createElement("div"); box.className = "item-box";
    const meta = document.createElement("div"); meta.className = "item-meta mono";
    meta.textContent = `ส่วนที่ ${{j + 1}} | ${{it.audio_start_sec.toFixed(2)}}s - ${{it.audio_end_sec.toFixed(2)}}s (${{it.duration_sec.toFixed(2)}}s)`;
    const txt = document.createElement("div"); txt.textContent = (s.raw_items && s.raw_items[j]) ? s.raw_items[j] : it.text;
    box.appendChild(meta); box.appendChild(txt); detailItems.appendChild(box);
  }});
  document.querySelectorAll(".slide-btn").forEach((el) => {{ el.classList.toggle("active", Number(el.dataset.idx) === idx); }});
  if (seekVideo && !videoWrap.classList.contains("preview-mode") && video.readyState >= 1) {{
    const seekEpsilon = (s.global_start_sec > 0 && s.total_duration_sec > 0.06) ? 0.03 : 0;
    video.currentTime = s.global_start_sec + seekEpsilon; video.play().catch(() => {{}});
  }}
}}
video.addEventListener("error", () => {{ videoWrap.classList.add("preview-mode"); videoStatus.style.display = "block"; }}, true);
video.addEventListener("loadedmetadata", () => {{ if (!manualPreview) videoWrap.classList.remove("preview-mode"); videoStatus.style.display = "none"; }});
video.addEventListener("timeupdate", () => {{
  const t = video.currentTime;
  const idx = SLIDES.findIndex((s, i) => t >= s.global_start_sec && (t < s.global_end_sec || i === SLIDES.length - 1));
  if (idx !== -1 && idx !== activeIdx) selectSlide(idx, false);
}});
document.getElementById("prevBtn").addEventListener("click", () => selectSlide(Math.max(0, activeIdx - 1), true));
document.getElementById("nextBtn").addEventListener("click", () => selectSlide(Math.min(SLIDES.length - 1, activeIdx + 1), true));
document.getElementById("togglePreviewBtn").addEventListener("click", (ev) => {{
  manualPreview = !manualPreview; videoWrap.classList.toggle("preview-mode", manualPreview);
  ev.target.textContent = manualPreview ? "โหมดเล่นวิดีโอ MP4" : "โหมดภาพสไลด์นิ่ง";
}});
document.querySelectorAll(".speed-btn").forEach((b) => b.addEventListener("click", () => {{
  video.playbackRate = Number(b.dataset.rate);
  document.querySelectorAll(".speed-btn").forEach((x) => x.classList.toggle("btn-primary", x === b));
}}));
document.getElementById("mp4FileInput").addEventListener("change", (ev) => {{
  const file = ev.target.files && ev.target.files[0]; if (!file) return;
  if (manualVideoUrl) URL.revokeObjectURL(manualVideoUrl);
  manualVideoUrl = URL.createObjectURL(file); video.src = manualVideoUrl; video.load();
  manualPreview = false; videoWrap.classList.remove("preview-mode"); videoStatus.style.display = "none";
}});
searchInput.addEventListener("input", () => renderSidebar(searchInput.value));
renderSidebar(); selectSlide(0, false);
</script>
</body>
</html>
"""
