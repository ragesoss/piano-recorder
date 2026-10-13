import json
import time
import wave
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from recorder import dsp
from recorder.config import REPO, load_config
from recorder.playalong import align_and_clean, click_track, find_click
from exercises.server import create_app  # play-along takes are made in the exercise app


def write_wav(path, x, rate=48000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(dsp.from_float(x.astype(np.float32), np.dtype("<i2")).tobytes())


def backing_signal(seconds, rate=48000, seed=1):
    t = np.arange(int(seconds * rate)) / rate
    rng = np.random.default_rng(seed)
    return 0.3 * np.sin(2 * np.pi * 196 * t) * (np.mod(t, 0.5) < 0.3) + 0.05 * rng.normal(size=len(t))


@pytest.fixture
def client(tmp_path):
    cfg = replace(load_config(REPO / "config" / "exercises-dev.toml"), takes_dir=tmp_path / "takes",
                  backing_dir=tmp_path / "backing", exercises_dir=tmp_path / "exercises")
    cfg.backing_dir.mkdir()
    write_wav(cfg.backing_dir / "test-tune.wav", backing_signal(3.0))
    (cfg.backing_dir / "test-tune.json").write_text(json.dumps({"title": "Test tune", "source": "nowhere"}))
    write_wav(cfg.backing_dir / "counted.wav", backing_signal(2.0, seed=2))
    (cfg.backing_dir / "counted.json").write_text(json.dumps({
        "title": "Counted", "tempo": {"bpm": 120, "first_note_s": 0.5, "pickup_beats": 1.5, "beats_per_bar": 4}}))
    with TestClient(create_app(cfg)) as c:
        yield c


def test_align_and_clean_finds_a_short_path_to_the_sample():
    rate, n, lag = 48000, 48000 * 10, 4321
    backing = backing_signal(10).astype(np.float32)
    t = np.arange(n) / rate
    inst = 0.2 * np.sin(2 * np.pi * 98 * t) * np.exp(-np.mod(t, 1.0) * 3)
    take = inst.copy()
    take[lag:] += np.convolve(backing, [0.028, 0.012, -0.004])[: n - lag]
    al, clean = align_and_clean(take, backing, rate)
    assert al.found and al.lag == lag
    assert al.backing_dbfs - al.left_dbfs > 40
    assert dsp.rms_dbfs(clean - inst) - dsp.rms_dbfs(inst) < -35


@pytest.mark.parametrize("bpm, pickup", [(80, 0.0), (110, 1.0), (300, 0.0)])
def test_a_click_is_found_at_the_start_to_the_sample(bpm, pickup):
    """A click in a take under a louder bass, as the exercise app records it: found to the
    sample, also at 300 bpm, where the search is narrowed to under one beat."""
    rate, lag = 48000, 8160  # 170 ms: the Pi's takes, 2026-10-13, were 160-190 ms
    click, _ = click_track({"bpm": bpm, "pickup_beats": pickup, "beats_per_bar": 4}, rate, 32.0)
    n = lag + len(click) + rate
    t = np.arange(n) / rate
    tau = np.mod(t, 60 / bpm)  # a bass note on every beat, 5 ms attacks
    take = 0.1 * np.sin(2 * np.pi * 55 * t) * (1 - np.exp(-tau / 0.005)) * np.exp(-tau * 4)
    take += 1e-4 * np.random.default_rng(bpm).normal(size=n)
    # the click returned by the mixer: -62 to -68 dBFS rms (the Pi's takes: -49 to -64)
    take[lag:lag + len(click)] += 0.05 * click
    al = find_click(take, click, rate, 60 / bpm)
    assert al.found and al.lag == lag
    assert al.as_dict()["taps"] is None  # not fitted, not taken out


def test_no_click_in_the_take_is_reported_not_found():
    click, _ = click_track({"bpm": 80, "pickup_beats": 0.0, "beats_per_bar": 4}, 48000, 16.0)
    al = find_click(np.random.default_rng(4).normal(0, 0.1, len(click)), click, 48000, 0.75)
    assert not al.found


def test_no_backing_in_the_take_is_reported_not_found():
    rng = np.random.default_rng(3)
    al, clean = align_and_clean(rng.normal(0, 0.1, 48000 * 3), backing_signal(2), 48000)
    assert not al.found


def test_playalong_take_is_lined_up_and_cleaned(client):
    assert [b["id"] for b in client.get("/api/backings").json()] == ["counted", "test-tune"]
    assert client.post("/api/record/start", json={"backing": "nope"}).status_code == 404
    take = client.post("/api/record/start", json={"backing": "test-tune"}).json()["take"]
    time.sleep(0.5)
    s = client.get("/api/status").json()
    assert s["state"] == "recording" and s["recording"]["backing"]["title"] == "Test tune"
    end = time.monotonic() + 15
    while time.monotonic() < end and client.get("/api/status").json()["state"] != "idle":
        time.sleep(0.1)  # the backing (3 s), the tail (2 s), then the analysis
    t = client.get("/api/takes").json()[0]
    assert t["id"] == take and t["backing"] == "Test tune" and t["clean"]
    assert 4.5 < t["seconds"] < 7
    pa = t["playalong"]
    # the fake device: the capture starts first, the backing a moment later, heard 9 ms after it plays
    assert pa["found"] and 9 <= pa["lag_ms"] < 500
    assert pa["backing_dbfs"] - pa["left_dbfs"] > 25
    assert client.get(f"/api/takes/{take}/clean").headers["content-type"] == "audio/wav"
    assert client.delete(f"/api/takes/{take}").status_code == 200
    assert client.get(f"/api/takes/{take}/clean").status_code == 404


def test_stopping_a_playalong_take_early_stops_the_backing(client):
    client.post("/api/record/start", json={"backing": "test-tune"})
    time.sleep(1.2)
    client.post("/api/record/stop")
    end = time.monotonic() + 10
    while time.monotonic() < end and client.get("/api/status").json()["state"] != "idle":
        time.sleep(0.1)
    t = client.get("/api/takes").json()[0]
    assert t["seconds"] < 2.5 and t["playalong"]["found"]


def test_a_take_can_be_lined_up_again(client):
    take = client.post("/api/record/start", json={"backing": "test-tune"}).json()["take"]
    end = time.monotonic() + 15
    while time.monotonic() < end and client.get("/api/status").json()["state"] != "idle":
        time.sleep(0.1)
    first = client.get("/api/takes").json()[0]["playalong"]
    again = client.post(f"/api/takes/{take}/lineup").json()["playalong"]
    assert again["found"] and abs(again["lag_samples"] - first["lag_samples"]) <= 1


def test_count_in_clicks_for_down_yonder():
    from recorder.playalong import count_in
    clicks, lead, times = count_in({"bpm": 120, "first_note_s": 0.5, "pickup_beats": 1.5}, 48000)
    assert times == [-2.75, -2.25, -1.75, -1.25, -0.75, -0.25, 0.25]
    assert abs(lead - 2.95) < 1e-9 and np.max(np.abs(clicks)) > 0.1


def test_playalong_with_a_count_in_says_where_the_backing_starts(client):
    take = client.post("/api/record/start", json={"backing": "counted"}).json()["take"]
    time.sleep(0.6)
    bk = client.get("/api/status").json()["recording"]["backing"]
    assert bk["count_in_s"] > 2.9 and bk["position"] < bk["count_in_s"]
    end = time.monotonic() + 20
    while time.monotonic() < end and client.get("/api/status").json()["state"] != "idle":
        time.sleep(0.1)
    pa = next(t for t in client.get("/api/takes").json() if t["id"] == take)["playalong"]
    assert pa["found"]
    assert pa["backing_starts_samples"] - pa["lag_samples"] == round(2.95 * 48000)
