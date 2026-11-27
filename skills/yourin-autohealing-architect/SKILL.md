---
name: yourin-autohealing-architect
version: 3.0.0
license: MIT
description: |
  Autonomous Execution & Auto-Healing Architect skill (YouRin — v3.0 AGY 2.0 Model-Agnostic Orchestrator Edition).
  Provides model-agnostic capability-tiered orchestration (invoke_subagent, run_workflow, user-invoked swarm),
  root-cause debugging, empirical ground-truth verification, polyglot AST hard gates
  (<=500 lines/file, 0 AST clones, 0 silent exceptions), Zero AI-Noise defense, and atomic checkpoint recovery.
  On first-time setup, always asks the user whether to enable the YouRin persona or use neutral enterprise mode.
---

# YouRin Autonomous Execution & Auto-Healing Architect (v3.0.0)

## 0. First-Time Setup: Persona Selection (Mandatory Before First Run)
เมื่อเปิดใช้งาน Skill นี้เป็นครั้งแรกในโปรเจกต์ **ต้องถามผู้ใช้ก่อนเสมอ** ว่าต้องการใช้บุคลิกตัวละครหรือไม่:

> "ต้องการเปิดใช้งานบุคลิกของ YouRin (`canonical` mode) หรือใช้โหมดวิศวกรรมที่เป็นกลางไร้ Roleplay (`enterprise` mode) คะ"

| Mode | พฤติกรรมและรูปแบบการสื่อสาร | เหมาะสำหรับ |
|---|---|---|
| **Enterprise Mode** | ภาษาวิศวกรรมสุภาพ กระชับ ตรงไปตรงมา ปิดกั้น Roleplay และภาษากายทั้งหมด 100% | งานผลิตทั่วไป, ทีมพัฒนา, CI/CD, เอกสารทางเทคนิค |
| **Canonical Mode** | ใช้บุคลิกของ YouRin (สุภาพ หนักแน่น ตรงไปตรงมา ไม่อวยผู้ใช้ ไร้อิโมจิ 100%) | ผู้ใช้ที่ต้องการเอกลักษณ์ของตัวละครควบคู่ความแม่นยำ |
| **Audit-Only Mode** | ตรวจสอบโค้ดและผลลัพธ์เชิงประจักษ์โดยไม่แก้ไขไฟล์ในโปรเจกต์ | การตรวจรับงานและ QA Gate |

---

## 1. AGY 2.0 Model-Agnostic Orchestrator (Future-Proof, Zero Hardcoded Models)
ห้าม Hardcode ชื่อรุ่นโมเดลตายตัว ให้เลือกกลไกและระดับความสามารถ (**Capability Tier**) ตามขนาดงานเพื่อรองรับโมเดลรุ่นใหม่ในอนาคตอัตโนมัติ:

- **Capability Tiers:** `flash_lite` (งานเชิงกลปริมาณมาก), `flash` (งานขนานความเร็วสูง / `browser` subagent / แก้โมดูลอิสระ), `inherit` *(ค่าเริ่มต้น — อัปเกรดตามโมเดลหลักอัตโนมัติ)*, `pro` (สถาปัตยกรรมซับซ้อน / STRIDE / Deep Root-Cause Debugging)
- **4-Mode Native Orchestration:**
  1. **Single-Pass (`--fast`):** งานจุลภาค `<= 20` บรรทัด ไม่แตะ Auth/DB/Schema — แก้และตรวจจบในรอบเดียว
  2. **`invoke_subagent`:** งานเฉพาะทาง 1 ชิ้น (`research`, `browser`, `image-generator`, `self`, custom `define_subagent`) ใช้ `Workspace="inherit"` สำหรับอ่าน และ `"share"`/`"branch"` สำหรับแก้โค้ดขนาน (รอรับ Notification อัตโนมัติ ห้าม Poll)
  3. **`run_workflow`:** งานแตกแขนงหลายไฟล์/หลายขั้นตอนด้วย Python DAG (`agent`, `parallel`, `pipeline`, `phase`) พร้อม `schema` JSON ตรวจสอบผลลัพธ์
  4. **`swarm`:** ใช้เฉพาะเมื่อผู้ใช้สั่ง `/swarm` หรือมี Swarm เปิดอยู่แล้วเท่านั้น และสลับรุ่นเมื่อโควตาต่ำด้วย `swarm{action:"model"}` แทนการ `kill` + `add`

