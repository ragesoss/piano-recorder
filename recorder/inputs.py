"""The room's inputs: what is plugged into each of the capture device's inputs, named, and which of them the apps
record. One file for every app on the control unit, not one list per config:

    ~/room-inputs.toml (an app config's `inputs_file`; $ROOM_INPUTS_FILE over it)

    [mics.nadine]                                     the room's mics, pickups and line sources: the list the pages
    name = "Nadine"                                   offer for an input. Its name: how the pages and every take's
    details = "Ear Trumpet Labs Nadine, on the bass"  sidecar name it; what it is; a pickup or mic on one kind of
    for = "bass"                                      instrument (none: the room's); the names it went by before
    aka = ["Bass mic"]                                the list, if any (a config's preset may still say one)

    [inputs.3]
    mic = "nadine"                                    the mic on input 3, from the list
    record = true                                     the apps keep it in their takes (false: named, not kept)
    since = "2026-10-20T18:02:11"                     when a page last changed it

    [inputs.2]
    name = "Shure SM57"                               a mic not on the list: its own name, details and `for`
    record = true

The pages say which of the list's mics is on an input, or that a mic not on the list is (named or not), or that
nothing is, and whether it is recorded (`PUT /api/inputs/N`, `add_routes`); a mic is in one place, so choosing it
for an input takes it off the one it was on. A mic not on the list joins it from the page when the player says so.
The player (2026-10-21), after the first version's free-text form: "the system should have a known set of mics,
and i should be able to pick which one is on a given input (or mark it as a mic that isn't on the known list)."

Every app reads the file again when it changes, so a mic chosen in one app is named in all of them, from their next
take on (and in a take being recorded: its sidecar's names are read again at its end). An input with no table is
not recorded and is "Input N". The device's inputs, and the capture channel each arrives on, are its profile's
(`[capture] inputs`).

A file with no [mics] (one written before the list, 2026-10-21) takes the list from `config/inputs.toml`, the
room's first copy, and an [inputs.N] of before (a `name`, no `mic`) whose name is a mic's on the list, or one it
went by, is that mic: the first change from a page writes the list in.

A take keeps the recorded inputs in input-number order; its sidecar's `channels` says which input each channel
is, by name (`channels_meta`), so the tools that read a take pick a channel by its name, not its place.

While the file does not exist (an app's first start, a config of before 2026-10-20, the tests), the app's own
config stands in for it as before: its `keep_channels` (else the profile's), in that order, named by its
`[inputs.N]`. The first change from a page writes the file, from what the app had.
"""

import fcntl
import json
import os
import re
import threading
import tomllib
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .config import REPO, Config, Input, Stream

NAME_MAX = 40
DETAILS_MAX = 200
KIND_RE = re.compile(r"^[a-z][a-z0-9 -]{0,29}$")  # a kind of instrument ("bass", "guitar", "mountain dulcimer")
MIC_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")  # a mic's key in the file ("bluebird-sl")
# The room's first copy (setup/install.sh installs it once): the list of mics for a room file that has none.
KNOWN_MICS = REPO / "config" / "inputs.toml"
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# An input whose peak over the last few blocks is above this hears something now. The 18i8's inputs with nothing in
# them read -110.5 to -111.6 dBFS rms, the Nadine on a still bass -96.5, the MXL 990 in a quiet room -67 rms with
# peaks of -52 (docs/components.md, swap log, 2026-10-14 and 2026-10-17).
SIGNAL_DBFS = -70.0
HEADER = """\
# The music room's inputs (recorder/inputs.py): the room's mics ([mics.*], the list the pages offer), and what is
# plugged into each of the interface's inputs, by its number, and whether the apps record it ([inputs.N]). Every app
# on the control unit reads this file; their pages write it (choosing a mic, recording an input or not), so comments
# added here are not kept. An input with no table is not recorded.
"""


class InputError(ValueError):
    pass


