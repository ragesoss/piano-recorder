"""The chord app's check judging (chords/listen.py): the chord judged while it sounds, its bass at any
register, the piano detector when the listener has one, and the study that replays kept checks
(tools/check_study.py). Synthetic strikes, with a room under them."""

import csv
import importlib.util
import json
import math
from dataclasses import replace

import numpy as np
import pytest

from chords import listen as LS
from chords.listen import Listener, parse_target
from recorder import dsp
from recorder.config import REPO, load_config

RATE = 48000


@pytest.fixture
def cfg(tmp_path):
    return replace(load_config(REPO / "config" / "chords-dev.toml"), takes_dir=tmp_path / "checks")


class Strikes:
    """A stand-in for the capture: chords struck at given times, each note six partials (1/h^1.5, a
    little sharp with h, the higher ones dying faster) decaying at `decay` per second, damped `sounds_s`
    after the strike (60 dB in 0.1 s), and the room under them: noise at -80 dBFS, a hum at 92 Hz at
    -75 dBFS, and each strike's thump at 115 Hz, 25 dB under it and gone in 0.3 s."""

    def __init__(self, strikes, channels: int, rate: int = RATE):
        self.strikes = strikes  # (time s, MIDI notes, sounds_s, decay per s)
        self.channels, self.rate, self.dtype = channels, rate, np.dtype("<i4")
        self.n = 0
        self.rng = np.random.default_rng(1)
        self.stderr_lines: list[str] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def read(self) -> np.ndarray:
        frames = int(LS.BLOCK_S * self.rate)
        t = (self.n + np.arange(frames)) / self.rate
        x = 10 ** (-80 / 20) * self.rng.normal(size=frames) + 10 ** (-75 / 20) * math.sqrt(2) * np.sin(
            2 * math.pi * 92.0 * t)
        for t0, notes, sounds_s, decay in self.strikes:
            s = t - t0
            m = s >= 0
            if not m.any():
                continue
            s = s[m]
            damp = np.where(s < sounds_s, 1.0, np.exp(-(s - sounds_s) * 69.0))  # 60 dB in 0.1 s
            x[m] += 0.05 * np.exp(-s / 0.1) * np.sin(2 * math.pi * 115.0 * s) * 10 ** (-25 / 20) * len(notes)
            for note in notes:
                f0 = 440 * 2 ** ((note - 69) / 12)
                for h in range(1, 7):
                    f = f0 * h * math.sqrt(1 + 3e-4 * h * h)
                    if f < self.rate / 2:
                        x[m] += 0.05 * np.exp(-decay * (1 + 0.3 * h) * s) * damp * np.sin(
                            2 * math.pi * f * s) / h ** 1.5
        self.n += frames
        y = dsp.from_float(np.clip(x, -1, 1).astype(np.float32), self.dtype)
        return np.repeat(y[:, None], self.channels, axis=1)


def feed(lis: Listener, src, seconds: float) -> None:
    fs = dsp.full_scale(src.dtype)
    for _ in range(int(round(seconds / LS.BLOCK_S))):
        lis._hear(src.read(), fs)


def check(cfg, target: str, notes, sounds_s: float = 3.0, decay: float = 1.0, at: float = 1.53):
    """One chord struck `at` s in (not on a block's edge: the attack is found a block late or early),
    checked against `target`; the check's status and kept sidecar."""
    lis = Listener(cfg, cfg.takes_dir)
    src = Strikes([(at, notes, sounds_s, decay)], len(cfg.inputs))
    lis.start_check(parse_target(target))
    feed(lis, src, at + LS.CHECK_S + 0.3)
    st = lis.status()["check"]
    feed(lis, src, LS.CLIP_S[1])
    side = json.loads((cfg.takes_dir / f"{lis.status()['check']['id']}.json").read_text())
    return st, side


def test_a_damped_chord_is_judged_while_it_sounds(cfg):
    st, side = check(cfg, "C", [60, 64, 67], sounds_s=0.25, decay=3.0)  # C4 E4 G4, the keys let go at once
    assert st["state"] == "done" and st["heard"] == "C" and st["right"] is True, st
    assert abs(side["onset_s"] - 0.03) <= 0.02  # struck 1.53 s in: the attack's block began at 1.5
    assert side["judged_s"][0] >= side["onset_s"] + LS.SKIP_S - 0.011 and side["judged_s"][1] < 0.5, side["judged_s"]


def test_a_chord_that_rings_is_judged_over_the_whole_span(cfg):
    st, side = check(cfg, "C", [48, 52, 55])  # C3 E3 G3, held
    assert st["right"] is True and side["bass"] == "C"
    assert side["judged_s"][1] >= LS.CHECK_S - 0.01 and side["sounds_s"] >= LS.CHECK_S - 0.05


@pytest.mark.parametrize("target,notes,bass", [
    ("F", [77, 81, 84], "F"),  # F5 A5 C6: nothing under E4 but the room's hum
    ("F", [65, 69, 72], "F"),  # F4 A4 C5: just over E4
    ("C/E", [64, 67, 72], "E"),  # E4 G4 C5
    ("G/B", [71, 74, 79], "B"),  # B4 D5 G5
])
def test_a_high_chord_has_its_bass(cfg, target, notes, bass):
    st, side = check(cfg, target, notes, decay=2.5)  # the treble dies sooner
    assert (st["bass"], st["right"]) == (bass, True), (st["heard"], side["notes_heard"])


@pytest.mark.parametrize("target,notes", [("F", [41, 45, 48]), ("G", [43, 47, 50]), ("Em", [40, 43, 47])])
def test_a_low_chord_that_rings_stays_right(cfg, target, notes):  # F2 A2 C3, G2 B2 D3, E2 G2 B2
    st, _ = check(cfg, target, notes)
    assert st["right"] is True, st["heard"]


