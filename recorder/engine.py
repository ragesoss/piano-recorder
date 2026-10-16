"""Recording and playback on the configured devices, one at a time."""

import collections
import json
import threading
import time
import wave
from dataclasses import dataclass, field

import numpy as np

from . import audio, dsp, peers, playalong
from .backing import Backings
from .config import Config
from .takes import Takes

MIN_FREE_MB = 200
TAIL_S = 2.0  # a play-along take keeps recording this long after the backing ends
OFFSET_RANGE_DB = (-30.0, 12.0)  # the speakers' level offset; above 0 dB through `dsp.StreamLimiter` (2026-10-15:
# a guitar over a backing at 0 dB could not hear the dobro over its own playing)


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
    # a play-along take: the backing, prepared for the speakers, and how far it has played
    backing_id: str | None = None
    backing_title: str = ""
    backing: np.ndarray | None = None
    backing_rate: int = 48000
    backing_gain_db: float = 0.0
    backing_pos: float = 0.0
    count_in_s: float = 0.0  # the clicks before the backing (the backing starts this far into what is played)
    tempo: dict | None = None
    guide: str = "backing"  # what plays: "backing" (a file) or "click" (generated from a tempo)
    beat0_s: float | None = None  # a click: where beat 0 (the music's first note) is in what is played
    click: dict | None = None  # a click: how it was made (playalong.click_track), to rebuild it
    meta: dict = field(default_factory=dict)  # more for the take's sidecar (an exercise app's)
    backing_done: threading.Event = field(default_factory=threading.Event)
    played: list = field(default_factory=list)  # the blocks as written, for the alignment


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
    def __init__(self, cfg: Config, takes: Takes, backings: Backings | None = None, name: str = "the practice recorder"):
        self.cfg = cfg
        self.name = name  # how a peer is told who is busy
        self.takes = takes
        self.backings = backings or Backings(cfg.backing_dir)
        self.lock = threading.Lock()
        self.rec: _Recording | None = None
        self.analyzing: str | None = None  # a play-along take being lined up with its backing
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
            state = ("recording" if rec else "preparing" if pl and pl.preparing else "playing" if pl
                     else "analyzing" if self.analyzing else "idle")
            return {
                "state": state,
                "recording": rec and {
                    "take": rec.take_id,
                    "seconds": round(rec.frames / rec.rate, 1),
                    "meter_dbfs": round(max(rec.recent, default=-240.0), 1),
                    "peak_dbfs": round(rec.peak_dbfs, 1),
                    "clipped": rec.clipped,
                    "backing": rec.backing is not None and {
                        "kind": rec.guide,
                        "id": rec.backing_id,
                        "title": rec.backing_title,
                        "position": round(rec.backing_pos, 2),
                        "seconds": round(len(rec.backing) / rec.backing_rate, 1),
                        "count_in_s": round(rec.count_in_s, 2),
                        "beat0_s": rec.beat0_s,
                        "tempo": rec.tempo,
                    },
                },
                "analyzing": self.analyzing,
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

    def start_recording(self, backing_id: str | None = None, count_in: bool = True, meta: dict | None = None) -> str:
        """Record; with a backing, play it over the speakers meanwhile (a play-along take), after a
        count-in when the backing's tempo is known and `count_in` is on."""
        with self.lock:
            if self.rec:
                raise Busy("already recording")
            if self.takes.free_mb() < MIN_FREE_MB:
                raise LowSpace(f"less than {MIN_FREE_MB} MB free")
            audio.require(self.cfg.capture)
            if backing_id is not None:
                audio.require(self.cfg.playback)
        prepared = self._prepare_backing(backing_id) if backing_id is not None else None  # raises BackingNotFound
        tempo = self.backings.get(backing_id).tempo if backing_id is not None else None
        lead = 0.0
        if prepared is not None and count_in and tempo:
            title, y, rate, gain_db = prepared
            y, lead = playalong.with_count_in(y, rate, tempo)
            prepared = (title, y, rate, gain_db)
        rec = _Recording(take_id="", rate=self.cfg.capture.capture.rate, meta=dict(meta or {}))
        if prepared is not None:
            rec.backing_id = backing_id
            rec.backing_title, rec.backing, rec.backing_rate, rec.backing_gain_db = prepared
            rec.count_in_s, rec.tempo = lead, (tempo if lead else None)
        return self._start(rec)

    def start_click(self, tempo: dict, beats: float, count_in: bool = True, through: bool = True,
                    title: str = "Click", meta: dict | None = None) -> str:
        """Record over a click in `tempo` ({"bpm", "pickup_beats", "beats_per_bar"}): a bar's
        count-in (with `count_in`), then every beat through `beats` beats from the first note
        (with `through`). The take's sidecar gets `meta` and, once lined up, where beat 0 is."""
        with self.lock:
            if self.rec:
                raise Busy("already recording")
            if self.takes.free_mb() < MIN_FREE_MB:
                raise LowSpace(f"less than {MIN_FREE_MB} MB free")
            audio.require(self.cfg.capture)
            audio.require(self.cfg.playback)
        rate = self.cfg.playback.playback.rate
        click = {"beats": float(beats), "count_in_bars": 1 if count_in else 0, "through": bool(through)}
        y, beat0 = playalong.click_track(tempo, rate, **click)
        rec = _Recording(take_id="", rate=self.cfg.capture.capture.rate, backing_title=title, backing=y,
                         backing_rate=rate, tempo=dict(tempo), guide="click", beat0_s=beat0, click=click,
                         count_in_s=beat0, meta=dict(meta or {}))
        return self._start(rec)

    def _start(self, rec: _Recording) -> str:
        self._claim()  # a peer's music would end up in the take
        self.stop_playback()
        with self.lock:
            if self.rec:
                raise Busy("already recording")
            self.error = None
            rec.take_id = self.takes.new_id()
            rec.thread = threading.Thread(target=self._record, args=(rec,), daemon=True)
            self.rec = rec
            rec.thread.start()
            return rec.take_id

    def _prepare_backing(self, backing_id: str) -> tuple[str, np.ndarray, int, float]:
        """A backing at the playback rate and the takes' loudness (as takes play)."""
        title = self.backings.get(backing_id).title
        x, rate = self.backings.load(backing_id)
        want = self.cfg.playback.playback.rate
        if rate != want:
            x = np.interp(np.arange(int(len(x) * want / rate)) / want, np.arange(len(x)) / rate, x).astype(np.float32)
            rate = want
        loud = self.cfg.loudness
        y, gain_db = dsp.prepare_for_playback(x, rate, loud.target_dbfs, loud.limit_dbfs, loud.max_gain_db)
        return title, y.astype(np.float32), rate, gain_db

    def _play_backing(self, rec: _Recording) -> None:
        """Feed the backing to the speakers during a play-along take, keeping what was written."""
        pb = audio.Playback(self.cfg.playback)
        y, rate = rec.backing, rec.backing_rate
        try:
            pb.start()
            pos, block = 0, rate // 10
            lim = self._limiter(rate)
            while pos < len(y) and not rec.stop.is_set():
                chunk = lim.process(y[pos:pos + block] * np.float32(10 ** (self.level_offset_db / 20)))
                if not pb.write(chunk):
                    self.error = "the backing stopped: the playback device went away"
                    break
                rec.played.append(chunk)
                pos += len(chunk)
                with self.lock:
                    rec.backing_pos = pos / rate
        except Exception as e:
            self.error = f"the backing failed: {e}"
        finally:
            if rec.stop.is_set():
                pb.abort()
            else:
                pb.close()
            rec.backing_done.set()

    def _claim(self) -> None:
        try:
            peers.claim(self.cfg.peers)
        except peers.PeerBusy as e:
            raise Busy(str(e))

    def yield_speakers(self) -> None:
        """A peer wants the speakers: stop playing. A recording is kept."""
        if self.rec:
            raise Busy(f"{self.name} is recording")
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
        feeder, end_frame = None, None
        try:
            with wave.open(str(path), "wb") as w:
                w.setnchannels(cap.channels)
                w.setsampwidth(cap.dtype.itemsize)
                w.setframerate(cap.rate)
                while not rec.stop.is_set():
                    if feeder is not None and rec.backing_done.is_set():
                        if end_frame is None:
                            end_frame = rec.frames + int(TAIL_S * cap.rate)
                        elif rec.frames >= end_frame:
                            break
                    block = cap.read()
                    if block is None:
                        said = next((l for l in reversed(cap.stderr_lines) if l.strip()), "")
                        self.error = ((f"could not record: {said}" if said else "could not record: the capture device sent nothing")
                                      if rec.frames == 0 else
                                      "the recording stopped: the capture device stopped sending audio")
                        break
                    w.writeframes(block.tobytes())
                    if rec.backing is not None and feeder is None:
                        # the backing starts once the capture is surely running, so the take
                        # holds all of it (arecord can take longer to start than aplay)
                        feeder = threading.Thread(target=self._play_backing, args=(rec,), daemon=True)
                        feeder.start()
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
            rec.stop.set()  # also ends the backing, if it is still playing
            if feeder is not None:
                feeder.join(timeout=5)
            cap.stop()
            xr = cap.xruns()
            if xr and not self.error:
                self.error = f"{len(xr)} dropouts (xruns) during the recording"
            empty = rec.frames == 0  # it never started (the device busy or gone): no take
            if empty:
                self.takes.delete(rec.take_id)
            else:
                self.takes.finish(rec.take_id, rec.peak_dbfs)
                if rec.meta:
                    self.takes.update_meta(rec.take_id, rec.meta)
            with self.lock:
                self.rec = None
                if rec.backing is not None and not empty:
                    self.analyzing = rec.take_id
                self.takes_version += 1
        if rec.backing is not None and not empty:
            self._line_up(rec)

    def line_up_again(self, take_id: str) -> dict:
        """Line a play-along take up with its backing again (after a change to how it is done).
        The backing as played is rebuilt from the take's sidecar: the same file, prepared the same
        way, at the level offset of the time."""
        meta = self.takes.meta(take_id)
        if meta.get("guide") == "click" and meta.get("click") and meta.get("tempo"):
            with self.lock:
                if self.rec and self.rec.take_id == take_id:
                    raise Busy("that take is being recorded")
                self.analyzing = take_id
            rate = self.cfg.playback.playback.rate
            y, beat0 = playalong.click_track(meta["tempo"], rate, **meta["click"])
            rec = _Recording(take_id=take_id, rate=rate, backing_title=meta.get("backing_title", "Click"), backing=y,
                             backing_rate=rate, tempo=meta["tempo"], guide="click", beat0_s=beat0,
                             click=meta["click"], count_in_s=beat0)
            rec.played = [self._limiter(rate).process(y * np.float32(10 ** (float(meta.get("level_offset_db", 0.0)) / 20)))]
            self._line_up(rec, level_offset_db=float(meta.get("level_offset_db", 0.0)))
            return self.takes.meta(take_id)
        if not meta.get("backing"):
            raise ValueError("not a play-along take")
        with self.lock:
            if self.rec and self.rec.take_id == take_id:
                raise Busy("that take is being recorded")
            self.analyzing = take_id
        title, y, rate, gain_db = self._prepare_backing(meta["backing"])  # raises BackingNotFound
        lead, tempo = 0.0, meta.get("tempo")
        if float(meta.get("count_in_s", 0.0)) > 0 and tempo:
            y, lead = playalong.with_count_in(y, rate, tempo)
        rec = _Recording(take_id=take_id, rate=rate, backing_id=meta["backing"], backing_title=title,
                         backing=y, backing_rate=rate, backing_gain_db=gain_db, count_in_s=lead,
                         tempo=tempo if lead else None)
        rec.played = [self._limiter(rate).process(y * np.float32(10 ** (float(meta.get("level_offset_db", 0.0)) / 20)))]
        self._line_up(rec, level_offset_db=float(meta.get("level_offset_db", 0.0)))
        return self.takes.meta(take_id)

    def _line_up(self, rec: _Recording, level_offset_db: float | None = None) -> None:
        """Find the backing in a play-along take, to the sample, and save the take without it
        (a click: only find it)."""
        meta = {"guide": rec.guide, "backing": rec.backing_id, "backing_title": rec.backing_title,
                "backing_gain_db": round(rec.backing_gain_db, 1),
                "count_in_s": round(rec.count_in_s, 4), "tempo": rec.tempo,
                "level_offset_db": self.level_offset_db if level_offset_db is None else level_offset_db}
        if rec.guide == "click":
            meta["click"] = rec.click
        try:
            played = np.concatenate(rec.played) if rec.played else np.zeros(0, dtype=np.float32)
            channels, rate = self.takes.load(rec.take_id)
            take = channels if channels.ndim == 1 else channels.mean(axis=1)
            if len(played) and len(take):
                if rec.guide == "click":  # found near the start, in every channel; it stays in the take
                    al, clean = playalong.find_click(channels, played, rate, 60.0 / float(rec.tempo["bpm"])), None
                else:
                    al, clean = playalong.align_and_clean(take, played, rate)
                meta["playalong"] = al.as_dict()
                meta["playalong"]["loopback"] = self.cfg.capture.loopback
                # where the backing file's own sample 0 is in the take (after any count-in)
                start = al.lag + int(round(rec.count_in_s * rate))
                meta["playalong"]["backing_starts_samples"] = start
                meta["playalong"]["backing_starts_ms"] = round(1000.0 * start / rate, 2)
                if rec.guide == "click":
                    # where the music's beat 0 (its first note) is in the take: what an exercise's
                    # takes.csv calls beat0_s
                    b0 = al.lag + int(round(rec.beat0_s * rate))
                    meta["beat0_s"] = round(b0 / rate, 4) if al.found else None
                if al.found and clean is not None:
                    self.takes.write_clean(rec.take_id, clean, rate)
                else:
                    self.takes.clean_path(rec.take_id).unlink(missing_ok=True)
        except Exception as e:
            self.error = f"could not line the take up with its backing: {e}"
        finally:
            self.takes.update_meta(rec.take_id, meta)
            with self.lock:
                self.analyzing = None
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
            lim = self._limiter(rate)
            while pos < len(y) and not pl.stop.is_set():
                g = np.float32(10 ** (self.level_offset_db / 20))
                if not pb.write(lim.process(y[pos:pos + block] * g)):
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

    def _limiter(self, rate: int) -> dsp.StreamLimiter:
        """What playback goes through last: the offset held under the takes' peak ceiling."""
        return dsp.StreamLimiter(rate, self.cfg.loudness.limit_dbfs)

    def set_level_offset(self, offset_db: float) -> float:
        lo, hi = OFFSET_RANGE_DB
        self.level_offset_db = round(min(hi, max(lo, float(offset_db))), 1)
        self._settings_path.write_text(json.dumps({"level_offset_db": self.level_offset_db}))
        return self.level_offset_db

    def touched(self) -> None:
        with self.lock:
            self.takes_version += 1
