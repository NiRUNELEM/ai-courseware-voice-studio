# -*- coding: utf-8 -*-
"""Royal Society Thai number, version, code, symbol, and breath-pause reading normalizer."""

from __future__ import annotations

import re
from functools import lru_cache

try:
    from pythainlp.util import num_to_thaiword as _pythai_num_to_word
except ImportError:  # pragma: no cover - fallback when pythainlp is absent
    _pythai_num_to_word = None

_THAI_DIGITS = ("ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า")
_HTTP_STATUS_CODES = {
    "200", "201", "204", "301", "302", "304",
    "400", "401", "403", "404", "405", "409", "415", "422", "429",
    "500", "502", "503", "504",
}
_CODE_PREFIX_RE = re.compile(
    r"(?P<prefix>รหัส(?:สถานะ)?|สถานะ|เอชทีทีพี|พอร์ต|หมายเลข|เอกสาร|อาร์เอฟซี|มาตรฐาน|ทีแอลเอส|ยูทีเอฟ|เอสเอชเอ|โอทีพี|เวอร์ชัน|เอ็กซ์\s*ดอท)"
    r"\s+(?P<code>\d{2,5})\b"
)
_PREF_RE = re.compile(
    r"(?P<era>พ\.ศ\.|ค\.ศ\.)\s*(?P<year>\d{4})\b"
)
_TIME_COLON_RE = re.compile(r"\b(?P<h>\d{1,2}):(?P<m>\d{2})(?:\s*น\.)?\b")
_DASHED_CODE_RE = re.compile(r"\b(\d+(?:-\d+)+)\b")
_DOTTED_VER_RE = re.compile(r"\b(\d+(?:\.\d+)+)\b")
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


@lru_cache(maxsize=512)
def number_to_thai(num_str: str) -> str:
    """Converts a cardinal ASCII integer string into standard Thai spoken words."""
    digits = re.sub(r"\D", "", num_str or "")
    if not digits:
        return ""
    if len(digits) > 1 and digits.startswith("0"):
        return digits_to_thai(digits)
    n = int(digits)
    if _pythai_num_to_word is not None and n <= 999999999:
        return str(_pythai_num_to_word(n))
    return _fallback_num_to_thai(n)


