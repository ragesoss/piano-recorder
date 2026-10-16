"""Hearing chords as they are played, and checking them.

Every 100 ms the inputs a page has chosen, mixed to one signal, are heard over the last `WINDOW_S`
(`livechords`: the band's own listener, copied from dobro-transcription), and the chord shown is
smoothed over time: a forward HMM over the 36 chords, staying put with probability `STAY` from one
frame to the next. Nothing is named while the level is within `QUIET_DB` of the quietest of the last
10 s (or of `QUIET_CEIL_DBFS`, if nothing that quiet has been heard yet).

A check: a chord is asked for; the next attack (a rise of `ATTACK_DB` within 0.3 s, over the quiet)
starts it, and the chord is judged over `CHECK_SPAN_S` after the attack, once the strike has passed.
The clip (every kept channel, from just before the attack) and what was asked, heard and scored are
kept in the config's takes_dir, for study on the desktop (`tools/pull-checks`). A label is the same
from the Listen view: the last `LABEL_S` kept with the chord the player says it was.

It listens only while a page asks (`listen()`: `LISTEN_S` more each time), and lets the inputs go for
`YIELD_S` when another program asks (`yield_inputs()`: a recorder about to record).
"""

from __future__ import annotations

import collections
import json
import math
import re
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from recorder import audio, dsp
from recorder.config import Config

from . import livechords as L

LISTEN_S = 5.0  # a page's ask keeps the inputs open this long
YIELD_S = 10.0  # after letting the inputs go to a peer, or failing to open them: wait this long
BLOCK_S = 0.1
WINDOW_S = 0.4  # the chord shown is of this much audio, up to now
KEEP_S = 6.0  # audio kept, for a check's clip and a label
STAY = 0.85  # the smoothing: the chord shown stays with this probability from one frame to the next
BETA = 12.0  # how sharply a frame's scores pick among the chords
QUIET_DB = 12.0  # a chord is heard while the level is this far over the quietest of the last 10 s...
QUIET_CEIL_DBFS = -50.0  # ...or over this, if nothing that quiet has been heard (playing from the start;
# the room through the condenser was -57.5 dBFS rms, docs/components.md T4)
ATTACK_DB = 9.0  # a check starts at a rise this big within 0.3 s
CHECK_SPAN_S = (0.1, 1.1)  # a check judges the chord over this span after its attack
CLIP_S = (0.3, 1.6)  # a check's clip: this long before the attack, and after it
LABEL_S = 2.0
MIN_FFT = 16384  # at 48 kHz, bins of 2.9 Hz: the bass's semitones apart (2.4 Hz at E1)

NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
SUFFIX = {"maj": "", "min": "m", "7": "7"}
QUALITY_WORDS = {"maj": "major", "min": "minor", "7": "seven"}
NAME_RE = re.compile(r"^([A-G])([#b]?)(m|7)?$")
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}(-\d+)?$")


def chord_name(c: tuple[int, str]) -> str:
    return NAMES[c[0]] + SUFFIX[c[1]]


def parse_chord(name: str) -> tuple[int, str]:
    """"D", "F#m", "Bb7" -> (pitch class, quality). Raises ValueError."""
    m = NAME_RE.match(name or "")
    if not m:
        raise ValueError(f"not a chord: {name!r}")
    pc = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[m.group(1)] + {"#": 1, "b": -1, "": 0}[m.group(2)]
    return pc % 12, {"m": "min", "7": "7", None: "maj"}[m.group(3)]


def detector() -> str:
    """Which listener judged: the dobro-transcription revision the copy came from."""
    head = (Path(L.__file__).read_text().splitlines() or [""])[0]
    m = re.search(r"\(([0-9a-f]{6,})\)", head)
    return f"livechords@{m.group(1)}" if m else "livechords"


@dataclass
class Check:
    target: tuple[int, str]
    state: str = "waiting"  # waiting (for the attack), hearing, done
    attack: int | None = None  # the attack's position in the stream (samples)
    heard: tuple[int, str] | None = None
    right: bool | None = None
    scores: list | None = None
    id: str | None = None  # once its clip is kept (when the clip's end has been heard)
    meta: dict | None = None  # what to keep with the clip


