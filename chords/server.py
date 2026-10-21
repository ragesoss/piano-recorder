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
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from recorder.config import Config, load_config
from recorder.inputs import Inputs, add_routes

from .listen import DemoSource, Listener, parse_target

STATIC = Path(__file__).parent / "static"
FONTS = Path(__file__).resolve().parent.parent / "exercises" / "static" / "fonts"  # the exercise app's
SYSTEM_CONFIG = Path("/etc/chord-listener.toml")


class ListenIn(BaseModel):
    inputs: list[int] | None = None  # input numbers to hear, mixed
    instrument: str | None = None  # what is being played (kept with each check)


class CheckIn(BaseModel):
    # a chord ("D", "F#m", "Bb7", "Cdim7", "C/E": an inversion), a note ("E4") or two ("C+G"); None: a
    # strike (one chord played, what it was said afterwards)
    target: str | None = None


class LabelIn(BaseModel):
    chord: str | None = None  # what it was, as a check's target is named; None: nothing


def create_app(cfg: Config, source=None, inputs: Inputs | None = None) -> FastAPI:
    inputs = inputs or Inputs(cfg)
    listener = Listener(cfg, cfg.takes_dir, source, inputs=inputs)
    app = FastAPI(title="Chords", docs_url=None, redoc_url=None)
    app.state.listener = listener
    add_routes(app, inputs)  # the room's inputs, shared by the apps: GET /api/inputs, PUT /api/inputs/N, /inputs.js

    def target_of(name: str | None):
        try:
            return parse_target(name) if name else None
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

    @app.post("/api/listen")
    def listen(body: ListenIn | None = None):
        """Keep listening a few seconds more (the page asks again while it is open)."""
        body = body or ListenIn()
        known = {i.number for i in inputs.kept()}
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
        listener.start_check(target_of(body.target))
        return {"ok": True}

    @app.post("/api/check/cancel")
    def cancel():
        listener.cancel_check()
        return {"ok": True}

    @app.post("/api/label")
    def label(body: LabelIn):
        try:
            return {"id": listener.label(target_of(body.chord))}
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.get("/api/checks")
    def checks():
        return listener.checks()

    @app.get("/api/checks/{sid}/audio")
    def check_audio(sid: str, mix: bool = False):
        """The clip as kept (every channel), or with `mix` as a browser plays it (the mixed inputs, mono)."""
        try:
            if mix:
                return Response(listener.listen_wav(sid), media_type="audio/wav", headers={"Cache-Control": "no-cache"})
            path = listener.path(sid)
        except FileNotFoundError:
            raise HTTPException(404, "no such check")
        return FileResponse(path, media_type="audio/wav", filename=path.name)

    @app.post("/api/checks/{sid}/label")
    def relabel(sid: str, body: LabelIn):
        """What a kept clip was, as the player says (a strike's answer)."""
        try:
            return listener.relabel(sid, target_of(body.chord))
        except FileNotFoundError:
            raise HTTPException(404, "no such check")

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
    p.add_argument("--demo", nargs="?", const="-,G,C/E,D7,-,A3,C+G,Bdim,Cdim7/Eb", metavar="CHORDS",
                   help="chords played by the program in place of the inputs, a new one every 2.5 s (comma-separated)")
    a = p.parse_args()
    cfg = load_config(Path(os.environ.get("CHORD_LISTENER_CONFIG", SYSTEM_CONFIG)))
    source = None
    inputs = Inputs(cfg)
    if a.demo:
        chords = a.demo.split(",")
        source = lambda: DemoSource(chords, max(1, len(inputs.kept())), cfg.capture.capture.rate)  # noqa: E731
    uvicorn.run(create_app(cfg, source, inputs), host=cfg.web_host, port=cfg.web_port, log_level="warning")


if __name__ == "__main__":
    main()
