"""The transcribe app: the player records a take on the phone and sees the transcription pipeline's
notation and tab for it there (the piano's as a grand staff), with the take's own sound and a cursor;
"Review it" sends the take to the review app to be checked and corrected (review.py).

A separate app on its own port (8006), with its own takes (store.py: one folder each, laid out as
a folder of the transcription project's corpus). It uses the recorder's engine, devices, click and
line-up (recorder/), and hands the inputs and speakers over with the other programs as they do
(recorder/peers.py). The transcription runs after the take, in the background (jobs.py).

Run on the control unit by systemd (setup/take-transcriber.service), or for development:
TAKE_TRANSCRIBER_CONFIG=config/transcribe-dev.toml python -m transcriber.server
"""

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from recorder import audio
from recorder.backing import Backings
from recorder.config import Config, load_config
from recorder.engine import Busy, Engine, LowSpace
from recorder.takes import TakeNotFound, Takes

from . import VERSION
from .jobs import Jobs
from .review import Reviews
from .settings import GRAND_STAFF, load_settings
from .store import TAKE, Folders

STATIC = Path(__file__).parent / "static"
ALPHATAB = STATIC / "alphatab"  # alphaTab 1.8.4 (MPL-2.0), vendored: the page draws the notation and tab
FONTS = Path(__file__).parent.parent / "exercises" / "static" / "fonts"  # the exercise app's (SIL OFL 1.1)
SYSTEM_CONFIG = Path("/etc/take-transcriber.toml")
CHOICES_MAX_BYTES = 20000
METERS = (2, 3, 4)  # beats in a bar the page offers
BPM_RANGE = (20.0, 300.0)
SAFE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)?$")


class StartIn(BaseModel):
    instrument: str  # "dobro", "bass", "piano" (GET /api/setup)
    input: int | None = None  # the input that carries it (default: the config's for it)
    bpm: float = 90.0
    beats_per_bar: int = 4
    count_in: bool = True  # a bar of clicks first
    click: bool = True  # the click all through (off: the beat tracker finds the beat from `bpm`)
    title: str = ""


class TitleIn(BaseModel):
    title: str


class SettingsIn(BaseModel):
    level_offset_db: float


