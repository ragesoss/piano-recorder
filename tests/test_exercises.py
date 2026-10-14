import json
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from exercises.server import create_app
from recorder.config import REPO, load_config
from recorder.playalong import click_track
from recorder.server import create_app as create_piano_app

PAGE = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'


def add_exercise(root, ex_id, bpm=240, pickup=1.0, bars=2, beats_per_bar=4, pages=1):
    d = root / ex_id
    d.mkdir(parents=True)
    names = []
    for n in range(1, pages + 1):
        (d / f"page-{n}.svg").write_text(PAGE)
        names.append(f"page-{n}.svg")
    (d / "exercise.json").write_text(json.dumps({
        "title": f"Exercise {ex_id}", "instrument": "bass", "bars": bars, "pages": names,
        "tempo": {"bpm": bpm, "pickup_beats": pickup, "beats_per_bar": beats_per_bar},
        "beats": pickup + bars * beats_per_bar}))


@pytest.fixture
def client(tmp_path):
    cfg = replace(load_config(REPO / "config" / "exercises-dev.toml"), takes_dir=tmp_path / "takes",
                  backing_dir=tmp_path / "backing", exercises_dir=tmp_path / "exercises")
    cfg.exercises_dir.mkdir()
    add_exercise(cfg.exercises_dir, "bass-001-walks", pages=2)
    add_exercise(cfg.exercises_dir, "bass-002-roots")
    with TestClient(create_app(cfg)) as c:
        yield c


def wait_idle(client, limit=20):
    end = time.monotonic() + limit
    while time.monotonic() < end and client.get("/api/status").json()["state"] != "idle":
        time.sleep(0.1)


def test_exercises_and_their_pages_are_listed(client):
    exs = client.get("/api/exercises").json()
    assert [e["id"] for e in exs] == ["bass-001-walks", "bass-002-roots"]
    assert exs[0]["pages"] == ["page-1.svg", "page-2.svg"] and exs[0]["tempo"]["bpm"] == 240
    r = client.get("/api/exercises/bass-001-walks/pages/2")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert client.get("/api/exercises/bass-001-walks/pages/3").status_code == 404
    assert client.get("/api/exercises/../pages/1").status_code == 404


def test_an_exercise_take_over_its_click_knows_where_its_first_note_is(client):
    take = client.post("/api/record/start", json={"exercise": "bass-001-walks", "tempo_bpm": 200}).json()["take"]
    time.sleep(0.4)
    bk = client.get("/api/status").json()["recording"]["backing"]
    assert bk["kind"] == "click" and bk["tempo"]["bpm"] == 200 and bk["beat0_s"] > 0
    wait_idle(client)  # the click (a bar's count-in, then 9 beats at 200), the tail, the line-up
    t = next(x for x in client.get("/api/takes").json() if x["id"] == take)
    assert t["exercise"] == "bass-001-walks" and t["exercise_title"] == "Exercise bass-001-walks"
    assert t["tempo_bpm"] == 200
    _, beat0 = click_track({"bpm": 200, "pickup_beats": 1.0, "beats_per_bar": 4}, 48000, 9.0)
    pa = t["playalong"]
    # the fake device hears its own playback 9 ms late; the click starts after the first capture block
    assert pa["found"] and abs(t["beat0_s"] - (pa["lag_ms"] / 1000 + beat0)) < 1e-3
    assert 0.009 <= t["beat0_s"] - beat0 < 0.5
    assert not t["clean"]  # the click stays in the take
    assert [x["id"] for x in client.get("/api/takes", params={"exercise": "bass-001-walks"}).json()] == [take]
    assert client.get("/api/takes", params={"exercise": "bass-002-roots"}).json() == []
    again = client.post(f"/api/takes/{take}/lineup").json()
    assert abs(again["beat0_s"] - t["beat0_s"]) < 1e-3


