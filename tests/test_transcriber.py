"""The transcribe app (transcriber/): a take recorded on the fake device becomes a folder of the
transcription project's corpus, is transcribed (by a stand-in for the pipeline, run as the real one is,
in its own interpreter), drawn and heard on the page, and handed to the review app. The dobro and the
bass as notation and tab, the piano as a grand staff."""

import csv
import importlib.util
import json
import os
import subprocess
import sys
import time
import wave
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from recorder import dsp
from recorder.config import REPO, load_config
from transcriber import VERSION
from transcriber.jobs import Jobs
from transcriber.review import review_id
from transcriber.server import create_app
from transcriber.settings import Pipeline, Review, load_settings
from transcriber.store import COLUMNS, Folders

DEV = REPO / "config" / "transcribe-dev.toml"
DOBRO_TRANSCRIPTION = Path.home() / "play" / "dobro-transcription"

# A stand-in for dobro-transcription's package: the functions job/run_pipeline.py calls, a note a beat. The
# piano's: read at the take's own rate (48 kHz), a chord and a note a beat on its two staves, its events by strike
# from the top key down and its notes found in another order (as `pianoscore`'s), each note's end its own; with
# FAKE_PIANO_EARLY set, a note before the downbeat, and so a bar before it (as `pianoscore` writes one).
FAKE = {
    "__init__.py": "",
    "detect.py": "import numpy as np\nSR = 22050\ndef load(path):\n    return np.zeros(SR * 3)\n",
    "chords.py": ("import numpy as np\ndef beat_grid(y, bpm):\n    assert len(y) == 22050 * 3, 'the tracker reads 22.05 kHz'\n"
                  "    return np.arange(0.5, 3.0, 60.0 / (bpm * 1.02))\n"),
    "pianoscore.py": ("import numpy as np\ndef load(path):\n    return np.zeros(48000 * 3), 48000\n"
                      "def to_piano_bars(events, beats_per_bar=4):\n    return [([0], [0])] * 2\n"),
    "transcribe.py": '''import os
from pathlib import Path
from types import SimpleNamespace as NS

def transcribe(y, bpm, beats_per_bar=4, metronome=False, instrument="dobro", downbeat_s=None, sr=22050, **kw):
    spb, d = 60.0 / bpm, (downbeat_s if downbeat_s is not None else 0.5)
    if instrument == "piano":
        assert sr == 48000 and len(y) == 48000 * 3, "the piano is read at its own rate"
        early = 4.0 if os.environ.get("FAKE_PIANO_EARLY") else 0.0
        strikes = [(0.0, [67, 64, 60], 1), (1.0, [48], 2), (2.0, [65, 62], 1), (2.0, [50], 2)]
        events = [NS(onset_beats=b + early, duration_beats=1.0, midi=m, string=st, fret=0, bar=1 + int(b + early) // beats_per_bar,
                     attack=True, technique=None) for b, ms, st in strikes for m in ms]
        written = [NS(onset_s=d + e.onset_beats * spb - early * spb, midi=e.midi, offset_s=d + e.midi / 100.0) for e in events]
        written = written[1:] + written[:1]  # found in another order than written (the first strike's top key last)
        if early:
            events.insert(0, NS(onset_beats=3.75, duration_beats=0.25, midi=72, string=1, fret=0, bar=1, attack=True, technique=None))
            written.append(NS(onset_s=d - 0.25 * spb, midi=72, offset_s=None))
        return NS(events=events, written=written, bpm=bpm, beats_per_bar=beats_per_bar, pickup_beats=0.0, tuning={},
                  instrument="piano")
    events = [NS(onset_beats=float(k), duration_beats=1.0, midi=55 + k, string=3, fret=k, bar=1 + k // beats_per_bar,
                 attack=k != 2, technique=None if k != 2 else "slide") for k in range(6)]
    written = [NS(onset_s=d + k * spb, offset_s=None) for k in range(6)]
    return NS(events=events, written=written, bpm=bpm, beats_per_bar=beats_per_bar, pickup_beats=0.0,
              tuning={1: 62, 2: 59, 3: 55, 4: 50, 5: 47, 6: 43})

def to_bars(events, beats_per_bar, pickup):
    return [[0]] * 2

def write_transcription(t, path, title):
    staves = "<staves>2</staves>" if getattr(t, "instrument", "") == "piano" else ""
    Path(path).write_text(f"<score-partwise><work><work-title>{title}</work-title></work>{staves}</score-partwise>")
''',
}

