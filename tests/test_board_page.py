"""The board's page in headless Chrome at 1180x820, 820x1180 and 390x844 (tests/board_page.cjs, Playwright for
Node), against the fixture's cards and stand-ins for the review app and the exercise app.

Needs node and Playwright's package (with its Chromium): found by node itself, or in $PLAYWRIGHT_NODE_PATH (a
node_modules folder holding playwright). Skipped without them. The screenshots go to $BOARD_SHOTS if set."""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import uvicorn

from board.server import create_app

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "board-cards.json"
SCRIPT = REPO / "tests" / "board_page.cjs"
SIZES = ["1180x820", "820x1180", "390x844"]


def node_env() -> dict | None:
    if not shutil.which("node"):
        return None
    env = dict(os.environ)
    if os.environ.get("PLAYWRIGHT_NODE_PATH"):
        env["NODE_PATH"] = os.environ["PLAYWRIGHT_NODE_PATH"]
    ok = subprocess.run(["node", "-e", "require.resolve('playwright')"], env=env, capture_output=True, cwd=REPO)
    return env if ok.returncode == 0 else None


ENV = node_env()
pytestmark = pytest.mark.skipif(ENV is None, reason="needs node with Playwright (PLAYWRIGHT_NODE_PATH)")

REVIEWS = [
    {"id": "take-check-2026-10-19-180234", "title": "Check the take: Guitalele tune", "kind": "take-check",
     "subtitle": "Recorded 2026-10-19T16:35:54, strings, 139.7 bpm", "created": "2026-10-19", "demo": False,
     "questions": 6, "answered": 0, "last_answer": "", "origin": "pipeline", "archived": False},
    {"id": "2026-10-19-free-bluegrass", "title": "Free bluegrass recordings: keep or drop", "kind": "free",
     "subtitle": "The 2026-10-19 harvest's starter set, heard by ear", "created": "2026-10-19", "demo": False,
     "questions": 33, "answered": 9, "last_answer": "2026-10-19T19:33:32-07:00", "origin": "agent", "archived": True},
    {"id": "take-check-2026-10-19-082229", "title": "Check the take: Test: Red Wing take 1 (imported)", "kind": "take-check",
     "subtitle": "Recorded 2026-10-19T08:22:28, dobro, 100 bpm", "created": "2026-10-19", "demo": False,
     "questions": 8, "answered": 0, "last_answer": "", "origin": "pipeline", "archived": False},
    {"id": "2026-10-19-coyote-trail", "title": "Coyote Trail", "demo": False, "questions": 2, "answered": 2},
    {"id": "2026-10-20-arco", "title": "The arco challenge", "kind": "free", "demo": False, "questions": 4,
     "answered": 1, "origin": "agent", "archived": True},
]  # the first two have cards linking to them (guitalele-key, free-bluegrass-review): shown on those cards, the second
#   archived ("Archived"); the arco one archived and linked by none: no card
EXERCISES = ([{"id": f"dobro-{i}", "instrument": "dobro", "status": "needed", "to_review": 0} for i in range(3)]
             + [{"id": f"banjo-{i}", "instrument": "banjo", "status": "needed", "to_review": 0} for i in range(8)]
             + [{"id": f"generic-{i}", "instrument": "generic", "status": "needed", "to_review": 0} for i in range(21)])


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    class Apps(BaseHTTPRequestHandler):
        def do_GET(self):
            body = {"/api/reviews": {"reviews": REVIEWS}, "/api/exercises": EXERCISES}.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *a):
            pass
    apps = ThreadingHTTPServer(("127.0.0.1", 0), Apps)
    threading.Thread(target=apps.serve_forever, daemon=True).start()
    other = f"http://127.0.0.1:{apps.server_address[1]}"
    data = tmp_path_factory.mktemp("board-data")
    port = free_port()
    cfg = {"data_dir": data, "port": port,
           "live": {"review": other, "exercises": other, "review_area": "dobro", "exercises_area": "dobro", "timeout": 1, "ttl": 1}}
    server = uvicorn.Server(uvicorn.Config(create_app(cfg), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/", data
    server.should_exit = True
    apps.shutdown()


@pytest.mark.parametrize("size", SIZES)
def test_the_page(site, size, tmp_path):
    url, data = site
    shutil.copy(FIXTURE, data / "cards.json")  # each size from the fixture, with no edits
    for f in ("changes.jsonl", "changes.applied.jsonl"):
        (data / f).unlink(missing_ok=True)
    shots = Path(os.environ.get("BOARD_SHOTS") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["node", str(SCRIPT), url, size, str(shots)], env=ENV, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr + r.stdout
    assert (shots / f"board-{size}-all.png").is_file()
