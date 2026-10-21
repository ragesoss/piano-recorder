"""What the exercise app's page needs to choose the next take and show it: the inputs and what each
carries, the choices as left, the inputs' levels between takes, a free take over a click, a backing
heard on its own, and a take's waveform."""

import json
import time
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from exercises.server import create_app
from recorder.config import REPO, load_config
from recorder.playalong import click_track
from test_exercises import add_exercise, wait_idle
from test_playalong import backing_signal, write_wav

INSTRUMENTS = [
    {"id": "beard", "kind": "dobro", "short": "Beard Radio Standard E", "name": "Beard Radio Standard E", "details": ""},
    {"id": "cleveland", "kind": "bass", "short": "New Standard Cleveland", "name": "New Standard Cleveland", "details": ""},
    {"id": "d1", "kind": "guitar", "short": "Martin D-1", "name": "Martin D-1", "details": ""},
]


@pytest.fixture
def client(tmp_path):
    cfg = replace(load_config(REPO / "config" / "exercises-dev.toml"), takes_dir=tmp_path / "takes",
                  backing_dir=tmp_path / "backing", exercises_dir=tmp_path / "exercises")
    cfg.exercises_dir.mkdir()
    cfg.backing_dir.mkdir()
    add_exercise(cfg.exercises_dir, "bass-001-walks")
    (cfg.exercises_dir / "status.json").write_text(json.dumps({"exercises": {}, "instruments": INSTRUMENTS}))
    write_wav(cfg.backing_dir / "tune.wav", backing_signal(3.0))
    (cfg.backing_dir / "tune.json").write_text(json.dumps({"title": "Tune", "added": "2026-10-15T21:02:00"}))
    write_wav(cfg.backing_dir / "older.wav", backing_signal(2.0, seed=2))
    with TestClient(create_app(cfg)) as c:
        yield c


def take(client, take_id):
    return next(t for t in client.get("/api/takes").json() if t["id"] == take_id)


def record_briefly(client, body, seconds=0.5):
    r = client.post("/api/record/start", json=body)
    assert r.status_code == 200, r.text
    take_id = r.json()["take"]
    time.sleep(seconds)
    client.post("/api/record/stop")
    wait_idle(client)
    return take_id


def test_the_inputs_are_listed_with_their_place_in_a_take(client):
    # every input of the device, by number; the recorded ones (before the room's inputs file: the config's
    # keep_channels, in its order) with their channel
    got = client.get("/api/inputs").json()["inputs"]
    assert [(i["input"], i["channel"], i["record"]) for i in got] == [(1, 2, True), (2, None, False), (3, 0, True),
                                                                      (4, 1, True)]
    assert {k: got[2][k] for k in ("name", "details", "for", "named")} == {
        "name": "Nadine", "details": "Ear Trumpet Labs Nadine, the upright bass's mic, phantom power", "for": "bass",
        "named": True}  # the room's list's
    assert {k: got[1][k] for k in ("name", "named", "socket")} == {"name": "Input 2", "named": False,
                                                                   "socket": "Fake 2"}


def test_the_choices_come_back_as_left(client):
    assert client.get("/api/choices").json() == {}
    mine = {"instruments": ["cleveland"], "kind": "exercise", "exercise": "bass-001-walks", "tempo": {"bass-001-walks": 200}}
    assert client.put("/api/choices", json=mine).status_code == 200
    back = client.get("/api/choices").json()
    assert {k: back[k] for k in mine} == mine and back["saved"]
    assert client.put("/api/choices", json={"x": "y" * 30000}).status_code == 413
    assert client.put("/api/choices", json=[1, 2]).status_code == 422


def test_a_take_on_two_instruments_says_what_each_channel_carried(client, tmp_path):
    t = take(client, record_briefly(client, {"instruments": ["d1", "cleveland"], "click": False}))
    assert t["kind"] == "free" and t["instrument_id"] is None
    assert t["instrument_name"] == "Martin D-1 and New Standard Cleveland"
    assert [i["instrument_id"] for i in t["instruments"]] == ["d1", "cleveland"]
    # by default: the bass's pickups carry the bass, the room mic the guitar (it has no pickup)
    assert [(c["input"], c["carries"]) for c in t["channels"]] == [(3, "cleveland"), (4, "d1"), (1, "cleveland")]
    assert t["channels"][1] == {"channel": 1, "input": 4, "input_name": "MXL 990", "carries": "d1",
                                "carries_name": "Martin D-1", "input_details": "MXL 990: cardioid large-diaphragm condenser, phantom power"}
    assert t["channels"][0]["input_for"] == "bass" and "input_for" not in t["channels"][1]
    side = json.loads((tmp_path / "takes" / f"{t['id']}.json").read_text())
    assert "instrument" not in side  # two kinds: no one kind for the take


def test_one_instrument_with_pickups_leaves_the_room_mic_to_the_room(client):
    t = take(client, record_briefly(client, {"exercise": "bass-001-walks", "instruments": ["cleveland"]}, 0.2))
    assert t["kind"] == "exercise" and t["instrument_id"] == "cleveland"
    assert [c["carries"] for c in t["channels"]] == ["cleveland", "room", "cleveland"]
    t = take(client, record_briefly(client, {"instruments": ["beard"], "click": False}))
    assert [c["carries"] for c in t["channels"]] == ["none", "beard", "none"]  # a dobro: the room mic only