# A stand-in for the review app's generator, with or without the take-check kind.
MAKE_REVIEW = '''"""Stand-in for review/make_review.py."""
import argparse, json, os, sys
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=("disagree", "grid", "free", {kinds}))
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--install")
    a = ap.parse_args()
    folder = Path(a.paths[0])
    Path(os.environ["CALLED"]).write_text(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(),
        "pythonpath": os.environ.get("PYTHONPATH"), "review_data": os.environ.get("REVIEW_DATA")}}))
    rid = "take-check-" + folder.name.replace("_", "-")
    print(f"{{rid}}: 3 questions, 120 KB in /somewhere")
    print("  q1  Bars 1-2 (2 clips)")
    print("installed in /review-data/reviews/" + rid)

if __name__ == "__main__":
    main()
'''


def fake_pipeline(root: Path) -> Path:
    src = root / "src"
    (src / "transcribe").mkdir(parents=True)
    for name, text in FAKE.items():
        (src / "transcribe" / name).write_text(text)
    return src


def fake_review(root: Path, kinds: str) -> Path:
    (root / "review").mkdir(parents=True)
    (root / "review" / "__init__.py").write_text("")
    (root / "review" / "make_review.py").write_text(MAKE_REVIEW.format(kinds=kinds))
    return root


@pytest.fixture
def setup(tmp_path, monkeypatch):
    cfg = replace(load_config(DEV), takes_dir=tmp_path / "takes")
    src = fake_pipeline(tmp_path / "pipeline")
    review = fake_review(tmp_path / "dt", '"take-check"')
    monkeypatch.setenv("CALLED", str(tmp_path / "called.json"))
    settings = load_settings(cfg)
    settings = replace(settings, pipeline=Pipeline(python=sys.executable, src=src, threads=1, nice=0),
                       review=Review(root=review, python=sys.executable, data=tmp_path / "review-data"))
    monkeypatch.setattr("transcriber.server.load_settings", lambda cfg: settings)
    return cfg, tmp_path


@pytest.fixture
def client(setup):
    cfg, _ = setup
    with TestClient(create_app(cfg)) as c:
        yield c


def wait_for(client, take_id, limit=40):
    """Until the take is saved and transcribed (or failed)."""
    end = time.monotonic() + limit
    while time.monotonic() < end:
        r = client.get(f"/api/takes/{take_id}")
        if r.status_code == 200 and (r.json()["transcription"] or {}).get("state") in ("done", "failed") \
                and not r.json()["job"]:
            return r.json()
        time.sleep(0.1)
    raise AssertionError(f"{take_id} not transcribed in {limit} s: {client.get('/api/status').json()}")


def flac_info(path: Path) -> tuple[int, int, int]:
    """A FLAC file's rate, channels and bits per sample (its STREAMINFO block)."""
    b = path.read_bytes()
    assert b[:4] == b"fLaC" and b[4] & 0x7F == 0
    x = int.from_bytes(b[8 + 10:8 + 18], "big")
    return x >> 44, ((x >> 41) & 0x7) + 1, ((x >> 36) & 0x1F) + 1


def record(client, seconds, **body):
    take = client.post("/api/record/start", json={"instrument": "dobro", "bpm": 240, **body}).json()["take"]
    time.sleep(seconds)
    client.post("/api/record/stop")
    return take


