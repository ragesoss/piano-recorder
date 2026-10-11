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

    def delete(self, take_id: str) -> None:
        self.path(take_id).unlink()
        (self.dir / f"{take_id}.json").unlink(missing_ok=True)

    def load(self, take_id: str) -> tuple[np.ndarray, int]:
        """A take as float32, (frames,) for mono or (frames, channels)."""
        with wave.open(str(self.path(take_id))) as w:
            rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            data = w.readframes(w.getnframes())
        dtype = {2: np.dtype("<i2"), 4: np.dtype("<i4")}[width]
        x = dsp.to_float(np.frombuffer(data, dtype=dtype))
        return (x if ch == 1 else x.reshape(-1, ch)), rate

    def free_mb(self) -> float:
        return shutil.disk_usage(self.dir).free / 1e6
