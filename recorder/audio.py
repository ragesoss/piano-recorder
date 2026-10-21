"""The hardware layer: open the capture and playback devices a profile
describes (docs/components.md, slots 2 and 3).

Audio goes straight to the ALSA hw devices through arecord and aplay, with
no PulseAudio or PipeWire in between. The "fake" backend stands in for
development without hardware.
"""

import re
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from . import dsp
from .config import Control, Profile, Stream


class DeviceMissing(Exception):
    pass


def present(profile: Profile) -> bool:
    if profile.backend == "fake":
        return True
    return Path(f"/proc/asound/{profile.card}").exists()


def require(profile: Profile) -> None:
    if not present(profile):
        raise DeviceMissing(
            f"{profile.description or profile.name}: ALSA card {profile.card!r} not found"
        )


# Controls

def _amixer(profile: Profile, *args: str) -> str:
    return subprocess.run(
        ["amixer", "-c", profile.card, *args], capture_output=True, text=True, check=True
    ).stdout


def get_control(profile: Profile, control: Control) -> list[str]:
    if profile.backend == "fake":
        return [str(control.set)] if control.set is not None else []
    m = re.search(r": values=(.*)", _amixer(profile, "cget", f"name={control.name}"))
    return m.group(1).split(",") if m else []


def set_control(profile: Profile, control: Control, value: int | str) -> None:
    if profile.backend == "fake":
        return
    _amixer(profile, "-q", "cset", f"name={control.name}", str(value))


def apply_controls(profile: Profile, stream: Stream) -> None:
    """Set every control the profile gives a value for."""
    for control in stream.controls.values():
        if control.set is not None:
            set_control(profile, control, control.set)


def get_volume(profile: Profile) -> float | None:
    """The playback volume control as 0-1, or None if the profile has none."""
    c = profile.playback.controls.get("volume")
    if c is None or c.min is None or c.max is None:
        return None
    if profile.backend == "fake":
        return 1.0
    values = get_control(profile, c)
    return (int(values[0]) - c.min) / (c.max - c.min) if values else None


def set_volume(profile: Profile, fraction: float) -> None:
    c = profile.playback.controls.get("volume")
    if c is None or c.min is None or c.max is None:
        return
    fraction = min(1.0, max(0.0, fraction))
    set_control(profile, c, round(c.min + fraction * (c.max - c.min)))


# The fake device's loopback

class _FakeLoop:
    """What the fake device has played, so its capture can hear it again
    (a fake profile with loopback = true): mono samples from a start time."""

    def __init__(self):
        self.t0: float | None = None
        self.chunks: list[np.ndarray] = []
        self.played = np.zeros(0, dtype=np.float32)
        self.owner: object | None = None
        self.lock = threading.Lock()  # the playback thread adds while the capture thread hears

    def start(self, t0: float) -> object:
        """A new playback: earlier ones (a thread still finishing) no longer count. Returns its token."""
        with self.lock:
            self.t0, self.chunks, self.played = t0, [], np.zeros(0, dtype=np.float32)
            self.owner = object()
            return self.owner

    def add(self, x: np.ndarray, owner: object | None = None) -> None:
        x = (x if x.ndim == 1 else x.mean(axis=1)).astype(np.float32)
        with self.lock:
            if owner is None or owner is self.owner:  # not a playback that has been superseded
                self.chunks.append(x)

    def heard(self, t_first: float, frames: int, rate: int) -> np.ndarray:
        """The played sound at times t_first + i/rate (zero where nothing played)."""
        out = np.zeros(frames, dtype=np.float32)
        with self.lock:
            if self.t0 is None:
                return out
            if self.chunks:
                self.played = np.concatenate([self.played] + self.chunks)
                self.chunks = []
            played, t0 = self.played, self.t0
        k0 = int(round((t_first - t0) * rate))
        lo, hi = max(k0, 0), min(k0 + frames, len(played))
        if hi > lo:
            out[lo - k0:hi - k0] = played[lo:hi]
        return out