def _fallback_num_to_thai(n: int) -> str:
    """Minimal fallback Thai cardinal converter up to 999,999 when pythainlp is absent."""
    if n < 10:
        return _THAI_DIGITS[n]
    units = ("", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน", "ล้าน")
    s = str(n)
    if len(s) > 7:
        return " ".join(_THAI_DIGITS[int(c)] for c in s)
    out = []
    length = len(s)
    for idx, ch in enumerate(s):
        d = int(ch)
        pos = length - idx - 1
        if d == 0:
            continue
        if pos == 1 and d == 1:
            out.append("สิบ")
        elif pos == 1 and d == 2:
            out.append("ยี่สิบ")
        elif pos == 0 and d == 1 and length > 1:
            out.append("เอ็ด")
        else:
            out.append(_THAI_DIGITS[d] + units[pos])
    return "".join(out)


def digits_to_thai(digits: str) -> str:
    """Spells each digit individually with spaces for codes, ports, and serial numbers."""
    clean = re.sub(r"\D", "", digits or "")
    return " ".join(_THAI_DIGITS[int(c)] for c in clean)


def expand_pause_markers(text: str) -> str:
    """Converts explicit Thai/English script pause annotations into Edge-TTS punctuation breaks."""
    out = text or ""
    out = re.sub(r"\[เว้นจังหวะ(?:ยาว|มาก|นาน)[^\]]*\]|\[pause\s+long\]", " ... ", out, flags=re.IGNORECASE)
    out = re.sub(r"\[เว้นจังหวะ[^\]]*\]|\[pause(?:\s+short)?\]", ", ", out, flags=re.IGNORECASE)
    return out


_THAI_NUM_TOKEN_RE = r"(?:ศูนย์|หนึ่ง|สอง|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า|(?:[ยี่สามสี่ห้าหกเจ็ดแปดเก้า]?สิบ|[^\s]*ร้อย|[^\s]*พัน)[^\s,]*)"
_PREFIX_TIGHT = (
    r"ทั้ง|บทที่|สไลด์ที่|เสาหลักที่|รูปแบบที่|ขั้นตอนที่|ขั้นที่|กลุ่มที่|ส่วนที่|"
    r"ประการที่|โมเดลที่|กรณีที่|ข้อที่|ใบที่|ไฟล์ที่|ตารางที่|ครั้งที่|วันที่|ระดับที่|ระดับ|ความยาว|อายุ|เวลา|ปี|เลขที่"
)
_UNIT_TIGHT = (
    r"ด้าน|ขั้นตอน|นาที|วินาที|ชั่วโมง|สไลด์|หน้า|กรณี|เงื่อนไข|รูปแบบ|กลุ่ม|"
    r"เสาหลัก|ข้อ|ประการ|ระดับ|ส่วน|ชั้น|ใบ|ชุด|เดือน|ปี|วัน|คน|เท่า|ครั้ง|โมง|ทุ่ม|หลัก|ตัว|แบบ"
)
_SMALL_THAI_NUM = r"(?:หนึ่ง|สอง|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า|สิบ)"
_THAI_DIGIT_PAT = r"(?:ศูนย์|หนึ่ง|สอง|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า)"
_HTTP_REASON_WORDS = r"(?:โอเค|ครีเอเต็ด|รีไดเรกต์|ฟาวด์|แบด|อันออทอไรซ์|ฟอร์บิดเดน|นอต|คอนฟลิกต์|อันโพรเซสซาเบิล|ทู|เอ็กซ์ไพร์|อินเทอร์นัล)"


def _tighten_thai_number_flow(text: str) -> str:
    """Joins Thai cardinal words naturally with Thai classifiers/prefixes so speech flows without choppy gaps."""
    out = re.sub(rf"({_THAI_NUM_TOKEN_RE})\s+จุด\s+({_THAI_NUM_TOKEN_RE})", r"\1จุด\2", text)
    out = re.sub(rf"({_THAI_NUM_TOKEN_RE})\s+จุด\s+({_THAI_NUM_TOKEN_RE})", r"\1จุด\2", out)
    out = re.sub(rf"({_THAI_NUM_TOKEN_RE})\s+({_UNIT_TIGHT})", r"\1\2", out)
    out = re.sub(rf"({_PREFIX_TIGHT})\s+({_THAI_NUM_TOKEN_RE})(?!\s+(?:{'|'.join(_THAI_DIGITS)})\b)", r"\1\2", out)
    out = re.sub(rf"((?:{_PREFIX_TIGHT}|{_UNIT_TIGHT}|{_THAI_NUM_TOKEN_RE}))\s+(นี้|นั้น|กันก่อน|ทั้งหมด|โดยตรง)(?=[ก-ฮะ-์\s,.]|$)", r"\1\2", out)
    def _repl_range(m: re.Match[str]) -> str:
        before = m.string[max(0, m.start() - 10) : m.start()].strip()
        if any(before.endswith(d) for d in _THAI_DIGITS):
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"

    out = re.sub(
        rf"({_SMALL_THAI_NUM})\s+(ถึง|และ|หรือ|กับ)\s+({_SMALL_THAI_NUM})(?!\s+(?:{'|'.join(_THAI_DIGITS)})\b)",
        _repl_range,
        out,
    )
    out = re.sub(
        r"\s+(ใน|ของ|จาก|ถึง|สู่)\s+(?=(?:ทั้ง|บทที่|สไลด์ที่))",
        r" \1",
        out,
    )
    out = re.sub(r"\s+(ของ)(?=บทที่|สไลด์ที่)", r"\1", out)
    out = re.sub(r"([ก-ฮะ-์]+)\s+กันก่อน(ค่ะ|นะคะ)", r"\1กันก่อน\2", out)
    out = re.sub(rf"((?:(?:ศูนย์|หนึ่ง|สอง|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า)\s+){{2}}(?:ศูนย์|หนึ่ง|สอง|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า))\s+({_HTTP_REASON_WORDS})", r"\1, \2", out)
    return out
def _convert_version_segment(seg: str) -> str:
    if len(seg) >= 2 and seg.startswith("0"):
        return digits_to_thai(seg)
    if len(seg) >= 3:
        return digits_to_thai(seg)
    return number_to_thai(seg)


def normalize_thai_numbers_and_symbols(text: str) -> str:
    """Transforms digits, years, codes, versions, and symbols into spoken Thai TTS words."""
    out = expand_pause_markers(text)
    if not out.strip():
        return ""

    # Buddhist & Christian eras (พ.ศ. 2567 / ค.ศ. 2026)
    out = _PREF_RE.sub(
        lambda m: (
            ("พุทธศักราช " if m.group("era").startswith("พ") else "คริสต์ศักราช ")
            + number_to_thai(m.group("year"))
        ),
        out,
    )
    # Time expressions 10:15 -> สิบ นาฬิกา สิบห้า นาที
    out = _TIME_COLON_RE.sub(
        lambda m: f"{number_to_thai(m.group('h'))} นาฬิกา"
        + (f" {number_to_thai(m.group('m'))} นาที" if int(m.group("m")) > 0 else ""),
        out,
    )
    # Percentages (85% / 99.9%)
    out = _PCT_RE.sub(
        lambda m: " ".join(_convert_version_segment(p) for p in m.group(1).split(".")) + " เปอร์เซ็นต์"
        if "." in m.group(1)
        else f"{number_to_thai(m.group(1))} เปอร์เซ็นต์",
        out,
    )
    # Dashed standard/ISBN sequences (e.g., 978-616-262-818-4 or 800-63)
    def _repl_dashed(m: re.Match[str]) -> str:
        parts = m.group(1).split("-")
        if len(parts) == 2 and parts[0] == "800":
            return f"{number_to_thai(parts[0])} ขีด {number_to_thai(parts[1])}"
        return ", ".join(digits_to_thai(p) if len(p) >= 3 or len(parts) >= 4 else number_to_thai(p) for p in parts)

    out = _DASHED_CODE_RE.sub(_repl_dashed, out)

    # Dotted versions / standards (1.4.32, 2.0, X.509 -> เอ็กซ์.509)
    out = re.sub(r"([ก-ฮะ-์]+)\.(\d{2,4})\b", lambda m: f"{m.group(1)} ดอท {digits_to_thai(m.group(2))}", out)
    out = _DOTTED_VER_RE.sub(
        lambda m: " จุด ".join(_convert_version_segment(p) for p in m.group(1).split(".")),
        out,
    )

    # Numbered list markers like "1. " or "2. "
    out = re.sub(r"(?:^|(?<=\s))(\d{1,2})\.\s+", lambda m: f"{number_to_thai(m.group(1))}, ", out)

    # Code-prefixed numbers (HTTP 401, พอร์ต 3010, รหัส 422, เอกสาร 101, RFC 7617)
    def _repl_code_prefixed(m: re.Match[str]) -> str:
        prefix, code = m.group("prefix"), m.group("code")
        if prefix.startswith("พอร์ต") or prefix.startswith("อาร์เอฟซี") or prefix.startswith("หมายเลข") or prefix.startswith("เอกสาร"):
            return f"{prefix} {digits_to_thai(code)}"
        if code in _HTTP_STATUS_CODES or len(code) >= 3 and code.startswith("0"):
            return f"{prefix} {digits_to_thai(code)}"
        if prefix.startswith("เอ็กซ์") and len(code) == 3:
            return f"{prefix} {digits_to_thai(code)}"
        if code == "800":
            return f"{prefix} {number_to_thai(code)}"
        return f"{prefix} {digits_to_thai(code) if len(code) >= 4 else number_to_thai(code)}"

    out = _CODE_PREFIX_RE.sub(_repl_code_prefixed, out)

    # Standalone HTTP status codes (e.g., "เทียบ 403", "ตอบ 401")
    out = re.sub(
        r"\b(" + "|".join(sorted(_HTTP_STATUS_CODES)) + r")\b(?!\s*(?:หน้า|คน|บาท|นาที|วินาที|ชั่วโมง|สไลด์|ข้อ|รายการ|กรณี|เงื่อนไข))",
        lambda m: digits_to_thai(m.group(1)),
        out,
    )

    # Special standard spacing like "800 63"
    out = re.sub(r"\b800\s+63\b", "แปดร้อย หกสิบสาม", out)

    # Remaining standalone ASCII integers -> proper Thai cardinals
    out = re.sub(r"\b(\d+)\b", lambda m: number_to_thai(m.group(1)), out)

    # Clean non-spoken symbols into pauses or spaces
    out = out.replace("—", ", ").replace("–", ", ").replace(":", " ")
    out = re.sub(r"\s+-\s+|\s+-|-\s+", ", ", out)
    out = re.sub(r"(?<![A-Za-z0-9])-(?![A-Za-z0-9])", " ", out)
    out = re.sub(r"\(([^)]+)\)", r", \1, ", out)
    out = re.sub(r"\s+([.,!?])", r"\1", out)
    out = _tighten_thai_number_flow(out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out