def create_app(cfg: Config) -> FastAPI:
    settings = load_settings(cfg)
    folders = Folders(cfg.takes_dir)
    work = Takes(cfg.takes_dir / ".work")  # the engine's takes while they record and line up
    engine = Engine(cfg, work, Backings(work.dir / "backing"), name="the transcribe app")
    jobs = Jobs(cfg, settings, folders, work, busy=lambda: engine.rec is not None)
    reviews = Reviews(settings, folders)
    app = FastAPI(title="Transcribe", docs_url=None, redoc_url=None)
    app.state.engine, app.state.jobs, app.state.reviews, app.state.folders = engine, jobs, reviews, folders
    choices_path = cfg.takes_dir / "choices.json"
    inputs = {i.number: (c, i) for c, i in enumerate(cfg.inputs)}
    for t in work.list():  # takes the app stopped before saving (a restart mid-take): save them now
        jobs.submit(t.id, build=True)

    def guard(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except TakeNotFound:
            raise HTTPException(404, "no such take")
        except Busy as e:
            raise HTTPException(409, str(e))
        except LowSpace as e:
            raise HTTPException(507, str(e))
        except audio.DeviceMissing as e:
            raise HTTPException(503, str(e))

    def after_take(take_id: str) -> None:
        """Once the take has ended and been lined up with its click: save it and transcribe it."""
        rec = engine.rec
        if rec is not None and rec.take_id == take_id and rec.thread is not None:
            rec.thread.join()
        else:
            while engine.rec is not None and engine.rec.take_id == take_id:
                time.sleep(0.1)
        if engine.rec is None:  # not if the next take started while this one was lined up: its own end lets go
            jobs.pause(False)
        try:
            work.path(take_id)
        except TakeNotFound:  # it never started (the device busy or gone): no take
            (work.dir / f"{take_id}.json").unlink(missing_ok=True)
            return
        jobs.submit(take_id, build=True)

    # The page and its files

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

    @app.get("/alphatab/{path:path}")
    def alphatab(path: str):
        """alphaTab's script, its music font (Bravura, SIL OFL 1.1), its player's SoundFont (sonivox.sf2,
        Apache-2.0) and their licences."""
        full = (ALPHATAB / path).resolve()
        if not SAFE_PATH.match(path) or ALPHATAB.resolve() not in full.parents or not full.is_file():
            raise HTTPException(404, "no such file")
        types = {".js": "text/javascript", ".woff2": "font/woff2", ".woff": "font/woff", ".txt": "text/plain",
                 ".sf2": "application/octet-stream"}
        return FileResponse(full, media_type=types.get(full.suffix, "text/plain"),
                            headers={"Cache-Control": "max-age=86400"})

    # What the app is doing

    @app.get("/api/status")
    def status():
        s = engine.status()
        s["job"] = jobs.status()
        s["queue"] = jobs.queued()
        s["review"] = reviews.status()
        s["takes_version"] = f"{s['takes_version']}.{folders.version}"
        if s["state"] == "idle" and (s["job"] or s["queue"]):
            s["state"] = "transcribing"
        return s

    @app.get("/api/setup")
    def setup():
        """What can be recorded, on which inputs (`tab`: written as notation and tab, else as a grand staff),
        and whether the review app can check a take, and of which instruments."""
        ok, why = reviews.available()
        return {
            "instruments": [{"kind": i.kind, "name": i.name, "input": i.input, "tab": i.kind not in GRAND_STAFF}
                            for i in settings.instruments],
            "inputs": [{"input": i.number, "channel": c, "name": i.name, "details": i.details, "for": i.for_kind}
                       for c, i in enumerate(cfg.inputs)],
            "review": {"available": ok, "why": why, "port": settings.review.port,
                       "instruments": list(reviews.instruments())},
            "meters": list(METERS),
            "version": VERSION,
        }

    @app.post("/api/monitor")
    def monitor():
        """Keep the inputs' levels coming in the status for a few seconds (the page asks again)."""
        engine.monitor()
        return {"ok": True}

    @app.get("/api/choices")
    def get_choices():
        try:
            return json.loads(choices_path.read_text())
        except (OSError, ValueError):
            return {}

    @app.put("/api/choices")
    def put_choices(body: dict = Body(...)):
        text = json.dumps({**body, "saved": datetime.now().isoformat(timespec="seconds")})
        if len(text) > CHOICES_MAX_BYTES:
            raise HTTPException(413, "too much")
        tmp = choices_path.with_suffix(".json.tmp")
        tmp.write_text(text)
        tmp.replace(choices_path)
        return {"ok": True}

    # Recording

    @app.post("/api/record/start")
    def record_start(body: StartIn):
        inst = settings.instrument(body.instrument)
        if inst is None:
            raise HTTPException(400, f"not an instrument this app records: {body.instrument!r}")
        number = body.input if body.input is not None else inst.input
        if number not in inputs:
            raise HTTPException(400, f"no input {number} (kept: {sorted(inputs)})")
        if not BPM_RANGE[0] <= body.bpm <= BPM_RANGE[1]:
            raise HTTPException(400, "tempo out of range (20-300)")
        if body.beats_per_bar not in METERS:
            raise HTTPException(400, f"beats in a bar: one of {list(METERS)}")
        channel, inp = inputs[number]
        info = {"instrument": inst.kind, "instrument_name": inst.name, "input": number, "input_name": inp.name,
                "channel": channel, "bpm": round(float(body.bpm), 2), "beats_per_bar": body.beats_per_bar,
                "count_in_bars": 1 if body.count_in else 0, "click": bool(body.click),
                "title": " ".join(body.title.split())[:120], "recorded_at": datetime.now().isoformat(timespec="seconds")}
        tempo = {"bpm": float(body.bpm), "pickup_beats": 0.0, "beats_per_bar": body.beats_per_bar}
        jobs.pause(True)  # the pipeline holds still while the take records
        try:
            if body.click or body.count_in:
                take = guard(engine.start_click, tempo, None, count_in=body.count_in, through=body.click,
                             title="Click", meta={"transcribe": info})
            else:
                take = guard(engine.start_recording, meta={"transcribe": info})
        except HTTPException:
            jobs.pause(False)
            raise
        try:
            work.update_meta(take, {"transcribe": info})  # now, not only at its end: a restart mid-take keeps it
        except OSError:
            pass
        threading.Thread(target=after_take, args=(take,), daemon=True).start()
        return {"take": take}

    @app.post("/api/record/stop")
    def record_stop():
        return {"take": engine.stop_recording()}

    @app.post("/api/stop")
    def stop():
        engine.stop_playback()
        return {"ok": True}

    @app.post("/api/yield")
    def yield_inputs():
        """Another program wants the inputs or the speakers (recorder/peers.py): refused while recording."""
        guard(engine.yield_speakers)
        return {"released": True}

    @app.put("/api/settings")
    def put_settings(body: SettingsIn):
        """The click's level over the speakers."""
        return {"level_offset_db": engine.set_level_offset(body.level_offset_db)}

    # The takes

    @app.get("/api/takes")
    def list_takes():
        out = folders.list()
        for t in out:
            t["job"] = jobs.state_of(t["id"])
        return out

    @app.get("/api/takes/{take_id}")
    def get_take(take_id: str):
        t = guard(folders.summary, take_id)
        t["job"] = jobs.state_of(take_id)
        return t

    def take_file(take_id: str, rel: str) -> Path:
        path = guard(folders.path, take_id) / rel
        if not path.is_file():
            raise HTTPException(404, f"this take has no {rel} yet")
        return path

    @app.get("/api/takes/{take_id}/transcription.musicxml")
    def take_musicxml(take_id: str):
        return FileResponse(take_file(take_id, "transcription.musicxml"), media_type="application/xml",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/api/takes/{take_id}/transcription.json")
    def take_json(take_id: str):
        return FileResponse(take_file(take_id, "transcription.json"), media_type="application/json",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/api/takes/{take_id}/play/{name}")
    def take_sound(take_id: str, name: str):
        """The take for the page: full.mp3, or half.mp3 (half speed, the pitch kept)."""
        if name not in ("full.mp3", "half.mp3"):
            raise HTTPException(404, "full.mp3 or half.mp3")
        return FileResponse(take_file(take_id, f"play/{name}"), media_type="audio/mpeg")

    @app.get("/api/takes/{take_id}/take.flac")
    def take_flac(take_id: str):
        return FileResponse(take_file(take_id, TAKE), media_type="audio/flac", filename=f"{take_id}.flac")

    @app.put("/api/takes/{take_id}/title")
    def set_title(take_id: str, body: TitleIn):
        guard(folders.set_title, take_id, body.title)
        return guard(get_take, take_id)

    @app.post("/api/takes/{take_id}/transcribe")
    def transcribe_again(take_id: str):
        """Transcribe it (again): after a failure, a new pipeline, or a take brought in (transcriber/importer.py)."""
        guard(folders.path, take_id)
        if not (folders.path(take_id) / TAKE).is_file():
            raise HTTPException(409, "this take is still being saved")
        jobs.submit(take_id)
        return {"ok": True}

    @app.post("/api/takes/{take_id}/review")
    def review(take_id: str):
        """Make the review of it (the review app's take-check), in the background."""
        tr = guard(folders.meta, take_id).get("transcription") or {}
        if tr.get("state") != "done":
            raise HTTPException(409, "it is not transcribed yet")
        guard(reviews.start, take_id)
        return {"ok": True}

    @app.delete("/api/takes/{take_id}")
    def delete(take_id: str):
        guard(folders.path, take_id)
        r = reviews.status()
        if r and r["take"] == take_id:
            raise HTTPException(409, "its review is being made: wait for it")
        jobs.cancel(take_id)
        end = time.monotonic() + 5
        while jobs.state_of(take_id) == "running" and time.monotonic() < end:
            time.sleep(0.05)
        guard(folders.delete, take_id)
        return {"ok": True}

    return app


def main() -> None:
    cfg = load_config(Path(os.environ.get("TAKE_TRANSCRIBER_CONFIG", SYSTEM_CONFIG)))
    uvicorn.run(create_app(cfg), host=cfg.web_host, port=cfg.web_port, log_level="warning")


if __name__ == "__main__":
    main()