_FAKE_LOOPS: dict[str, _FakeLoop] = {}


def _fake_loop(profile: Profile) -> _FakeLoop | None:
    if profile.backend != "fake" or not profile.loopback:
        return None
    return _FAKE_LOOPS.setdefault(profile.name, _FakeLoop())


# Capture

class _FakeSource:
    """Room noise at about -72 dBFS, with a decaying 220 Hz note every 4 s,
    paced in real time; with loopback, the fake playback comes back too. Each
    channel after the first is 6 dB quieter than the one before (so meters differ)."""

    def __init__(self, stream: Stream, dtype: np.dtype, loop: _FakeLoop | None = None,
                 loop_db: float = -31.0, loop_delay_s: float = 0.009):
        self.stream = stream
        self.dtype = dtype
        self.rng = np.random.default_rng(0)
        self.t0 = time.monotonic()
        self.n = 0
        self.loop, self.loop_gain, self.loop_delay = loop, 10 ** (loop_db / 20), loop_delay_s

    def read(self, frames: int) -> bytes:
        rate, ch = self.stream.rate, self.stream.channels
        due = self.t0 + (self.n + frames) / rate
        time.sleep(max(0.0, due - time.monotonic()))
        t = (self.n + np.arange(frames)) / rate
        since = np.mod(t, 4.0)
        note = 0.2 * np.exp(-since * 1.5) * np.sin(2 * np.pi * 220 * t)
        x = note + self.rng.normal(0, 10 ** (-72 / 20), frames)
        if self.loop is not None:
            x = x + self.loop_gain * self.loop.heard(self.t0 + self.n / rate - self.loop_delay, frames, rate)
        self.n += frames
        mono = dsp.from_float(x.astype(np.float32), self.dtype)
        gains = 10 ** (-6.0 * np.arange(ch) / 20)
        return np.round(mono[:, None] * gains).astype(self.dtype).tobytes()


