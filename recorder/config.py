"""Load the recorder's config and the hardware profiles it names.

The config picks the parts; a profile describes one audio device
(docs/components.md, slots 2 and 3). This module and recorder.audio are the
only code that knows card ids, control names and formats.
"""

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARDWARE_DIR = REPO / "hardware"
SYSTEM_CONFIG = Path("/etc/practice-recorder.toml")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Control:
    name: str
    min: int | None = None
    max: int | None = None
    set: int | str | None = None


@dataclass(frozen=True)
class Stream:
    device: str
    format: str
    rate: int
    channels: int
    keep_channels: tuple[int, ...] = ()
    remove_dc: bool = False
    buffer_ms: int = 2000
    controls: dict[str, Control] = field(default_factory=dict)
    # the device's inputs (a capture stream's): how many, input N on channel N - 1 (0: one per channel), and what
    # each socket is, for the pages ("Front, mic or line")
    inputs: int = 0
    sockets: tuple[str, ...] = ()


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    backend: str  # "alsa" or "fake"
    card: str | None
    usb_id: str | None
    capture: Stream | None
    playback: Stream | None
    path: Path
    # The device's own playback comes back in its capture (T7). Play-along takes then
    # carry the backing electrically, which lines them up to the sample and can be
    # subtracted (recorder/playalong.py).
    loopback: bool = False
    # Settings of the fake backend (backend = "fake" only): its loopback level and delay.
    fake: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Input:
    """What is plugged into one of the device's inputs: the room's inputs file's [inputs.N] (recorder/inputs.py),
    or before it a program's own config's. A mic from the room's list (`mic`) brings its name, details and kind."""
    number: int  # the interface's input number: the capture channel's index plus one
    name: str  # "Nadine"
    details: str = ""  # "Ear Trumpet Labs Nadine, on the bass"
    for_kind: str = ""  # a pickup or mic on one kind of instrument ("bass"); "": a mic for the room
    mic: str = ""  # the mic's id in the room's list ("nadine"); "": a mic not on the list, or nothing named
    record: bool = True  # the apps keep it in their takes
    named: bool = True  # False: nothing says what is on it ("Input 2")
    socket: str = ""  # the profile's words for the socket ("Front, mic, line or instrument")
    since: str = ""  # when a page last changed it (ISO 8601)


@dataclass(frozen=True)
class Loudness:
    target_dbfs: float
    limit_dbfs: float
    max_gain_db: float


@dataclass(frozen=True)
class Config:
    capture: Profile
    playback: Profile
    takes_dir: Path
    backing_dir: Path  # backing tracks for play-along takes (recorder/backing.py)
    web_host: str
    web_port: int
    address: str | None
    loudness: Loudness
    path: Path
    # Other programs that play through the same speakers: their base URLs.
    # See recorder/peers.py.
    peers: list[str] = field(default_factory=list)
    # The exercise app's exercises (exercises/store.py); None for the practice recorder.
    exercises_dir: Path | None = None
    # What the config itself says is plugged into each kept input, in its keep_channels order: what stands in for
    # the room's inputs file (inputs_path, recorder/inputs.py) while there is none.
    inputs: tuple[Input, ...] = ()
    # The room's inputs file, shared by every app ($ROOM_INPUTS_FILE, else the config's inputs_file, else
    # ~/room-inputs.toml).
    inputs_path: Path = Path("~/room-inputs.toml").expanduser()


