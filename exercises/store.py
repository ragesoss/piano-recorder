"""Exercises on disk: one folder per exercise in exercises_dir, put there from the desktop
by tools/push-exercise.

    <exercises_dir>/<id>/exercise.json   what it is, its tempo and length, its pages
    <exercises_dir>/<id>/page-1.svg ...  the score, rendered by MuseScore on the desktop
    <exercises_dir>/<id>/score.musicxml  the score itself
    <exercises_dir>/status.json          what the project has of each exercise (tools/push-status,
                                         from dobro-transcription's exercises/status.py): "good",
                                         "wanted" (a new take, and why) or "needed" (no take yet),
                                         and which of this app's takes it has; and the
                                         instruments the project knows, for the takes
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
    status: str = ""  # from status.json: "good", "wanted", "needed"; "" when it does not say
    status_note: str = ""  # why a new take is wanted
    project_takes: list[str] = field(default_factory=list)  # this app's takes the project has
    status_updated: str = ""  # when status.json was made


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

    def status(self) -> dict:
        """status.json, or nothing when it is missing or unreadable (the app works without it)."""
        try:
            raw = json.loads((self.dir / "status.json").read_text())
            return raw if isinstance(raw.get("exercises"), dict) else {}
        except (OSError, ValueError, AttributeError):
            return {}

    def instruments(self) -> list[dict]:
        """The instruments the project knows (from status.json): id, kind (dobro, bass, ...),
        a short name, the name, details."""
        out = []
        for i in self.status().get("instruments", []) or []:
            if isinstance(i, dict) and i.get("id") and i.get("kind"):
                out.append({k: str(i.get(k) or (i.get("name") if k == "short" else "") or "")
                            for k in ("id", "kind", "short", "name", "details")})
        return out

    def get(self, exercise_id: str, status: dict | None = None) -> Exercise:
        status = self.status() if status is None else status
        st = status.get("exercises", {}).get(exercise_id) or {}
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
                source=raw.get("source", ""), note=raw.get("note", ""),
                status=str(st.get("status", "")), status_note=str(st.get("note", "")),
                project_takes=[str(t) for t in st.get("app_takes", [])], status_updated=str(status.get("generated", "")))
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise ExerciseNotFound(f"{exercise_id}: {e}")

    def list(self) -> list[Exercise]:
        out = []
        status = self.status()
        for d in sorted(self.dir.iterdir()):
            if d.is_dir() and ID_RE.match(d.name) and (d / "exercise.json").exists():
                try:
                    out.append(self.get(d.name, status))
                except ExerciseNotFound:
                    continue
        return out

    def page(self, exercise_id: str, n: int) -> Path:
        ex = self.get(exercise_id)
        if not 1 <= n <= len(ex.pages):
            raise ExerciseNotFound(f"{exercise_id} page {n}")
        return self.dir / exercise_id / ex.pages[n - 1]