def test_without_the_click_through_only_the_count_in_plays(client):
    take = client.post("/api/record/start", json={"exercise": "bass-002-roots", "click": False}).json()["take"]
    time.sleep(0.3)
    s = client.get("/api/status").json()["recording"]["backing"]
    # the count-in only: it ends before the first note (4 beats at 240 bpm, then 0.3 s)
    assert s["seconds"] < s["beat0_s"] + 0.5
    wait_idle(client)
    t = next(x for x in client.get("/api/takes").json() if x["id"] == take)
    assert t["exercise"] == "bass-002-roots"


def test_a_take_without_an_exercise_or_click_is_a_plain_recording(client):
    take = client.post("/api/record/start", json={}).json()["take"]
    time.sleep(0.4)
    assert client.get("/api/status").json()["recording"]["backing"] is False
    client.post("/api/record/stop")
    wait_idle(client)
    t = next(x for x in client.get("/api/takes").json() if x["id"] == take)
    assert t["exercise"] is None and t["playalong"] is None


def test_unknown_exercises_and_tempos_are_refused(client):
    assert client.post("/api/record/start", json={"exercise": "nope"}).status_code == 404
    assert client.post("/api/record/start", json={"exercise": "bass-002-roots", "tempo_bpm": 900}).status_code == 400


def test_the_piano_recorder_no_longer_makes_play_along_takes(tmp_path):
    cfg = replace(load_config(REPO / "config" / "dev.toml"), takes_dir=tmp_path / "takes", backing_dir=tmp_path / "b")
    with TestClient(create_piano_app(cfg)) as c:
        assert c.post("/api/playalong/start", json={"backing": "x"}).status_code in (404, 405)
        assert c.get("/api/backings").status_code in (404, 405)



def test_the_listing_says_what_the_project_has_and_what_is_new_here(client, tmp_path):
    exdir = tmp_path / "exercises"
    status = {"generated": "2026-10-14T10:00:00-07:00", "exercises": {
        "bass-001-walks": {"status": "good", "note": "", "app_takes": ["2000-01-01_000000"]},
        "bass-002-roots": {"status": "wanted", "note": "cleaner, please", "app_takes": []}},
        "instruments": [{"id": "cleveland", "kind": "bass", "short": "Cleveland", "name": "New Standard Cleveland",
                         "details": "Perpetual Stark"}]}
    (exdir / "status.json").write_text(json.dumps(status))
    exs = {e["id"]: e for e in client.get("/api/exercises").json()}
    assert exs["bass-001-walks"]["status"] == "good" and exs["bass-001-walks"]["to_review"] == 0
    assert exs["bass-002-roots"]["status"] == "wanted" and exs["bass-002-roots"]["status_note"] == "cleaner, please"
    assert client.get("/api/instruments").json()[0]["short"] == "Cleveland"
    assert client.post("/api/record/start", json={"exercise": "bass-002-roots", "instrument_id": "nope"}).status_code == 400
    take = client.post("/api/record/start", json={"exercise": "bass-001-walks", "instrument_id": "cleveland"}).json()["take"]
    wait_idle(client)
    t = next(x for x in client.get("/api/takes").json() if x["id"] == take)
    assert t["instrument_id"] == "cleveland" and t["instrument_name"] == "New Standard Cleveland"
    ex = next(e for e in client.get("/api/exercises").json() if e["id"] == "bass-001-walks")
    assert ex["takes"] == 1 and ex["to_review"] == 1 and ex["last_take"]
    status["exercises"]["bass-001-walks"]["app_takes"].append(take)  # brought into the project
    (exdir / "status.json").write_text(json.dumps(status))
    assert next(e for e in client.get("/api/exercises").json() if e["id"] == "bass-001-walks")["to_review"] == 0


def test_without_a_status_file_the_app_still_lists_and_records(client):
    exs = client.get("/api/exercises").json()
    assert all(e["status"] == "" and e["to_review"] == 0 for e in exs)
    assert client.get("/api/instruments").json() == []