def _stream(raw: dict, where: str) -> Stream:
    try:
        controls = {
            key: Control(**value) for key, value in raw.get("controls", {}).items()
        }
        stream = Stream(
            device=raw["device"],
            format=raw["format"],
            rate=int(raw["rate"]),
            channels=int(raw["channels"]),
            keep_channels=tuple(raw.get("keep_channels", range(int(raw["channels"])))),
            remove_dc=bool(raw.get("remove_dc", False)),
            buffer_ms=int(raw.get("buffer_ms", 2000)),
            controls=controls,
            inputs=int(raw.get("inputs", 0)),
            sockets=tuple(str(x) for x in raw.get("sockets", ())),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError(f"{where}: {e}") from e
    if any(c < 0 or c >= stream.channels for c in stream.keep_channels):
        raise ConfigError(f"{where}: keep_channels outside 0-{stream.channels - 1}")
    if not 0 <= stream.inputs <= stream.channels:
        raise ConfigError(f"{where}: inputs: 0-{stream.channels} (input N on channel N - 1)")
    return stream


def load_profile(name: str, hardware_dir: Path = HARDWARE_DIR) -> Profile:
    path = hardware_dir / f"{name}.toml"
    if not path.exists():
        raise ConfigError(f"no profile {name!r}: {path} does not exist")
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    backend = raw.get("backend", "alsa")
    if backend not in ("alsa", "fake"):
        raise ConfigError(f"{path}: unknown backend {backend!r}")
    if backend == "alsa" and not raw.get("card"):
        raise ConfigError(f"{path}: an alsa profile needs a card id")
    return Profile(
        name=raw.get("name", name),
        description=raw.get("description", ""),
        backend=backend,
        card=raw.get("card"),
        usb_id=raw.get("usb_id"),
        capture=_stream(raw["capture"], f"{path} [capture]") if "capture" in raw else None,
        playback=_stream(raw["playback"], f"{path} [playback]") if "playback" in raw else None,
        path=path,
        loopback=bool(raw.get("loopback", False)),
        fake=dict(raw.get("fake", {})),
    )


def config_path() -> Path:
    """$PRACTICE_RECORDER_CONFIG, else the system config."""
    return Path(os.environ.get("PRACTICE_RECORDER_CONFIG", SYSTEM_CONFIG))


def load_config(path: Path | None = None) -> Config:
    path = Path(path) if path else config_path()
    if not path.exists():
        raise ConfigError(f"no config at {path}")
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    hardware_dir = Path(raw.get("hardware_dir", HARDWARE_DIR))
    capture = load_profile(raw["capture"], hardware_dir)
    playback = load_profile(raw["playback"], hardware_dir)
    if capture.capture is None:
        raise ConfigError(f"profile {capture.name!r} has no [capture]")
    if playback.playback is None:
        raise ConfigError(f"profile {playback.name!r} has no [playback]")
    if "keep_channels" in raw:
        # the inputs this program keeps, in place of the profile's (the exercise app keeps a third)
        stream = replace(capture.capture, keep_channels=tuple(int(c) for c in raw["keep_channels"]))
        if not stream.keep_channels or any(c < 0 or c >= stream.channels for c in stream.keep_channels):
            raise ConfigError(f"{path}: keep_channels outside 0-{stream.channels - 1}")
        capture = replace(capture, capture=stream)
    def place(value: str) -> Path:
        d = Path(value).expanduser()
        return d if d.is_absolute() else REPO / d

    named = raw.get("inputs", {})
    kept = [c + 1 for c in capture.capture.keep_channels]
    if not isinstance(named, dict) or any(not str(k).isdigit() or int(k) not in kept for k in named):
        raise ConfigError(f"{path}: [inputs] names an input not kept (kept: {kept})")
    inputs = []
    for n in kept:
        entry = named.get(str(n), {})
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: [inputs.{n}] is not a table")
        inputs.append(Input(number=n, name=str(entry.get("name") or f"Input {n}"), details=str(entry.get("details", "")),
                            for_kind=str(entry.get("for", "")), named=bool(entry.get("name"))))

    takes_dir = place(raw["takes_dir"])
    backing_dir = place(raw.get("backing_dir", "~/backing"))
    web = raw.get("web", {})
    loud = raw.get("loudness", {})
    return Config(
        capture=capture,
        playback=playback,
        takes_dir=takes_dir,
        backing_dir=backing_dir,
        web_host=web.get("host", "0.0.0.0"),
        web_port=int(web.get("port", 8000)),
        address=raw.get("address"),
        loudness=Loudness(
            target_dbfs=float(loud.get("target_dbfs", -21.0)),
            limit_dbfs=float(loud.get("limit_dbfs", -1.0)),
            max_gain_db=float(loud.get("max_gain_db", 30.0)),
        ),
        path=path,
        peers=[str(u) for u in raw.get("peers", [])],
        exercises_dir=place(raw["exercises_dir"]) if "exercises_dir" in raw else None,
        inputs=tuple(inputs),
        inputs_path=place(os.environ.get("ROOM_INPUTS_FILE") or raw.get("inputs_file") or "~/room-inputs.toml"),
    )
