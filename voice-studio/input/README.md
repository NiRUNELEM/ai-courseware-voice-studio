# Input Formats, Templates & Thai Pronunciation Specification (`input/`)

Complete guide for formatting scripts, subtitles, slides, and media files for **Voice & Media Studio** (`studio.py`).

---

## 1. Directory Layout (`input/`)

- `sample_neutral.json` — Ready-to-run default demo narration script
- `schema/input_script.schema.json` — Formal JSON Schema for structured scripts
- `templates/` — Starter files for every supported input format:
  - `script_template.json` (recommended multi-segment script + inline phonetic vocab)
  - `script_template.md` (`=== Slide 01: Title ===` or `# Heading`)
  - `script_template.txt` (plain text paragraphs split by blank lines)
  - `subtitles_template.srt` (timed `.srt` / `.vtt` subtitles stripped cleanly into spoken items)
  - `custom_vocab.example.json` (optional custom domain/brand dictionary)
- `slides/` — Optional custom `.pptx` decks or `slide_01.png`, `slide_02.png` frame directories
- `media/` — Optional `.wav`, `.mp3`, `.m4a`, `.flac`, `.mp4`, `.mkv`, `.webm` files for Whisper ASR transcription / dubbing

---

## 2. Quick Scaffold & Pre-Flight Validation Commands

Create a new input file from template and validate it before spending TTS/GPU time:

```bash
# Scaffold starter files inside input/
python studio.py input init my_talk.json
python studio.py input init my_notes.md --format md
python studio.py input init my_subs.srt --format srt
python studio.py input init my_vocab.json --format vocab

# Validate format, referenced slides/PPTX/vocab paths, and preview Thai phonetics
python studio.py input check input/sample_neutral.json
python studio.py input check input/my_talk.json --vocab input/my_vocab.json
```

---

## 3. Supported Input Formats

1. **Structured JSON (`.json`)** — Supports `slides[]` (or `segments[]`), optional `slides_png_dir`, `pptx_path` (auto-converted to 1080p PNG slides when `slides_png_dir` is omitted), `timeline_json`, `reveal_manifest`, `extra_vocab_path`, and inline `phonetic_vocab`.
2. **Markdown (`.md`)** — Split by `# Heading` or `=== Slide 01: Title ===`, paragraphs separated by blank lines.
3. **Plain Text (`.txt`)** — Paragraphs separated by blank lines (`\n\n`).
4. **Subtitles (`.srt`, `.vtt`)** — Strips `WEBVTT`, cue indices, timestamps (`00:00:01,000 --> 00:00:04,500`), and markup tags while recording segment duration estimates.
5. **Audio / Video Media (`.wav`, `.mp3`, `.m4a`, `.flac`, `.ogg`, `.opus`, `.mp4`, `.mkv`, `.webm`, `.mov`)** — Automatically runs Whisper ASR (`transcribe`) first and feeds `script_from_audio.json` into `prepare -> tts -> convert -> qa -> align -> render`.

---

## 4. Critical Thai Phonetic & Prosody Rules (`OmniVoice-Thai` / `PremwadeeNeural -> RVC v2`)

1. **Natural Thai Pronunciation for Loanwords (`ดาต้าเบส` & Compound Terms)**:
   - **Correct**: Write `ดาต้าเบส` as one connected Thai loanword (or English `database`, mapped automatically in `src/voice_studio/content/phonetic_vocab.json`). Keep `text_mode: "phonetic_spaced"` with `zero_latin_phonetic: true` for clean Thai+English synthesis in `gawr_gura_hybrid` (`OmniVoice-Thai + Vocos 24kHz + RVC v2`), avoiding awkward trailing `-์ด` halant stops where Thai reading variants sound clearer.
   - **Avoid**: Space-separated `เด ทะ เบส` or `ดาต้า เบส`, which isolates standalone `"เบส"` and can alter Thai pitch contour. Also avoid `เดย์-ทะ-เบส` which sounds unnatural compared to standard Thai `ดาต้าเบส`.
2. **Custom Project Vocabulary Without Editing Tool Source**:
   - Add `"extra_vocab_path": "input/my_vocab.json"` or an inline `"phonetic_vocab": {"words": {...}}` block directly inside your JSON input script.
3. **Clause Spacing & Breathing Pauses**:
   - End full Thai sentences cleanly with `. ` after polite particles (`ค่ะ.`, `นะคะ.`), use `, ` to mark short natural breath groups around technical loanwords (`clause_spacing: true`), and use `[เว้นจังหวะสั้น]` / `[เว้นจังหวะยาว]` markers where longer pauses are needed.
