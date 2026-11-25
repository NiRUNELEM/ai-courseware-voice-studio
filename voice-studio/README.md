# Voice & Media Studio

Standalone general-purpose **Whisper ASR Transcription + Thai/English TTS (`OmniVoice-Thai` / `Edge-TTS`) + `Vocos 24kHz` Phase Polish + Gawr Gura / Custom RVC v2 Vocal Conversion + Broadcast Audio Mastering + 1080p@60fps Video Studio** for Local Workstation and Google Colab Pro.

> **Educational Scope & Notices:** Configured for internal / non-commercial educational use (`license_scope: internal_non_commercial_education`). Upstream notices live in [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md) and [`licenses/`](licenses/).

---

## 1. Directory Structure

```text
voice-studio/
├── studio.py                        # Main CLI (`run`, `transcribe`, `input`, `voices`, `models`, `doctor`, `colab`, `mcp`, `bundle`, `status`, `clean`, `verify`)
├── requirements.txt                 # Single consolidated dependencies file (Local + Torch/RVC/ASR + Colab + Tests)
├── pyproject.toml                   # Python package metadata (`voice-studio`)
├── configs/
│   ├── studio.yaml                  # Local workstation profile (40 kHz / 16-bit PCM, whisper-small)
│   ├── colab_pro.yaml               # Google Colab Pro profile (`colab_cli`, L4/A100, FP32 RVC, 48 kHz / 24-bit SoXR-33, NVENC p7, whisper-large-v3-turbo)
│   ├── custom_voice.example.yaml    # Template for custom RVC v2 .pth/.index tuning + OmniVoice-Thai / Edge-TTS
│   └── mcp_config.example.json      # Antigravity MCP config (`voice-studio` + official `colab-mcp`)
├── input/                           # Complete input workspace (README.md, schema/, templates/, media/, slides/, sample_neutral.json)
├── models/                          # Verified RVC v2 weights & Applio runtime (`models/manifest.json`)
├── tools/omnivoice_thai_studio/     # Standalone Thai Zero-Shot Voice Cloning + Vocos + Mastering CLI & ZIP
├── samples/                         # Calibration samples (`01`-`06`) & verified flow evaluations (`flow_local/`, `flow_mcp/`)
├── output/                          # Generated jobs (`output/<job_id>/deliverables/`)
├── colab/ & exports/                # Google Colab notebook & verified production bundle ZIP
├── src/
│   ├── voice_studio/                # Core studio engine + MCP server + Colab bridges
│   └── quality/                     # 5-gate Polyglot AST architecture verifier
└── tests/                           # Tool, security, input & Colab bridge tests
```

---

## 2. Environment & Input Preparation

```bash
# Install all dependencies from the single requirements file
pip install -r requirements.txt

# Inspect Python, CUDA/CPU, FFmpeg/NVENC, Whisper ASR & verify SHA-256 model integrity
python studio.py doctor
python studio.py models verify

# Inspect input formats, create a script from template, and validate phonetics before running
python studio.py input formats
python studio.py input init my_script.json
python studio.py input check input/sample_neutral.json
```

See [`input/README.md`](input/README.md) and [`input/schema/input_script.schema.json`](input/schema/input_script.schema.json) for JSON schema, `.srt`/`.vtt`/`.md`/`.txt` formats, automatic `.pptx -> .png` slide export, inline custom `phonetic_vocab`, and Thai pronunciation rules.

---

## 3. Running Workflows & Custom Voice Tuning

Pipeline steps: `transcribe -> prepare -> tts -> convert -> qa -> align -> render`.

```bash
# Full hybrid flow: OmniVoice-Thai + Vocos 24kHz -> Gawr Gura RVC v2 -> 48kHz/24-bit + 1080p60 MP4
python studio.py run --config configs/colab_pro.yaml --input input/sample_neutral.json --voice gawr_gura_hybrid --target colab_cli --job demo_hybrid

# Local workstation baseline: Edge-TTS PremwadeeNeural -> Gawr Gura RVC v2 -> 1080p MP4 + WAV + MP3
python studio.py run --input input/sample_neutral.json --voice gawr_gura --job demo_gura

# Custom RVC v2 voice model tuning (.pth + .index under models/rvc/<speaker>/)
cp configs/custom_voice.example.yaml my_custom_job.yaml
python studio.py run --config my_custom_job.yaml

# Auto-remove intermediate work dirs (work/, audio_stage1/, audio_items_raw/, video_segments_1080p/) upon completion
python studio.py run --input input/sample_neutral.json --job demo_gura --clean-work

# Clean intermediate dirs afterwards or reset all old output/ and cache/ jobs
python studio.py clean --job demo_gura
python studio.py clean --all

# Direct Edge-TTS baseline (audio only)
python studio.py run --input input/sample_neutral.json --voice direct_tts --audio-only --job demo_direct

# Transcribe audio/video via Whisper ASR or auto-dub + render
python studio.py transcribe --input path/to/video.mp4 --out output/my_transcript
python studio.py run --input path/to/audio.wav --voice gawr_gura --job dub_gura

# Run an external project job YAML without mixing files into voice-studio/
python studio.py run --config path/to/external_job.yaml
```

For standalone zero-shot Thai voice cloning with custom `--ref-audio`, see [`tools/omnivoice_thai_studio/README.md`](tools/omnivoice_thai_studio/README.md).

---

## 4. Google Colab Pro Cloud, MCP & Live Terminal (`configs/colab_pro.yaml`)

`configs/colab_pro.yaml` enforces **`target: colab_cli`**, **`gpu_tier: l4`**, and **`high_ram: true`** BEFORE execution: mismatched/stale CPU sessions are disconnected automatically and a fresh L4/A100 GPU session is provisioned (with automatic session recovery polling up to `420s` per attempt) and streams live terminal logs (`[colab-live] | ...`).

```bash
# First-run MCP registration + check (`voice-studio` + official `googlecolab/colab-mcp`)
python studio.py mcp --setup
python studio.py mcp --status

# Authorize Google Colab CLI OAuth once & verify GPU config
python studio.py colab auth --target colab_cli
python studio.py colab ping --config configs/colab_pro.yaml --ensure

# Run full Colab Pro pipeline with Live Terminal & SHA-256 download (run sequentially, one job at a time)
python studio.py bundle
python studio.py run --config configs/colab_pro.yaml --voice gawr_gura_hybrid --job colab_pro_demo --no-resume
python studio.py colab logs --config configs/colab_pro.yaml --job colab_pro_demo
python studio.py colab exec --config configs/colab_pro.yaml --cmd "nvidia-smi"
```

---

## 5. Verification Gates

```bash
python studio.py verify
python studio.py models verify
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider -v tests/
```
