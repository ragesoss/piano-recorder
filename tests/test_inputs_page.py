"""The room's inputs on the four apps' pages (tests/inputs_page.cjs, Playwright for Node, muted): the practice
recorder, the exercise app, the chord app and the transcribe app on the fake device, sharing one room inputs file; a
mic named on one page shows on another, a take keeps every recorded input named, the transcribe app's bass finds its
input by name. Needs node and Playwright's package (found by node, or in $PLAYWRIGHT_NODE_PATH); skipped without.
The screenshots go to $INPUTS_SHOTS if set."""

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

SCRIPT = REPO / "tests" / "inputs_page.cjs"
ROOM = """[inputs.1]
name = "Piezo"
details = "Fishman Full Circle, through the Fishman Pro-EQ Platinum DI"
for = "bass"
record = true

[inputs.3]
name = "Nadine"
details = "Ear Trumpet Labs Nadine, on the bass"
for = "bass"
record = true

[inputs.4]
name = "Room mic"
details = "MXL 990"
record = true
"""


def node_env() -> dict | None:
    """node's environment, if it finds Playwright (itself, or in $PLAYWRIGHT_NODE_PATH): as tests/test_board_page.py."""
    if not shutil.which("node"):
        return None
    env = dict(os.environ)
    if os.environ.get("PLAYWRIGHT_NODE_PATH"):
        env["NODE_PATH"] = os.environ["PLAYWRIGHT_NODE_PATH"]
    ok = subprocess.run(["node", "-e", "require.resolve('playwright')"], env=env, capture_output=True, cwd=REPO)
    return env if ok.returncode == 0 else None


ENV = node_env()
pytestmark = pytest.mark.skipif(ENV is None, reason="needs node with Playwright (PLAYWRIGHT_NODE_PATH)")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app) -> tuple[str, uvicorn.Server]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}/", server


def test_the_inputs_on_every_apps_page(tmp_path, room_inputs):
    from chords.server import create_app as chords_app
    from exercises.server import create_app as exercise_app
    from recorder.server import create_app as practice_app
    from transcriber.server import create_app as transcribe_app
    room_inputs.write_text(ROOM)
    cfg = lambda name, **kw: replace(load_config(REPO / "config" / name), takes_dir=tmp_path / name / "takes", **kw)  # noqa: E731
    ex = cfg("exercises-dev.toml", exercises_dir=tmp_path / "exercises", backing_dir=tmp_path / "backing")
    ex.exercises_dir.mkdir()
    urls, servers = [], []
    for app in (practice_app(cfg("dev.toml", backing_dir=tmp_path / "backing")), exercise_app(ex),
                chords_app(cfg("chords-dev.toml")), transcribe_app(cfg("transcribe-dev.toml"))):
        url, server = serve(app)
        urls.append(url)
        servers.append(server)
    shots = Path(os.environ.get("INPUTS_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["node", str(SCRIPT), *urls, str(shots)], env=ENV, capture_output=True, text=True, timeout=240)
    finally:
        for s in servers:
            s.should_exit = True
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all ok" in r.stdout and (shots / "practice-390-recording.png").is_file()
