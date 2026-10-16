"""The chord app: the chord being played, as the band's listener hears it, and checks of it (play the
chord asked for; it says what it heard; each check is kept, for study). A fourth program on the
control unit, on its own port; it only listens (`chords/listen.py`).

Run on the control unit by systemd (setup/chord-listener.service), or for development:
CHORD_LISTENER_CONFIG=config/chords-dev.toml python -m chords.server [--demo]
(--demo: chords played by the program itself, in place of the inputs).
"""

import argparse
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from recorder.config import Config, load_config

from .listen import DemoSource, Listener, parse_chord

STATIC = Path(__file__).parent / "static"
FONTS = Path(__file__).resolve().parent.parent / "exercises" / "static" / "fonts"  # the exercise app's
SYSTEM_CONFIG = Path("/etc/chord-listener.toml")


class ListenIn(BaseModel):
    inputs: list[int] | None = None  # input numbers to hear, mixed
    instrument: str | None = None  # what is being played (kept with each check)


class CheckIn(BaseModel):
    target: str  # "D", "F#m", "Bb7"


class LabelIn(BaseModel):
    chord: str | None = None  # what it was; None: no chord


def create_app(cfg: Config, source=None) -> FastAPI:
    listener = Listener(cfg, cfg.takes_dir, source)
    app = FastAPI(title="Chords", docs_url=None, redoc_url=None)
    app.state.listener = listener

    def chord_of(name: str | None):
        try:
            return parse_chord(name) if name else None
        except ValueError as e:
            raise HTTPException(400, str(e))

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
        if not name.endswith(".woff2") or "/" in name or not path.is_file():
            raise HTTPException(404, "no such font")
        return FileResponse(path, media_type="font/woff2", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/status")
    def status():
        return listener.status()

    @app.get("/api/inputs")
    def inputs():
        return [{"input": i.number, "channel": c, "name": i.name, "details": i.details, "for": i.for_kind}
                for c, i in enumerate(cfg.inputs)]

    @app.post("/api/listen")
    def listen(body: ListenIn | None = None):
        """Keep listening a few seconds more (the page asks again while it is open)."""
        body = body or ListenIn()
        known = {i.number for i in cfg.inputs}
        if body.inputs is not None and (not body.inputs or any(n not in known for n in body.inputs)):
            raise HTTPException(400, "no such input")
        listener.listen(body.inputs, body.instrument)
        return {"ok": True}

    @app.post("/api/yield")
    def yield_inputs():
        """Another program wants the inputs (recorder/peers.py): let them go for a while."""
        listener.yield_inputs()
        return {"released": True}

    @app.post("/api/stop")
    def stop():
        listener.stop()
        return {"ok": True}

    @app.post("/api/check")
    def check(body: CheckIn):
        listener.start_check(chord_of(body.target))
        return {"ok": True}

    @app.post("/api/check/cancel")
    def cancel():
        listener.cancel_check()
        return {"ok": True}

    @app.post("/api/label")
    def label(body: LabelIn):
        try:
            return {"id": listener.label(chord_of(body.chord))}
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.get("/api/checks")
    def checks():
        return listener.checks()

    @app.get("/api/checks/{sid}/audio")
    def check_audio(sid: str):
        try:
            path = listener.path(sid)
        except FileNotFoundError:
            raise HTTPException(404, "no such check")
        return FileResponse(path, media_type="audio/wav", filename=path.name)

    @app.delete("/api/checks/{sid}")
    def delete(sid: str):
        try:
            listener.delete(sid)
        except FileNotFoundError:
            raise HTTPException(404, "no such check")
        return {"ok": True}

    return app


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--demo", action="store_true", help="chords played by the program, in place of the inputs")
    a = p.parse_args()
    cfg = load_config(Path(os.environ.get("CHORD_LISTENER_CONFIG", SYSTEM_CONFIG)))
    source = None
    if a.demo:
        chords = ["G", "C", "D7", "Em", "A", "Bm", "F", "Bb7"]
        source = lambda: DemoSource(chords, len(cfg.inputs), cfg.capture.capture.rate)  # noqa: E731
    uvicorn.run(create_app(cfg, source), host=cfg.web_host, port=cfg.web_port, log_level="warning")


if __name__ == "__main__":
    main()
