# -*- coding: utf-8 -*-
"""HTML5 Studio Voice Audition player template renderer for synchronized audio review."""

from __future__ import annotations

import html
from typing import Any, Dict, List

from voice_studio.video.players_video import SHARED_PLAYER_CSS, fmt_mmss, safe_json_embed


def render_audition_player_html(
    total_slides: int,
    title: str,
    master_mp3: str,
    master_wav: str,
    slides_payload: List[Dict[str, Any]],
) -> str:
    """Renders standalone synchronized Studio Voice Audition HTML5 player."""
    total_dur = sum(float(s.get("total_duration_sec", 0.0)) for s in slides_payload)
    esc_title = html.escape(title)
    esc_mp3 = html.escape(master_mp3)
    esc_wav = html.escape(master_wav)
    slides_json = safe_json_embed(slides_payload)

    return f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc_title} — Voice Audition ({total_slides} segments)</title>
<style>{SHARED_PLAYER_CSS}
.container {{ max-width: 1480px; margin: 0 auto; padding: 18px 24px; display: grid; gap: 16px; }}
.master-panel {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; display: grid; gap: 10px; }}
.master-top {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; }}
.cards {{ display: grid; gap: 14px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px; display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 16px; }}
.card.playing {{ border-color: var(--cyan); background: #16243b; }}
@media (max-width: 900px) {{ .card {{ grid-template-columns: 1fr; }} }}
.thumb {{ width: 100%; aspect-ratio: 16 / 9; object-fit: cover; border-radius: 8px; border: 1px solid var(--border); background: #020617; }}
.card-head {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 8px; }}
.card-title {{ font-size: 1rem; font-weight: 700; color: var(--text); }}
.script-box {{ background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; font-size: 0.86rem; margin-top: 8px; }}
.script-label {{ font-size: 0.75rem; font-weight: 700; color: var(--cyan); margin-bottom: 3px; text-transform: uppercase; }}
.item-chips {{ display: grid; gap: 6px; margin-top: 8px; }}
.chip {{ background: var(--surface-alt); border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 0.82rem; }}
</style>
</head>
<body>
<header>
  <div class="brand">
    <h1>{esc_title} — Studio Voice Audition Player</h1>
    <p>ตรวจสอบบทพูด สัทศาสตร์ และจังหวะเวลาทั้ง {total_slides} ช่วงเสียง</p>
  </div>
  <div class="badges">
    <span class="badge">{total_slides} Segments</span>
    <span class="badge mono">{fmt_mmss(total_dur)} ({total_dur:.1f}s)</span>
    <label class="btn" style="display:inline-block;">
      เลือกไฟล์เสียงจากเครื่อง (.wav/.mp3)
      <input type="file" id="localAudioInput" multiple accept="audio/*,.wav,.mp3" style="display:none;">
    </label>
  </div>
</header>
<main class="container">
  <section class="master-panel">
    <div class="master-top">
      <div>
        <strong>Full Master Track Audition</strong>
        <div id="audioStatus" style="font-size:0.84rem;color:var(--muted);">
          พร้อมซิงก์ {total_slides} ช่วงเสียง (<span class="mono">{esc_mp3}</span> / <span class="mono">{esc_wav}</span>)
        </div>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;">
        <input type="search" id="searchInput" placeholder="ค้นหาช่วงเสียงหรือคำอ่าน...">
        <button type="button" class="btn btn-primary" id="playAllBtn">เล่นต่อเนื่องทั้งหมด</button>
        <button type="button" class="btn" id="stopBtn">หยุดเล่น</button>
      </div>
    </div>
    <audio id="masterAudio" controls preload="metadata" style="width:100%;">
      <source src="{esc_mp3}" type="audio/mpeg">
      <source src="{esc_wav}" type="audio/wav">
    </audio>
  </section>
  <section class="cards" id="cardsRoot"></section>
</main>
<script>
const SLIDES = {slides_json};
const masterAudio = document.getElementById("masterAudio");
const audioStatus = document.getElementById("audioStatus");
const cardsRoot = document.getElementById("cardsRoot");
const searchInput = document.getElementById("searchInput");
const localFileMap = new Map(); const slidePlayer = new Audio();
let sequenceIndex = -1; let sequenceActive = false;
function safeMediaUrl(u) {{ if (!u) return ""; return /^https?:|^blob:|^[A-Za-z0-9_.\\/\\-]+$/.test(String(u)) ? String(u) : ""; }}
function fmtTime(sec) {{ const s = Math.max(0, Math.floor(sec || 0)); return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0"); }}
function highlightCard(slideNo) {{ document.querySelectorAll(".card").forEach((c) => {{ c.classList.toggle("playing", Number(c.dataset.slideNo) === slideNo); }}); }}
async function playSlide(idx, autoContinue = false) {{
  if (idx < 0 || idx >= SLIDES.length) {{ sequenceActive = false; highlightCard(-1); return; }}
  sequenceIndex = idx; sequenceActive = autoContinue; const s = SLIDES[idx];
  highlightCard(s.slide_no); masterAudio.pause();
  const pad = String(s.slide_no).padStart(2, "0");
  const candidates = [localFileMap.get(`slide_${{pad}}.wav`), localFileMap.get(`slide_${{pad}}.mp3`), safeMediaUrl(s.audio_wav), safeMediaUrl(s.audio_mp3)].filter(Boolean);
  for (const url of candidates) {{
    try {{ slidePlayer.src = url; await slidePlayer.play(); audioStatus.textContent = `กำลังเล่นช่วงที่ ${{pad}} / ${{SLIDES.length}}: ${{s.raw_title}}`; return; }}
    catch (_err) {{ continue; }}
  }}
  if (masterAudio.readyState >= 1 || localFileMap.has("master")) {{
    slidePlayer.pause(); const seekEps = (s.global_start_sec > 0 && s.total_duration_sec > 0.06) ? 0.03 : 0;
    masterAudio.currentTime = s.global_start_sec + seekEps; masterAudio.play().catch(() => {{}});
    audioStatus.textContent = `ข้ามไปช่วงที่ ${{pad}} บน Master Track (${{fmtTime(s.global_start_sec)}})`; return;
  }}
  sequenceActive = false; audioStatus.textContent = `ยังไม่พบไฟล์ WAV/MP3 สำหรับช่วงที่ ${{pad}}`;
}}
slidePlayer.addEventListener("ended", () => {{ if (sequenceActive) playSlide(sequenceIndex + 1, true); else highlightCard(-1); }});
masterAudio.addEventListener("play", () => {{ sequenceActive = false; slidePlayer.pause(); }});
masterAudio.addEventListener("timeupdate", () => {{
  const t = masterAudio.currentTime;
  const found = SLIDES.find((s, i) => t >= s.global_start_sec && (t < s.global_end_sec || i === SLIDES.length - 1));
  if (found) highlightCard(found.slide_no);
}});
document.getElementById("playAllBtn").addEventListener("click", () => playSlide(0, true));
document.getElementById("stopBtn").addEventListener("click", () => {{ sequenceActive = false; slidePlayer.pause(); masterAudio.pause(); highlightCard(-1); }});
document.getElementById("localAudioInput").addEventListener("change", (ev) => {{
  const files = Array.from(ev.target.files || []);
  files.forEach((f) => {{
    if (localFileMap.has(f.name)) URL.revokeObjectURL(localFileMap.get(f.name));
    const url = URL.createObjectURL(f); localFileMap.set(f.name, url);
    if (f.name.includes("complete")) {{ localFileMap.set("master", url); masterAudio.src = url; masterAudio.load(); }}
  }});
  audioStatus.textContent = `นำเข้าไฟล์เสียงจากเครื่องแล้ว ${{files.length}} ไฟล์`;
}});
function renderCards(filterText = "") {{
  const q = filterText.trim().toLowerCase(); cardsRoot.innerHTML = "";
  SLIDES.forEach((s, idx) => {{
    const hay = `${{s.slide_no}} ${{s.raw_title}} ${{s.phonetic_title}} ${{s.script}} ${{s.spoken_phonetic}}`.toLowerCase();
    if (q && !hay.includes(q)) return;
    const card = document.createElement("article"); card.className = "card"; card.dataset.slideNo = String(s.slide_no);
    const left = document.createElement("div"); const img = document.createElement("img");
    img.className = "thumb"; img.loading = "lazy"; img.src = safeMediaUrl(s.slide_png); img.alt = `Segment ${{s.slide_no}}`; left.appendChild(img);
    const right = document.createElement("div"); const head = document.createElement("div"); head.className = "card-head";
    const titleEl = document.createElement("div"); titleEl.className = "card-title"; titleEl.textContent = `Segment ${{String(s.slide_no).padStart(2, "0")}}: ${{s.raw_title}}`;
    const actions = document.createElement("div"); actions.style.cssText = "display:flex;gap:8px;align-items:center;";
    const timeBadge = document.createElement("span"); timeBadge.className = "badge mono";
    timeBadge.textContent = `${{fmtTime(s.global_start_sec)}} - ${{fmtTime(s.global_end_sec)}} (${{s.total_duration_sec.toFixed(1)}}s)`;
    const playBtn = document.createElement("button"); playBtn.type = "button"; playBtn.className = "btn btn-primary";
    playBtn.textContent = `เล่นช่วงที่ ${{String(s.slide_no).padStart(2, "0")}}`; playBtn.addEventListener("click", () => playSlide(idx, false));
    actions.appendChild(timeBadge); actions.appendChild(playBtn); head.appendChild(titleEl); head.appendChild(actions);
    const origBox = document.createElement("div"); origBox.className = "script-box";
    const origLbl = document.createElement("div"); origLbl.className = "script-label"; origLbl.textContent = "บทบรรยายต้นฉบับ (Original Script)";
    const origBody = document.createElement("div"); origBody.textContent = s.script;
    origBox.appendChild(origLbl); origBox.appendChild(origBody);
    const phonBox = document.createElement("div"); phonBox.className = "script-box";
    const phonLbl = document.createElement("div"); phonLbl.className = "script-label"; phonLbl.textContent = "บทอ่านสัทศาสตร์สำหรับ TTS (Normalized Phonetic)";
    const phonBody = document.createElement("div"); phonBody.textContent = s.spoken_phonetic;
    phonBox.appendChild(phonLbl); phonBox.appendChild(phonBody);
    const chips = document.createElement("div"); chips.className = "item-chips";
    (s.audio_items || []).forEach((it, j) => {{
      const ch = document.createElement("div"); ch.className = "chip";
      ch.textContent = `[${{j + 1}}] (${{it.audio_start_sec.toFixed(2)}}s - ${{it.audio_end_sec.toFixed(2)}}s) ${{it.text}}`; chips.appendChild(ch);
    }});
    right.appendChild(head); right.appendChild(origBox); right.appendChild(phonBox); right.appendChild(chips);
    card.appendChild(left); card.appendChild(right); cardsRoot.appendChild(card);
  }});
}}
searchInput.addEventListener("input", () => renderCards(searchInput.value));
renderCards();
</script>
</body>
</html>
"""
