# -*- coding: utf-8 -*-
"""Tests for Royal Society Thai reading normalization, breath-group spacing, and pause markers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from voice_studio.content import (  # noqa: E402
    add_thai_clause_spacing,
    expand_pause_markers,
    normalize_thai_numbers_and_symbols,
    select_tts_text,
)


class TestThaiReadingAndProsody(unittest.TestCase):
    def test_01_pause_marker_expansion_and_thai_numbers(self) -> None:
        self.assertEqual(expand_pause_markers("คำแรก[เว้นจังหวะสั้น]คำที่สอง[เว้นจังหวะยาว]จบ"), "คำแรก, คำที่สอง ... จบ")

        converted = normalize_thai_numbers_and_symbols(
            "บทที่ 10 คืนสถานะ HTTP 401 หรือ 403 พอร์ต 3010 เวอร์ชัน 1.4.32 ปี พ.ศ. 2567 รหัส 978-616-262-818-4 ค่ะ"
        )
        self.assertNotRegex(converted, r"\d")
        self.assertIn("สิบ", converted)
        self.assertIn("สี่ ศูนย์ หนึ่ง", converted)
        self.assertIn("สี่ ศูนย์ สาม", converted)
        self.assertIn("สาม ศูนย์ หนึ่ง ศูนย์", converted)
        self.assertIn("หนึ่งจุดสี่จุดสามสิบสอง", converted)
        self.assertIn("พุทธศักราช สองพันห้าร้อยหกสิบเจ็ด", converted)
        self.assertIn("เก้า เจ็ด แปด, หก หนึ่ง หก", converted)

    def test_02_breath_group_chunking_and_select_tts_mode(self) -> None:
        long_text = (
            "สวัสดีค่ะผู้เรียนทุกท่าน ในบทเรียนนี้ทุกคนจะได้เรียนรู้การสร้างระบบปลอดภัยสมัยใหม่"
            "เพื่อนำไปประยุกต์ใช้งานจริงบนเซิร์ฟเวอร์ที่มีประสิทธิภาพสูงอย่างเป็นระบบค่ะ "
            "นอกจากนี้ยังได้ฝึกวิเคราะห์โครงสร้างข้อมูลทั้งหมดด้วยนะคะ"
        )
        spaced = add_thai_clause_spacing(long_text)
        self.assertIn("สวัสดีค่ะ ผู้เรียนทุกท่าน,", spaced)
        self.assertIn("ค่ะ. นอกจากนี้", spaced)
        # Ensure long clauses are broken cleanly without splitting inside compound words
        for clause in spaced.replace("...", ".").split(","):
            self.assertLessEqual(len(clause.strip()), 145)

        tts_out = select_tts_text(
            "บทที่ 10 คืนรหัส 422 ค่ะ",
            "บทที่ 10 คืนรหัส 422 ค่ะ",
            text_mode="phonetic_spaced",
            clause_spacing=True,
        )
        self.assertNotRegex(tts_out, r"[A-Za-z0-9]")
        self.assertIn("สี่ สอง สอง", tts_out)

    def test_03_rvc_f0_stabilizer_reduces_pitch_warble(self) -> None:
        import numpy as np
        from voice_studio.audio.stage2_rvc import smooth_voiced_f0

        raw = np.array([0.0, 0.0, 220.0, 234.0, 206.0, 236.0, 208.0, 222.0, 220.0, 0.0], dtype=np.float64)
        sm = smooth_voiced_f0(raw)
        self.assertEqual(float(sm[0]), 0.0)
        self.assertEqual(float(sm[-1]), 0.0)
        raw_jitter = float(np.std(np.diff(raw[2:9])))
        sm_jitter = float(np.std(np.diff(sm[2:9])))
        self.assertLess(sm_jitter, raw_jitter * 0.4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
