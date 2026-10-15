import time
from pathlib import Path

import numpy as np
import pytest

from recorder import audio, dsp
from recorder.config import REPO, ConfigError, load_config, load_profile


def test_both_configs_load():
    pi = load_config(REPO / "config" / "practice.toml")
    assert pi.capture.name == "scarlett-18i8-gen3" and pi.playback.name == "scarlett-18i8-gen3"
    assert pi.capture.capture.keep_channels == (2, 3)
    assert pi.loudness.target_dbfs == -21.0
    assert pi.takes_dir == Path.home() / "takes"
    dev = load_config(REPO / "config" / "dev.toml")
    assert dev.capture.backend == "fake"
    assert dev.takes_dir == REPO / "takes-dev"


def test_takes_dir_expands_home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / "c.toml"
    cfg.write_text('capture = "fake"\nplayback = "fake"\ntakes_dir = "~/takes"\n')
    assert load_config(cfg).takes_dir == tmp_path / "takes"


def test_missing_profile_is_a_config_error(tmp_path: Path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('capture = "nope"\nplayback = "fake"\ntakes_dir = "t"\n')
    with pytest.raises(ConfigError, match="no profile 'nope'"):
        load_config(cfg)


def test_unsupported_format_says_so():
    with pytest.raises(NotImplementedError, match="S24_3LE"):
        dsp.dtype_for("S24_3LE")


def sine(level_dbfs: float, seconds: float, rate: int = 48000, freq: float = 440.0):
    t = np.arange(int(seconds * rate)) / rate
    return (10 ** (level_dbfs / 20) * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_gated_rms_ignores_silence():
    rate = 48000
    x = np.concatenate([sine(-20, 2.0), np.zeros(rate * 2, dtype=np.float32)])
    # a sine's rms is 3 dB under its peak
    assert dsp.gated_rms_dbfs(x, rate) == pytest.approx(-23.0, abs=0.1)
    assert dsp.gated_rms_dbfs(np.zeros(rate, dtype=np.float32), rate) is None


def test_limiter_keeps_peaks_under_the_ceiling():
    rate = 48000
    x = sine(-12, 1.0)
    x[24000:24050] = 0.99  # a hard attack
    y = dsp.limit(x * 3, rate, ceiling_dbfs=-1.0)
    assert dsp.peak_dbfs(y) <= -1.0 + 1e-3
    # away from the attack, the boosted sine (peak -2.5 dBFS) passes untouched
    assert dsp.peak_dbfs(y[2000:20000]) == pytest.approx(dsp.peak_dbfs(x[2000:20000] * 3), abs=0.05)


def test_prepare_for_playback_reaches_the_target():
    rate = 48000
    rng = np.random.default_rng(1)
    # piano-like: quiet body with a few loud attacks (crest factor ~20 dB)
    x = sine(-36, 6.0, freq=262.0)
    for k in range(6):
        x[k * rate: k * rate + 200] += rng.uniform(-0.3, 0.3, 200).astype(np.float32)
    y, gain = dsp.prepare_for_playback(x, rate, target_dbfs=-21.0, limit_dbfs=-1.0, max_gain_db=30.0)
    assert dsp.peak_dbfs(y) <= -1.0 + 1e-3
    assert dsp.gated_rms_dbfs(y, rate) == pytest.approx(-21.0, abs=1.0)
    assert gain > 10


def test_max_gain_caps_quiet_takes():
    rate = 48000
    y, gain = dsp.prepare_for_playback(sine(-65, 2.0), rate, -21.0, -1.0, max_gain_db=30.0)
    assert gain == 30.0


def test_fake_capture_shapes_blocks_and_runs_in_real_time():
    p = load_profile("fake")
    t0 = time.monotonic()
    with audio.Capture(p) as cap:
        blocks = [cap.read() for _ in range(5)]
    elapsed = time.monotonic() - t0
    assert all(b.shape == (4800, 1) and b.dtype == np.int16 for b in blocks)
    assert 0.4 < elapsed < 1.0
    with audio.Capture(p, raw=True) as cap:
        raw = cap.read()
    assert raw.shape == (4800, 2)
    assert np.array_equal(raw[:, 0], raw[:, 1])


def test_fake_playback_takes_mono_and_paces():
    p = load_profile("fake")
    t0 = time.monotonic()
    with audio.Playback(p) as pb:
        assert pb.write(np.zeros(24000, dtype=np.float32))
    assert 0.4 < time.monotonic() - t0 < 0.8


def test_profiles_load():
    bmg = load_profile("bmg-04f")
    assert bmg.capture.keep_channels == (0,) and bmg.capture.controls["gain"].set == 127
    s = load_profile("scarlett-18i8-gen3")
    assert (s.card, s.usb_id, s.loopback) == ("USB", "1235:8214", False)
    assert (s.capture.format, s.capture.rate, s.capture.channels) == ("S32_LE", 48000, 20)
    assert s.capture.keep_channels == (2, 3)
    assert s.capture.controls["phantom_3_4"].set == "on"
    assert s.playback.channels == 8
    assert [s.playback.controls[k].set for k in ("out_01", "out_02")] == ["Mix A", "Mix B"]


def test_playback_mixes_a_take_down_to_the_device_channels():
    from dataclasses import replace
    p = load_profile("fake")
    p = replace(p, playback=replace(p.playback, channels=8))
    with audio.Playback(p) as pb:
        assert pb.write(np.zeros((4800, 2), dtype=np.float32))
        assert pb.write(np.zeros((4800, 8), dtype=np.float32))


def test_dc_remover_takes_out_a_constant_offset():
    rate = 48000
    dcr = dsp.DCRemover(rate)
    block = np.full((4800, 1), -4, dtype=np.int16) + (np.arange(4800) % 3 - 1).astype(np.int16)[:, None]
    out = [dcr.process(block) for _ in range(20)][-1]
    assert abs(out.mean()) < 0.1
