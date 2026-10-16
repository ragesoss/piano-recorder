"""Takes on disk: one WAV per take in takes_dir, named by its start time,
with an optional JSON sidecar for the note."""

import json
import re
import shutil
import wave
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from . import dsp

ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}$")
ID_FORMAT = "%Y-%m-%d_%H%M%S"


@dataclass
class Take:
    id: str
    started: str  # ISO 8601, local time
    seconds: float
    note: str
    peak_dbfs: float | None
    backing: str | None = None  # a play-along take: the backing's title
    playalong: dict | None = None  # where the backing sits in it (recorder/playalong.py)
    clean: bool = False  # a copy without the backing exists
    # a take made in the exercise app: which exercise, at what tempo, and where its beat 0 (the
    # music's first note) is in the take, once lined up with the click
    exercise: str | None = None
    exercise_title: str | None = None
    tempo_bpm: float | None = None
    beat0_s: float | None = None
    # which instrument it was played on, of those the project knows (or "a guitar (not listed)")
    instrument_id: str | None = None
    instrument_name: str | None = None
    # the exercise app's: "exercise", "playalong" or "free"; the instruments (one or two:
    # {"instrument_id", "instrument_name", "kind"}); and what each channel carried, in order
    # ({"channel", "input", "input_name", "carries", "carries_name"}: an instrument's id, "room" or "none")
    kind: str | None = None
    instruments: list | None = None
    channels: list | None = None


class TakeNotFound(Exception):
    pass


class Takes:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, take_id: str) -> Path:
        if not ID_RE.match(take_id):
            raise TakeNotFound(take_id)
        p = self.dir / f"{take_id}.wav"
        if not p.exists():
            raise TakeNotFound(take_id)
        return p

    def new_id(self) -> str:
        take_id = datetime.now().strftime(ID_FORMAT)
        while (self.dir / f"{take_id}.wav").exists():  # two takes in one second
            take_id = datetime.fromtimestamp(datetime.strptime(take_id, ID_FORMAT).timestamp() + 1).strftime(ID_FORMAT)
        return take_id

    def _meta(self, take_id: str) -> dict:
        side = self.dir / f"{take_id}.json"
        try:
            return json.loads(side.read_text())
        except (OSError, ValueError):
            return {}

    def _write_meta(self, take_id: str, meta: dict) -> None:
        side = self.dir / f"{take_id}.json"
        tmp = side.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=1))
        tmp.replace(side)

    def get(self, take_id: str) -> Take:
        p = self.path(take_id)
        try:
            with wave.open(str(p)) as w:
                seconds = w.getnframes() / w.getframerate()
        except (wave.Error, EOFError):
            seconds = 0.0
        meta = self._meta(take_id)
        return Take(
            id=take_id,
            started=datetime.strptime(take_id, ID_FORMAT).isoformat(),
            seconds=round(seconds, 2),
            note=meta.get("note", ""),
            peak_dbfs=meta.get("peak_dbfs"),
            backing=meta.get("backing_title") or meta.get("backing"),
            playalong=meta.get("playalong"),
            clean=self.clean_path(take_id).exists(),
            exercise=meta.get("exercise"),
            exercise_title=meta.get("exercise_title"),
            tempo_bpm=(meta.get("tempo") or {}).get("bpm"),
            beat0_s=meta.get("beat0_s"),
            instrument_id=meta.get("instrument_id"),
            instrument_name=meta.get("instrument_name"),
            kind=meta.get("kind"),
            instruments=meta.get("instruments"),
            channels=meta.get("channels"),
        )

    def list(self) -> list[Take]:
        ids = sorted((p.stem for p in self.dir.glob("*.wav") if ID_RE.match(p.stem)), reverse=True)
        return [self.get(i) for i in ids]

    def set_note(self, take_id: str, note: str) -> Take:
        self.path(take_id)
        meta = self._meta(take_id)
        meta["note"] = note[:500]
        self._write_meta(take_id, meta)
        return self.get(take_id)

    def finish(self, take_id: str, peak_dbfs: float) -> None:
        meta = self._meta(take_id)
        meta["peak_dbfs"] = round(peak_dbfs, 1)
        self._write_meta(take_id, meta)

    def meta(self, take_id: str) -> dict:
        self.path(take_id)
        return self._meta(take_id)

    def update_meta(self, take_id: str, values: dict) -> None:
        meta = self._meta(take_id)
        meta.update(values)
        self._write_meta(take_id, meta)

    def clean_path(self, take_id: str) -> Path:
        """A play-along take with its backing subtracted (it is not a take of its own)."""
        return self.dir / f"{take_id}.clean.wav"

    def write_clean(self, take_id: str, x: np.ndarray, rate: int) -> None:
        self.path(take_id)
        tmp = self.dir / f"{take_id}.clean.wav.tmp"
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(dsp.from_float(x, np.dtype("<i2")).tobytes())
        tmp.replace(self.clean_path(take_id))

    def delete(self, take_id: str) -> None:
        self.path(take_id).unlink()
        (self.dir / f"{take_id}.json").unlink(missing_ok=True)
        self.clean_path(take_id).unlink(missing_ok=True)

    def load(self, take_id: str) -> tuple[np.ndarray, int]:
        """A take as float32, (frames,) for mono or (frames, channels)."""
        with wave.open(str(self.path(take_id))) as w:
            rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            data = w.readframes(w.getnframes())
        dtype = {2: np.dtype("<i2"), 4: np.dtype("<i4")}[width]
        x = dsp.to_float(np.frombuffer(data, dtype=dtype))
        return (x if ch == 1 else x.reshape(-1, ch)), rate

    def peaks(self, take_id: str, n: int = 400) -> "list[float]":
        """The take's loudest sample in each of `n` equal slices (0-1, all channels), read a second
        at a time: a waveform to draw."""
        with wave.open(str(self.path(take_id))) as w:
            rate, ch, width, total = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            dtype = {2: np.dtype("<i2"), 4: np.dtype("<i4")}[width]
            fs = float(dsp.full_scale(dtype))
            out = np.zeros(n)
            edges = np.linspace(0, total, n + 1).astype(np.int64)
            pos = 0
            while pos < total:
                x = np.frombuffer(w.readframes(rate), dtype=dtype).reshape(-1, ch)
                if not len(x):
                    break
                a = np.abs(x.astype(np.int64)).max(axis=1) / fs
                slot = np.searchsorted(edges, pos + np.arange(len(a)), side="right") - 1
                np.maximum.at(out, np.clip(slot, 0, n - 1), a)
                pos += len(a)
        return [round(float(v), 4) for v in out]

    def free_mb(self) -> float:
        return shutil.disk_usage(self.dir).free / 1e6