@dataclass(frozen=True)
class Mic:
    """One of the room's mics, pickups or line sources: the list the pages offer for an input."""
    id: str  # its key in the file ("bluebird-sl")
    name: str  # "Bluebird SL": how the pages and every take's sidecar name it
    details: str = ""  # what it is
    for_kind: str = ""  # a pickup or mic on one kind of instrument ("bass"); "": a mic for the room
    aka: tuple[str, ...] = ()  # names it went by before the list ("Room mic")

    def answers_to(self, name: str) -> bool:
        return _same(self.name, name) or any(_same(a, name) for a in self.aka)


def mic_id(name: str, taken) -> str:
    """A new mic's key from its name ("Shure SM57" -> "shure-sm57"), not one of `taken`."""
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:36].strip("-") or "mic"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    return key


def parse_mics(raw: dict, where) -> tuple[dict[str, Mic] | None, list[str]]:
    """The [mics.*] tables, in their order (None: the file has none)."""
    tables = raw.get("mics")
    if tables is None:
        return None, []
    if not isinstance(tables, dict):
        return {}, [f"{where}: [mics] is not a table"]
    out, problems = {}, []
    for key, entry in tables.items():
        if not MIC_ID_RE.match(str(key)) or not isinstance(entry, dict):
            problems.append(f"{where}: [mics.{key}]: a mic's key is lowercase letters, digits and dashes")
            continue
        try:
            aka = entry.get("aka", [])
            mic = Mic(id=key, name=clean_name(entry.get("name") or key), details=clean_details(entry.get("details", "")),
                      for_kind=clean_kind(entry.get("for", "")),
                      aka=tuple(clean_name(a) for a in (aka if isinstance(aka, list) else [aka])))
        except InputError as e:
            problems.append(f"{where}: [mics.{key}]: {e}")
            continue
        if any(m.answers_to(mic.name) for m in out.values()):
            problems.append(f"{where}: [mics.{key}]: another mic on the list is called {mic.name}")
            continue
        out[key] = mic
    return out, problems


def mic_named(name: str, mics: dict[str, Mic]) -> Mic | None:
    """The mic on the list with this name, or that went by it (any case)."""
    return next((m for m in mics.values() if m.answers_to(name)), None)


def as_mic(i: Input, mics: dict[str, Mic]) -> Input:
    """An input as the list has its mic: the mic's name, details and kind; an input of before the list named as one
    of its mics (or as one went by) is that mic."""
    m = mics.get(i.mic) if i.mic else (mic_named(i.name, mics) if i.named else None)
    if m is None:
        return replace(i, mic="")
    return replace(i, mic=m.id, name=m.name, details=m.details, for_kind=m.for_kind)


def device_inputs(stream: Stream) -> int:
    """How many inputs the capture device has (its profile's `inputs`; else one per channel)."""
    return stream.inputs or stream.channels


def socket_of(stream: Stream, number: int) -> str:
    return stream.sockets[number - 1] if 0 < number <= len(stream.sockets) else ""


def clean_name(value) -> str:
    name = " ".join(CONTROL_RE.sub(" ", str(value or "")).split())
    if not name:
        raise InputError("a name is needed")
    if len(name) > NAME_MAX:
        raise InputError(f"a name of {NAME_MAX} letters at most")
    return name


def clean_details(value) -> str:
    details = " ".join(CONTROL_RE.sub(" ", str(value or "")).split())
    if len(details) > DETAILS_MAX:
        raise InputError(f"details of {DETAILS_MAX} letters at most")
    return details


def clean_kind(value) -> str:
    kind = " ".join(str(value or "").lower().split())
    if kind and not KIND_RE.match(kind):
        raise InputError("an instrument's kind: a word or two, lowercase (\"bass\", \"guitar\")")
    return kind


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


