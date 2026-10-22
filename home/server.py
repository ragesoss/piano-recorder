"""The control unit's home page: what is playing, and the way into each app.

    python -m home.server    (config from $ROOM_HOME_CONFIG or /etc/room-home.toml)

The apps keep their own pages and ports. Only one of them plays through the
speakers at a time; they hand over between themselves (recorder/peers.py),
so this page only shows and links, plus "Stop everything".

An app is plain HTTP unless its entry says `scheme = "https"` (the contribution
app, dobro-transcription's website/contribute.py: a browser lets a page use the
microphone only on a secure page). Its tile links to it over HTTPS, and this
server asks its status over HTTPS too, on 127.0.0.1 without checking its
certificate: the certificate is for practice.local, from the room's own
authority, so it cannot match 127.0.0.1; the request never leaves the unit and
carries nothing secret (a status read, an app's own stop paths). Checking it
against the authority's certificate, the hostname aside, would add a file this
server must find, and would show a running app as "Not running" whenever its
certificate lapsed or the authority's file was missing or replaced, for no gain
on loopback.
"""

import json
import os
import re
import ssl
import tomllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

STATIC = Path(__file__).parent / "static"
FONTS = Path(__file__).parent.parent / "exercises" / "static" / "fonts"  # the exercise app's (SIL OFL 1.1)
DEFAULT_CONFIG = Path("/etc/room-home.toml")


def load(path: Path | None = None) -> dict:
    path = Path(path or os.environ.get("ROOM_HOME_CONFIG") or DEFAULT_CONFIG)
    return tomllib.loads(path.read_text())


SCHEMES = ("http", "https")


def _loopback_tls() -> ssl.SSLContext:
    """TLS for an HTTPS app asked on 127.0.0.1: its certificate not checked (the module's docstring says why)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _call(method: str, url: str, timeout: float = 1.5, context: ssl.SSLContext | None = None):
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=timeout, context=context) as r:
        return json.loads(r.read() or b"null")


def create_app(cfg: dict) -> FastAPI:
    apps = cfg.get("apps", [])
    for a in apps:
        if a.get("scheme", "http") not in SCHEMES:
            raise ValueError(f"app {a.get('id')!r}: scheme {a['scheme']!r}, not http or https")
    app = FastAPI(title="Home", docs_url=None, redoc_url=None)
    pool = ThreadPoolExecutor(max_workers=4)
    tls = _loopback_tls()

    def scheme(a: dict) -> str:
        return a.get("scheme", "http")

    def base(a: dict) -> str:
        return f"{scheme(a)}://127.0.0.1:{a['port']}"

    def call(method: str, a: dict, path: str, timeout: float = 1.5):
        return _call(method, base(a) + path, timeout, tls if scheme(a) == "https" else None)

    def one_status(a: dict) -> dict:
        out = {k: a[k] for k in ("id", "name", "about", "port")} | {"scheme": scheme(a)}
        try:
            out["status"], out["up"] = call("GET", a, "/api/status"), True
        except Exception:
            out["status"], out["up"] = None, False
        return out

    @app.get("/")
    def page():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/icon.svg")
    def icon():
        return FileResponse(STATIC / "icon.svg", media_type="image/svg+xml")

    @app.get("/fonts/{name}")
    def font(name: str):
        path = FONTS / name
        if not re.match(r"^[A-Za-z0-9-]+\.woff2$", name) or not path.is_file():
            raise HTTPException(404, "no such font")
        return FileResponse(path, media_type="font/woff2", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/status")
    def status():
        return {"apps": list(pool.map(one_status, apps))}

    @app.post("/api/stop-all")
    def stop_all():
        for a in apps:
            for path in a.get("stop", []):
                try:
                    call("POST", a, path, timeout=3)
                except Exception:
                    pass
        return status()

    return app


def main() -> None:
    cfg = load()
    uvicorn.run(create_app(cfg), host="0.0.0.0", port=int(cfg.get("port", 80)), log_level="warning")


if __name__ == "__main__":
    main()
