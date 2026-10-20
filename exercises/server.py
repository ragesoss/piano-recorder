"""The exercise app: record exercise takes with the score on the same screen (an iPad,
held landscape), to a click or a backing track from the speakers.

A separate app from the practice recorder (the piano's), on its own port, with its own
takes; it uses the recorder's engine, devices and line-up (recorder/), and hands the
speakers over with the other programs like they do (recorder/peers.py).

Run on the control unit by systemd (setup/exercise-recorder.service), or for development:
EXERCISE_RECORDER_CONFIG=config/exercises-dev.toml python -m exercises.server
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from recorder import audio
from recorder.backing import BackingNotFound, Backings
from recorder.config import Config, ConfigError, load_config
from recorder.engine import Busy, Engine, LowSpace
from recorder.takes import TakeNotFound, Takes

from .store import ExerciseNotFound, Exercises

STATIC = Path(__file__).parent / "static"
FONTS = STATIC / "fonts"
SYSTEM_CONFIG = Path("/etc/exercise-recorder.toml")
CHOICES_MAX_BYTES = 20000  # the page's choices for the next take (PUT /api/choices)


class StartIn(BaseModel):
    exercise: str | None = None
    tempo_bpm: float | None = None  # None: the exercise's; without an exercise, a click at this tempo
    click: bool = True  # a click on every beat through the exercise (a free take: until stopped)
    count_in: bool = True  # a bar of clicks before it
    beats_per_bar: int | None = None  # a free take's click (4 if not said)
    backing: str | None = None  # a backing track instead of the click
    instrument_id: str | None = None  # which known instrument (GET /api/instruments), or "other:<kind>"
    instruments: list[str] | None = None  # one or two, in place of instrument_id
    inputs: dict[str, str] | None = None  # input number -> what it carries: an instrument's id, "room" or "none"


class NoteIn(BaseModel):
    note: str


class PlayIn(BaseModel):
    start: float = 0.0


class HearIn(BaseModel):
    count_in: bool = True  # the take's bar of count-in first
    click: bool = True  # and its click on every beat


class SettingsIn(BaseModel):
    level_offset_db: float


OTHER_KIND_RE = re.compile(r"^[a-z][a-z0-9 -]{0,29}$")  # "other:guitar": a kind of instrument, not one the project knows
# exercises for any instrument (dobro-transcription's exercises/generic, ids generic-NNN-...): played on whatever the
# take says, which it must name (a known instrument, or a kind); its sidecar's `instrument` is that kind
ANY_KIND = "generic"


def instrument_meta(instrument_id: str, known: list[dict]) -> dict | None:
    """What a take's sidecar says it was played on: a known instrument (GET /api/instruments)
    by its name, or one not listed by its kind ("other:guitar": "a guitar (not listed)";
    "other:": "an instrument (not listed)"). None: not a known instrument or a kind."""
    by_id = {i["id"]: i for i in known}
    if instrument_id in by_id:
        i = by_id[instrument_id]
        return {"instrument_id": instrument_id, "instrument_name": i["name"], "kind": i["kind"]}
    if not instrument_id.startswith("other:"):
        return None
    kind = instrument_id[len("other:"):]
    if not kind:
        return {"instrument_id": instrument_id, "instrument_name": "an instrument (not listed)", "kind": ""}
    if not OTHER_KIND_RE.match(kind):
        return None
    article = "an" if kind[0] in "aeiou" else "a"
    return {"instrument_id": instrument_id, "instrument_name": f"{article} {kind} (not listed)", "kind": kind}


def default_carries(inp, instruments: list[dict]) -> str:
    """What an input carries if the page does not say: a pickup (`for` a kind) the chosen
    instrument of its kind; a room mic the one chosen instrument without a pickup of its own, or
    the room (none or two such)."""
    if inp.for_kind:
        return next((i["instrument_id"] for i in instruments if i["kind"] == inp.for_kind), "none")
    return "room"  # refined in played_on, which knows the other inputs


def played_on(body: StartIn, known: list[dict], inputs: tuple) -> dict:
    """The sidecar's instruments and channels for a take (raises HTTPException)."""
    ids = body.instruments if body.instruments is not None else ([body.instrument_id] if body.instrument_id else [])
    if len(ids) > 2 or len(set(ids)) != len(ids):
        raise HTTPException(400, "one or two instruments")
    insts = [instrument_meta(i, known) for i in ids]
    if any(i is None for i in insts):
        raise HTTPException(400, "unknown instrument")
    meta: dict = {}
    if insts:
        meta["instruments"] = insts
        meta["instrument_name"] = " and ".join(i["instrument_name"] for i in insts)
    if len(insts) == 1:
        meta["instrument_id"] = insts[0]["instrument_id"]
    picked_up = {inp.for_kind for inp in inputs if inp.for_kind}
    unheard = [i["instrument_id"] for i in insts if i["kind"] not in picked_up]
    names = {i["instrument_id"]: i["instrument_name"] for i in insts} | {"room": "the room", "none": "nothing"}
    sent = body.inputs or {}
    if any(not k.isdigit() or int(k) not in {inp.number for inp in inputs} for k in sent):
        raise HTTPException(400, "no such input")
    channels = []
    for c, inp in enumerate(inputs):
        carries = sent.get(str(inp.number))
        if carries is None:
            carries = default_carries(inp, insts)
            if not inp.for_kind and len(unheard) == 1:
                carries = unheard[0]
        if carries not in names:
            raise HTTPException(400, f"input {inp.number} cannot carry {carries!r}")
        channels.append({"channel": c, "input": inp.number, "input_name": inp.name, "carries": carries,
                         "carries_name": names[carries]})
    if inputs:
        meta["channels"] = channels
    return meta