class Inputs:
    """The room's inputs as one app sees them: the shared file, read again whenever it changes."""

    def __init__(self, cfg: Config, path: Path | None = None):
        self.stream = cfg.capture.capture
        self.count = device_inputs(self.stream)
        self.path = Path(path or cfg.inputs_path)
        self.lock = threading.RLock()
        # the room's first copy's list of mics: for a file with none (or no file)
        try:
            self._known, self._known_problems = parse_mics(tomllib.loads(KNOWN_MICS.read_text(encoding="utf-8")),
                                                           KNOWN_MICS)
        except (OSError, ValueError) as e:
            self._known, self._known_problems = {}, [f"{KNOWN_MICS}: {e}"]
        self._known = self._known or {}
        # what the app's own config says (keep_channels and [inputs.N]): used while the file does not exist
        self._seed = tuple(as_mic(i, self._known) for i in cfg.inputs if 0 < i.number <= self.count)
        self._stamp: tuple | None = None
        self._entries: dict[int, Input] | None = None  # None: no file (the seed)
        self._mics: dict[str, Mic] = dict(self._known)  # the list in effect: the file's, else the first copy's
        self.problems: list[str] = list(self._known_problems)
        self._version = 0
        self._load()

    # Reading

    @property
    def version(self) -> int:
        """Raised whenever what the room's inputs are changes (the pages read /api/inputs again)."""
        self._load()
        return self._version

    def _default(self, number: int) -> Input:
        return Input(number=number, name=f"Input {number}", record=False, named=False,
                     socket=socket_of(self.stream, number))

    def _load(self) -> None:
        """Read the file again if it changed (cheap when it did not: one stat)."""
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            stamp = None
        with self.lock:
            if stamp == self._stamp and (stamp is not None or self._entries is None):
                return
            if stamp is None:
                self._entries, self._stamp, self._mics = None, None, dict(self._known)
                self.problems = list(self._known_problems)
                self._version += 1
                return
            try:
                raw = tomllib.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                self.problems = [f"{self.path}: {e}"]  # the last good reading stands
                self._stamp = stamp
                return
            self._entries, self._mics, self.problems = self._parse(raw)
            self._stamp = stamp
            self._version += 1

    def _parse(self, raw: dict) -> tuple[dict[int, Input], dict[str, Mic], list[str]]:
        mics, problems = parse_mics(raw, self.path)
        if mics is None:
            mics, problems = dict(self._known), list(self._known_problems)
        out = {}
        tables = raw.get("inputs", {})
        if not isinstance(tables, dict):
            return out, mics, problems + [f"{self.path}: [inputs] is not a table"]
        for key, entry in tables.items():
            if not str(key).isdigit() or not 0 < int(key) <= self.count or not isinstance(entry, dict):
                problems.append(f"{self.path}: [inputs.{key}]: the device has inputs 1-{self.count}")
                continue
            n = int(key)
            mic = str(entry.get("mic") or "")
            if mic and mic not in mics:
                problems.append(f"{self.path}: [inputs.{key}]: no mic {mic!r} on the list")
            try:
                name = clean_name(entry.get("name") or (mic if mic else f"Input {n}"))
                details = clean_details(entry.get("details", ""))
                kind = clean_kind(entry.get("for", ""))
            except InputError as e:
                problems.append(f"{self.path}: [inputs.{key}]: {e}")
                continue
            i = Input(number=n, name=name, details=details, for_kind=kind, record=bool(entry.get("record", True)),
                      named=True, socket=socket_of(self.stream, n), since=str(entry.get("since", "")), mic=mic)
            out[n] = as_mic(i, mics)  # a mic not on the list (none of that key): its key as its name
        return out, mics, problems

    def all(self) -> tuple[Input, ...]:
        """Every input of the device, by number: named or not, recorded or not."""
        self._load()
        with self.lock:
            if self._entries is None:
                seed = {i.number: replace(i, socket=socket_of(self.stream, i.number)) for i in self._seed}
                return tuple(seed.get(n) or self._default(n) for n in range(1, self.count + 1))
            return tuple(self._entries.get(n) or self._default(n) for n in range(1, self.count + 1))

    def kept(self) -> tuple[Input, ...]:
        """The inputs a take records, in its channel order: the recorded ones by number (before the file: the
        config's keep_channels, in its order)."""
        self._load()
        with self.lock:
            if self._entries is None:
                return tuple(replace(i, socket=socket_of(self.stream, i.number)) for i in self._seed)
        return tuple(i for i in self.all() if i.record)

    def channels(self, inputs: tuple[Input, ...]) -> tuple[int, ...]:
        """The capture channels that carry these inputs (input N on channel N - 1)."""
        return tuple(i.number - 1 for i in inputs)

    def get(self, number: int) -> Input:
        if not 0 < int(number) <= self.count:
            raise InputError(f"the device has inputs 1-{self.count}")
        return self.all()[int(number) - 1]

    def mics(self) -> tuple[Mic, ...]:
        """The room's mics, in the list's order."""
        self._load()
        with self.lock:
            return tuple(self._mics.values())

    def _answers_to(self, i: Input, name: str) -> bool:
        """The input's mic is called this, or went by it before the list."""
        m = self._mics.get(i.mic) if i.mic else None
        return m.answers_to(name) if m else _same(i.name, name)

    def named(self, name: str) -> Input | None:
        """The input with this name (any case; a name its mic went by too), if one has it."""
        return next((i for i in self.all() if i.named and self._answers_to(i, name)), None)

    def pick(self, name: str | None, kind: str = "", number: int | None = None) -> tuple[Input | None, str]:
        """The recorded input an instrument is heard on by default (the transcribe app's presets): the one named
        `name`; else one `for` the instrument's kind (the bass's piezo when the Nadine is not plugged in); else
        input `number` (a config of before 2026-10-20), unless a name was asked for and a mic is named on it (the
        mic there has changed); else the room's; else the first recorded. Returns it and how it was found ("name",
        "kind", "number", "room", "first"; None and "none": nothing is recorded)."""
        kept = self.kept()
        if name:
            hit = next((i for i in kept if self._answers_to(i, name)), None)
            if hit:
                return hit, "name"
        if kind:
            hit = next((i for i in kept if i.for_kind == kind), None)
            if hit:
                return hit, "kind"
        if number is not None:
            hit = next((i for i in kept if i.number == number), None)
            if hit and not (name and hit.named):  # by number only where nothing names a mic on it
                return hit, "number"
        hit = next((i for i in kept if not i.for_kind), None)
        if hit:
            return hit, "room"
        return (kept[0], "first") if kept else (None, "none")

    # Changing

    def update(self, number: int, *, mic=None, name=None, details=None, for_kind=None, record=None, clear=False,
               add_to_list=False) -> Input:
        """Say what is on an input: a mic from the room's list (`mic`, its id; taken off the input it was on), a
        mic not on the list (`name`, `details`, `for_kind`; a name a mic on the list has is that mic), or nothing
        (`clear`: no table, not recorded); put the input's mic not on the list onto the list (`add_to_list`);
        record it or not. None: as it was. Written to the file at once (made from what the app had, if there was
        none, with the room's first copy's list of mics)."""
        if not 0 < int(number) <= self.count:
            raise InputError(f"the device has inputs 1-{self.count}")
        number = int(number)
        lock_path = self.path.with_name(self.path.name + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, open(lock_path, "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)  # another app writing at the same moment waits
            try:
                self._stamp = None  # read it fresh: another app may have written it
                self._load()
                entries = dict(self._entries) if self._entries is not None else {i.number: i for i in self._seed}
                mics = dict(self._mics)
                old = entries.get(number) or self._default(number)
                if clear:
                    entries.pop(number, None)
                else:
                    entries[number] = self._changed(entries, mics, old, mic, name, details, for_kind, record,
                                                    add_to_list)
                self._write(entries, mics)
                self._stamp = None
                self._load()
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
        return self.get(number)

    def _changed(self, entries, mics, old: Input, mic, name, details, for_kind, record, add_to_list) -> Input:
        """`update`'s new entry for one input (`entries` and `mics` changed in place: a mic moved, one listed)."""
        n = old.number
        if mic is None and name is not None:
            hit = mic_named(clean_name(name), mics)
            if hit is not None:
                mic = hit.id  # a mic on the list, called by its name
        if mic:
            m = mics.get(str(mic))
            if m is None:
                raise InputError(f"no mic {mic!r} on the room's list")
            for k in [k for k, e in entries.items() if k != n and e.mic == m.id]:
                del entries[k]  # a mic is in one place: it was on input k, and is on this one now
            new = replace(old, mic=m.id, name=m.name, details=m.details, for_kind=m.for_kind, named=True)
        elif name is not None or details is not None or for_kind is not None:
            if name is None and (old.mic or not old.named):
                raise InputError(f"input {n} has {'the ' + old.name + ', from the list' if old.mic else 'no mic'}: "
                                 "choose a mic, or name one not on the list")
            mine = old.named and not old.mic  # changing the mic not on the list that is there; else a new one
            new = replace(old, mic="", named=True,
                          name=clean_name(name) if name is not None else old.name,
                          details=clean_details(details) if details is not None else (old.details if mine else ""),
                          for_kind=clean_kind(for_kind) if for_kind is not None else (old.for_kind if mine else ""))
        else:
            new = old
        if add_to_list:
            if not new.named or new.mic:
                raise InputError(f"input {n} has {'the ' + new.name + ', on the list' if new.mic else 'no mic'}"
                                 ": only a mic not on the list can join it")
            m = Mic(id=mic_id(new.name, mics), name=new.name, details=new.details, for_kind=new.for_kind)
            mics[m.id] = m
            new = replace(new, mic=m.id)
        new = replace(new, record=bool(record) if record is not None else (old.record if old.named else True))
        if new != old or not old.named:
            new = replace(new, since=datetime.now().isoformat(timespec="seconds"))
        return new

    def _write(self, entries: dict[int, Input], mics: dict[str, Mic]) -> None:
        lines = [HEADER]
        for m in mics.values():
            lines.append(f"[mics.{m.id}]")
            lines.append(f"name = {json.dumps(m.name, ensure_ascii=False)}")
            if m.details:
                lines.append(f"details = {json.dumps(m.details, ensure_ascii=False)}")
            if m.for_kind:
                lines.append(f"for = {json.dumps(m.for_kind, ensure_ascii=False)}")
            if m.aka:
                lines.append(f"aka = {json.dumps(list(m.aka), ensure_ascii=False)}")
            lines.append("")
        for n in sorted(entries):
            i = entries[n]
            lines.append(f"[inputs.{n}]")
            if i.mic:
                lines.append(f"mic = {json.dumps(i.mic)}")
            else:
                lines.append(f"name = {json.dumps(i.name, ensure_ascii=False)}")
                if i.details:
                    lines.append(f"details = {json.dumps(i.details, ensure_ascii=False)}")
                if i.for_kind:
                    lines.append(f"for = {json.dumps(i.for_kind, ensure_ascii=False)}")
            lines.append(f"record = {'true' if i.record else 'false'}")
            if i.since:
                lines.append(f"since = {json.dumps(i.since)}")
            lines.append("")
        text = "\n".join(lines)
        tomllib.loads(text)  # what is written reads back
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)

    # For the pages and the sidecars

    def as_json(self, kept: tuple[Input, ...] | None = None) -> list[dict]:
        """Every input as the pages list it; `channel` is its place in a take now (None: not recorded)."""
        kept = self.kept() if kept is None else kept
        place = {i.number: c for c, i in enumerate(kept)}
        return [{"input": i.number, "name": i.name, "named": i.named, "mic": i.mic, "details": i.details,
                 "for": i.for_kind, "record": i.number in place, "channel": place.get(i.number), "socket": i.socket,
                 "since": i.since}
                for i in self.all()]

    def mics_json(self) -> list[dict]:
        """The room's mics as the pages list them, each with the input it is on now (None: not plugged in)."""
        on = {i.mic: i.number for i in self.all() if i.mic}
        return [{"id": m.id, "name": m.name, "details": m.details, "for": m.for_kind, "input": on.get(m.id)}
                for m in self.mics()]


def channels_meta(kept: tuple[Input, ...], inputs: Inputs | None = None) -> list[dict]:
    """A take's sidecar `channels`: which input each channel carried, by name (read again from `inputs`, if given,
    so a mic named while the take was recorded is named in it)."""
    now = {i.number: i for i in inputs.all() if i.named} if inputs is not None else {}
    out = []
    for c, i in enumerate(kept):
        i = now.get(i.number, i)
        entry = {"channel": c, "input": i.number, "input_name": i.name}
        if i.details:
            entry["input_details"] = i.details
        if i.for_kind:
            entry["input_for"] = i.for_kind
        out.append(entry)
    return out


def merge_channels(app_channels: list | None, kept: tuple[Input, ...], inputs: Inputs | None = None) -> list[dict]:
    """The sidecar's `channels`: the app's own per channel (the exercise app's `carries`), with each input's name,
    details and kind as the room has them."""
    base = channels_meta(kept, inputs)
    mine = {int(c.get("input", -1)): c for c in (app_channels or []) if isinstance(c, dict)}
    return [{**mine.get(b["input"], {}), **b} for b in base]


def input_levels(peaks, fs: float, count: int) -> list[float]:
    """Every device input's level (dBFS) from a block's per-channel peaks (input N on channel N - 1)."""
    from .dsp import db
    return [round(db(int(p) / fs), 1) for p in list(peaks)[:count]]


STATIC = Path(__file__).resolve().parent / "static"


def add_routes(app, inputs: Inputs, kept=None) -> None:
    """The inputs' routes, the same in every app: GET /api/inputs (every input of the device: its mic, what it is,
    whether it is recorded and its channel in a take now; and the room's mics), PUT /api/inputs/N (a mic from the
    list on it, a mic not on the list, or nothing; that mic onto the list; record it or not: {"mic", "name",
    "details", "for", "clear", "add_to_list", "record"}, each optional) and the pages' shared script, /inputs.js
    (recorder/static/inputs.js). `kept`: a function giving the app's own idea of the inputs a take holds now, if it
    has one."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from pydantic import BaseModel, ConfigDict, Field

    class InputIn(BaseModel):
        model_config = ConfigDict(populate_by_name=True)
        mic: str | None = None
        name: str | None = None
        details: str | None = None
        for_kind: str | None = Field(None, alias="for")
        clear: bool = False
        add_to_list: bool = False
        record: bool | None = None

    def listing() -> dict:
        return {"inputs": inputs.as_json(kept() if kept else None), "mics": inputs.mics_json(),
                "problems": list(inputs.problems), "version": inputs.version, "signal_dbfs": SIGNAL_DBFS}

    @app.get("/api/inputs")
    def list_inputs():
        """Every input of the device, by number."""
        return listing()

    @app.put("/api/inputs/{number}")
    def put_input(number: int, body: InputIn):
        """Say which mic is on an input (one from the room's list, one not on it, or none), put a mic not on the
        list onto it, record the input or not: in the room's inputs file, for every app, from its next take."""
        try:
            inputs.update(number, mic=body.mic, name=body.name, details=body.details, for_kind=body.for_kind,
                          record=body.record, clear=body.clear, add_to_list=body.add_to_list)
        except InputError as e:
            raise HTTPException(400, str(e))
        except OSError as e:
            raise HTTPException(500, f"could not write {inputs.path}: {e}")
        return listing()

    @app.get("/inputs.js")
    def inputs_js():
        return FileResponse(STATIC / "inputs.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})
