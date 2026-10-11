"""Load the recorder's config and the hardware profiles it names.

The config picks the parts; a profile describes one audio device
(docs/components.md, slots 2 and 3). This module and recorder.audio are the
only code that knows card ids, control names and formats.
"""

import os
import tomllib
from dataclasses import dataclass, field
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
    web_host: str
    web_port: int
    address: str | None
    loudness: Loudness
    path: Path
    # Other programs that play through the same speakers: their base URLs.
    # See recorder/peers.py.
    peers: list[str] = field(default_factory=list)


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
        )
    except (KeyError, TypeError) as e:
        raise ConfigError(f"{where}: {e}") from e
    if any(c < 0 or c >= stream.channels for c in stream.keep_channels):
        raise ConfigError(f"{where}: keep_channels outside 0-{stream.channels - 1}")
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
    takes_dir = Path(raw["takes_dir"]).expanduser()
    if not takes_dir.is_absolute():
        takes_dir = REPO / takes_dir
    web = raw.get("web", {})
    loud = raw.get("loudness", {})
    return Config(
        capture=capture,
        playback=playback,
        takes_dir=takes_dir,
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
    )