def create_app(cfg: Config) -> FastAPI:
    if cfg.exercises_dir is None:
        raise ConfigError(f"{cfg.path}: the exercise app needs exercises_dir")
    takes = Takes(cfg.takes_dir)
    backings = Backings(cfg.backing_dir)
    exercises = Exercises(cfg.exercises_dir)
    engine = Engine(cfg, takes, backings, name="the exercise app")
    app = FastAPI(title="Exercises", docs_url=None, redoc_url=None)
    app.state.engine = engine
    choices_path = cfg.takes_dir / "choices.json"

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

    @app.get("/fonts/{name}")
    def font(name: str):
        path = FONTS / name
        if not re.match(r"^[A-Za-z0-9-]+\.woff2$", name) or not path.is_file():
            raise HTTPException(404, "no such font")
        return FileResponse(path, media_type="font/woff2", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/status")
    def status():
        return engine.status()

    @app.get("/api/inputs")
    def list_inputs():
        """What is plugged into each input the app keeps, in the take's channel order."""
        return [{"input": i.number, "channel": c, "name": i.name, "details": i.details, "for": i.for_kind}
                for c, i in enumerate(cfg.inputs)]

    @app.post("/api/monitor")
    def monitor():
        """Keep the inputs' levels coming in the status for a few seconds (the page asks again)."""
        engine.monitor()
        return {"ok": True}

    @app.get("/api/choices")
    def get_choices():
        """The page's choices for the next take, as it left them (or {} the first time)."""
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

    @app.get("/api/exercises")
    def list_exercises():
        """The exercises, each with its takes here: how many, the last one, and how many the
        project does not have yet (newer than the newest it has: to review)."""
        mine: dict[str, list] = {}
        for t in takes.list():
            if t.exercise:
                mine.setdefault(t.exercise, []).append(t)
        out = []
        for e in exercises.list():
            here = mine.get(e.id, [])
            newest = max(e.project_takes, default="")
            out.append({**e.__dict__, "takes": len(here), "last_take": max((t.started for t in here), default=None),
                        "to_review": sum(1 for t in here if t.id > newest and t.id not in e.project_takes)})
        return out

    @app.get("/api/instruments")
    def list_instruments():
        return exercises.instruments()

    @app.get("/api/exercises/{exercise_id}/pages/{n}")
    def exercise_page(exercise_id: str, n: int):
        path = guard(exercises.page, exercise_id, n)
        return FileResponse(path, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})

    @app.get("/api/exercises/{exercise_id}/readme")
    def exercise_readme(exercise_id: str):
        """The exercise's instructions (its README in the project), as Markdown."""
        text = guard(exercises.readme, exercise_id)
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8", headers={"Cache-Control": "no-cache"})

    @app.post("/api/exercises/{exercise_id}/hear")
    def hear_exercise(exercise_id: str, body: HearIn | None = None):
        """Hear the exercise's chart over the speakers (its notes, rendered when it was pushed), at
        its tempo, with the count-in and the click its take gets. It stops when a take starts."""
        body = body or HearIn()
        ex = guard(exercises.get, exercise_id)
        try:
            path, beat0 = exercises.audio(exercise_id)
        except ExerciseNotFound:
            raise HTTPException(404, "this exercise has no audio yet: push it again (tools/push-exercise)")
        guard(engine.play_chart, ex.id, path, beat0, ex.tempo, ex.beats, count_in=body.count_in, through=body.click)
        return {"ok": True}

    @app.get("/api/backings")
    def list_backings():
        return [b.__dict__ for b in backings.list()]

    @app.post("/api/backings/{backing_id}/play")
    def play_backing(backing_id: str, body: PlayIn | None = None):
        """Hear a backing track over the speakers before a take (and set their level)."""
        guard(engine.play_backing, backing_id, body.start if body else 0.0)
        return {"ok": True}

    @app.post("/api/record/start")
    def record_start(body: StartIn | None = None):
        """Record: with an exercise, over its click (or a backing); the take is tagged with it."""
        body = body or StartIn()
        meta = {}
        ex = None
        if body.exercise:
            ex = guard(exercises.get, body.exercise)
            meta = {"exercise": ex.id, "exercise_title": ex.title, "instrument": ex.instrument}
        meta["kind"] = "exercise" if ex else "playalong" if body.backing else "free"
        on = played_on(body, exercises.instruments(), cfg.inputs)
        if ex is not None and len(on.get("instruments", [])) > 1:
            raise HTTPException(400, "an exercise is for one instrument")
        if ex is not None and ex.instrument == ANY_KIND:
            insts = on.get("instruments", [])
            if len(insts) != 1 or not insts[0]["kind"]:
                raise HTTPException(400, "an exercise for any instrument needs to know what it is played on: "
                                         "an instrument, or its kind (other:mandolin)")
            meta["exercise_instrument"] = ANY_KIND
            meta["instrument"] = insts[0]["kind"]  # what it was played on, as a free take's
        meta |= on
        kinds = [i["kind"] for i in on.get("instruments", []) if i["kind"]]
        if not meta.get("instrument") and len(kinds) == 1:  # a free take: at least the kind
            meta["instrument"] = kinds[0]
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
        if ex is None and body.click and body.tempo_bpm:  # a free take over a click, until stopped
            if not 20 <= body.tempo_bpm <= 300:
                raise HTTPException(400, "tempo out of range (20-300)")
            bpb = body.beats_per_bar or 4
            if not 1 <= bpb <= 12:
                raise HTTPException(400, "beats in a bar out of range (1-12)")
            tempo = {"bpm": float(body.tempo_bpm), "pickup_beats": 0.0, "beats_per_bar": bpb}
            return {"take": guard(engine.start_click, tempo, None, count_in=body.count_in, through=True,
                                  title="Click", meta=meta)}
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

    @app.get("/api/takes/{take_id}/peaks")
    def take_peaks(take_id: str, n: int = 400):
        """A waveform: the loudest sample in each of n slices (0-1)."""
        if not 10 <= n <= 2000:
            raise HTTPException(400, "n out of range (10-2000)")
        return {"peaks": guard(takes.peaks, take_id, n)}

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
