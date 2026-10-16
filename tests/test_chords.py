"""The chord app (chords/): the chord heard, checks and labels, and letting the inputs go."""

import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chords import listen as LS
from chords.listen import DemoSource, Listener, chord_name, parse_chord, parse_target
from chords.server import create_app
from recorder import dsp
from recorder.config import REPO, load_config

ALL = [n + s for n in LS.NAMES for s in ("", "m", "7", "dim", "dim7")]


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
    assert [parse_chord(n) for n in ("C", "F#m", "Bb7", "Eb", "Bdim", "C/E", "Am/C", "C/C")] == [
        ((0, "maj"), None), ((6, "min"), None), ((10, "7"), None), ((3, "maj"), None), ((11, "dim"), None),
        ((0, "maj"), 4), ((9, "min"), 0), ((0, "maj"), None)]
    assert all(chord_name(*parse_chord(n)) == n for n in ALL + ["C/E", "G/B", "D/F#", "Am/C"])
    for bad in ("H", "Cmaj7", "", "c", "C#m7", "C/H", "C/"):
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
    right = sum(a is not None and LS.same_chord(LS.parse_chord(a)[0], LS.parse_chord(b)[0]) for a, b in zip(shown, ALL))
    assert right >= 55, [x for x in zip(ALL, shown) if x[0] != x[1]]  # of 60: the listener's sanity, not piano


def test_inversions_are_named_by_whats_in_the_bass(cfg):
    lis = Listener(cfg, cfg.takes_dir)
    played = ["C/E", "C/G", "Am/C", "G/B", "D/F#", "C", "Cdim7/Eb"]
    src = demo(played, cfg)
    shown = []
    for _ in played:
        feed(lis, src, 1.5)
        shown.append((lis.now["chord"], lis.now["bass"]))
        feed(lis, src, 1.0)
    assert shown == [("C/E", "E"), ("C/G", "G"), ("Am/C", "C"), ("G/B", "B"), ("D/F#", "F#"), ("C", "C"),
                     ("Ebdim7", "Eb")], shown


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
    lis.start_check(parse_target("Dm"))
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
    lis.start_check(parse_target("Dm"))  # asked for Dm; the next strike (5 s) is F
    feed(lis, src, 2.5)
    st = lis.status()["check"]
    assert st["state"] == "done" and st["heard"] == "F" and st["right"] is False
    assert [(c["target"], c["heard"], c["chord_right"], c["bass_right"]) for c in lis.checks()] == [
        ("Dm", "F", False, False), ("Dm", "Dm", True, True)]
    lis.delete(st["id"])
    assert len(lis.checks()) == 1 and lis.status()["check"] is None


def test_an_inversion_check_judges_the_chord_and_the_bass(cfg):
    lis = Listener(cfg, cfg.takes_dir)
    src = demo(["C", "C/E", "C/G"], cfg)
    lis.start_check(parse_target("C/E"))
    feed(lis, src, 3.8)  # the strike at 2.5 s: C/E
    st = lis.status()["check"]
    assert (st["target"], st["heard"], st["right"], st["bass"]) == ("C/E", "C/E", True, "E")
    lis.start_check(parse_target("C/E"))
    feed(lis, src, 2.5)  # at 5 s: C/G, the chord right and the bass not
    st = lis.status()["check"]
    assert (st["heard"], st["chord_right"], st["bass_right"], st["right"]) == ("C/G", True, False, False)


def test_a_single_note_and_two_notes_are_named_as_such(cfg):
    lis = Listener(cfg, cfg.takes_dir)
    played = ["E4", "C+G", "A3", "C/E", "D+F#", "C"]
    src = demo(["-"] + played, cfg)
    feed(lis, src, 2.5)  # a rest first: the quiet a single note is heard over
    shown = []
    for _ in played:
        feed(lis, src, 1.5)
        shown.append((lis.now["kind"], lis.now["chord"], lis.now["detail"]))
        feed(lis, src, 1.0)
    assert shown == [("note", "E4", "E4, a single note"), ("pair", "C+G", "C3 and G3, a fifth"),
                     ("note", "A3", "A3, a single note"), ("chord", "C/E", "C major, E in the bass"),
                     ("pair", "D+F#", "D3 and F#3, a major third"), ("chord", "C", "C major")], shown


def test_note_and_pair_checks_judge_what_was_asked(cfg):
    assert parse_target("E4").midi == 64 and parse_target("Cb4").midi == 59 and parse_target("B#3").midi == 60
    assert parse_target("C+G").pcs == (0, 7) and parse_target("C/E").kind == "chord"
    for bad in ("C+C", "E10", "C++G"):
        with pytest.raises(ValueError):
            parse_target(bad)
    lis = Listener(cfg, cfg.takes_dir)
    src = demo(["E4", "E4", "E3", "C+G"], cfg)
    feed(lis, src, 2.4)
    for target, want in (("E4", True), ("E4", False), ("C+G", True)):  # the strikes at 2.5, 5 and 7.5 s
        lis.start_check(parse_target(target))
        feed(lis, src, 2.5)
        st = lis.status()["check"]
        assert st["state"] == "done" and st["right"] is want, (target, st)
    assert [(c["target"], c["heard"], c["target_kind"]) for c in reversed(lis.checks())] == [
        ("E4", "E4", "note"), ("E4", "E3", "note"), ("C+G", "C+G", "pair")]


def test_a_label_keeps_the_last_two_seconds_with_what_it_was(cfg):
    lis = Listener(cfg, cfg.takes_dir)
    feed(lis, demo(["A7"], cfg, every_s=10.0, decay=0.3), 3.0)
    sid = lis.label(parse_target("A"))
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
