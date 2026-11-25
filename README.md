# AI Courseware & Thai Voice Studio

[![License: MIT](https://img.shields.io/badge/License-MIT-0F172A.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-176B9E.svg)](voice-studio/pyproject.toml)
[![Third-Party Notices](https://img.shields.io/badge/Notices-THIRD__PARTY__LICENSES-475569.svg)](THIRD_PARTY_LICENSES.md)

เครื่องมือผลิตเสียงบรรยายภาษาไทยและวิดีโอการสอน (`1080p 60fps`) พร้อม AI Skills สำหรับงานผลิตและตรวจสอบคุณภาพ, คู่มือแนวทางการสั่งงาน AI (`docs/`), และโปรเจกต์ตัวอย่างตั้งต้น (`sample-project/`) โดยผู้ใช้สามารถเตรียมเนื้อหาและกำหนดสไตล์สื่อของตนเองได้อย่างอิสระ และเลือกได้ว่าจะประมวลผลการสร้างเสียงและการตัดต่อวิดีโอบนเครื่อง **Local** หรือ **Google Colab**

![แผนผังกระบวนการผลิตสื่อและเสียงบรรยาย](docs/pipeline_architecture_th.webp)

---

## สถาปัตยกรรมระบบ

```mermaid
flowchart LR
    U["1. เตรียมเนื้อหาและแผนการผลิต<br/>สคริปต์ · สื่อภาพ · สไตล์ที่กำหนดเอง"] --> R["2. เลือกสภาพแวดล้อมการรัน<br/>Local (เมื่อทรัพยากรรองรับ)<br/>หรือ Google Colab (Compute Units)"]
    R --> V["3. สังเคราะห์และปรับแต่งเสียง<br/>Thai Prosody · TTS · Vocos<br/>RVC v2 + F0 Stabilizer"]
    V --> M["4. มาสเตอร์เสียงและประกอบวิดีโอ<br/>48kHz / 24-bit · -18.5 LUFS<br/>FFmpeg Local หรือ Colab NVENC"]
    M --> Q["5. ตรวจสอบคุณภาพ<br/>Whisper ASR · 24-bit Audio QA<br/>ffprobe · Code Verification"]
```

---

## ตัวอย่างผลลัพธ์ (Sample Output Preview & Player)

ตัวอย่างภาพสไลด์ ไฟล์เสียง และวิดีโอ `1080p 60fps` ที่สร้างจริงจาก [`sample-project/`](sample-project/README.md) ด้วย `voice-studio` บน Google Colab L4:

| สไลด์ตัวอย่างที่ 1 | สไลด์ตัวอย่างที่ 2 | สไลด์ตัวอย่างที่ 3 |
|---|---|---|
| ![Slide 01](sample-project/slides/slide_01.png) | ![Slide 02](sample-project/slides/slide_02.png) | ![Slide 03](sample-project/slides/slide_03.png) |

### วิดีโอตัวอย่าง

https://github.com/user-attachments/assets/bc78122e-2775-4485-a2a1-10df6acb81fa

---

## โครงสร้าง Repository

```text
ai-courseware-voice-studio/
├── README.md
├── LICENSE                                       # MIT License
├── THIRD_PARTY_LICENSES.md                       # แหล่งที่มาและสัญญาอนุญาตของโมเดลและไลบรารี
├── docs/
│   ├── PIPELINE_GUIDE_TH.md                      # คู่มือการใช้งานและการสั่งงาน AI
│   └── pipeline_architecture_th.webp             # แผนผังกระบวนการทำงาน
├── skills/                                       # AI Skills (รวมเหลือ 2 ชุด, เชื่อม Symlink ไปยัง .agents/skills/)
│   ├── README.md
│   ├── yourin-autohealing-architect/SKILL.md     # Execution, Auto-Healing & Verification
│   └── voice-studio-production/SKILL.md          # CLI/MCP/Colab Bridges, Model Discovery, Local/Colab & Thai Prosody
├── sample-project/                               # โปรเจกต์ตัวอย่างตั้งต้น
│   ├── README.md
│   ├── sample_script.json
│   ├── job_sample.yaml
│   └── app/
└── voice-studio/                                 # เครื่องมือ Voice Studio CLI
    ├── studio.py
    ├── configs/
    ├── input/
    ├── colab/
    ├── licenses/
    ├── src/
    └── tests/
```

---

## ความต้องการของระบบ (Prerequisites)

| สภาพแวดล้อม | ข้อกำหนดที่จำเป็น |
|---|---|
| **พื้นฐาน (จำเป็นทุกโหมด)** | `Python 3.11+`, `ffmpeg` & `ffprobe` (พร้อม `libsoxr`), แพ็กเกจใน `voice-studio/requirements.txt` |
| **รันบนเครื่อง Local (Direct TTS / ตัดต่อ)** | RAM `>= 4 GB`, อินเทอร์เน็ตสำหรับ `edge-tts` |
| **รันบนเครื่อง Local (OmniVoice-Thai + RVC v2)** | NVIDIA GPU CUDA (`>= 8 GB VRAM`, `sm_75+`), RAM `>= 16 GB`, พื้นที่ว่าง `>= 10 GB` |
| **รันบน Google Colab (`--target colab_cli`)** | บัญชี Google Colab (Compute Units) พร้อมติดตั้งและลงชื่อเข้าใช้ `colab-cli` |
| **Sample App (ทางเลือก)** | `Bun >= 1.3` (สำหรับรันตัวอย่างใน `sample-project/app`) |

---

## เริ่มต้นใช้งาน (Quick Start)

### 1. ติดตั้งและตรวจความพร้อมของเครื่อง

```bash
cd voice-studio
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python studio.py doctor
.venv/bin/python studio.py verify
.venv/bin/python studio.py input check ../sample-project/sample_script.json
```

### 2. ทดสอบโปรเจกต์ตัวอย่างและเลือกปลายทางการรัน

```bash
# ทดสอบโค้ดตัวอย่าง
cd ../sample-project/app
bun test

# รันบนเครื่อง Local (เมื่อทรัพยากรเครื่องรองรับ)
cd ..
../voice-studio/.venv/bin/python ../voice-studio/studio.py run \
  --config job_sample.yaml --voice direct_tts --target local

# รันบน Google Colab Pro (ใช้ Compute Units)
../voice-studio/.venv/bin/python ../voice-studio/studio.py run \
  --config job_sample.yaml --voice gawr_gura_hybrid --target colab_cli
```

### 3. การนำโมเดลเสียงของตนเองมาใช้งานและปรับจูนทุกส่วนผ่าน YAML

1. คัดลอกไฟล์ [voice-studio/configs/custom_voice.example.yaml](voice-studio/configs/custom_voice.example.yaml)
2. ปรับแต่งได้ครบทุกชั้นโดย **ไม่ต้องแก้โค้ด Python**:
   - **Stage 1 TTS:** `tts_backend`, `tts_voice`, `fallback_voice`, `hf_model_repo`, `hf_speaker`, `hf_speed`, `text_mode`, `pacing_sec`
   - **Stage 2 RVC v2:** `local_pth`, `local_index`, `repo_id`, `pitch_shift`, `index_rate`, `volume_envelope`, `protect`, `f0_stabilizer` (`true`/`false`), `pre_gate_rms_ms`, `pre_gate_ramp_ms`, `rvc_fp32`
   - **Stage 3 Mastering:** `pre_rvc_conditioning`, `highpass_hz`, `lowpass_hz`, `eq_preset` (`anime_female` | `warm_male` | `neutral` | `off`) หรือ `eq_bands` แบบกำหนดเอง, `comp_threshold_db`, `comp_ratio`, `gate_ratio`, `gate_release_ms`, `limiter_db`
   - **Video & Colab:** `video_codec` (`auto` | `hevc` | `h264` | `libx264` | `libx265`), `fps`, `nvenc_preset`, `nvenc_cq`, `progress_bar`, `transition_frames`, `colab.gpu_fallback_tiers`
3. ดูรายละเอียดเพิ่มเติมใน [docs/PIPELINE_GUIDE_TH.md](docs/PIPELINE_GUIDE_TH.md) และ [voice-studio/README.md](voice-studio/README.md)

### 4. การใช้งาน Python SDK

```python
from voice_studio import VoiceStudio

studio = VoiceStudio("voice-studio/configs/studio.yaml")
print(studio.voices())  # แสดงโปรไฟล์เสียงและ eq_presets ทั้งหมด

res = studio.run(
    input_path="sample-project/sample_script.json",
    job_id="custom_sdk_run",
    voice="direct_tts",
    target="local",
    output_overrides={"eq_preset": "neutral", "highpass_hz": 85, "video_codec": "h264"},
)
```

### 5. การใช้งาน AI Skills และ MCP Server (16 Tools)

- **AI Skills:** เปิด Repository นี้ใน AI Coding Agent ที่รองรับ `.agents/skills/` เพื่อโหลด Skill ทั้ง 2 ชุดใน [`skills/`](skills/README.md) (รองรับการค้นหาโมเดลเสียงจาก Hugging Face/GitHub, การถามเลือกโหมดสื่อสารครั้งแรก และเลือก Local หรือ Google Colab แยกขั้นสร้างเสียงและตัดต่อ)
- **MCP Server:** ตั้งค่าการเชื่อมต่อ Voice Studio MCP Server จากตัวอย่างใน [voice-studio/configs/mcp_config.example.json](voice-studio/configs/mcp_config.example.json) (`python voice-studio/studio.py mcp`) รองรับครบ **16 เครื่องมือ** ได้แก่ `studio_doctor`, `studio_voices`, `studio_models_verify`, `studio_prepare_preview`, `studio_transcribe`, `studio_run` (รองรับ `voice_overrides`, `output_overrides`, `slides`, `extra_vocab_path`), `studio_status`, `studio_config`, `studio_bundle`, `studio_colab_ping`, `studio_colab_logs`, `studio_colab_download`, `studio_colab_exec`, `studio_colab_stop`, `studio_auth_status`, และ `studio_auth_login`

---

## สัญญาอนุญาต (License)

- ซอร์สโค้ดและเอกสารใน Repository นี้เผยแพร่ภายใต้ [MIT License](LICENSE)
- โมเดล AI และไลบรารีของบุคคลที่สามทั้งหมดไม่รวมอยู่ใน Git และอยู่ภายใต้สัญญาอนุญาตของเจ้าของผลงานต้นฉบับ โปรดดูรายละเอียดใน [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)
