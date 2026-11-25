---
name: voice-studio-production
license: MIT
description: |
  Unified Voice Studio Production, MCP/Bridge Orchestration, Model Discovery & Thai TTS Prosody Skill.
  Follows the user's own materials, chosen style, and production plan.
  Always inspects local hardware first, asks for Voice Model (including upstream model search on Hugging Face/GitHub),
  and asks Local vs Google Colab (CU) separately for Voice Synthesis and Video Rendering via CLI or MCP bridges.
---

# Unified Voice Studio Production, MCP/Bridge & Thai Prosody Skill

Skill ชุดเดียวรวมครบตั้งแต่การตรวจทรัพยากรเครื่อง, การเชื่อมต่อ **MCP / CLI / Colab Bridge**, การค้นหาและเลือกโมเดลเสียงจากแหล่งต้นทาง, มาตรฐานคำอ่านและวรรณยุกต์ภาษาไทย, การสังเคราะห์เสียง/กันเสียงสั่น, การตัดต่อวิดีโอ และการตรวจสอบคุณภาพ โดย **ยึดตามเนื้อหา สไตล์ และแผนการผลิตที่ผู้ใช้เตรียมมาเอง**

---

## 1. Prerequisites & Tooling Interfaces (CLI, MCP Server & Bridges)

ก่อนเริ่มงาน AI Agent ต้องตรวจสภาพแวดล้อมด้วย `python voice-studio/studio.py doctor` (หรือ MCP tool `studio_doctor`):

| ช่องทางการสั่งงาน | วิธีใช้งาน | เหมาะสำหรับ |
|---|---|---|
| **CLI (`studio.py`)** | `python voice-studio/studio.py <doctor\|voices\|input\|run\|transcribe\|models\|bundle\|verify>` | สั่งรันตรงผ่าน Terminal หรือ CI/CD |
| **Voice Studio MCP Server** | ตั้งค่าจาก `voice-studio/configs/mcp_config.example.json` (`studio.py mcp`) เรียกใช้ `studio_doctor`, `studio_voices`, `studio_models_verify`, `studio_run`, `studio_status`, `studio_transcribe`, `studio_colab_*` | ให้ AI Coding Agent สั่งงานผ่าน MCP Protocol โดยตรง |
| **Colab Bridges** | เลือกผ่าน `--target`: `colab_cli` (แนะนำ — รองรับ GPU Auto-Fallback `L4 -> T4 -> A100` เมื่อติด 503), `colab_drive` (Google Drive queue), `colab_agent` (HTTP agent) หรือ `colab-mcp` | รันงานหนักบน Google Colab Pro (ใช้ Compute Units) |

### ข้อกำหนดขั้นต่ำ
- **พื้นฐาน:** `Python 3.11+` + `voice-studio/requirements.txt`, `ffmpeg` & `ffprobe` (`libsoxr`)
- **Local Direct TTS / ตัดต่อ:** RAM `>= 4 GB`, อินเทอร์เน็ตสำหรับ `edge-tts`
- **Local Neural TTS + RVC v2:** NVIDIA GPU CUDA (`>= 8 GB VRAM`, `sm_75+`), RAM `>= 16 GB`, พื้นที่ว่าง `>= 10 GB`
- **Google Colab (`colab_cli`):** บัญชี Google Colab (CU) + ลงชื่อเข้าใช้ `colab-cli` แล้ว (`colab status`)

---

## 2. Interactive Pre-Run Interview (ต้องถามผู้ใช้ให้ครบก่อนรันเสมอ)

รายงานผลจาก `studio.py doctor` ก่อน แล้วถามผู้ใช้ครบ 4 ข้อ (ห้ามเดาหรือใช้ Colab CU โดยไม่ได้รับการยืนยัน):

