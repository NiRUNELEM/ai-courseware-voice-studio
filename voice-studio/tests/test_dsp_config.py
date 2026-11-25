# -*- coding: utf-8 -*-
"""Unit tests for configurable DSP parameters (EQ presets, custom EQ bands, limiter, F0 stabilizer)."""

from __future__ import annotations

import numpy as np

from voice_studio.audio.mastering import resolve_eq_filters
from voice_studio.audio.stage2_rvc import _install_f0_stabilizer, condition_rvc_input_tensor
from voice_studio.config.voices import build_voice_profile, profile_to_rvc_dict


def test_01_eq_presets_and_custom_bands() -> None:
    assert len(resolve_eq_filters("anime_female")) == 4
    assert len(resolve_eq_filters("warm_male")) == 4
    assert len(resolve_eq_filters("neutral")) == 2
    assert resolve_eq_filters("off") == []
    custom = resolve_eq_filters("anime_female", eq_bands=[{"f": 300, "q": 1.5, "g": -1.5}])
    assert custom == ["equalizer=f=300:t=q:w=1.5:g=-1.5"]


def test_02_build_voice_profile_exposes_dsp_fields() -> None:
    prof = build_voice_profile(
        {
            "profile": "custom_rvc",
            "rvc": {
                "enabled": True,
                "f0_stabilizer": False,
                "pre_gate_rms_ms": 20.0,
                "pre_gate_ramp_ms": 30.0,
            },
        },
        output_block={
            "highpass_hz": 80,
            "lowpass_hz": 16000,
            "eq_preset": "warm_male",
            "comp_threshold_db": -14.0,
            "comp_ratio": 1.8,
            "gate_ratio": 6.0,
            "gate_release_ms": 140.0,
            "limiter_db": -1.2,
        },
    )
    d = profile_to_rvc_dict(prof)
    assert d["f0_stabilizer"] is False
    assert d["pre_gate_rms_ms"] == 20.0
    assert d["pre_gate_ramp_ms"] == 30.0
    assert d["highpass_hz"] == 80
    assert d["lowpass_hz"] == 16000
    assert d["eq_preset"] == "warm_male"
    assert d["comp_threshold_db"] == -14.0
    assert d["comp_ratio"] == 1.8
    assert d["gate_ratio"] == 6.0
    assert d["gate_release_ms"] == 140.0
    assert d["limiter_db"] == -1.2


def test_03_f0_stabilizer_toggle_and_pre_gate_windows() -> None:
    class DummyPipe:
        def get_f0(self, *_args, **_kwargs):
            raw = np.array([200.0, 220.0, 180.0, 230.0, 190.0, 210.0], dtype=np.float64)
            return np.zeros_like(raw, dtype=int), raw

    pipe = DummyPipe()
    _install_f0_stabilizer(pipe, enabled=False)
    _, f0_off = pipe.get_f0()
    assert float(f0_off[2]) == 180.0

    _install_f0_stabilizer(pipe, enabled=True)
    _, f0_on = pipe.get_f0()
    assert float(f0_on[2]) != 180.0

    sig = np.sin(2.0 * np.pi * 220.0 * np.arange(1600) / 16000.0).astype(np.float32) * 0.2
    out = condition_rvc_input_tensor(sig, sr=16000, rms_ms=20.0, ramp_ms=30.0)
    assert out.shape == sig.shape
