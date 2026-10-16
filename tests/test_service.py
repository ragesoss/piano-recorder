import re
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from recorder.config import REPO, load_config
from recorder.server import create_app


@pytest.fixture
def client(tmp_path):
    cfg = replace(load_config(REPO / "config" / "dev.toml"), takes_dir=tmp_path)
    with TestClient(create_app(cfg)) as c:
        yield c


def wait_for(client, state, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = client.get("/api/status").json()
        if s["state"] == state:
            return s
        time.sleep(0.05)
    raise AssertionError(f"state never became {state!r}: {s}")


def test_page_and_status(client):
    assert "Practice recorder" in client.get("/").text
    s = client.get("/api/status").json()
    assert s["state"] == "idle" and s["devices"]["capture"] is True


def test_page_fonts_are_served(client):
    """The page's fonts come from the app itself: the exercise app's (exercises/static/fonts/)."""
    names = re.findall(r"url\(/fonts/([^)]+)\)", client.get("/").text)
    assert len(names) == 2
    for name in names:
        r = client.get(f"/fonts/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == "font/woff2"
    for bad in ("nope.woff2", "OFL-public-sans.txt", "public-sans.ttf"):
        assert client.get(f"/fonts/{bad}").status_code == 404


def test_record_list_play_note_delete(client):
    take = client.post("/api/record/start").json()["take"]
    time.sleep(1.2)
    s = client.get("/api/status").json()
    assert s["state"] == "recording" and s["recording"]["seconds"] >= 1.0
    assert s["recording"]["meter_dbfs"] > -80
    assert client.post("/api/record/start").status_code == 409
    assert client.post("/api/record/stop").json()["take"] == take
    wait_for(client, "idle")

    takes = client.get("/api/takes").json()
    assert [t["id"] for t in takes] == [take]
    assert takes[0]["seconds"] >= 1.0 and takes[0]["peak_dbfs"] is not None

    assert client.post(f"/api/takes/{take}/play", json={"start": 0}).status_code == 200
    s = wait_for(client, "playing")
    assert s["playing"]["take"] == take and s["playing"]["gain_db"] != 0
    client.post("/api/stop")
    wait_for(client, "idle")

    assert client.put(f"/api/takes/{take}/note", json={"note": "left hand rushed"}).json()["note"] == "left hand rushed"
    assert client.get(f"/api/takes/{take}/audio").headers["content-type"] == "audio/wav"
    assert client.delete(f"/api/takes/{take}").status_code == 200
    assert client.get("/api/takes").json() == []


def test_bad_take_ids_are_404(client):
    assert client.post("/api/takes/../../etc/passwd/play").status_code == 404
    assert client.post("/api/takes/2026-01-01_000000/play").status_code == 404
    assert client.delete("/api/takes/nope").status_code == 404


def test_yield_stops_playback_but_not_a_recording(client):
    assert client.post("/api/yield").status_code == 200
    take = client.post("/api/record/start").json()["take"]
    assert client.post("/api/yield").status_code == 409
    client.post("/api/record/stop")
    wait_for(client, "idle")
    client.post(f"/api/takes/{take}/play", json={"start": 0})
    wait_for(client, "playing")
    assert client.post("/api/yield").status_code == 200
    wait_for(client, "idle")


def test_level_offset_is_clamped_and_kept(client, tmp_path):
    assert client.put("/api/settings", json={"level_offset_db": 20}).json()["level_offset_db"] == 12.0
    assert client.put("/api/settings", json={"level_offset_db": -40}).json()["level_offset_db"] == -30.0
    assert client.put("/api/settings", json={"level_offset_db": 6}).json()["level_offset_db"] == 6.0
    assert client.put("/api/settings", json={"level_offset_db": -8}).json()["level_offset_db"] == -8.0
    assert client.get("/api/status").json()["level_offset_db"] == -8.0


def test_a_recording_that_gets_no_audio_leaves_no_take(client, monkeypatch):
    # the device busy (another program holds it): arecord exits at once, saying so
    from recorder import audio
    monkeypatch.setattr(audio.Capture, "read", lambda self: None)
    monkeypatch.setattr(audio.Capture, "start", lambda self: self.stderr_lines.append(
        "arecord: main:850: audio open error: Device or resource busy"))
    client.post("/api/record/start")
    s = wait_for(client, "idle")
    assert "Device or resource busy" in s["error"]
    assert client.get("/api/takes").json() == []
