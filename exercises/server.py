"""The exercise app: record exercise takes with the score on the same screen (an iPad,
held landscape), to a click or a backing track from the speakers.

A separate app from the practice recorder (the piano's), on its own port, with its own
takes; it uses the recorder's engine, devices and line-up (recorder/), and hands the
speakers over with the other programs like they do (recorder/peers.py).

Run on the control unit by systemd (setup/exercise-recorder.service), or for development:
EXERCISE_RECORDER_CONFIG=config/exercises-dev.toml python -m exercises.server
"""

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from recorder import audio
from recorder.backing import BackingNotFound, Backings
from recorder.config import Config, ConfigError, load_config
from recorder.engine import Busy, Engine, LowSpace
from recorder.takes import TakeNotFound, Takes

from .store import ExerciseNotFound, Exercises

STATIC = Path(__file__).parent / "static"
SYSTEM_CONFIG = Path("/etc/exercise-recorder.toml")


class StartIn(BaseModel):
    exercise: str | None = None
    tempo_bpm: float | None = None  # None: the exercise's
    click: bool = True  # a click on every beat through the exercise
    count_in: bool = True  # a bar of clicks before it
    backing: str | None = None  # a backing track instead of the click


class NoteIn(BaseModel):
    note: str


class PlayIn(BaseModel):
    start: float = 0.0


class SettingsIn(BaseModel):
    level_offset_db: float


def create_app(cfg: Config) -> FastAPI:
    if cfg.exercises_dir is None:
        raise ConfigError(f"{cfg.path}: the exercise app needs exercises_dir")
    takes = Takes(cfg.takes_dir)
    backings = Backings(cfg.backing_dir)
    exercises = Exercises(cfg.exercises_dir)
    engine = Engine(cfg, takes, backings, name="the exercise app")
    app = FastAPI(title="Exercises", docs_url=None, redoc_url=None)
    app.state.engine = engine

    def guard(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except TakeNotFound:
            raise HTTPException(404, "no such take")
        except BackingNotFound:
            raise HTTPException(404, "no such backing track")
        except ExerciseNotFound:
            raise HTTPException(404, "no such exercise")
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

    @app.get("/api/exercises")
    def list_exercises():
        return [e.__dict__ for e in exercises.list()]

    @app.get("/api/exercises/{exercise_id}/pages/{n}")
    def exercise_page(exercise_id: str, n: int):
        path = guard(exercises.page, exercise_id, n)
        return FileResponse(path, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})

    @app.get("/api/backings")
    def list_backings():
        return [b.__dict__ for b in backings.list()]

    @app.post("/api/record/start")
    def record_start(body: StartIn | None = None):
        """Record: with an exercise, over its click (or a backing); the take is tagged with it."""
        body = body or StartIn()
        meta = {}
        ex = None
        if body.exercise:
            ex = guard(exercises.get, body.exercise)
            meta = {"exercise": ex.id, "exercise_title": ex.title, "instrument": ex.instrument}
        if body.backing:
            return {"take": guard(engine.start_recording, body.backing, body.count_in, meta=meta)}
        if ex is not None and (body.click or body.count_in):
            tempo = dict(ex.tempo)
            if body.tempo_bpm:
                if not 20 <= body.tempo_bpm <= 300:
                    raise HTTPException(400, "tempo out of range (20-300)")
                tempo["bpm"] = float(body.tempo_bpm)
            return {"take": guard(engine.start_click, tempo, ex.beats, count_in=body.count_in,
                                  through=body.click, title=ex.title, meta=meta)}
        return {"take": guard(engine.start_recording, meta=meta)}

    @app.post("/api/record/stop")
    def record_stop():
        return {"take": engine.stop_recording()}

    @app.get("/api/takes")
    def list_takes(exercise: str | None = None):
        return [t.__dict__ for t in takes.list() if exercise is None or t.exercise == exercise]

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

    @app.post("/api/takes/{take_id}/lineup")
    def line_up_again(take_id: str):
        """Line a take up with its click or backing again."""
        try:
            return guard(engine.line_up_again, take_id)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/takes/{take_id}/clean")
    def take_clean(take_id: str):
        """A take with its click or backing subtracted."""
        guard(takes.path, take_id)
        path = takes.clean_path(take_id)
        if not path.exists():
            raise HTTPException(404, "this take has no copy without its click or backing")
        return FileResponse(path, media_type="audio/wav", filename=path.name)

    @app.put("/api/settings")
    def settings(body: SettingsIn):
        return {"level_offset_db": engine.set_level_offset(body.level_offset_db)}

    return app


def main() -> None:
    cfg = load_config(Path(os.environ.get("EXERCISE_RECORDER_CONFIG", SYSTEM_CONFIG)))
    uvicorn.run(create_app(cfg), host=cfg.web_host, port=cfg.web_port, log_level="warning")


if __name__ == "__main__":
    main()
