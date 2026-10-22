"""The home page (home/server.py): the tiles' statuses, Stop everything, the fonts; an app served over HTTPS (the
contribution app) linked as https:// and its status read over HTTPS, its certificate for practice.local not checked
on 127.0.0.1; and the page itself in headless Chromium, muted (tests/home_page.cjs, Playwright for Node: skipped
without it, found by node or in $PLAYWRIGHT_NODE_PATH; the screenshots go to $HOME_SHOTS if set)."""

import os
import re
import shutil
import socket
import subprocess
import threading
import time
import tomllib
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from home.server import create_app

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tests" / "home_page.cjs"


def test_home_page_and_status_with_apps_down():
    cfg = {"apps": [{"id": "practice", "name": "Practice", "about": "x", "port": 1, "stop": ["/api/stop"]}]}
    with TestClient(create_app(cfg)) as c:
        assert "Music room" in c.get("/").text
        apps = c.get("/api/status").json()["apps"]
        assert apps[0]["id"] == "practice" and apps[0]["up"] is False and apps[0]["scheme"] == "http"
        assert c.post("/api/stop-all").status_code == 200


def test_home_page_fonts_are_served():
    """The page's fonts come from the home server: the exercise app's (exercises/static/fonts/)."""
    with TestClient(create_app({"apps": []})) as c:
        names = re.findall(r"url\(/fonts/([^)]+)\)", c.get("/").text)
        assert len(names) == 2
        for name in names:
            r = c.get(f"/fonts/{name}")
            assert r.status_code == 200 and r.headers["content-type"] == "font/woff2"
        for bad in ("nope.woff2", "OFL-public-sans.txt", "public-sans.ttf"):
            assert c.get(f"/fonts/{bad}").status_code == 404


# --- an app over HTTPS ---------------------------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app, **ssl) -> tuple[int, uvicorn.Server]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", **ssl))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    return port, server


def certificate(tmp_path: Path) -> dict:
    """A self-signed certificate for practice.local alone: like the unit's, it names that host and not 127.0.0.1."""
    if not shutil.which("openssl"):
        pytest.skip("needs openssl")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=practice.local",
                    "-addext", "subjectAltName=DNS:practice.local", "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True)
    return {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}


def stand_in(hits: list) -> FastAPI:
    """An app as the contribution app answers the home page: a page, its status, and a stop path to count."""
    app = FastAPI()

    @app.get("/")
    def page():
        return HTMLResponse("<!doctype html><title>Contribute</title><h1>Contribute</h1>")

    @app.get("/api/status")
    def status():
        return {"state": "ready"}

    @app.post("/api/stop")
    def stop():
        hits.append("stop")
        return {}

    return app


@pytest.fixture
def https_app(tmp_path):
    hits = []
    port, server = serve(stand_in(hits), **certificate(tmp_path))
    yield port, hits
    server.should_exit = True


def test_an_https_app_is_asked_over_https_and_linked_with_its_scheme(https_app):
    port, hits = https_app
    cfg = {"apps": [{"id": "practice", "name": "Practice", "about": "x", "port": 1, "stop": []},
                    {"id": "contribute", "name": "Contribute", "about": "y", "port": port, "scheme": "https",
                     "stop": ["/api/stop"]}]}
    with TestClient(create_app(cfg)) as c:
        practice, contribute = c.get("/api/status").json()["apps"]
        assert practice["scheme"] == "http" and practice["up"] is False
        assert contribute["scheme"] == "https" and contribute["up"] is True and contribute["status"] == {"state": "ready"}
        c.post("/api/stop-all")
        assert hits == ["stop"]  # Stop everything reaches it over HTTPS too (the contribution app itself lists none)
        assert '${a.scheme || "http"}://${host}:${a.port}/' in c.get("/").text
    # the same app asked over plain HTTP does not answer: the scheme is what reached it
    plain = {"apps": [dict(cfg["apps"][1], scheme="http")]}
    with TestClient(create_app(plain)) as c:
        assert c.get("/api/status").json()["apps"][0]["up"] is False


def test_a_scheme_other_than_http_or_https_is_refused():
    with pytest.raises(ValueError, match="ftp"):
        create_app({"apps": [{"id": "x", "name": "X", "about": "", "port": 1, "scheme": "ftp", "stop": []}]})


def test_the_room_config_lists_the_contribution_app_over_https():
    apps = tomllib.loads((REPO / "config" / "home.toml").read_text())["apps"]
    create_app({"apps": apps})  # every scheme known
    tile = next(a for a in apps if a["id"] == "contribute")
    assert tile["port"] == 8008 and tile["scheme"] == "https" and tile["stop"] == []
    assert [a["port"] for a in apps].count(8008) == 1
    assert all(a.get("scheme", "http") == "http" for a in apps if a["id"] != "contribute")


# --- the page --------------------------------------------------------------------------------------------------------

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


@pytest.mark.skipif(ENV is None, reason="needs node with Playwright (PLAYWRIGHT_NODE_PATH)")
def test_the_page_links_the_https_app_with_https(tmp_path, https_app):
    """The room's own config/home.toml, its nine tiles: the practice recorder a stand-in over HTTP, the contribution app
    a stand-in over HTTPS, the rest not running. The page at a phone's and an iPad's sizes (tests/home_page.cjs)."""
    port, _ = https_app
    http_port, http_server = serve(stand_in([]))
    closed = free_port()  # nothing listens there
    apps = tomllib.loads((REPO / "config" / "home.toml").read_text())["apps"]
    ports = {"practice": http_port, "contribute": port}
    cfg = {"apps": [dict(a, port=ports.get(a["id"], closed)) for a in apps]}
    home_port, home = serve(create_app(cfg))
    shots = Path(os.environ.get("HOME_SHOTS") or tmp_path / "shots")
    shots.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["node", str(SCRIPT), f"http://127.0.0.1:{home_port}/", str(http_port), str(port), str(len(apps)),
                            str(shots)], env=ENV, capture_output=True, text=True, timeout=120)
    finally:
        home.should_exit = http_server.should_exit = True
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all ok" in r.stdout and (shots / "home-820x1180.png").is_file()