class Capture:
    """Read blocks from the capture device.

    Blocks are int arrays shaped (frames, channels), in the profile's sample
    format. By default they hold the kept channels (`keep`, else the
    profile's) with DC removed; raw=True gives every channel as the device
    sends it. Each block also leaves every device input's peak in
    `input_peaks` (input N on channel N - 1), kept or not: the pages' meters.
    """

    def __init__(self, profile: Profile, block_s: float = 0.1, raw: bool = False, keep: tuple[int, ...] | None = None):
        self.profile = profile
        self.stream = profile.capture if keep is None else replace(profile.capture, keep_channels=tuple(keep))
        if any(c < 0 or c >= self.stream.channels for c in self.stream.keep_channels):
            raise ValueError(f"keep: channels 0-{self.stream.channels - 1}")
        self.n_inputs = self.stream.inputs or self.stream.channels
        self.input_peaks: np.ndarray | None = None
        self.dtype = dsp.dtype_for(self.stream.format)
        self.block_frames = int(self.stream.rate * block_s)
        self.raw = raw
        self.dc = dsp.DCRemover(self.stream.rate) if self.stream.remove_dc and not raw else None
        self.proc: subprocess.Popen | None = None
        self.stderr_lines: list[str] = []
        self.frames_read = 0
        self._fake: _FakeSource | None = None

    @property
    def rate(self) -> int:
        return self.stream.rate

    @property
    def channels(self) -> int:
        return self.stream.channels if self.raw else len(self.stream.keep_channels)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def start(self) -> None:
        if self.profile.backend == "fake":
            f = self.profile.fake
            self._fake = _FakeSource(self.stream, self.dtype, _fake_loop(self.profile),
                                     float(f.get("loopback_db", -31.0)), float(f.get("loopback_delay_ms", 9.0)) / 1000)
            return
        require(self.profile)
        apply_controls(self.profile, self.stream)
        s = self.stream
        self.proc = subprocess.Popen(
            # No -q: arecord's overrun warnings are how xruns are counted.
            ["arecord", "-D", s.device, "-f", s.format, "-r", str(s.rate),
             "-c", str(s.channels), "-t", "raw", f"--buffer-time={s.buffer_ms * 1000}",
             # A large buffer for safety, but data every 100 ms, not every buffer/4.
             "--period-time=100000"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        for line in self.proc.stderr:
            self.stderr_lines.append(line.decode(errors="replace").rstrip())

    def read(self) -> np.ndarray | None:
        """The next block, or None once the device has stopped sending."""
        nbytes = self.block_frames * self.stream.channels * self.dtype.itemsize
        if self._fake is not None:
            data = self._fake.read(self.block_frames)
        else:
            data = self.proc.stdout.read(nbytes)
        if len(data) < nbytes:
            return None
        frames = np.frombuffer(data, dtype=self.dtype).reshape(-1, self.stream.channels)
        self.frames_read += len(frames)
        self.input_peaks = np.max(np.abs(frames[:, :self.n_inputs].astype(np.int64)), axis=0)
        if self.raw:
            return frames
        kept = frames[:, list(self.stream.keep_channels)]
        if self.dc is not None:
            fs = dsp.full_scale(self.dtype)
            kept = np.clip(np.round(self.dc.process(kept)), -fs, fs - 1).astype(self.dtype)
        return kept

    def xruns(self) -> list[str]:
        return [line for line in self.stderr_lines if "overrun" in line.lower()]

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


# Playback

class Playback:
    """Write audio to the playback device.

    write() takes float32 audio in [-1, 1), shaped (frames,) or
    (frames, channels). Mono is copied to every channel of the device.
    """

    def __init__(self, profile: Profile):
        self.profile = profile
        self.stream = profile.playback
        self.dtype = dsp.dtype_for(self.stream.format)
        self.proc: subprocess.Popen | None = None
        self.stderr_lines: list[str] = []
        self._fake_t0: float | None = None
        self._fake_n = 0
        self._loop: _FakeLoop | None = None

    @property
    def rate(self) -> int:
        return self.stream.rate

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def start(self) -> None:
        if self.profile.backend == "fake":
            self._fake_t0 = time.monotonic()
            self._loop = _fake_loop(self.profile)
            if self._loop is not None:
                self._loop_owner = self._loop.start(self._fake_t0)
            return
        require(self.profile)
        apply_controls(self.profile, self.stream)
        s = self.stream
        self.proc = subprocess.Popen(
            ["aplay", "-q", "-D", s.device, "-f", s.format, "-r", str(s.rate),
             "-c", str(s.channels), "-t", "raw", f"--buffer-time={s.buffer_ms * 1000}"],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        for line in self.proc.stderr:
            self.stderr_lines.append(line.decode(errors="replace").rstrip())

    def write(self, x: np.ndarray) -> bool:
        """Queue audio; False if the device has gone away. Mono goes to every channel; so does a
        take with another number of channels than the device's (mixed to mono first: a take of
        two inputs played on the 18i8's eight outputs)."""
        if x.ndim == 2 and x.shape[1] != self.stream.channels:
            x = x.mean(axis=1)
        if x.ndim == 1:
            x = np.repeat(x[:, None], self.stream.channels, axis=1)
        if self._fake_t0 is not None:
            if self._loop is not None:
                self._loop.add(x, self._loop_owner)
            self._fake_n += len(x)
            due = self._fake_t0 + self._fake_n / self.stream.rate
            time.sleep(max(0.0, due - time.monotonic()))
            return True
        try:
            self.proc.stdin.write(dsp.from_float(x, self.dtype).tobytes())
            return True
        except (BrokenPipeError, ValueError):
            return False

    def close(self) -> None:
        """Let queued audio finish, then release the device."""
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except BrokenPipeError:
                pass
            self.proc.wait()

    def abort(self) -> None:
        """Stop now, dropping queued audio."""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
