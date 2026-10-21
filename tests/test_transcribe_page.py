"""The transcribe app's score at a phone's and an iPad's width, turned (tests/transcribe_page.cjs, Playwright for Node,
muted): a take's score drawn, a bar tapped, the window turned; every bar and everything drawn inside the card, nothing
of the score in the take's cursor, no sideways scroll, a tap playing the take from its bar with the cursor in it.

The take is made here: a made-up score on one staff (24 bars of eighth notes, a scale up and down), its beats, and
silent sound. Needs node and Playwright's package (with its Chromium), found by node or in $PLAYWRIGHT_NODE_PATH, and
ffmpeg; skipped without them. The screenshots go to $TRANSCRIBE_SHOTS if set."""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
import uvicorn

from recorder.config import REPO, load_config
from transcriber.server import create_app

SCRIPT = REPO / "tests" / "transcribe_page.cjs"
DEV = REPO / "config" / "transcribe-dev.toml"
TAKE = "2026-10-20_120000"
BARS, BPM, BEAT0 = 24, 120.0, 0.5
STEPS = ["C", "D", "E", "F", "G", "A", "B"]



def node_env() -> dict | None:
    """node's environment, if it finds Playwright (itself, or in $PLAYWRIGHT_NODE_PATH): as tests/test_board_page.py."""
    if not shutil.which("node"):
        return None
    env = dict(os.environ)
    if os.environ.get("PLAYWRIGHT_NODE_PATH"):
        env["NODE_PATH"] = os.environ["PLAYWRIGHT_NODE_PATH"]
    ok = subprocess.run(["node", "-e", "require.resolve('playwright')"], env=env, capture_output=True, cwd=REPO)
    return env if ok.returncode == 0 else None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


ENV = node_env()
pytestmark = pytest.mark.skipif(ENV is None or not shutil.which("ffmpeg"),
                                reason="needs node with Playwright (PLAYWRIGHT_NODE_PATH) and ffmpeg")


def score_xml(bars: int = BARS) -> str:
    """One staff in 4/4, eight eighth notes a bar, a scale from C4 up two octaves and down again."""
    notes, k = [], 0
    for b in range(bars):
        attrs = ("<attributes><divisions>2</divisions><key><fifths>0</fifths></key><time><beats>4</beats>"
                 "<beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>") if b == 0 else ""
        out = []
        for _ in range(8):
            i = k % 28
            step = i if i < 14 else 28 - i
            out.append(f"<note><pitch><step>{STEPS[step % 7]}</step><octave>{4 + step // 7}</octave></pitch>"
                       "<duration>1</duration><voice>1</voice><type>eighth</type></note>")
            k += 1
        notes.append(f'<measure number="{b + 1}">{attrs}{"".join(out)}</measure>')
    return ('<?xml version="1.0" encoding="UTF-8"?><score-partwise version="3.1"><part-list><score-part id="P1">'
            f'<part-name>Guitar</part-name></score-part></part-list><part id="P1">{"".join(notes)}</part></score-partwise>')


def make_take(root: Path, take_id: str = TAKE) -> Path:
    """A take folder as the app keeps one, transcribed: the made-up score, its beats, and silent sound to play."""
    d = root / take_id
    (d / "play").mkdir(parents=True, exist_ok=True)
    seconds = BEAT0 + BARS * 4 * 60 / BPM + 1
    (d / "transcription.musicxml").write_text(score_xml())
    beats = [{"beat": k, "t": round(BEAT0 + k * 60 / BPM, 3)} for k in range(BARS * 4 + 1)]
    (d / "transcription.json").write_text(json.dumps({"beats": beats, "notes": []}))
    (d / "meta.json").write_text(json.dumps({
        "instrument": "strings", "instrument_name": "Any stringed instrument", "kind": None, "kind_name": "",
        "input": 4, "input_name": "Room mic", "bpm": BPM, "beats_per_bar": 4, "count_in_bars": 0, "click": True,
        "title": "A scale, made up", "recorded_at": "2026-10-20T12:00:00", "seconds": round(seconds, 2),
        "downbeat_s": BEAT0, "click_found": True,
        "transcription": {"state": "done", "finished": "2026-10-20T12:01:00", "bars": BARS, "notes": BARS * 8, "bpm": BPM}}))
    for name, length in (("full.mp3", seconds), ("half.mp3", 2 * seconds)):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "anullsrc=r=48000:cl=mono", "-t", f"{length:.2f}", "-b:a", "32k", str(d / "play" / name)], check=True)
    return d


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    takes = tmp_path_factory.mktemp("transcribe-takes")
    make_take(takes)
    cfg = replace(load_config(DEV), takes_dir=takes)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(cfg), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/"
    server.should_exit = True


@pytest.mark.parametrize("turn", [("844x390", "390x844"), ("390x844", "844x390"), ("1180x820", "820x1180")])
def test_the_score_stays_inside_its_card_when_the_window_turns(site, turn, tmp_path):
    shots = Path(os.environ.get("TRANSCRIBE_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["node", str(SCRIPT), site, TAKE, *turn, str(shots)], env=ENV, capture_output=True, text=True,
                       timeout=180)
    assert r.returncode == 0, r.stderr + r.stdout
    rows = json.loads(r.stdout.strip().splitlines()[-1])
    narrower = int(turn[1].split("x")[0]) < int(turn[0].split("x")[0])
    # turned narrower, more rows: the render that drew a row into the take's cursor before 2026-10-20
    assert (rows["to"] > rows["from"]) if narrower else (rows["to"] < rows["from"]), rows