1. **เลือกหรือค้นหาโมเดลเสียง (Voice Model Selection & Upstream Discovery):**
   - **ใช้โปรไฟล์ที่มีอยู่:** `direct_tts` (CPU Local), `omnivoice_thai` (`OmniVoice-Thai + Vocos` หรือโคลนจาก `--ref-audio` `5–10s`), `gawr_gura_hybrid` / `gawr_gura` (RVC v2 + F0 Stabilizer)
   - **ใช้โมเดลเสียงของตนเอง:** ระบุไฟล์ `.pth` + `.index` หรือ Hugging Face `repo_id` ผ่าน `voice-studio/configs/custom_voice.example.yaml` พร้อมกำหนด `pitch_shift`
   - **ค้นหาโมเดลใหม่จากแหล่งต้นทาง (Model Discovery):** หากผู้ใช้สั่ง *"ค้นหาโมเดลเสียง/TTS จาก Hugging Face หรือ GitHub"* ให้ AI ค้นหาจากแหล่งต้นทาง (`huggingface.co`, `github.com`), สรุปชื่อโมเดล + URL + **สัญญาอนุญาต (License)** ให้ผู้ใช้เลือกก่อนดาวน์โหลด, นำมาตั้งค่าใน `custom_voice.yaml` และตรวจความสมบูรณ์ด้วย `studio.py models verify`
2. **เลือกเครื่องสำหรับ "การสร้างเสียง":** Local (เมื่อเครื่องรองรับ, 0 CU) หรือ Google Colab (`--target colab_cli`, ใช้ CU, เลือก GPU `L4`/`T4`/`A100`)
3. **เลือกเครื่องสำหรับ "การตัดต่อและเรนเดอร์วิดีโอ":** Local FFmpeg, Google Colab NVENC (`1080p60`), หรือ Audio Only (`.wav`/`.mp3`)
4. **ยืนยันรูปแบบเอาต์พุต โหมดงาน และขั้นสุ่มทดสอบ (Pre-Production Spot-Test):**
   - เลือกไฟล์ที่ต้องการ (`wav`, `mp3`, `mp4`) และ `resume` หรือ `--no-resume`
   - ถามก่อนเสมอว่าจะ **รันสุ่มทดสอบ 1–3 รายการก่อน** เพื่อฟังเสียง/ตรวจภาพ หรือจะ **รันงานจริงเต็มชุดทันที** (และถามย้ำทุกครั้งเมื่อมีการเปลี่ยนโมเดลเสียง ค่าจูน DSP หรือสไตล์สื่อ)

---

## 3. Thai TTS Prosody & Spoken Tone-Mark Standard

- **โหมดคำอ่านไทย 100% (`text_mode: "phonetic_spaced"`, `zero_latin_phonetic: true`):** แปลงคำภาษาอังกฤษและตัวเลขดิบทั้งหมดก่อนส่งเข้า TTS ภาษาไทย (รหัส/พอร์ต `200` -> `สอง ศูนย์ ศูนย์`, ทศนิยม `1.4` -> `หนึ่งจุดสี่`, จำนวนนับเชื่อมคำลักษณะนาม `สิบห้านาที`)
- **จังหวะหายใจ (Royal Society -> TTS):** ใช้ `. ` หลังคำลงท้าย (`ค่ะ.`, `นะคะ.`), `, ` คั่นอนุประโยคและคำทับศัพท์ที่เรียงติดกัน (`[เว้นจังหวะสั้น]`), และ `... ` สำหรับหยุดยาว (`[เว้นจังหวะยาว]`)
- **วรรณยุกต์ภาษาพูด (ห้ามใช้รูปตัดวรรณยุกต์แบบทางการส่งเข้า TTS):** เติมวรรณยุกต์ให้ตรงเสียงพูดจริงใน `phonetic_vocab.json` เช่น `Ready -> เรดี้`, `Policy -> พอลิซี่`, `Cookie -> คุกกี้`, `Body -> บอดี้`, `Security -> ซีเคียวริตี้`, `Identity -> ไอเดนทิตี้`, `Proxy -> พร็อกซี่`, `Query -> คิวรี่`, `Family -> แฟมิลี่`, `Healthy -> เฮลตี้`, `Token -> โทเค็น`, `Duties -> ดิวตี้ส์` และเลี่ยงตัวสะกดซ้อน `-์ด`
- **ตรวจก่อนเรนเดอร์:** รัน `studio.py input check <script>` (เชื่อม `extra_vocab_path` ทั้งในสคริปต์และ job YAML) และตรวจ Regex บนผลลัพธ์สุดท้าย (`tts_items` ใน `*_slides_phonetic.json`) ยืนยัน `LATIN == 0`, `DIGITS == 0`, และ `TONE_HITS == 0` ครบ 100% ก่อนส่งเข้า TTS ทุกครั้ง

