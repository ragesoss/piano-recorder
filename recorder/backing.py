"""Backing tracks for play-along takes: one WAV per track in backing_dir, with
a JSON sidecar saying what it is and where it came from. tools/push-backing
puts them there from the desktop."""

import json
import re
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from . import dsp

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass
class Backing:
    id: str
    title: str
    seconds: float
    source: str  # where it came from (a path in the project it was taken from)
    note: str
    # for a count-in: {"bpm", "first_note_s" (in the file), "pickup_beats", "beats_per_bar"}; None: unknown
    tempo: dict | None = None
    added: str = ""  # when it was pushed (ISO 8601, local time): its sidecar's, else the file's time


class BackingNotFound(Exception):
    pass


class Backings:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, backing_id: str) -> Path:
        if not ID_RE.match(backing_id):
            raise BackingNotFound(backing_id)
        p = self.dir / f"{backing_id}.wav"
        if not p.exists():
            raise BackingNotFound(backing_id)
        return p

    def _meta(self, backing_id: str) -> dict:
        try:
            return json.loads((self.dir / f"{backing_id}.json").read_text())
        except (OSError, ValueError):
            return {}

    def get(self, backing_id: str) -> Backing:
        p = self.path(backing_id)
        try:
            with wave.open(str(p)) as w:
                seconds = w.getnframes() / w.getframerate()
        except (wave.Error, EOFError):
            seconds = 0.0
        meta = self._meta(backing_id)
        tempo = meta.get("tempo")
        if not (isinstance(tempo, dict) and tempo.get("bpm") and "first_note_s" in tempo):
            tempo = None
        added = meta.get("added")
        if not isinstance(added, str) or not added:
            added = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
        return Backing(id=backing_id, title=meta.get("title") or backing_id, seconds=round(seconds, 2),
                       source=meta.get("source", ""), note=meta.get("note", ""), tempo=tempo, added=added)

    def list(self) -> list[Backing]:
        ids = sorted(p.stem for p in self.dir.glob("*.wav") if ID_RE.match(p.stem))
        return [self.get(i) for i in ids]

    def load(self, backing_id: str) -> tuple[np.ndarray, int]:
        """A backing track as float32 mono (several channels are averaged)."""
        return load_wav(self.path(backing_id))


def load_wav(path: Path) -> tuple[np.ndarray, int]:
    """A 16- or 32-bit WAV as float32 mono (several channels are averaged), and its rate: a
    backing track, or an exercise's chart (exercises/chart.py)."""
    with wave.open(str(path)) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = w.readframes(w.getnframes())
    dtype = {2: np.dtype("<i2"), 4: np.dtype("<i4")}[width]
    x = dsp.to_float(np.frombuffer(data, dtype=dtype))
    return (x if ch == 1 else x.reshape(-1, ch).mean(axis=1)), rate
