"""The transcribe app's takes: one folder each in takes_dir, laid out as a folder of the transcription
project's corpus (dobro-transcription `tunes/<instrument>/<name>/`), so its review side reads it as one:

    <takes_dir>/<YYYY-MM-DD_HHMMSS>/
        take-01.flac             the instrument's input alone: mono, the device's rate, 24-bit FLAC
        takes.csv                the corpus's columns (`transcribe.evaluate.read_takes`)
        meta.json                what the app knows: instrument, tempo, meter, count-in, click, title, ...
        transcription.musicxml   the pipeline's notation and tab, the piano's grand staff, any stringed
                                 instrument's one staff (once transcribed)
        transcription.json       its notes and the score's beats in the take's time (job/run_pipeline.py)
        raw/<id>.flac, .json     every kept input as recorded (24-bit FLAC) and the engine's sidecar
        play/full.mp3, half.mp3  for the page: the take, and at half speed with its pitch kept
        transcribe.log           what the pipeline said on stderr
        chart-draft/             its chord chart as drafted (chart.py: draft.json, ...), once asked
        chart.log                what the drafter and the chart's review said

    <takes_dir>/.work/           the engine's own takes while they record and line up (recorder/takes.py),
                                 its speakers' level (settings.json)
    <takes_dir>/choices.json     the page's choices for the next take
"""

import csv
import io
import json
import shutil
import threading
from pathlib import Path

from recorder.takes import ID_RE, TakeNotFound

from .settings import chart_instrument, staves

TAKE = "take-01.flac"
COLUMNS = ("take", "beat0_s", "bpm", "metronome", "session", "pickup_beats", "beats_per_bar")
TITLE_MAX = 120


class Folders:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.version = 0  # raised on every change, for the page

    def touched(self) -> None:
        with self.lock:
            self.version += 1

    def path(self, take_id: str) -> Path:
        if not isinstance(take_id, str) or not ID_RE.match(take_id):
            raise TakeNotFound(take_id)
        d = self.root / take_id
        if not d.is_dir():
            raise TakeNotFound(take_id)
        return d

    def create(self, take_id: str) -> Path:
        if not ID_RE.match(take_id):
            raise TakeNotFound(take_id)
        d = self.root / take_id
        d.mkdir(exist_ok=True)
        return d

    def ids(self) -> list[str]:
        """Newest first."""
        return sorted((d.name for d in self.root.iterdir() if d.is_dir() and ID_RE.match(d.name)), reverse=True)

    def meta(self, take_id: str) -> dict:
        try:
            return json.loads((self.path(take_id) / "meta.json").read_text())
        except (OSError, ValueError):
            return {}

    def write_meta(self, take_id: str, meta: dict) -> None:
        d = self.path(take_id)
        with self.lock:
            tmp = d / "meta.json.tmp"
            tmp.write_text(json.dumps(meta, indent=1, ensure_ascii=False))
            tmp.replace(d / "meta.json")
            self.version += 1

    def update_meta(self, take_id: str, **changes) -> dict:
        """Change some of meta.json's keys (a dict value replaces the key's whole value)."""
        with self.lock:
            meta = self.meta(take_id)
            meta.update(changes)
            self.write_meta(take_id, meta)
        return meta

    def transcription(self, take_id: str) -> dict | None:
        try:
            return json.loads((self.path(take_id) / "transcription.json").read_text())
        except (OSError, ValueError):
            return None

    def write_takes_csv(self, take_id: str, beat0_s: float, bpm: float, metronome: bool, pickup_beats: float,
                        beats_per_bar: int) -> None:
        """takes.csv as the corpus has it: one row, the take."""
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(COLUMNS)
        w.writerow([TAKE, f"{beat0_s:.3f}", f"{bpm:g}", "yes" if metronome else "no", "", f"{pickup_beats:g}",
                    int(beats_per_bar)])
        d = self.path(take_id)
        tmp = d / "takes.csv.tmp"
        tmp.write_text(buf.getvalue())
        tmp.replace(d / "takes.csv")

    def set_title(self, take_id: str, title: str) -> dict:
        return self.update_meta(take_id, title=" ".join(str(title).split())[:TITLE_MAX])

    def delete(self, take_id: str) -> None:
        shutil.rmtree(self.path(take_id))
        self.touched()

    def summary(self, take_id: str) -> dict:
        """A take as the page lists it."""
        d = self.path(take_id)
        m = self.meta(take_id)
        tr = m.get("transcription") or {}
        return {
            "id": take_id,
            "title": m.get("title", ""),
            "instrument": m.get("instrument", ""),
            "instrument_name": m.get("instrument_name", ""),
            "tab": staves(m.get("instrument", "")) == "tab",  # drawn as notation and tab
            "staves": staves(m.get("instrument", "")),  # "tab", "grand" (the piano) or "one" (any stringed instrument)
            "kind": m.get("kind"),  # any stringed instrument's kind as chosen (None: not sure)
            "kind_name": m.get("kind_name", ""),
            "input": m.get("input"),
            "input_name": m.get("input_name", ""),
            "recorded_at": m.get("recorded_at", ""),
            "seconds": m.get("seconds"),
            "bpm": m.get("bpm"),
            "beats_per_bar": m.get("beats_per_bar"),
            "count_in_bars": m.get("count_in_bars"),
            "click": m.get("click"),
            "downbeat_s": m.get("downbeat_s"),
            "click_found": m.get("click_found"),
            "peak_dbfs": m.get("peak_dbfs"),
            "imported_from": m.get("imported_from"),
            "transcription": tr,
            "review": m.get("review"),
            "chart": m.get("chart"),  # its chord chart's review (chart.py): {"id", "made", ...} or {"error", ...}
            "chart_instrument": chart_instrument(m.get("instrument", ""), m.get("kind")),  # the drafter's, as played
            "playable": (d / "play" / "full.mp3").is_file() and (d / "play" / "half.mp3").is_file(),
            "has_take": (d / TAKE).is_file(),
        }

    def list(self) -> list[dict]:
        out = []
        for i in self.ids():
            try:
                out.append(self.summary(i))
            except TakeNotFound:
                continue  # deleted meanwhile
        return out