---

## 4. Voice Model Tuning Playbook, Rendering & Empirical QA Gates

### คู่มือการจูนโมเดลเสียงและการแก้ปัญหาเสียง (ใช้ได้กับทุกโมเดล RVC v2 / TTS ที่นำมาจูน)

| อาการที่พบ | สาเหตุหลัก (Root Cause) | แนวทางปรับจูนที่ต้องทำ |
|---|---|---|
| **เสียงสั่น / เกร็ง (Pitch Warble)** | RMVPE F0 jitter ระดับ 10ms หรือ `volume_envelope` ต่ำเกินไป | เปิด `smooth_voiced_f0()` (3-frame median + Hann), ใช้ `f0_method: rmvpe`, ตั้ง `volume_envelope: 0.75–0.85` |
| **เสียงแหบ / ก้องกร้าน (Hoarse / Vocal Fry)** | `pitch_shift` ต่ำกว่าย่านเสียงธรรมชาติของโมเดล หรือ `protect` สูงเกินไปจนลมหายใจต้นทางรั่ว, หรือ `declick` ทำลายฮาร์มอนิก | ปรับ `pitch_shift` ให้เข้าย่าน F0 ของโมเดล (เช่น `+1` ถึง `+2` สำหรับเสียงหญิงโทนใส), ลด `protect` ลงเหลือ `0.38–0.42`, ปิด `declick: False` บน RVC output |
| **อาการสะอึก / เสียงขาดกลางคำ (Hiccup / Chop)** | ใช้ Hard Noise Gate ซ้อนหลายชั้น (`ratio >= 10`, `release <= 65ms`) หรือ `gate_threshold` สูงเกินไปจนตัดพยัญชนะเบา/ท้ายคำไทย | ใช้ Gentle Expander Gate ชั้นเดียว (`ratio=3.0`, `range=0.01`, `release=180–200ms`, `threshold=0.002–0.0025`) และใช้หน้าต่าง RMS `25ms` + ramp `35ms` ก่อนเข้า RVC |
| **เสียงแตก / บาดหู (Clipping / Harshness)** | ดัน gain เกิน `0 dBFS` ก่อนเข้า Limiter หรือบูสต์ EQ ย่าน `2.4 kHz` สูงเกินไป | คุม Pre-Limiter Headroom `<= -1.8 dBFS`, ลด EQ `2.4 kHz` ไม่เกิน `+1.2 dB`, ตั้ง `alimiter` ที่ `-1.8 dB`, และเปิด `rvc_fp32: true` |

### มาตรฐานการประมวลผลและ QA Gates
- **Gain-Staging & Broadcast Mastering:** Stage 1 -> Stage 1.5 (`Vocos` + Soft Comp `1.5:1` + Gentle Gate + Pre-Limiter `-3.5 dB`) -> Stage 2 (`RVC v2 FP32` + `smooth_voiced_f0`) -> Stage 3 (`-18.5 LUFS`, Peak `-1.8 dBFS`, `48 kHz / 24-bit PCM SoXR-33`)
- **Video Pipe & Colab Hygiene:** ใช้ `pipe_fps = min(fps, 20)` คู่ `-r 60` สำหรับ NVENC, `DEFAULT_MAX_ZIP_BYTES >= 16 GiB`, และปิด Colab runtime ทันทีเมื่อจบงาน
- **QA Gates:** ตรวจ 24-bit PCM ด้วย `soundfile` (`near95 == 0`, `peak <= -1.5 dBFS`), ตรวจวิดีโอด้วย `ffprobe` (`1920x1080`, `60fps`, `duration delta <= 0.25s`), ถอดเสียงตรวจคำด้วย Whisper ASR และรัน `studio.py verify`