class Listener:
    def __init__(self, cfg: Config, directory: Path, source=None):
        self.cfg = cfg
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.source = source  # None: the capture device; else a factory of a Capture-like source
        self.rate = cfg.capture.capture.rate
        self.inputs = list(cfg.inputs)  # in the capture's kept order
        room = [c for c, i in enumerate(self.inputs) if not i.for_kind]
        self.mix = room or list(range(len(self.inputs)))  # channels heard (indices into the kept ones)
        self.instrument = "piano"
        self.lock = threading.Lock()
        self.until = 0.0
        self.wait_until = 0.0
        self.stop_ev = threading.Event()
        self.thread: threading.Thread | None = None
        self.error: str | None = None
        self._reset()

    def _reset(self) -> None:
        self.K = int(KEEP_S * self.rate)
        self.buf = np.zeros(self.K, dtype=np.float32)  # the mix, a ring: sample q at buf[q % K]
        self.raw: collections.deque = collections.deque(maxlen=int(KEEP_S / BLOCK_S) + 2)  # (start, block)
        self.pos = 0
        self.levels: collections.deque = collections.deque(maxlen=int(10 / BLOCK_S))
        self.post = np.full(len(L.CHORDS), 1.0 / len(L.CHORDS))
        self.now: dict = {"chord": None, "confidence": 0.0, "top": [], "level_dbfs": None, "quiet_dbfs": None}
        self.history: collections.deque = collections.deque(maxlen=12)
        self.check: Check | None = None
        self.dtype = None

    # --- asking, and letting go

    def listen(self, inputs: list[int] | None = None, instrument: str | None = None) -> None:
        """Keep listening for LISTEN_S more (start if need be), to these inputs (numbers) if given."""
        with self.lock:
            if inputs is not None:
                chans = [c for c, i in enumerate(self.inputs) if i.number in set(inputs)]
                if chans:
                    self.mix = chans
            if instrument:
                self.instrument = instrument
            if time.monotonic() < self.wait_until:
                return
            self.until = time.monotonic() + LISTEN_S
            if self.thread is not None:
                return
            if self.source is None and not audio.present(self.cfg.capture):
                self.error = "the mixer is not connected"
                return
            self.stop_ev = threading.Event()
            self.thread = threading.Thread(target=self._run, args=(self.stop_ev,), daemon=True)
            self.thread.start()

    def yield_inputs(self, wait: float = YIELD_S) -> None:
        """Another program wants the inputs (a recorder about to record): stop, and wait a while."""
        with self.lock:
            self.wait_until = time.monotonic() + wait
            th, ev = self.thread, self.stop_ev
        ev.set()
        if th is not None and th is not threading.current_thread():
            th.join(timeout=5)

    def stop(self) -> None:
        self.yield_inputs(wait=0.0)

    def _run(self, stop: threading.Event) -> None:
        cap = self.source() if self.source is not None else audio.Capture(self.cfg.capture)
        try:
            cap.start()
            fs = dsp.full_scale(cap.dtype)
            with self.lock:
                self.error = None
                self.dtype = cap.dtype
            while not stop.is_set() and time.monotonic() < self.until:
                block = cap.read()
                if block is None:  # another program has the inputs (a recording)
                    said = next((ln for ln in reversed(getattr(cap, "stderr_lines", [])) if ln.strip()), "")
                    with self.lock:
                        self.error = "the inputs are in use (a recording, or the exercise app listening)" + (
                            f": {said}" if said else "")
                        self.wait_until = time.monotonic() + YIELD_S
                    break
                self._hear(block, fs)
        except Exception as e:
            with self.lock:
                self.error = f"could not listen: {e}"
                self.wait_until = time.monotonic() + YIELD_S
        finally:
            cap.stop()
            ck = self.check
            if ck is not None and ck.state == "done" and ck.id is None:
                try:
                    self._keep(ck)
                except Exception:
                    pass
            with self.lock:
                self.thread = None
                self.now = {**self.now, "chord": None, "level_dbfs": None}

    # --- hearing

    def _between(self, p0: int, p1: int) -> np.ndarray:
        p0 = max(p0, self.pos - self.K, 0)
        return self.buf[np.arange(p0, max(p0, p1)) % self.K]

    def _hear(self, block: np.ndarray, fs: float) -> None:
        x = block.astype(np.float32) / fs
        mono = x[:, self.mix].mean(axis=1) if x.ndim > 1 else x
        n = len(mono)
        with self.lock:
            self.buf[np.arange(self.pos, self.pos + n) % self.K] = mono
            self.raw.append((self.pos, block.copy()))
            self.pos += n
            pos = self.pos
        win = self._between(pos - int(WINDOW_S * self.rate), pos)
        level = dsp.db(float(np.sqrt(np.mean(win ** 2))) if len(win) else 0.0)
        block_level = dsp.db(float(np.sqrt(np.mean(mono ** 2))) if n else 0.0)
        tr, bs = L.window_chroma(win, self.rate, 0.0, len(win) / self.rate, min_fft=MIN_FFT)
        s = L.chord_scores(tr, bs, 0.5)
        with self.lock:
            before = list(self.levels)[-3:]
            self.levels.append(block_level)
            quiet = min(min(self.levels), QUIET_CEIL_DBFS)
            loud = level > quiet + QUIET_DB
            if loud:
                prior = STAY * self.post + (1 - STAY) / len(self.post)
                like = np.exp(BETA * (s - s.max()))
                self.post = prior * like / max(float((prior * like).sum()), 1e-300)
            else:
                self.post = 0.7 * self.post + 0.3 / len(self.post)
            best = int(np.argmax(self.post))
            order = np.argsort(s)[::-1][:4]
            chord = chord_name(L.CHORDS[best]) if loud else None
            self.now = {"chord": chord, "quality": QUALITY_WORDS[L.CHORDS[best][1]] if loud else None,
                        "confidence": round(float(self.post[best]), 3) if loud else 0.0,
                        "top": [[chord_name(L.CHORDS[k]), round(float(s[k]), 3)] for k in order] if loud else [],
                        "level_dbfs": round(block_level, 1), "quiet_dbfs": round(quiet, 1)}
            if chord and self.now["confidence"] >= 0.5 and (not self.history or self.history[-1][1] != chord):
                self.history.append((datetime.now().isoformat(timespec="seconds"), chord))
            ck = self.check
            if ck is not None and ck.state == "waiting" and before and loud and \
                    block_level - min(before) >= ATTACK_DB:
                ck.state, ck.attack = "hearing", pos - n
            judge = ck is not None and ck.state == "hearing" and pos >= ck.attack + int(CHECK_SPAN_S[1] * self.rate)
        if judge:
            self._judge(ck)
        if ck is not None and ck.state == "done" and ck.id is None and pos >= ck.attack + int(CLIP_S[1] * self.rate):
            self._keep(ck)

    def _keep(self, ck: Check) -> None:
        """The check's clip, from just before its attack, kept with what was asked and heard."""
        sid = self._save("check", ck.attack - int(CLIP_S[0] * self.rate), ck.attack + int(CLIP_S[1] * self.rate),
                         ck.meta)
        with self.lock:
            ck.id = sid

    def _judge(self, ck: Check) -> None:
        a, b = (ck.attack + int(t * self.rate) for t in CHECK_SPAN_S)
        seg = self._between(a, b)
        tr, bs = L.window_chroma(seg, self.rate, 0.0, len(seg) / self.rate, min_fft=MIN_FFT)
        s = L.chord_scores(tr, bs, 0.5)
        order = np.argsort(s)[::-1]
        heard = L.CHORDS[int(order[0])]
        scores = [[chord_name(L.CHORDS[k]), round(float(s[k]), 3)] for k in order[:6]]
        with self.lock:
            ck.heard, ck.right, ck.scores, ck.state = heard, heard == ck.target, scores, "done"
            ck.meta = {"target": chord_name(ck.target), "heard": chord_name(heard), "right": heard == ck.target,
                       "scores": scores, "attack_s": CLIP_S[0], "judged_s": list(CHECK_SPAN_S)}

    # --- checks and labels

    def start_check(self, target: tuple[int, str]) -> None:
        with self.lock:
            self.check = Check(target=target)

    def cancel_check(self) -> None:
        with self.lock:
            self.check = None

    def label(self, chord: tuple[int, str] | None) -> str:
        """Keep the last LABEL_S with what the player says it was (None: no chord), and what was shown."""
        with self.lock:
            shown, pos = self.now.get("chord"), self.pos
        if pos < int(0.5 * self.rate):
            raise ValueError("nothing heard yet")
        said = chord_name(chord) if chord else None
        return self._save("label", pos - int(LABEL_S * self.rate), pos,
                          {"target": said, "heard": shown, "right": said == shown, "scores": self.now.get("top", [])})

    def _save(self, kind: str, p0: int, p1: int, meta: dict) -> str:
        with self.lock:
            blocks = [(s, b) for s, b in self.raw if s + len(b) > p0 and s < p1]
            dtype, mix, instrument = self.dtype, list(self.mix), self.instrument
        if not blocks:
            raise ValueError("nothing heard yet")
        x = np.concatenate([b for _, b in blocks])
        start = blocks[0][0]
        x = x[max(0, p0 - start): max(0, p1 - start)]
        sid = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        k = 1
        while (self.dir / f"{sid}.wav").exists():
            sid = datetime.now().strftime("%Y-%m-%d_%H%M%S") + f"-{k}"
            k += 1
        with wave.open(str(self.dir / f"{sid}.wav"), "wb") as w:
            w.setnchannels(x.shape[1] if x.ndim > 1 else 1)
            w.setsampwidth(np.dtype(dtype or x.dtype).itemsize)
            w.setframerate(self.rate)
            w.writeframes(x.tobytes())
        side = {"id": sid, "kind": kind, "time": datetime.now().isoformat(timespec="seconds"), **meta,
                "instrument": instrument, "detector": detector(), "seconds": round(len(x) / self.rate, 3),
                "mixed": [self.inputs[c].number for c in mix],
                "channels": [{"channel": c, "input": i.number, "name": i.name} for c, i in enumerate(self.inputs)]}
        (self.dir / f"{sid}.json").write_text(json.dumps(side, indent=1))
        return sid

    def checks(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json"), key=lambda q: q.stat().st_mtime_ns, reverse=True):  # newest first
            try:
                out.append(json.loads(p.read_text()))
            except (OSError, ValueError):
                continue
        return out

    def path(self, sid: str) -> Path:
        if not ID_RE.match(sid) or not (self.dir / f"{sid}.wav").exists():
            raise FileNotFoundError(sid)
        return self.dir / f"{sid}.wav"

    def delete(self, sid: str) -> None:
        self.path(sid).unlink()
        (self.dir / f"{sid}.json").unlink(missing_ok=True)
        with self.lock:
            if self.check is not None and self.check.id == sid:
                self.check = None

    # --- what the page shows

    def status(self) -> dict:
        with self.lock:
            on = self.thread is not None
            ck = self.check
            return {
                "state": "listening" if on else ("waiting" if time.monotonic() < self.wait_until else "idle"),
                **(self.now if on else {"chord": None, "confidence": 0.0, "top": [], "level_dbfs": None}),
                "history": [{"time": t, "chord": c} for t, c in self.history],
                "check": ck and {"target": chord_name(ck.target), "state": ck.state,
                                 "heard": ck.heard and chord_name(ck.heard), "right": ck.right,
                                 "scores": ck.scores, "id": ck.id},
                "mix": [self.inputs[c].number for c in self.mix],
                "instrument": self.instrument,
                "error": self.error,
                "device": self.cfg.capture.description or self.cfg.capture.name,
            }


class DemoSource:
    """A stand-in for the capture (the page tried on the desktop, and the tests): piano-like chords, a
    new one every `every_s`, each tone a few decaying partials, on every channel, in real time."""

    def __init__(self, chords: list[str], channels: int, rate: int = 48000, every_s: float = 2.5,
                 gap_s: float = 0.3, realtime: bool = True, decay: float = 1.2):
        self.chords = [parse_chord(c) for c in chords]
        self.channels, self.rate, self.every, self.gap, self.realtime = channels, rate, every_s, gap_s, realtime
        self.decay = decay  # per second, of each chord's tones
        self.dtype = np.dtype("<i4")
        self.n = 0
        self.t0 = 0.0
        self.stderr_lines: list[str] = []
        self.rng = np.random.default_rng(0)

    def start(self) -> None:
        self.t0 = time.monotonic()

    def stop(self) -> None:
        pass

    def read(self) -> np.ndarray:
        frames = int(BLOCK_S * self.rate)
        if self.realtime:
            time.sleep(max(0.0, self.t0 + (self.n + frames) / self.rate - time.monotonic()))
        t = (self.n + np.arange(frames)) / self.rate
        k = (t // self.every).astype(int)
        since = t - k * self.every
        x = 1e-4 * self.rng.normal(size=frames)
        for idx in np.unique(k):
            m = k == idx
            root, q = self.chords[int(idx) % len(self.chords)]
            tones = [48 + root + d for d in L.QUALITIES[q]] + [36 + root]
            env = np.where(since[m] < self.every - self.gap, np.exp(-self.decay * since[m]), 0.0)
            for tone in tones:
                f0 = 440 * 2 ** ((tone - 69) / 12)
                for h in range(1, 6):
                    x[m] += 0.05 * env * np.sin(2 * math.pi * f0 * h * since[m]) / h ** 1.5
        self.n += frames
        y = dsp.from_float(np.clip(x, -1, 1).astype(np.float32), self.dtype)
        return np.repeat(y[:, None], self.channels, axis=1)
