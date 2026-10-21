"""The exercise app's recording screen with an exercise's positions (tests/exercise_record_page.cjs, Playwright for
Node, muted): the score most of the screen, a cursor that waits in the count-in and then moves with the click in the
bar the page names, the score scrolling itself. The exercise is pushed with tools/push-exercise --to (so it needs
MuseScore) from tests/fixtures/dobro-002-g-scale.musicxml. Needs node and Playwright's package (found by node, or in
$PLAYWRIGHT_NODE_PATH); skipped without either. The screenshots go to $EXERCISE_SHOTS if set."""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
import uvicorn

from recorder.config import REPO, load_config

SCRIPT = REPO / "tests" / "exercise_record_page.cjs"
FIXTURE = REPO / "tests" / "fixtures" / "dobro-002-g-scale.musicxml"


def node_env() -> dict | None:
    """node's environment, if it finds Playwright (itself, or in $PLAYWRIGHT_NODE_PATH): as tests/test_board_page.py."""
    if not shutil.which("node"):
        return None
    env = dict(os.environ)
    if os.environ.get("PLAYWRIGHT_NODE_PATH"):
        env["NODE_PATH"] = os.environ["PLAYWRIGHT_NODE_PATH"]
    ok = subprocess.run(["node", "-e", "require.resolve('playwright')"], env=env, capture_output=True, cwd=REPO)
    return env if ok.returncode == 0 else None


def mscore() -> str | None:
    for c in (os.environ.get("MSCORE"), shutil.which("mscore4portable"), shutil.which("mscore")):
        if c and os.path.exists(c):
            return c
    return None


def serve(app) -> tuple[str, uvicorn.Server]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}/", server


ENV = node_env()
pytestmark = [pytest.mark.skipif(ENV is None, reason="needs node with Playwright (PLAYWRIGHT_NODE_PATH)"),
              pytest.mark.skipif(mscore() is None, reason="needs MuseScore (mscore)")]


def test_the_cursor_moves_with_the_click_while_recording(tmp_path):
    from exercises.server import create_app
    exercises = tmp_path / "exercises"
    exercises.mkdir()
    r = subprocess.run([sys.executable, str(REPO / "tools" / "push-exercise"), str(FIXTURE), "--no-audio", "--no-readme",
                        "--to", str(exercises)], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    (exercises / "status.json").write_text(json.dumps({"exercises": {}, "instruments": [
        {"id": "beard", "kind": "dobro", "short": "Beard", "name": "Beard Radio Standard E"}]}))
    cfg = replace(load_config(REPO / "config" / "exercises-dev.toml"), takes_dir=tmp_path / "takes",
                  backing_dir=tmp_path / "backing", exercises_dir=exercises)
    url, server = serve(create_app(cfg))
    shots = Path(os.environ.get("EXERCISE_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["node", str(SCRIPT), url, "dobro-002-g-scale", str(shots)], env=ENV, capture_output=True,
                           text=True, timeout=240)
    finally:
        server.should_exit = True
    print(r.stdout)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all ok" in r.stdout and (shots / "ipad-recording.png").is_file()