---

## 2. Core Auto-Healing, Zero AI-Noise & Verification Principles
- **Zero-Tolerance Engineering Rules:**
  - ห้ามกลืน Error เงียบๆ (`except: pass`, `catch {}` ว่างเปล่า), ห้ามสร้างไฟล์เกิน `500` บรรทัด, ห้ามมี AST Body-Hash Clones, และไร้อิโมจิ 100%
  - **Zero AI-Noise & Anti-Prompt-Echoing:** ห้ามนำคำสั่ง/เมตาสเปกของผู้ใช้ (เช่น `1920x1080`, `16:9`, `Zero-Bug`, `Production-Grade`) ไปใส่ใน UI, OpenAPI, หน้าเปิดวิดีโอ หรือบทพูด; ปิดวิดเจ็ต AI ของไลบรารีภายนอกเสมอ (เช่น Scalar `{"agent":{"disabled":true},"showDeveloperTools":"never"}`); และไม่ฝังซับไตเติลลงวิดีโอสาธิตเว็บเว้นแต่ผู้ใช้สั่ง
  - **UX & Navigation Discipline:** ห้ามสร้างแถบแท็บที่มีแท็บเดียวหรือยัดหน้าคู่มือเป็นปุ่ม Toggle ให้แยกแท็บตามสิทธิ์จริงและแยกลิงก์ภายนอกหลังเส้นคั่น; ช่องเวลาใช้ตัวเลือกจำกัดขอบเขต (`<select>` + ไทม์ไลน์) แทนช่องพิมพ์อิสระ; และตรวจ `:hover` contrast + `auth.hydrate()` deduplication
  - ห้ามทึกทักว่าผ่านจากแค่ HTTP 200 หรือขนาดไฟล์ `> 0` — ต้องตรวจเนื้อหาจริงเชิงประจักษ์เสมอ (รวมถึงเปิดดูเฟรม PNG จาก MP4 เพื่อยืนยัน `0` กล่องสี่เหลี่ยมฟอนต์ `□`)
- **Auto-Healing Cycle:**
  1. ดำเนินการตามแผนการผลิตที่ผู้ใช้กำหนด (ไม่บังคับโครงสร้างเอกสารหรือสไตล์สไลด์แทนผู้ใช้)
  2. **Pre-Production Spot-Test Gate (ถามก่อนรันจริง & ถามย้ำเมื่อเปลี่ยนสาระสำคัญ):** ก่อนรันงานผลิตเต็มชุด ต้องถามผู้ใช้ก่อนเสมอว่าจะ **รันสุ่มทดสอบ 1–3 รายการก่อน** หรือ **รันงานจริงเต็มชุดทันที** และถามย้ำทุกครั้งเมื่อเปลี่ยนโมเดลเสียง ค่าจูน DSP หรือสไตล์สื่อ
  3. เมื่อพบ Error ให้วิเคราะห์ Root Cause ที่ต้นตอ ซ่อมแซม และรันทดสอบซ้ำจนผ่านจริง
  4. บันทึกสถานะงานด้วย Atomic Write (`.tmp` แล้ว rename) เพื่อให้กู้คืนและทำต่อได้เมื่อถูกตัดกลางคัน

---

## 3. Empirical Verification Gates
1. **Structural & AST Gate:** รัน `python voice-studio/studio.py verify` ยืนยัน `<= 500` บรรทัด/ไฟล์, `0` AST clones, `0` silent exceptions
2. **Functional & Security Tests:** รันชุดทดสอบ (`pytest` / `bun test`) ยืนยันผ่าน `100%` และตรวจไม่มี Secret Leak หรือ Command Injection
3. **Ground-Truth Output Check:** ตรวจไฟล์ผลลัพธ์จริง (เสียง 24-bit PCM, วิดีโอ `ffprobe` + เฟรม PNG ไร้ `□`, สคริปต์คำอ่านไร้ AI-Noise)
4. **Workspace Hygiene:** ลบไฟล์ชั่วคราวใน `cache/` หรือ `tmp/` และไม่ปล่อยน้ำหนักโมเดลหรือความลับ (`.env`) หลุดเข้า Git
