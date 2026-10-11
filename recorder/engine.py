"""Recording and playback on the configured devices, one at a time."""

import collections
import json
import threading
import time
import wave
from dataclasses import dataclass, field

import numpy as np

from . import audio, dsp, peers
from .config import Config
from .takes import Takes

MIN_FREE_MB = 200
OFFSET_RANGE_DB = (-30.0, 0.0)


class Busy(Exception):
    pass


class LowSpace(Exception):
    pass


@dataclass
class _Recording:
    take_id: str
    stop: threading.Event = field(default_factory=threading.Event)
    frames: int = 0
    rate: int = 48000
    peak_dbfs: float = -240.0
    recent: collections.deque = field(default_factory=lambda: collections.deque(maxlen=3))
    clipped: bool = False
    thread: threading.Thread | None = None


@dataclass
class _Playing:
    take_id: str
    stop: threading.Event = field(default_factory=threading.Event)
    position: float = 0.0
    seconds: float = 0.0
    gain_db: float = 0.0
    preparing: bool = True
    thread: threading.Thread | None = None


class Engine:
    def __init__(self, cfg: Config, takes: Takes):
        self.cfg = cfg
        self.takes = takes
        self.lock = threading.Lock()
        self.rec: _Recording | None = None
        self.play_: _Playing | None = None
        self.error: str | None = None
        self.takes_version = 0
        self._prepared: tuple | None = None  # (take_id, mtime, audio, rate, gain_db)
        self._settings_path = takes.dir / "settings.json"
        try:
            self.level_offset_db = float(json.loads(self._settings_path.read_text())["level_offset_db"])
        except (OSError, ValueError, KeyError):
            self.level_offset_db = 0.0

    # Status

    def status(self) -> dict:
        with self.lock:
            rec, pl = self.rec, self.play_
            state = "recording" if rec else ("preparing" if pl and pl.preparing else ("playing" if pl else "idle"))
            return {
                "state": state,
                "recording": rec and {
                    "take": rec.take_id,
                    "seconds": round(rec.frames / rec.rate, 1),
                    "meter_dbfs": round(max(rec.recent, default=-240.0), 1),
                    "peak_dbfs": round(rec.peak_dbfs, 1),
                    "clipped": rec.clipped,
                },
                "playing": pl and {
                    "take": pl.take_id,
                    "position": round(pl.position, 2),
                    "seconds": round(pl.seconds, 2),
                    "gain_db": round(pl.gain_db, 1),
                },
                "devices": {
                    "capture": audio.present(self.cfg.capture),
                    "playback": audio.present(self.cfg.playback),
                    "capture_name": self.cfg.capture.description or self.cfg.capture.name,
                    "playback_name": self.cfg.playback.description or self.cfg.playback.name,
                },
                "free_mb": round(self.takes.free_mb()),
                "level_offset_db": self.level_offset_db,
                "error": self.error,
                "takes_version": self.takes_version,
            }

    # Recording

    def start_recording(self) -> str:
        with self.lock:
            if self.rec:
                raise Busy("already recording")
            if self.takes.free_mb() < MIN_FREE_MB:
                raise LowSpace(f"less than {MIN_FREE_MB} MB free")
            audio.require(self.cfg.capture)
        self._claim()  # a peer's music would end up in the take
        self.stop_playback()
        with self.lock:
            self.error = None
            rec = _Recording(take_id=self.takes.new_id(), rate=self.cfg.capture.capture.rate)
            rec.thread = threading.Thread(target=self._record, args=(rec,), daemon=True)
            self.rec = rec
            rec.thread.start()
            return rec.take_id

    def _claim(self) -> None:
        try:
            peers.claim(self.cfg.peers)
        except peers.PeerBusy as e:
            raise Busy(str(e))

    def yield_speakers(self) -> None:
        """A peer wants the speakers: stop playing. A recording is kept."""
        if self.rec:
            raise Busy("the practice recorder is recording")
        self.stop_playback()

    def stop_recording(self) -> str | None:
        rec = self.rec
        if rec is None:
            return None
        rec.stop.set()
        rec.thread.join(timeout=5)
        return rec.take_id

    def _record(self, rec: _Recording) -> None:
        cap = audio.Capture(self.cfg.capture)
        path = self.takes.dir / f"{rec.take_id}.wav"
        try:
            cap.start()
        except Exception as e:  # device gone between the check and here
            with self.lock:
                self.error = f"could not start recording: {e}"
                self.rec = None
            return
        fs = dsp.full_scale(cap.dtype)
        last_space_check = time.monotonic()
        try:
            with wave.open(str(path), "wb") as w:
                w.setnchannels(cap.channels)
                w.setsampwidth(cap.dtype.itemsize)
                w.setframerate(cap.rate)
                while not rec.stop.is_set():
                    block = cap.read()
                    if block is None:
                        self.error = "the recording stopped: the capture device stopped sending audio"
                        break
                    w.writeframes(block.tobytes())
                    peak = int(np.max(np.abs(block.astype(np.int32))))
                    level = dsp.db(peak / fs)
                    with self.lock:
                        rec.frames += len(block)
                        rec.recent.append(level)
                        rec.peak_dbfs = max(rec.peak_dbfs, level)
                        rec.clipped = rec.clipped or peak >= fs - 1
                    if time.monotonic() - last_space_check > 10:
                        last_space_check = time.monotonic()
                        if self.takes.free_mb() < MIN_FREE_MB:
                            self.error = f"the recording stopped: less than {MIN_FREE_MB} MB free"
                            break
        finally:
            cap.stop()
            xr = cap.xruns()
            if xr and not self.error:
                self.error = f"{len(xr)} dropouts (xruns) during the recording"
            self.takes.finish(rec.take_id, rec.peak_dbfs)
            with self.lock:
                self.rec = None
                self.takes_version += 1

    # Playback

    def play(self, take_id: str, start_s: float = 0.0) -> None:
        self.takes.path(take_id)  # raises TakeNotFound
        with self.lock:
            if self.rec:
                raise Busy("recording: stop it before playing")
            audio.require(self.cfg.playback)
        self._claim()
        self.stop_playback()
        with self.lock:
            self.error = None
            pl = _Playing(take_id=take_id, position=max(0.0, start_s))
            pl.thread = threading.Thread(target=self._play, args=(pl,), daemon=True)
            self.play_ = pl
            pl.thread.start()

    def stop_playback(self) -> None:
        pl = self.play_
        if pl is None:
            return
        pl.stop.set()
        pl.thread.join(timeout=5)

    def _prepare(self, take_id: str) -> tuple[np.ndarray, int, float]:
        mtime = self.takes.path(take_id).stat().st_mtime
        if self._prepared and self._prepared[:2] == (take_id, mtime):
            return self._prepared[2:]
        x, rate = self.takes.load(take_id)
        want = self.cfg.playback.playback.rate
        if rate != want:  # a capture and playback device at different rates
            t_new = np.arange(int(len(x) * want / rate)) / want
            t_old = np.arange(len(x)) / rate
            x = (np.interp(t_new, t_old, x) if x.ndim == 1 else
                 np.stack([np.interp(t_new, t_old, x[:, c]) for c in range(x.shape[1])], axis=1)).astype(np.float32)
            rate = want
        loud = self.cfg.loudness
        y, gain_db = dsp.prepare_for_playback(x, rate, loud.target_dbfs, loud.limit_dbfs, loud.max_gain_db)
        self._prepared = (take_id, mtime, y, rate, gain_db)
        return y, rate, gain_db

    def _play(self, pl: _Playing) -> None:
        try:
            y, rate, gain_db = self._prepare(pl.take_id)
        except Exception as e:
            with self.lock:
                self.error = f"could not prepare {pl.take_id}: {e}"
                self.play_ = None
            return
        with self.lock:
            pl.seconds = len(y) / rate
            pl.gain_db = gain_db
            pl.preparing = False
        pb = audio.Playback(self.cfg.playback)
        try:
            pb.start()
            pos = min(int(pl.position * rate), len(y))
            block = rate // 10
            while pos < len(y) and not pl.stop.is_set():
                g = np.float32(10 ** (self.level_offset_db / 20))
                if not pb.write(y[pos:pos + block] * g):
                    self.error = "playback stopped: the playback device went away"
                    break
                pos += block
                with self.lock:
                    pl.position = min(pos, len(y)) / rate
        except Exception as e:
            self.error = f"playback failed: {e}"
        finally:
            if pl.stop.is_set():
                pb.abort()
            else:
                pb.close()
            with self.lock:
                if self.play_ is pl:
                    self.play_ = None

    # Takes and settings

    def delete(self, take_id: str) -> None:
        if self.play_ and self.play_.take_id == take_id:
            self.stop_playback()
        if self.rec and self.rec.take_id == take_id:
            raise Busy("that take is being recorded")
        self.takes.delete(take_id)
        with self.lock:
            self.takes_version += 1

    def set_level_offset(self, offset_db: float) -> float:
        lo, hi = OFFSET_RANGE_DB
        self.level_offset_db = round(min(hi, max(lo, float(offset_db))), 1)
        self._settings_path.write_text(json.dumps({"level_offset_db": self.level_offset_db}))
        return self.level_offset_db

    def touched(self) -> None:
        with self.lock:
            self.takes_version += 1
