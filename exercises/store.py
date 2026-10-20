"""Exercises on disk: one folder per exercise in exercises_dir, put there from the desktop
by tools/push-exercise.

    <exercises_dir>/<id>/exercise.json   what it is, its tempo and length, its pages
    <exercises_dir>/<id>/page-1.svg ...  the score, rendered by MuseScore on the desktop
    <exercises_dir>/<id>/score.musicxml  the score itself
    <exercises_dir>/<id>/readme.md       how to play it (the exercise's README.md in the project),
                                         shown above the score; optional
    <exercises_dir>/<id>/chart.wav       its notes as audio (exercises/chart.py, at push time), for
                                         the app to play with its click before a take; optional
    <exercises_dir>/status.json          what the project has of each exercise (tools/push-status,
                                         from dobro-transcription's exercises/status.py): "good",
                                         "wanted" (a new take, and why) or "needed" (no take yet),
                                         and which of this app's takes it has (an exercise for
                                         any instrument, "generic": the kinds it has takes on,
                                         played_on); and the instruments the project knows, for
                                         the takes
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
    readme: bool = False  # it has instructions (readme.md)
    audio: dict = field(default_factory=dict)  # its chart as audio: {"seconds", "sound"}; {} when it has none
    source: str = ""  # where it came from (a path in the project it was taken from)
    note: str = ""
    status: str = ""  # from status.json: "good", "wanted", "needed"; "" when it does not say
    status_note: str = ""  # why a new take is wanted
    project_takes: list[str] = field(default_factory=list)  # this app's takes the project has
    status_updated: str = ""  # when status.json was made
    # an exercise for any instrument (instrument "generic"): the kinds the project has takes of it on, and how
    # many (status.json's played_on: {"mandolin": 2}); {} for the others
    played_on: dict = field(default_factory=dict)


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
                readme=self._readme_path(d, raw) is not None,
                audio={k: v for k, v in (raw.get("audio") or {}).items() if k in ("seconds", "sound")}
                if self._audio_path(d, raw) is not None else {},
                source=raw.get("source", ""), note=raw.get("note", ""),
                status=str(st.get("status", "")), status_note=str(st.get("note", "")),
                project_takes=[str(t) for t in st.get("app_takes", [])], status_updated=str(status.get("generated", "")),
                played_on={str(k): int(v) for k, v in (st.get("played_on") or {}).items()
                           if isinstance(v, (int, float)) and not isinstance(v, bool)}
                if isinstance(st.get("played_on"), dict) else {})
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

    @staticmethod
    def _readme_path(d: Path, raw: dict) -> Path | None:
        """The instructions' file, named in exercise.json: in the exercise's own folder only."""
        name = raw.get("readme")
        if not isinstance(name, str) or not name or name != Path(name).name or not (d / name).is_file():
            return None
        return d / name

    @staticmethod
    def _audio_path(d: Path, raw: dict) -> Path | None:
        """The chart's audio, named in exercise.json ({"file", "beat0_s", ...}): in its own folder only."""
        a = raw.get("audio")
        name = a.get("file") if isinstance(a, dict) else None
        if not isinstance(name, str) or not name or name != Path(name).name or not (d / name).is_file():
            return None
        return d / name

    def audio(self, exercise_id: str) -> tuple[Path, float]:
        """The exercise's chart as audio, and where its beat 0 is in it (s)."""
        d = self._folder(exercise_id)
        try:
            raw = json.loads((d / "exercise.json").read_text())
            path = self._audio_path(d, raw)
            if path is None:
                raise ExerciseNotFound(f"{exercise_id} has no audio")
            return path, float(raw["audio"].get("beat0_s", 0.0))
        except (OSError, ValueError, AttributeError, TypeError) as e:
            raise ExerciseNotFound(f"{exercise_id} audio: {e}")

    def readme(self, exercise_id: str) -> str:
        """The exercise's instructions (Markdown)."""
        d = self._folder(exercise_id)
        try:
            path = self._readme_path(d, json.loads((d / "exercise.json").read_text()))
            if path is None:
                raise ExerciseNotFound(f"{exercise_id} has no readme")
            return path.read_text(encoding="utf-8")
        except (OSError, ValueError, AttributeError) as e:
            raise ExerciseNotFound(f"{exercise_id} readme: {e}")

    def page(self, exercise_id: str, n: int) -> Path:
        ex = self.get(exercise_id)
        if not 1 <= n <= len(ex.pages):
            raise ExerciseNotFound(f"{exercise_id} page {n}")
        return self.dir / exercise_id / ex.pages[n - 1]
