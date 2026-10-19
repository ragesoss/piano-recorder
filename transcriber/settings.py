"""The transcribe app's own settings, from the same file as the recorder's (recorder/config.py reads the
devices, the kept inputs, takes_dir, peers, web and loudness there):

    [instruments.dobro]   name = "Dobro", input = 4    the pipeline's instrument, and the input that carries it
    [instruments.bass]    name = "Bass", input = 3     (the page can choose another of the kept inputs)
    [instruments.piano]   name = "Piano", input = 4    (the piano: a grand staff, no tab)
    [pipeline]            python, src, threads, nice    the transcription project's package, and how to run it
    [review]              root, python, data, port      the review app's generator (dobro-transcription)
"""

import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from recorder.config import REPO, Config, ConfigError

PIPELINE_INSTRUMENTS = ("dobro", "bass", "piano")  # what `transcribe --instrument` takes that this app records
GRAND_STAFF = ("piano",)  # written as a grand staff (two staves, the hands), no tab: the others as notation and tab


@dataclass(frozen=True)
class Instrument:
    kind: str  # the pipeline's --instrument: "dobro", "bass", "piano"
    name: str  # "Dobro"
    input: int  # the interface's input number that carries it unless the page says otherwise


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
    kept = {i.number for i in cfg.inputs}
    insts = []
    for kind, entry in (raw.get("instruments") or {}).items():
        if kind not in PIPELINE_INSTRUMENTS:
            raise ConfigError(f"{cfg.path}: [instruments.{kind}]: the pipeline transcribes {', '.join(PIPELINE_INSTRUMENTS)}")
        if not isinstance(entry, dict) or int(entry.get("input", 0)) not in kept:
            raise ConfigError(f"{cfg.path}: [instruments.{kind}] needs an input this app keeps ({sorted(kept)})")
        insts.append(Instrument(kind=kind, name=str(entry.get("name") or kind.capitalize()), input=int(entry["input"])))
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