def test_a_low_chord_damped_keeps_its_bass(cfg):
    st, side = check(cfg, "F", [41, 45, 48], sounds_s=0.3, decay=2.0)
    assert st["bass"] == "F" and st["right"] is True, (st["heard"], side["judged_s"])


def test_the_span_starts_at_the_onset_not_the_block():
    rng = np.random.default_rng(2)
    rate, attack = RATE, int(1.0 * RATE)
    x = 10 ** (-70 / 20) * rng.normal(size=int(2.1 * rate))
    s = np.arange(len(x)) / rate - 1.08  # struck 80 ms into the attack's block
    on = s >= 0
    x[on] += 0.05 * np.exp(-4.0 * s[on]) * np.where(s[on] < 0.3, 1.0, np.exp(-(s[on] - 0.3) * 69)) * np.sin(
        2 * math.pi * 262 * s[on])
    sp = LS.judged_span(x.astype(np.float32), rate, attack)
    assert abs(sp["onset"] - int(1.08 * rate)) <= rate // 100
    assert sp["a"] == sp["onset"] + int(LS.SKIP_S * rate)
    assert 0.3 <= sp["sounds_s"] <= 0.5 and sp["room_dbfs"] < -65  # the damper, then the room
    held = LS.judged_span(x[int(1.2 * rate):].astype(np.float32), rate, 0)  # already sounding: no onset
    assert held["onset"] == 0 and held["room_dbfs"] is None


def test_the_sidecar_says_where_the_attack_and_the_span_are(cfg):
    st, side = check(cfg, "C", [60, 64, 67], sounds_s=0.25, decay=3.0)
    assert side["attack_s"] == LS.CLIP_S[0] and abs(side["seconds"] - sum(LS.CLIP_S)) < 0.11
    assert set(side) >= {"onset_s", "judged_s", "sounds_s", "peak_dbfs", "room_dbfs"}
    early = replace(cfg, takes_dir=cfg.takes_dir.parent / "early")
    st, side = check(early, "C", [60, 64, 67], at=0.43)  # struck before a second has been heard
    assert side["attack_s"] == 0.4 and abs(side["seconds"] - (0.4 + LS.CLIP_S[1])) < 0.01


def test_a_listener_with_a_piano_detector_judges_by_it(cfg, monkeypatch):
    calls = []

    def hear_piano(y, sr, a, b, vocab=None, rang=None, room=None):
        calls.append((a, b, rang))
        notes = [(50, 1.0), (53, 0.8), (57, 0.9)]  # D3 F3 A3, whatever was played
        return LS.L.PianoHeard(notes, (2, "min"), 50, np.eye(len(vocab.chords))[vocab.chords.index((2, "min"))]) \
            if hasattr(LS.L, "PianoHeard") else type("P", (), {"notes": notes, "chord": (2, "min"), "bass": 50,
                                                                "scores": None})()

    monkeypatch.setattr(LS.L, "hear_piano", hear_piano, raising=False)
    monkeypatch.setattr(LS.L, "quiet_peaks", lambda y, sr, a, b: ("peaks", round(b - a, 2)), raising=False)
    st, side = check(cfg, "Dm", [48, 52, 55])  # C struck; the detector says Dm
    assert (st["heard"], st["right"], side["notes_heard"]) == ("Dm", True, ["D3", "F3", "A3"])
    (a, b, rang), = calls
    assert rang == ("peaks", round(LS.RANG_S - 0.02, 2)) and b - a > 0.9  # what rang before the onset; the span
    lis = Listener(cfg, cfg.takes_dir)
    lis.instrument = "guitar"  # not a piano: the chroma, as before
    src = Strikes([(1.53, [48, 52, 55], 3.0, 1.0)], len(cfg.inputs))
    lis.start_check(parse_target("C"))
    feed(lis, src, 3.0)
    assert lis.status()["check"]["heard"] == "C" and len(calls) == 1


def study_module():
    spec = importlib.util.spec_from_file_location("check_study", REPO / "tools" / "check_study.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_study_replays_kept_checks_as_the_app_judged_them(cfg, tmp_path):
    played = [("C", [60, 64, 67], 0.25, 3.0), ("F", [77, 81, 84], 3.0, 2.5), ("Em", [40, 43, 47], 3.0, 1.0),
              ("G", [55, 59, 62], 3.0, 1.0)]
    for target, notes, sounds_s, decay in played:
        check(cfg, target, notes, sounds_s, decay)
    check(cfg, "Am", [48, 52, 55])  # asked for Am, C struck: kept as heard, scored wrong
    cs = study_module()
    rows = cs.study(cfg.takes_dir, {})
    kept = {json.loads(p.read_text())["id"]: json.loads(p.read_text()) for p in cfg.takes_dir.glob("*.json")}
    assert len(rows) == 5
    for r in rows:
        assert (r["heard"], r["right"], r["bass_right"]) == (kept[r["id"]]["heard"], kept[r["id"]]["right"],
                                                             kept[r["id"]]["bass_right"]), r
    by = {r["target"]: r for r in rows}
    assert (by["C"]["ring"], by["C"]["lowest"], by["C"]["highest"]) == ("damped", "C4", "G4")
    assert (by["F"]["lowest"], by["F"]["highest"], by["Em"]["lowest"]) == ("F5", "C6", "E2")
    assert by["G"]["ring"] == "rings" and by["Am"]["right"] is False
    out = tmp_path / "study.csv"
    assert cs.main([str(cfg.takes_dir), "--csv", str(out)]) == 0
    text = out.read_bytes()
    assert b"\r" not in text and len(list(csv.DictReader(open(out)))) == 5
