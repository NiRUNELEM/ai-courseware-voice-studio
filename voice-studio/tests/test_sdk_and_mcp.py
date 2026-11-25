# -*- coding: utf-8 -*-
"""Unit tests for the VoiceStudio Python SDK, 16-tool MCP server, video codec selection, and remote serialization."""

from __future__ import annotations

import json
from pathlib import Path

from voice_studio import EQ_PRESETS, VoiceStudio, __version__
from voice_studio.config.loader import load_pipeline_config
from voice_studio.mcp_server import handle_jsonrpc_request
from voice_studio.pipeline.orchestrator import _serialize_job_spec
from voice_studio.video.compositor import detect_best_video_encoder


def test_01_python_sdk_public_api_and_dry_run(tmp_path: Path) -> None:
    assert __version__ == "2.2.0"
    assert "warm_male" in EQ_PRESETS

    studio = VoiceStudio()
    voices_info = studio.voices()
    assert "gawr_gura_hybrid" in voices_info["voices"]
    assert "warm_male" in voices_info["eq_presets"]

    preview = studio.preview("ระบบ REST API บน Bun", voice="direct_tts", is_inline_text=True)
    assert preview["voice"] == "direct_tts"
    assert "phonetic_text" in preview and preview["tts_text"]

    script_p = tmp_path / "mini.json"
    script_p.write_text(
        json.dumps([{"slide_no": 1, "title": "Intro", "script": "สวัสดีครับ ทดสอบระบบ"}], ensure_ascii=False),
        encoding="utf-8",
    )
    res = studio.run(
        input_path=str(script_p),
        job_id="sdk_dry_test",
        voice="direct_tts",
        target="local",
        steps=["prepare", "tts", "convert"],
        voice_overrides={"fallback_voice": "", "rvc": {"enabled": False, "f0_stabilizer": False}},
        output_overrides={"dir": str(tmp_path / "out"), "eq_preset": "warm_male", "highpass_hz": 80, "video_codec": "h264"},
        dry_run=True,
    )
    assert res["status"] == "completed"


def test_02_mcp_server_exposes_all_16_tools_and_overrides(tmp_path: Path) -> None:
    list_resp = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert list_resp is not None
    tools = {t["name"]: t for t in list_resp["result"]["tools"]}
    assert len(tools) == 16
    assert "studio_colab_stop" in tools
    run_props = tools["studio_run"]["inputSchema"]["properties"]
    for key in ("voice_overrides", "output_overrides", "slides", "extra_vocab_path", "resume"):
        assert key in run_props
    cfg_props = tools["studio_config"]["inputSchema"]["properties"]
    for key in ("eq_preset", "highpass_hz", "limiter_db", "f0_stabilizer", "video_codec", "progress_bar"):
        assert key in cfg_props

    voices_resp = handle_jsonrpc_request(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "studio_voices", "arguments": {}}}
    )
    assert voices_resp is not None and not voices_resp["result"]["isError"]
    payload = json.loads(voices_resp["result"]["content"][0]["text"])
    assert "anime_female" in payload["eq_presets"]


def test_03_remote_job_spec_serializes_all_custom_fields() -> None:
    cfg = load_pipeline_config(
        overrides={
            "voice": {
                "profile": "gawr_gura_hybrid",
                "fallback_voice": "",
                "rvc": {"f0_stabilizer": False, "pre_gate_rms_ms": 18.0, "pre_gate_ramp_ms": 28.0},
            },
            "output": {
                "eq_preset": "warm_male",
                "eq_bands": [{"f": 300.0, "q": 1.4, "g": -1.5}],
                "highpass_hz": 80,
                "limiter_db": -1.4,
                "pre_rvc_conditioning": False,
                "video_codec": "h264",
                "progress_bar": False,
                "transition_frames": 12,
            },
        }
    )
    spec = _serialize_job_spec(cfg, for_remote=True)
    assert spec["voice"]["fallback_voice"] == ""
    assert spec["voice"]["rvc"]["f0_stabilizer"] is False
    assert spec["voice"]["rvc"]["pre_gate_rms_ms"] == 18.0
    assert spec["output"]["eq_preset"] == "warm_male"
    assert spec["output"]["eq_bands"] == [{"f": 300.0, "q": 1.4, "g": -1.5}]
    assert spec["output"]["highpass_hz"] == 80
    assert spec["output"]["limiter_db"] == -1.4
    assert spec["output"]["pre_rvc_conditioning"] is False
    assert spec["output"]["video_codec"] == "h264"
    assert spec["output"]["progress_bar"] is False
    assert spec["output"]["transition_frames"] == 12


def test_04_video_encoder_honors_h264_preference() -> None:
    enc = detect_best_video_encoder("h264")
    assert enc["codec"] in ("h264_nvenc", "libx264", "hevc_nvenc", "libx265", "none")
