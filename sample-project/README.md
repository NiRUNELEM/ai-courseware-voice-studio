# Sample Project — Flexible Starter Template for Voice Studio Pipeline

โฟลเดอร์นี้คือ **โปรเจกต์ตัวอย่างกลาง (Neutral Sample Project)** ที่รันทดสอบได้จริงทันที ผู้ใช้สามารถเตรียมเนื้อหา โครงสร้างเอกสาร และสไตล์สไลด์ของตนเองได้อย่างอิสระ แล้วใช้ Pipeline นี้สำหรับสร้างเสียงพากย์ ตัดต่อวิดีโอ และตรวจสอบคุณภาพอัตโนมัติ

## โครงสร้างไฟล์ตัวอย่าง
- `app/` — เว็บ API ตัวอย่างขนาดกะทัดรัดด้วย TypeScript + Bun (`GET /health`, `GET /items`, `POST /items`) พร้อมชุดทดสอบอัตโนมัติ (`index.test.ts`)
- `sample_script.json` — สคริปต์บทบรรยายตัวอย่าง 3 สไลด์ สำหรับทดสอบระบบแปลงคำอ่านไทยและการสังเคราะห์เสียง
- `sample_phonetic_vocab.json` — คลังคำศัพท์คำอ่านเฉพาะโปรเจกต์ (`extra_vocab_path`) สำหรับกำหนดคำอ่านทับศัพท์และวรรณยุกต์ภาษาไทยเพิ่มเติมจากฐานข้อมูลกลาง
- `job_sample.yaml` — ไฟล์ตั้งค่างานสำหรับ `voice-studio/studio.py` (เชื่อม `sample_phonetic_vocab.json` พร้อมเลือกปลายทาง `local` / `colab_cli` และโมเดลเสียงได้อิสระ)

## 1. ทดสอบรัน Sample App ด้วย Bun

```bash
cd sample-project/app
bun test
bun run index.ts
# ทดสอบ: curl http://localhost:3099/health
```

## 2. ตรวจทรัพยากรเครื่องและเลือกปลายทางรัน (Local vs Colab CU)

```bash
# ตรวจทรัพยากรเครื่อง (GPU / RAM / FFmpeg) และตรวจสคริปต์ตัวอย่าง
python ../voice-studio/studio.py doctor
python ../voice-studio/studio.py input check sample_script.json

# ทางเลือก A: รันบนเครื่อง Local (เมื่อทรัพยากรเครื่องรองรับ — ไม่ใช้ Colab CU)
python ../voice-studio/studio.py run --config job_sample.yaml --voice direct_tts --target local

# ทางเลือก B: รันบน Google Colab Pro (ใช้ Compute Units — รองรับ GPU Auto-Fallback เมื่อติด 503)
python ../voice-studio/studio.py run --config job_sample.yaml --voice gawr_gura_hybrid --target colab_cli
```

## 3. ตัวอย่างคำสั่งสำหรับสั่ง AI Agent ดำเนินการตามแผนการผลิตของคุณ

คัดลอกประโยคด้านล่างแล้วแทนที่ข้อความในวงเล็บ `[...]` เพื่อสั่ง AI Agent ได้ทันที:

> ใช้ `skills/yourin-autohealing-architect` และ `skills/voice-studio-production` โดยอ้างอิงโครงสร้างเริ่มต้นจาก `sample-project/` ดำเนินการตามแผนการผลิตและสไตล์ที่แนบไว้สำหรับหัวข้อ `[หัวข้อของคุณ]` โดยให้ตรวจทรัพยากรเครื่องแล้วถามก่อนว่าจะเลือกหรือค้นหาโมเดลเสียงใด และจะสร้างเสียงกับตัดต่อวิดีโอบน Local หรือ Google Colab (CU) จากนั้นรันตรวจคุณภาพ (Verify & QA) ให้ผ่านครบทุกข้อ
