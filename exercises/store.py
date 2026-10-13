"""Exercises on disk: one folder per exercise in exercises_dir, put there from the desktop
by tools/push-exercise.

    <exercises_dir>/<id>/exercise.json   what it is, its tempo and length, its pages
    <exercises_dir>/<id>/page-1.svg ...  the score, rendered by MuseScore on the desktop
    <exercises_dir>/<id>/score.musicxml  the score itself
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


@dataclass
class Exercise:
    id: str
    title: str
    instrument: str
    tempo: dict  # {"bpm", "pickup_beats", "beats_per_bar"}: what the click follows
    beats: float  # from the first note to the end of the last bar
    bars: int
    pages: list[str] = field(default_factory=list)
    source: str = ""  # where it came from (a path in the project it was taken from)
    note: str = ""


class ExerciseNotFound(Exception):
    pass


class Exercises:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _folder(self, exercise_id: str) -> Path:
        if not ID_RE.match(exercise_id):
            raise ExerciseNotFound(exercise_id)
        d = self.dir / exercise_id
        if not (d / "exercise.json").exists():
            raise ExerciseNotFound(exercise_id)
        return d

    def get(self, exercise_id: str) -> Exercise:
        d = self._folder(exercise_id)
        try:
            raw = json.loads((d / "exercise.json").read_text())
            tempo = raw["tempo"]
            return Exercise(
                id=exercise_id, title=raw.get("title") or exercise_id, instrument=raw.get("instrument", ""),
                tempo={"bpm": float(tempo["bpm"]), "pickup_beats": float(tempo.get("pickup_beats", 0.0)),
                       "beats_per_bar": int(tempo.get("beats_per_bar", 4))},
                beats=float(raw["beats"]), bars=int(raw.get("bars", 0)),
                pages=[p for p in raw.get("pages", []) if (d / p).exists()],
                source=raw.get("source", ""), note=raw.get("note", ""))
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise ExerciseNotFound(f"{exercise_id}: {e}")

    def list(self) -> list[Exercise]:
        out = []
        for d in sorted(self.dir.iterdir()):
            if d.is_dir() and ID_RE.match(d.name) and (d / "exercise.json").exists():
                try:
                    out.append(self.get(d.name))
                except ExerciseNotFound:
                    continue
        return out

    def page(self, exercise_id: str, n: int) -> Path:
        ex = self.get(exercise_id)
        if not 1 <= n <= len(ex.pages):
            raise ExerciseNotFound(f"{exercise_id} page {n}")
        return self.dir / exercise_id / ex.pages[n - 1]