def test_a_take_over_the_click_becomes_a_folder_of_the_corpus(client, setup):
    cfg, _ = setup
    take = record(client, 2.0, title="  Red   Wing ")
    t = wait_for(client, take)
    d = cfg.takes_dir / take
    assert t["transcription"]["state"] == "done", t["transcription"]
    # take-01.flac: the dobro's input (the room mic, input 4) alone, at the device's rate, 24-bit
    assert flac_info(d / "take-01.flac") == (48000, 1, 24)
    assert flac_info(d / "raw" / f"{take}.flac") == (48000, 3, 24)  # every kept input, as recorded
    side = json.loads((d / "raw" / f"{take}.json").read_text())
    assert side["guide"] == "click" and side["playalong"]["found"]
    # takes.csv: the corpus's columns, as dobro-transcription's read_takes reads them
    rows = list(csv.DictReader((d / "takes.csv").open()))
    assert tuple(rows[0]) == COLUMNS and len(rows) == 1
    assert rows[0]["take"] == "take-01.flac" and rows[0]["metronome"] == "yes" and float(rows[0]["bpm"]) == 240
    assert rows[0]["beats_per_bar"] == "4"
    meta = json.loads((d / "meta.json").read_text())
    for k, v in {"instrument": "dobro", "bpm": 240.0, "beats_per_bar": 4, "count_in_bars": 1, "click": True,
                 "title": "Red Wing", "input": 4, "channel": 1, "app_version": VERSION, "click_found": True}.items():
        assert meta[k] == v, k
    assert meta["recorded_at"].startswith(take[:10])
    # bar 1's downbeat where the click put it: a bar's count-in at 240 bpm, then the first capture block, the
    # fake device's 9 ms; beat0_s is it (the take has no key yet), and the pipeline was told it
    assert 1.2 < meta["downbeat_s"] < 1.5
    assert abs(float(rows[0]["beat0_s"]) - meta["downbeat_s"]) < 1e-3
    log = (d / "transcribe.log").read_text()
    assert "--metronome" in log and f"--downbeat {meta['downbeat_s']:.4f}" in log and "--track-beats" not in log
    # the pipeline's output, and the notes with their times on the click's beats
    assert (d / "transcription.musicxml").read_text().startswith("<score-partwise>")
    tr = json.loads((d / "transcription.json").read_text())
    assert tr["grid"] == "click" and tr["bars"] == 2 and tr["bpm"] == 240
    assert [b["t"] for b in tr["beats"][:3]] == pytest.approx([meta["downbeat_s"] + k * 0.25 for k in range(3)], abs=1e-3)
    n = tr["notes"][2]
    assert (n["beat"], n["pitch"], n["string"], n["fret"], n["attack"], n["technique"]) == (2.0, "A3", 3, 2, False, "slide")
    assert n["length_beats"] == 1.0 and n["length_s"] == pytest.approx(0.25, abs=1e-3)
    assert t["transcription"]["notes"] == 6 and t["transcription"]["seconds"] > 0
    # the page's sound, at full and half speed
    assert t["playable"]
    for name in ("full.mp3", "half.mp3"):
        r = client.get(f"/api/takes/{take}/play/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and len(r.content) > 1000
    assert client.get(f"/api/takes/{take}/play/take-01.flac").status_code == 404
    assert client.get(f"/api/takes/{take}/transcription.json").json()["notes"][0]["t"] == tr["notes"][0]["t"]
    assert list((cfg.takes_dir / ".work").glob("*.wav")) == []  # the engine's copy is gone


def test_free_play_finds_the_beat_from_the_tempo_given(client, setup):
    cfg, _ = setup
    take = record(client, 1.0, click=False, count_in=False)
    assert client.get("/api/status").json()["recording"] is None
    t = wait_for(client, take)
    d = cfg.takes_dir / take
    meta = json.loads((d / "meta.json").read_text())
    assert meta["click"] is False and meta["count_in_bars"] == 0 and meta["downbeat_s"] is None
    log = (d / "transcribe.log").read_text()
    assert "--track-beats" in log and "--metronome" not in log and "--downbeat" not in log
    tr = json.loads((d / "transcription.json").read_text())
    assert tr["grid"] == "notes" and tr["bpm"] == pytest.approx(240 * 1.02, abs=0.1)  # the tracker's tempo
    assert t["transcription"]["bpm"] == tr["bpm"]
    row = next(csv.DictReader((d / "takes.csv").open()))
    assert row["metronome"] == "no" and float(row["bpm"]) == pytest.approx(tr["bpm"])  # the tempo transcribed at


def test_a_count_in_without_the_click_tells_where_bar_1_is(client, setup):
    cfg, _ = setup
    take = client.post("/api/record/start", json={"instrument": "bass", "bpm": 240, "beats_per_bar": 3,
                                                  "click": False}).json()["take"]
    time.sleep(0.3)
    bk = client.get("/api/status").json()["recording"]["backing"]
    assert bk["kind"] == "click" and bk["seconds"] is None and bk["tempo"]["beats_per_bar"] == 3
    time.sleep(1.2)
    client.post("/api/record/stop")
    wait_for(client, take)
    d = cfg.takes_dir / take
    meta = json.loads((d / "meta.json").read_text())
    assert meta["instrument"] == "bass" and meta["input"] == 3 and meta["channel"] == 0  # the Nadine
    assert meta["click_found"] and 0.9 < meta["downbeat_s"] < 1.2  # 3 beats at 240, the first block, 9 ms
    log = (d / "transcribe.log").read_text()
    assert "--instrument bass" in log and "--track-beats" in log and "--downbeat" in log and "--beats-per-bar 3" in log


def test_another_input_can_carry_the_instrument(client, setup):
    cfg, _ = setup
    take = record(client, 1.0, instrument="bass", input=1, click=False, count_in=False)
    wait_for(client, take)
    meta = json.loads((cfg.takes_dir / take / "meta.json").read_text())
    assert meta["input"] == 1 and meta["input_name"] == "Piezo" and meta["channel"] == 2


def test_a_piano_take_is_a_folder_of_the_corpus_and_a_grand_staff(client, setup):
    cfg, _ = setup
    piano = next(i for i in client.get("/api/setup").json()["instruments"] if i["kind"] == "piano")
    assert piano == {"kind": "piano", "name": "Piano", "input": 4, "tab": False}  # the room mic, as the exercise app's
    take = record(client, 2.0, instrument="piano", bpm=120)
    t = wait_for(client, take)
    d = cfg.takes_dir / take
    assert t["transcription"]["state"] == "done", t["transcription"]
    assert t["tab"] is False and t["instrument"] == "piano" and t["instrument_name"] == "Piano"
    # the folder as for the others: the room mic's channel alone, every kept input in raw/, the corpus's takes.csv
    assert flac_info(d / "take-01.flac") == (48000, 1, 24) and flac_info(d / "raw" / f"{take}.flac") == (48000, 3, 24)
    meta = json.loads((d / "meta.json").read_text())
    for k, v in {"instrument": "piano", "input": 4, "input_name": "Room mic", "channel": 1, "bpm": 120.0, "click": True,
                 "count_in_bars": 1, "click_found": True}.items():
        assert meta[k] == v, k
    rows = list(csv.DictReader((d / "takes.csv").open()))
    assert tuple(rows[0]) == COLUMNS and rows[0]["metronome"] == "yes"
    assert abs(float(rows[0]["beat0_s"]) - meta["downbeat_s"]) < 1e-3
    log = (d / "transcribe.log").read_text()
    assert "--instrument piano" in log and "--metronome" in log and f"--downbeat {meta['downbeat_s']:.4f}" in log
    assert "<staves>2</staves>" in (d / "transcription.musicxml").read_text()
    # its notes: pitch, time, length, the staff and hand, beats; no strings or frets
    tr = json.loads((d / "transcription.json").read_text())
    assert tr["instrument"] == "piano" and tr["tuning"] is None and tr["grid"] == "click" and tr["bars"] == 2
    assert [b["t"] for b in tr["beats"][:3]] == pytest.approx([meta["downbeat_s"] + k * 0.5 for k in range(3)], abs=1e-3)
    notes = tr["notes"]
    assert [(n["beat"], n["pitch"], n["staff"], n["hand"]) for n in notes] == [
        (0.0, "G4", 1, "right"), (0.0, "E4", 1, "right"), (0.0, "C4", 1, "right"), (1.0, "C3", 2, "left"),
        (2.0, "F4", 1, "right"), (2.0, "D4", 1, "right"), (2.0, "D3", 2, "left")]
    for n in notes:
        assert not {"string", "fret", "technique"} & set(n), n
        assert n["end_s"] == pytest.approx(n["t"] - n["beat"] * 0.5 + n["midi"] / 100.0, abs=1e-3)  # its own end
        assert n["t"] == pytest.approx(meta["downbeat_s"] + n["beat"] * 0.5, abs=1e-3)
        assert n["length_beats"] == 1.0 and n["length_s"] == pytest.approx(0.5, abs=1e-3)


def test_a_piano_note_before_the_downbeat_puts_bar_1_a_bar_before_it(client, setup, monkeypatch):
    cfg, _ = setup
    monkeypatch.setenv("FAKE_PIANO_EARLY", "1")
    take = record(client, 2.0, instrument="piano", bpm=120)
    wait_for(client, take)
    meta = json.loads((cfg.takes_dir / take / "meta.json").read_text())
    tr = json.loads((cfg.takes_dir / take / "transcription.json").read_text())
    # the score's bar 1 is the bar before the downbeat: its beat 0 four beats before it, the downbeat its beat 4
    assert tr["beat0_s"] == pytest.approx(meta["downbeat_s"] - 2.0, abs=1e-3)
    assert next(b["t"] for b in tr["beats"] if b["beat"] == 4.0) == pytest.approx(meta["downbeat_s"], abs=1e-3)
    assert all(abs(n["t"] - (tr["beat0_s"] + n["beat"] * 0.5)) < 1e-3 for n in tr["notes"])


def test_free_play_on_the_piano_tracks_the_beat_at_the_pipelines_rate(client, setup):
    cfg, _ = setup
    take = record(client, 1.0, instrument="piano", click=False, count_in=False)
    t = wait_for(client, take)
    assert t["transcription"]["state"] == "done", t["transcription"]  # the stand-in checks both rates
    tr = json.loads((cfg.takes_dir / take / "transcription.json").read_text())
    assert tr["grid"] == "notes" and tr["bpm"] == pytest.approx(240 * 1.02, abs=0.1)
    assert all(b["t"] < c["t"] for b, c in zip(tr["beats"], tr["beats"][1:]))


def test_the_first_estimate_is_the_instruments(setup):
    cfg, _ = setup
    jobs = Jobs(cfg, load_settings(cfg), Folders(cfg.takes_dir), None)
    assert jobs.estimate(40.0, "piano", True) == 6.0 and jobs.estimate(40.0, "dobro", True) == 18.0


@pytest.mark.parametrize("body, code", [({"instrument": "banjo"}, 400), ({"instrument": "dobro", "input": 5}, 400),
                                        ({"instrument": "dobro", "bpm": 900}, 400),
                                        ({"instrument": "dobro", "beats_per_bar": 5}, 400)])
def test_what_cannot_be_recorded_is_refused(client, body, code):
    assert client.post("/api/record/start", json=body).status_code == code
    assert client.get("/api/status").json()["recording"] is None


def test_a_peer_is_refused_while_this_app_records(client):
    client.post("/api/record/start", json={"instrument": "dobro", "click": False, "count_in": False})
    time.sleep(0.3)
    r = client.post("/api/yield")
    assert r.status_code == 409 and "transcribe app" in r.json()["detail"]
    client.post("/api/record/stop")
    assert client.post("/api/yield").status_code == 200


def test_takes_are_listed_newest_first_renamed_and_deleted(client, setup):
    cfg, _ = setup
    first = record(client, 0.5, click=False, count_in=False)
    time.sleep(1.1)
    second = record(client, 0.5, click=False, count_in=False)
    wait_for(client, first), wait_for(client, second)
    assert [t["id"] for t in client.get("/api/takes").json()] == [second, first]
    r = client.put(f"/api/takes/{first}/title", json={"title": "A waltz\n for later"})
    assert r.status_code == 200 and r.json()["title"] == "A waltz for later"
    assert json.loads((cfg.takes_dir / first / "meta.json").read_text())["title"] == "A waltz for later"
    assert client.delete(f"/api/takes/{first}").status_code == 200
    assert not (cfg.takes_dir / first).exists() and [t["id"] for t in client.get("/api/takes").json()] == [second]
    for bad in ("..", "2026-10-19", "2026-10-19_120000"):
        assert client.delete(f"/api/takes/{bad}").status_code == 404
        assert client.get(f"/api/takes/{bad}/transcription.json").status_code == 404


def test_a_take_that_could_not_be_transcribed_says_why_and_can_be_again(client, setup):
    cfg, tmp = setup
    broken = tmp / "pipeline" / "src" / "transcribe" / "transcribe.py"
    good = broken.read_text()
    broken.write_text(good + "\ndef transcribe(*a, **k):\n    raise RuntimeError('no notes today')\n")
    take = record(client, 0.5, click=False, count_in=False)
    t = wait_for(client, take)
    assert t["transcription"]["state"] == "failed" and "no notes today" in t["transcription"]["error"]
    assert client.post(f"/api/takes/{take}/review").status_code == 409  # not transcribed: nothing to review
    broken.write_text(good)
    assert client.post(f"/api/takes/{take}/transcribe").status_code == 200
    time.sleep(0.2)
    assert wait_for(client, take)["transcription"]["state"] == "done"


def test_review_it_runs_the_review_apps_take_check_and_links_to_it(client, setup):
    cfg, tmp = setup
    # (its take_check.py names no instruments: it reads the dobro and the bass, as first deployed)
    assert client.get("/api/setup").json()["review"] == {"available": True, "why": "", "port": 8005,
                                                         "instruments": ["dobro", "bass"]}
    take = record(client, 0.5, click=False, count_in=False)
    wait_for(client, take)
    assert client.post(f"/api/takes/{take}/review").status_code == 200
    end = time.monotonic() + 20
    while client.get("/api/status").json()["review"] and time.monotonic() < end:
        time.sleep(0.1)
    r = client.get(f"/api/takes/{take}").json()["review"]
    assert r["id"] == "take-check-" + take.replace("_", "-") and r["kind"] == "take-check"
    called = json.loads((tmp / "called.json").read_text())
    root = tmp / "dt"
    assert called["argv"] == ["take-check", str(cfg.takes_dir / take), "--install", "local"]
    assert called["cwd"] == str(root) and called["pythonpath"] == f"{root}:{root / 'pipeline' / 'src'}"
    assert called["review_data"] == str(tmp / "review-data")


def test_review_it_waits_for_the_review_app_to_have_take_check(setup):
    cfg, tmp = setup
    mr = tmp / "dt" / "review" / "make_review.py"
    mr.write_text(MAKE_REVIEW.format(kinds='"borderline"') + "\n# take-check: to come\n")
    with TestClient(create_app(cfg)) as c:
        rv = c.get("/api/setup").json()["review"]
        assert rv["available"] is False and "take-check" in rv["why"]
        take = record(c, 0.5, click=False, count_in=False)
        wait_for(c, take)
        r = c.post(f"/api/takes/{take}/review")
        assert r.status_code == 409 and "doesn't check takes yet" in r.json()["detail"]
        mr.write_text(MAKE_REVIEW.format(kinds='"borderline", "take-check"'))  # deployed: no restart needed
        assert c.get("/api/setup").json()["review"]["available"] is True


def test_review_it_waits_for_the_review_app_to_check_piano_takes(client, setup):
    cfg, tmp = setup
    take = record(client, 0.5, instrument="piano", click=False, count_in=False)
    wait_for(client, take)
    assert client.get("/api/setup").json()["review"]["instruments"] == ["dobro", "bass"]
    r = client.post(f"/api/takes/{take}/review")
    assert r.status_code == 409 and r.json()["detail"] == "the review app doesn't check piano takes yet"
    # deployed: its take_check.py names the piano (no restart needed)
    (tmp / "dt" / "review" / "take_check.py").write_text('INSTRUMENTS = ("dobro", "bass", "piano")  # what it reads\n')
    assert client.get("/api/setup").json()["review"]["instruments"] == ["dobro", "bass", "piano"]
    assert client.post(f"/api/takes/{take}/review").status_code == 200
    end = time.monotonic() + 20
    while client.get("/api/status").json()["review"] and time.monotonic() < end:
        time.sleep(0.1)
    assert client.get(f"/api/takes/{take}").json()["review"]["id"] == "take-check-" + take.replace("_", "-")


def test_the_review_id_is_read_from_make_reviews_output():
    out = "take-check-2026-10-19-143012: 5 questions, 812 KB in /home/x/.cache/transcription-review/abc\n  q1 ...\n"
    assert review_id(out) == "take-check-2026-10-19-143012"
    assert review_id("installed in /x\ntake-check-2026-10-19-143012\n") == "take-check-2026-10-19-143012"
    assert review_id("Traceback (most recent call last):\n") is None


def test_a_transcription_waits_while_this_app_records(setup):
    cfg, tmp = setup
    folders = Folders(cfg.takes_dir)
    d = folders.create("2026-10-19_120000")
    (d / "meta.json").write_text("{}")
    recording = [True]
    jobs = Jobs(cfg, load_settings(cfg), folders, None, busy=lambda: recording[0])
    jobs.sound = lambda take_id: None
    ran = []
    jobs.transcribe = lambda take_id: ran.append(take_id) or {}
    jobs.submit("2026-10-19_120000")
    time.sleep(1.5)
    assert ran == [] and jobs.queued() == ["2026-10-19_120000"]
    recording[0] = False
    end = time.monotonic() + 5
    while not ran and time.monotonic() < end:
        time.sleep(0.05)
    assert ran == ["2026-10-19_120000"]


def load_job_script():
    spec = importlib.util.spec_from_file_location("run_pipeline", REPO / "transcriber" / "job" / "run_pipeline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_scores_beats_are_placed_by_the_notes_when_there_is_no_click():
    job = load_job_script()
    spb = 0.6
    beats = [0, 0, 1, 1.5, 2, 3, 6, 7, 7.5, 8]  # a chord on beat 0, then a gap of beats 4-5
    rng = np.random.default_rng(3)
    drift = lambda b: 0.5 + b * spb * (1 + 0.004 * b)  # slowing a little
    times = [drift(b) + rng.normal(0, 0.01) for b in beats]
    q = np.arange(0, 12)
    t = job.fit_times(beats, times, q, spb)
    assert np.all(np.diff(t) > 0)
    assert np.max(np.abs(t[:9] - [drift(b) for b in q[:9]])) < 0.04
    assert job.fit_times([], [], [0, 1], spb).tolist() == [0.0, 0.0]


def test_the_scores_beats_with_a_pickup_start_in_the_pickup_bar():
    job = load_job_script()
    assert job.score_beats(1.5, 4, 5.5)[:3] == [(0.5, 0, 4), (1.5, 1, 1), (2.5, 1, 2)]
    assert job.score_beats(0.0, 3, 6.0) == [(float(k), k // 3 + 1, k % 3 + 1) for k in range(7)]
    assert job.pitch_name(55) == "G3" and job.pitch_name(62) == "D4"


def write_wav(path: Path, x: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(dsp.from_float(x.astype(np.float32), np.dtype("<i2")).tobytes())


def test_a_recording_made_elsewhere_is_brought_in_as_a_take(setup, monkeypatch, tmp_path, capsys):
    cfg, _ = setup
    t = np.arange(44100 * 2) / 44100
    src = tmp_path / "phone.wav"
    write_wav(src, np.stack([0.3 * np.sin(2 * np.pi * 196 * t), 0.1 * np.sin(2 * np.pi * 98 * t)], axis=1), 44100)
    monkeypatch.setenv("TAKE_TRANSCRIBER_CONFIG", str(DEV))
    monkeypatch.setattr("transcriber.importer.load_config", lambda path: cfg)
    from transcriber import importer
    importer.main([str(src), "--instrument", "dobro", "--bpm", "100", "--click", "--downbeat", "0.5",
                   "--channel", "0", "--title", "From the phone", "--no-transcribe"])
    take = capsys.readouterr().out.split(":")[0]
    d = cfg.takes_dir / take
    assert flac_info(d / "take-01.flac") == (44100, 1, 24)  # its own rate, its channel 0
    meta = json.loads((d / "meta.json").read_text())
    assert meta["imported_from"] == "phone.wav" and meta["click"] and meta["downbeat_s"] == 0.5 and meta["seconds"] == 2.0
    row = next(csv.DictReader((d / "takes.csv").open()))
    assert row["beat0_s"] == "0.500" and row["metronome"] == "yes"


def test_a_piano_exercise_take_is_brought_in(setup, monkeypatch, tmp_path, capsys):
    cfg, _ = setup
    t = np.arange(48000 * 2) / 48000
    src = tmp_path / "take-01.wav"
    write_wav(src, 0.3 * np.sin(2 * np.pi * 261.6 * t)[:, None], 48000)
    monkeypatch.setattr("transcriber.importer.load_config", lambda path: cfg)
    from transcriber import importer
    importer.main([str(src), "--instrument", "piano", "--bpm", "60", "--click", "--downbeat", "4.366", "--no-transcribe"])
    take = capsys.readouterr().out.split(":")[0]
    meta = json.loads((cfg.takes_dir / take / "meta.json").read_text())
    assert meta["instrument"] == "piano" and meta["instrument_name"] == "Piano" and meta["downbeat_s"] == 4.366
    assert Folders(cfg.takes_dir).summary(take)["tab"] is False


def test_the_page_draws_with_alphatab_from_the_app_itself(client):
    page = client.get("/").text
    assert '<script src="/alphatab/alphaTab.min.js">' in page and "http" not in page.split("<script>")[0].replace("http-equiv", "")
    js = client.get("/alphatab/alphaTab.min.js")
    assert js.status_code == 200 and "alphaTab v1.8.4" in js.text[:2000]
    assert client.get("/alphatab/font/Bravura.woff2").headers["content-type"] == "font/woff2"
    assert "Mozilla Public License" in client.get("/alphatab/LICENSE").text
    for bad in ("../server.py", "font/../../server.py", ".hidden", "nope.js"):
        assert client.get(f"/alphatab/{bad}").status_code == 404
    for name in ("bricolage-grotesque.woff2", "public-sans.woff2"):
        assert client.get(f"/fonts/{name}").status_code == 200


def test_the_choices_come_back_as_left(client):
    assert client.get("/api/choices").json() == {}
    client.put("/api/choices", json={"instrument": "bass", "bpm": 120, "bpb": 3})
    got = client.get("/api/choices").json()
    assert got["instrument"] == "bass" and got["bpb"] == 3 and "saved" in got


def test_the_other_apps_hand_over_to_this_one_and_the_home_page_lists_it():
    import tomllib
    for name in ("practice.toml", "exercises.toml"):
        raw = tomllib.loads((REPO / "config" / name).read_text())
        assert "http://127.0.0.1:8006" in raw["peers"], name
    mine = tomllib.loads((REPO / "config" / "transcribe.toml").read_text())
    assert mine["web"]["port"] == 8006 and set(mine["peers"]) == {f"http://127.0.0.1:{p}" for p in range(8000, 8005)}
    home = tomllib.loads((REPO / "config" / "home.toml").read_text())
    tile = next(a for a in home["apps"] if a["port"] == 8006)
    assert tile["id"] == "transcribe" and tile["stop"] == ["/api/record/stop"]
    load_settings(load_config(REPO / "config" / "transcribe.toml"))  # the unit's config loads


def pipeline_python() -> str | None:
    if not (DOBRO_TRANSCRIPTION / "pipeline" / "src" / "transcribe" / "transcribe.py").is_file():
        return None
    r = subprocess.run(["python3", "-c", "import librosa, scipy, soundfile"], capture_output=True)
    return "python3" if r.returncode == 0 else None


@pytest.mark.skipif(pipeline_python() is None, reason="needs dobro-transcription's pipeline and its dependencies")
def test_the_real_pipeline_transcribes_a_real_take(setup, tmp_path):
    """Red Wing's first 12 s (dobro-transcription tunes/dobro/red-wing, over a click at 100 bpm from 0.5 s)."""
    cfg, _ = setup
    settings = load_settings(cfg)
    settings = replace(settings, pipeline=Pipeline(python=pipeline_python(), src=DOBRO_TRANSCRIPTION / "pipeline" / "src"))
    folders = Folders(cfg.takes_dir)
    d = folders.create("2026-10-08_160500")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(DOBRO_TRANSCRIPTION / "tunes/dobro/red-wing/take-01.flac"),
                    "-t", "12", "-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24", str(d / "take-01.flac")],
                   check=True)
    folders.write_meta(d.name, {"instrument": "dobro", "bpm": 100, "beats_per_bar": 4, "click": True, "downbeat_s": 0.5,
                                "seconds": 12.0})
    jobs = Jobs(cfg, settings, folders, None)
    jobs.current = {"take": d.name, "stage": "", "started": time.monotonic(), "paused_s": 0.0}
    tr = jobs.transcribe(d.name)
    assert tr["state"] == "done" and tr["notes"] >= 20 and tr["bars"] >= 4 and tr["grid"] == "click"
    data = json.loads((d / "transcription.json").read_text())
    first = data["notes"][0]
    assert (first["pitch"], first["beat"], first["string"], first["fret"]) == ("G3", 0.0, 3, 0)  # Red Wing's first note
    assert abs(first["t"] - 0.5) < 0.05
    assert "<score-partwise" in (d / "transcription.musicxml").read_text()


@pytest.mark.skipif(pipeline_python() is None, reason="needs dobro-transcription's pipeline and its dependencies")
def test_the_real_pipeline_transcribes_a_real_piano_take(setup):
    """Piano exercise 008, G1 to G7 (dobro-transcription exercises/piano/008-gs-across: the room mic, over the exercise
    app's click at 60 bpm, a bar's count-in; bar 1's downbeat 4.370 s): three Gs on the bass staff, four on the treble."""
    cfg, _ = setup
    src = DOBRO_TRANSCRIPTION / "exercises/piano/008-gs-across/take-01.flac"
    if not src.is_file():
        pytest.skip("no piano take")
    settings = load_settings(cfg)
    settings = replace(settings, pipeline=Pipeline(python=pipeline_python(), src=DOBRO_TRANSCRIPTION / "pipeline" / "src"))
    folders = Folders(cfg.takes_dir)
    d = folders.create("2026-10-17_231852")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(src), "-c:a", "flac", "-sample_fmt", "s32",
                    "-bits_per_raw_sample", "24", str(d / "take-01.flac")], check=True)
    folders.write_meta(d.name, {"instrument": "piano", "bpm": 60, "beats_per_bar": 4, "click": True, "downbeat_s": 4.370,
                                "seconds": 39.8})
    jobs = Jobs(cfg, settings, folders, None)
    jobs.current = {"take": d.name, "stage": "", "started": time.monotonic(), "paused_s": 0.0}
    tr = jobs.transcribe(d.name)
    assert tr["state"] == "done" and tr["notes"] == 7 and tr["bars"] == 7 and tr["grid"] == "click"
    data = json.loads((d / "transcription.json").read_text())
    assert [(n["pitch"], n["beat"], n["staff"], n["hand"]) for n in data["notes"]] == [
        ("G1", 0.0, 2, "left"), ("G2", 4.0, 2, "left"), ("G3", 8.0, 2, "left"), ("G4", 12.0, 1, "right"),
        ("G5", 16.0, 1, "right"), ("G6", 20.0, 1, "right"), ("G7", 24.0, 1, "right")]
    assert all(abs(n["t"] - (4.370 + n["beat"])) < 0.1 and "string" not in n for n in data["notes"])  # struck 0-80 ms early
    xml = (d / "transcription.musicxml").read_text()
    assert "<staves>2</staves>" in xml and '<clef number="2"><sign>F</sign>' in xml and "staff-details" not in xml
    row = next(csv.DictReader((d / "takes.csv").open()))
    assert row["beat0_s"] == "4.370" and row["metronome"] == "yes"
