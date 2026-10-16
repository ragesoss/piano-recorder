"""The chord app (chords/): the chord heard, checks and labels, and letting the inputs go."""

import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chords import listen as LS
from chords.listen import DemoSource, Listener, chord_name, parse_chord
from chords.server import create_app
from recorder import dsp
from recorder.config import REPO, load_config

ALL = [n + s for n in LS.NAMES for s in ("", "m", "7")]


@pytest.fixture
def cfg(tmp_path):
    return replace(load_config(REPO / "config" / "chords-dev.toml"), takes_dir=tmp_path / "checks")


def feed(listener: Listener, src: DemoSource, seconds: float, each=None):
    """Hear `seconds` of the source, block by block, without a thread (and without waiting)."""
    fs = dsp.full_scale(src.dtype)
    for _ in range(int(round(seconds / LS.BLOCK_S))):
        listener._hear(src.read(), fs)
        if each:
            each(listener)


def demo(chords, cfg, **kw):
    return DemoSource(chords, len(cfg.inputs), cfg.capture.capture.rate, realtime=False, **kw)


def test_chord_names_go_both_ways():
    assert [parse_chord(n) for n in ("C", "F#m", "Bb7", "Eb")] == [(0, "maj"), (6, "min"), (10, "7"), (3, "maj")]
    assert all(chord_name(parse_chord(n)) == n for n in ALL)
    for bad in ("H", "Cmaj7", "", "c", "C#m7"):
        with pytest.raises(ValueError):
            parse_chord(bad)


def test_the_chord_shown_is_the_one_played(cfg):
    lis = Listener(cfg, cfg.takes_dir)
    src = demo(ALL, cfg)
    shown = []
    for k in range(len(ALL)):
        feed(lis, src, 1.5)  # 1.5 s into each chord
        shown.append(lis.now["chord"])
        feed(lis, src, 1.0)
    right = sum(a == b for a, b in zip(shown, ALL))
    assert right >= 33, list(zip(ALL, shown))  # synthesized tones: the listener's own sanity, not piano


def test_nothing_is_named_in_the_quiet(cfg):
    lis = Listener(cfg, cfg.takes_dir)
    src = demo(["G"], cfg, every_s=4.0, gap_s=3.0)  # a second of G, then three of quiet
    feed(lis, src, 0.8)
    assert lis.now["chord"] == "G"
    feed(lis, src, 3.0)
    assert lis.now["chord"] is None


def test_a_check_hears_the_chord_after_its_attack_and_keeps_the_clip(cfg):
    lis = Listener(cfg, cfg.takes_dir)
    src = demo(["Dm", "Dm", "F"], cfg)  # strikes at 0, 2.5 and 5 s, each after a quiet gap but the first
    lis.start_check(parse_chord("Dm"))
    feed(lis, src, 2.4)
    assert lis.status()["check"]["state"] == "waiting"  # a chord already sounding is not the check's
    feed(lis, src, 1.2)  # the strike at 2.5 s, judged over 2.6-3.6 s
    st = lis.status()["check"]
    assert st["state"] == "done" and st["heard"] == "Dm" and st["right"] is True
    assert st["id"] is None  # shown at once; kept once the clip's end (4.1 s) is heard
    feed(lis, src, 0.5)
    st = lis.status()["check"]
    side = json.loads((cfg.takes_dir / f"{st['id']}.json").read_text())
    assert side["kind"] == "check" and side["target"] == "Dm" and side["instrument"] == "piano"
    assert side["mixed"] == [4] and [c["input"] for c in side["channels"]] == [3, 4, 1]  # the room mic heard
    assert side["detector"].startswith("livechords@") and side["scores"][0][0] == "Dm"
    assert abs(side["seconds"] - sum(LS.CLIP_S)) < 0.11
    lis.start_check(parse_chord("Dm"))  # asked for Dm; the next strike (5 s) is F
    feed(lis, src, 2.5)
    st = lis.status()["check"]
    assert st["state"] == "done" and st["heard"] == "F" and st["right"] is False
    assert [(c["target"], c["heard"]) for c in lis.checks()] == [("Dm", "F"), ("Dm", "Dm")]
    lis.delete(st["id"])
    assert len(lis.checks()) == 1 and lis.status()["check"] is None


def test_a_label_keeps_the_last_two_seconds_with_what_it_was(cfg):
    lis = Listener(cfg, cfg.takes_dir)
    feed(lis, demo(["A7"], cfg, every_s=10.0, decay=0.3), 3.0)
    sid = lis.label(parse_chord("A"))
    side = json.loads((cfg.takes_dir / f"{sid}.json").read_text())
    assert side["kind"] == "label" and side["target"] == "A" and side["heard"] == "A7" and side["right"] is False
    assert abs(side["seconds"] - LS.LABEL_S) < 0.01


def test_the_page_listens_while_it_asks_and_lets_the_inputs_go(cfg):
    src = lambda: DemoSource(["G", "C"], len(cfg.inputs), cfg.capture.capture.rate)  # noqa: E731
    with TestClient(create_app(cfg, src)) as c:
        assert c.get("/api/status").json()["state"] == "idle"
        assert c.post("/api/listen", json={"inputs": [3, 4], "instrument": "guitar"}).status_code == 200
        time.sleep(1.2)
        st = c.get("/api/status").json()
        assert st["state"] == "listening" and st["chord"] == "G" and st["mix"] == [3, 4] and st["instrument"] == "guitar"
        assert c.post("/api/listen", json={"inputs": [9]}).status_code == 400
        assert c.post("/api/check", json={"target": "H"}).status_code == 400
        assert c.post("/api/yield").status_code == 200  # a recorder about to record
        assert c.get("/api/status").json()["state"] == "waiting"
        c.post("/api/listen")
        assert c.get("/api/status").json()["state"] == "waiting"  # still letting go
        assert c.get("/api/inputs").json()[1] == {"input": 4, "channel": 1, "name": "Room mic",
                                                  "details": "The condenser, pointed at the player", "for": ""}


def test_the_copied_listener_is_the_projects():
    src = REPO.parent / "dobro-transcription" / "pipeline" / "src" / "transcribe" / "livechords.py"
    if not src.exists():
        pytest.skip("dobro-transcription is not beside this repo")
    copy = (REPO / "chords" / "livechords.py").read_text().split("\n", 2)[2]
    assert copy == src.read_text(), "the chord listener changed in dobro-transcription: run tools/sync-livechords"