def test_what_each_input_carries_can_be_said_and_is_checked(client):
    body = {"instruments": ["cleveland"], "inputs": {"1": "none", "3": "cleveland", "4": "cleveland"}, "click": False}
    t = take(client, record_briefly(client, body))
    assert [(c["input"], c["carries"]) for c in t["channels"]] == [(3, "cleveland"), (4, "cleveland"), (1, "none")]
    for bad in ({"inputs": {"4": "d1"}}, {"inputs": {"2": "cleveland"}}, {"inputs": {"x": "room"}}):
        assert client.post("/api/record/start", json={"instruments": ["cleveland"], **bad}).status_code == 400, bad
    assert client.post("/api/record/start", json={"instruments": ["cleveland", "cleveland"]}).status_code == 400
    assert client.post("/api/record/start", json={"instruments": ["beard", "d1", "cleveland"]}).status_code == 400
    assert client.post("/api/record/start", json={"exercise": "bass-001-walks",
                                                  "instruments": ["cleveland", "d1"]}).status_code == 400


def test_each_input_has_its_own_meter_while_recording(client):
    client.post("/api/record/start", json={"instruments": ["cleveland"], "click": False})
    time.sleep(0.6)
    rec = client.get("/api/status").json()["recording"]
    client.post("/api/record/stop")
    wait_idle(client)
    m = rec["meters_dbfs"]
    # the fake device: each channel 6 dB under the one before; kept in the order 2, 3, 0
    assert len(m) == 3 and len(rec["peaks_dbfs"]) == 3
    assert m[0] < m[2] and m[1] < m[0]


def test_a_free_click_has_no_end_and_its_take_is_lined_up_again_from_the_beats_it_played(client, tmp_path):
    take_id = client.post("/api/record/start", json={"instruments": ["d1"], "click": True, "tempo_bpm": 180,
                                                     "beats_per_bar": 3}).json()["take"]
    time.sleep(0.5)
    bk = client.get("/api/status").json()["recording"]["backing"]
    assert bk["kind"] == "click" and bk["seconds"] is None and bk["tempo"]["beats_per_bar"] == 3
    time.sleep(1.5)  # a bar's count-in (1 s at 180) and some beats
    client.post("/api/record/stop")
    wait_idle(client)
    t = take(client, take_id)
    side = json.loads((tmp_path / "takes" / f"{take_id}.json").read_text())
    assert side["click"]["beats"] >= 1 and side["click"]["count_in_bars"] == 1
    _, beat0 = click_track({"bpm": 180, "pickup_beats": 0, "beats_per_bar": 3}, 48000, side["click"]["beats"])
    assert t["playalong"]["found"] and abs(t["beat0_s"] - (t["playalong"]["lag_ms"] / 1000 + beat0)) < 1e-3
    again = client.post(f"/api/takes/{take_id}/lineup").json()
    assert abs(again["beat0_s"] - t["beat0_s"]) < 1e-3
    assert client.post("/api/record/start", json={"click": True, "tempo_bpm": 400}).status_code == 400
    assert client.post("/api/record/start", json={"click": True, "tempo_bpm": 90, "beats_per_bar": 20}).status_code == 400


def test_the_inputs_are_heard_between_takes_until_a_peer_wants_them(client):
    assert client.get("/api/status").json()["monitor"] is None
    client.post("/api/monitor")
    time.sleep(0.5)
    mon = client.get("/api/status").json()["monitor"]
    assert len(mon["meters_dbfs"]) == 3 and mon["meters_dbfs"][1] < mon["meters_dbfs"][0]
    take_id = record_briefly(client, {"click": False}, 0.3)  # a take stops the monitor and has its own meters
    assert take_id and client.get("/api/status").json()["monitor"] is None
    client.post("/api/monitor")
    time.sleep(0.3)
    assert client.get("/api/status").json()["monitor"] is not None
    assert client.post("/api/yield").status_code == 200  # a peer about to record: let the inputs go, a while
    client.post("/api/monitor")
    time.sleep(0.3)
    assert client.get("/api/status").json()["monitor"] is None


def test_a_backing_can_be_heard_on_its_own_and_says_when_it_came(client):
    bks = {b["id"]: b for b in client.get("/api/backings").json()}
    assert bks["tune"]["added"] == "2026-10-15T21:02:00" and bks["older"]["added"]  # the file's time
    assert client.post("/api/backings/tune/play", json={"start": 1.0}).status_code == 200
    time.sleep(0.4)
    pl = client.get("/api/status").json()["playing"]
    assert pl["backing"] == "tune" and pl["take"] is None and pl["position"] >= 1.0 and pl["seconds"] == pytest.approx(3.0, abs=0.1)
    client.post("/api/stop")
    assert client.get("/api/status").json()["playing"] is None
    assert client.post("/api/backings/nope/play").status_code == 404


def test_a_take_has_a_waveform(client):
    take_id = record_briefly(client, {"click": False}, 0.6)
    peaks = client.get(f"/api/takes/{take_id}/peaks", params={"n": 50}).json()["peaks"]
    assert len(peaks) == 50 and all(0 <= p <= 1 for p in peaks) and max(peaks) > 0
    assert client.get(f"/api/takes/{take_id}/peaks", params={"n": 5}).status_code == 400
    assert client.get("/api/takes/2000-01-01_000000/peaks").status_code == 404
