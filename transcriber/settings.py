"""The transcribe app's own settings, from the same file as the recorder's (recorder/config.py reads the
devices, the kept inputs, takes_dir, peers, web and loudness there):

    [instruments.dobro]   name = "Dobro", input = "Bluebird SL"
                                                       the pipeline's instrument, and the input that carries it,
                                                       by its mic's name in the room's inputs (recorder/inputs.py;
                                                       or a name it went by, "Room mic"; the page can choose
                                                       another of the recorded inputs). With no input of that
                                                       name: one `for` its kind, else the room's (`Inputs.pick`).
                                                       A number (configs of before 2026-10-20): the name the
                                                       config's own [inputs.N] gave that input
    [instruments.bass]    name = "Bass", input = "Nadine"
    [instruments.piano]   name = "Piano", input = "MXL 990"
                                                       (the piano: a grand staff, no tab)
    [instruments.strings] name = "Any stringed instrument", input = "MXL 990"
                                                       (the generic path: one staff, no tab; its kind chosen on
                                                       the page, `KINDS`, or "not sure")
    [pipeline]            python, src, threads, nice    the transcription project's package, and how to run it (its
                                                       chord-chart drafter too: chart.py)
    [review]              root, python, data, port      the review app's generator (dobro-transcription)
"""

import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from recorder.config import REPO, Config, ConfigError

PIPELINE_INSTRUMENTS = ("dobro", "bass", "piano", "strings")  # what `transcribe --instrument` takes that this app records
GRAND_STAFF = ("piano",)  # written as a grand staff (two staves, the hands), no tab
ONE_STAFF = ("strings",)  # written on one staff, no tab (any stringed instrument: no tuning, so no strings or frets)
# "strings": the kinds the page offers, in its order (dobro-transcription `transcribe.strings.kinds`: to the pipeline
# each is only a pitch range, a hint); none chosen is "not sure", and the range is read from the take
KINDS = {"mandolin": "Mandolin", "banjo": "Banjo", "guitar": "Guitar", "guitalele": "Guitalele",
         "ukulele": "Ukulele", "dulcimer": "Mountain dulcimer", "dobro": "Dobro", "bass": "Bass"}
# "Draft a chord chart" (chart.py): what the drafter's --instrument takes (dobro-transcription `transcribe.chart_draft`),
# in the page's order; "auto": not sure, read from the recording
CHART_INSTRUMENTS = {"auto": "Not sure", "bass": "Bass", "guitar": "Guitar", "dobro": "Dobro", "piano": "Piano",
                     "voice": "Voice", "band": "A band"}


def staves(kind: str) -> str:
    """How an instrument's transcription is written: "tab" (notation and tab), "grand" (a grand staff), "one" (one
    staff, no tab)."""
    return "grand" if kind in GRAND_STAFF else "one" if kind in ONE_STAFF else "tab"


def chart_instrument(instrument: str, kind: str | None = None) -> str:
    """The drafter's --instrument for a take: what it was played on, where the drafter knows it (any stringed
    instrument's kind when that is one), else "auto"."""
    played = kind if instrument == "strings" else instrument
    return played if played in CHART_INSTRUMENTS else "auto"


@dataclass(frozen=True)
class Instrument:
    kind: str  # the pipeline's --instrument: "dobro", "bass", "piano", "strings"
    name: str  # "Dobro"
    input: str  # the name of the input that carries it unless the page says otherwise ("MXL 990"; "": none)
    input_number: int | None = None  # a config of before 2026-10-20 named it by number: that input, if no name fits


@dataclass(frozen=True)
class Pipeline:
    python: str  # a venv's python with numpy, scipy, librosa and soundfile (a path, or a command name)
    src: Path  # the transcription project's package: the folder holding `transcribe/`
    threads: int = 2  # BLAS and numba threads for a run (the unit has 4: a recording keeps the rest)
    nice: int = 19  # a run is the least urgent work on the unit
    timeout_s: float = 1800.0


@dataclass(frozen=True)
class Review:
    root: Path  # the transcription project (dobro-transcription), where `review/make_review.py` is
    python: str  # the review app's venv python
    data: Path | None = None  # REVIEW_DATA: where `--install local` puts the review (the review app's)
    port: int = 8005  # the review app, for the page's link
    timeout_s: float = 900.0

    @property
    def make_review(self) -> Path:
        return self.root / "review" / "make_review.py"


@dataclass(frozen=True)
class Settings:
    instruments: tuple[Instrument, ...]
    pipeline: Pipeline
    review: Review
    keep_raw: bool = True  # each take's folder keeps every kept input as recorded (raw/)
    ffmpeg: str = "ffmpeg"

    def instrument(self, kind: str) -> Instrument | None:
        return next((i for i in self.instruments if i.kind == kind), None)


def _place(value: str) -> Path:
    d = Path(value).expanduser()
    return d if d.is_absolute() else REPO / d


def _command(value: str) -> str:
    """A python to run: a path (made absolute like the config's folders), or a command on the PATH."""
    if "/" not in value:
        return value
    return str(_place(value))


def load_settings(cfg: Config) -> Settings:
    with open(cfg.path, "rb") as f:
        raw = tomllib.load(f)
    legacy = {i.number: i.name for i in cfg.inputs if i.named}  # the config's own [inputs.N], before the inputs file
    insts = []
    for kind, entry in (raw.get("instruments") or {}).items():
        if kind not in PIPELINE_INSTRUMENTS:
            raise ConfigError(f"{cfg.path}: [instruments.{kind}]: the pipeline transcribes {', '.join(PIPELINE_INSTRUMENTS)}")
        value = entry.get("input") if isinstance(entry, dict) else None
        if isinstance(value, bool) or not isinstance(value, (str, int)) or (isinstance(value, str) and not value.strip()):
            raise ConfigError(f"{cfg.path}: [instruments.{kind}] needs an input: its mic's name (\"MXL 990\")")
        if isinstance(value, int):  # a config of before 2026-10-20: the input by number, named as the config named it
            name, number = legacy.get(value, ""), value
        else:
            name, number = " ".join(value.split()), None
        insts.append(Instrument(kind=kind, name=str(entry.get("name") or kind.capitalize()), input=name,
                                input_number=number))
    if not insts:
        raise ConfigError(f"{cfg.path}: no [instruments.*]: what is recorded, and on which input")
    p = raw.get("pipeline") or {}
    r = raw.get("review") or {}
    if "src" not in p or "root" not in r:
        raise ConfigError(f"{cfg.path}: [pipeline] src and [review] root are needed")
    pipeline = Pipeline(python=_command(str(p.get("python", "python3"))), src=_place(str(p["src"])),
                        threads=int(p.get("threads", 2)), nice=int(p.get("nice", 19)),
                        timeout_s=float(p.get("timeout_s", 1800.0)))
    review = Review(root=_place(str(r["root"])), python=_command(str(r.get("python", pipeline.python))),
                    data=_place(str(r["data"])) if r.get("data") else None, port=int(r.get("port", 8005)),
                    timeout_s=float(r.get("timeout_s", 900.0)))
    return Settings(instruments=tuple(insts), pipeline=pipeline, review=review,
                    keep_raw=bool(raw.get("keep_raw", True)), ffmpeg=str(raw.get("ffmpeg", "ffmpeg")))


def which(command: str) -> str | None:
    """The program a command names, if it can be run."""
    if "/" in command:
        return command if Path(command).is_file() else None
    return shutil.which(command)
