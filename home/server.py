"""The control unit's home page: what is playing, and the way into each app.

    python -m home.server    (config from $ROOM_HOME_CONFIG or /etc/room-home.toml)

The apps keep their own pages and ports. Only one of them plays through the
speakers at a time; they hand over between themselves (recorder/peers.py),
so this page only shows and links, plus "Stop everything".
"""

import json
import os
import tomllib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse

STATIC = Path(__file__).parent / "static"
DEFAULT_CONFIG = Path("/etc/room-home.toml")


def load(path: Path | None = None) -> dict:
    path = Path(path or os.environ.get("ROOM_HOME_CONFIG") or DEFAULT_CONFIG)
    return tomllib.loads(path.read_text())


def _call(method: str, url: str, timeout: float = 1.5):
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def create_app(cfg: dict) -> FastAPI:
    apps = cfg.get("apps", [])
    app = FastAPI(title="Home", docs_url=None, redoc_url=None)
    pool = ThreadPoolExecutor(max_workers=4)

    def base(a: dict) -> str:
        return f"http://127.0.0.1:{a['port']}"

    def one_status(a: dict) -> dict:
        out = {k: a[k] for k in ("id", "name", "about", "port")}
        try:
            out["status"], out["up"] = _call("GET", base(a) + "/api/status"), True
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

    @app.get("/api/status")
    def status():
        return {"apps": list(pool.map(one_status, apps))}

    @app.post("/api/stop-all")
    def stop_all():
        for a in apps:
            for path in a.get("stop", []):
                try:
                    _call("POST", base(a) + path, timeout=3)
                except Exception:
                    pass
        return status()

    return app


def main() -> None:
    cfg = load()
    uvicorn.run(create_app(cfg), host="0.0.0.0", port=int(cfg.get("port", 80)), log_level="warning")


if __name__ == "__main__":
    main()
