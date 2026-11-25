# Third-Party Software and Model Notices

This repository distributes original source code, scripts, and documentation under the [MIT License](LICENSE). Third-party libraries, pretrained models, fonts, and icon sets referenced or downloaded at runtime remain the property of their respective copyright holders and are governed by their own licenses. No third-party model weights are bundled in this repository.

## Pretrained Models and Voice Frameworks

| Component | Upstream Source | License |
|---|---|---|
| OmniVoice-Thai (`hotdogs/omnivoice-thai`) | https://huggingface.co/hotdogs/omnivoice-thai | See upstream model card |
| Vocos (`charactr/vocos-mel-24khz`) | https://huggingface.co/charactr/vocos-mel-24khz | MIT |
| Applio (RVC v2 Inference Framework) | https://github.com/IAHispano/Applio | MIT |
| ContentVec (`pytorch_model.bin`) | https://huggingface.co/IAHispano/Applio | MIT |
| RMVPE (`rmvpe.pt`) | https://huggingface.co/IAHispano/Applio | MIT |
| Gawr Gura RVC v2 Reference Model (`KeroroK66/GawrGura`, optional) | https://huggingface.co/KeroroK66/GawrGura | BigScience OpenRAIL-M |
| OpenAI Whisper (`openai/whisper-large-v3-turbo`, `openai/whisper-small`) | https://huggingface.co/openai/whisper-large-v3-turbo | MIT |

## Libraries, Runtimes, Fonts, and Icons

| Component | Upstream Source | License |
|---|---|---|
| edge-tts (`rany2/edge-tts`) | https://github.com/rany2/edge-tts | LGPL-3.0 |
| FFmpeg / libsoxr | https://ffmpeg.org/ | LGPL-2.1-or-later / GPL-2.0-or-later |
| Mermaid | https://mermaid.js.org/ | MIT |
| Prompt & Sarabun Fonts (Google Fonts) | https://fonts.google.com/ | SIL Open Font License 1.1 |
| Lucide Icons (via Iconify) | https://lucide.dev/ | ISC |
| Simple Icons (via Iconify) | https://simpleicons.org/ | CC0-1.0 |

## License Text Copies

Bundled upstream license notices for core runtime dependencies are preserved under [`voice-studio/licenses/`](voice-studio/licenses/):

- `Applio_LICENSE.txt` (MIT)
- `edge-tts_LICENSE.txt` (LGPL-3.0)
- `Whisper_MIT_NOTICE.md` (MIT)
- `GawrGura-RVC_README.md` & `GawrGura-RVC_metadata.json` (BigScience OpenRAIL-M)

## Custom Voice Models

Users who supply custom RVC v2 weights (`.pth` / `.index`) or reference audio recordings are solely responsible for ensuring they hold the necessary rights and permissions for those voice assets.
