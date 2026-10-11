"""The recorder's web service: the phone page and its JSON API.

Run on the control unit by systemd (setup/practice-recorder.service), or
for development: PRACTICE_RECORDER_CONFIG=config/dev.toml python -m recorder.server
"""

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import audio
from .config import Config, load_config
from .engine import Busy, Engine, LowSpace
from .takes import TakeNotFound, Takes

STATIC = Path(__file__).parent / "static"


class NoteIn(BaseModel):
    note: str


class PlayIn(BaseModel):
    start: float = 0.0


class SettingsIn(BaseModel):
    level_offset_db: float


def create_app(cfg: Config) -> FastAPI:
    takes = Takes(cfg.takes_dir)
    engine = Engine(cfg, takes)
    app = FastAPI(title="Practice recorder", docs_url=None, redoc_url=None)
    app.state.engine = engine

    def guard(fn, *args):
        try:
            return fn(*args)
        except TakeNotFound:
            raise HTTPException(404, "no such take")
        except Busy as e:
            raise HTTPException(409, str(e))
        except LowSpace as e:
            raise HTTPException(507, str(e))
        except audio.DeviceMissing as e:
            raise HTTPException(503, str(e))

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
        return engine.status()

    @app.post("/api/record/start")
    def record_start():
        return {"take": guard(engine.start_recording)}

    @app.post("/api/record/stop")
    def record_stop():
        return {"take": engine.stop_recording()}

    @app.get("/api/takes")
    def list_takes():
        return [t.__dict__ for t in takes.list()]

    @app.post("/api/takes/{take_id}/play")
    def play(take_id: str, body: PlayIn | None = None):
        guard(engine.play, take_id, body.start if body else 0.0)
        return {"ok": True}

    @app.post("/api/stop")
    def stop():
        engine.stop_playback()
        return {"ok": True}

    @app.post("/api/yield")
    def yield_speakers():
        """Another program wants the speakers (recorder/peers.py)."""
        guard(engine.yield_speakers)
        return {"released": True}

    @app.put("/api/takes/{take_id}/note")
    def note(take_id: str, body: NoteIn):
        t = guard(takes.set_note, take_id, body.note)
        engine.touched()
        return t.__dict__

    @app.delete("/api/takes/{take_id}")
    def delete(take_id: str):
        guard(engine.delete, take_id)
        return {"ok": True}

    @app.get("/api/takes/{take_id}/audio")
    def take_audio(take_id: str):
        path = guard(takes.path, take_id)
        return FileResponse(path, media_type="audio/wav", filename=path.name)

    @app.put("/api/settings")
    def settings(body: SettingsIn):
        return {"level_offset_db": engine.set_level_offset(body.level_offset_db)}

    return app


def main() -> None:
    cfg = load_config()
    uvicorn.run(create_app(cfg), host=cfg.web_host, port=cfg.web_port, log_level="warning")


if __name__ == "__main__":
    main()
