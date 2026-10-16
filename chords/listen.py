"""Hearing chords as they are played, and checking them.

Every 100 ms the inputs a page has chosen, mixed to one signal, are heard over the last `WINDOW_S`
(`livechords`: the band's own listener, copied from dobro-transcription), with a wider vocabulary
than the band's (major, minor, seven, diminished and diminished seven on 12 roots) and inversions
(any chord tone in the bass), and what is in the bass named on its own (the lowest strong peak): "C/E"
when it is not the root. The chord shown is smoothed over time: a forward HMM over the 60 chords,
staying put with probability `STAY` from one frame to the next; the bass shown changes once two frames
agree. A diminished seven (four roots, the same notes) is named by its bass when that is one of them.
How many notes sound (`livechords.notes_heard`: pitches found one harmonic series at a time) says
whether it is a single note ("E3"), two ("C+G, a fifth") or a chord; the kind shown changes once two
frames agree. Nothing is named while the level is within `QUIET_DB` of the quietest of the last 10 s
(or of `QUIET_CEIL_DBFS`, if nothing that quiet has been heard yet).

A check: a chord, a note ("E4", its octave too) or two notes ("C+G", any octave) is asked for; the
next attack (a rise of `ATTACK_DB` within 0.3 s, over the quiet) starts it, and it is judged over
`CHECK_SPAN_S` after the attack, once the strike has passed. A strike is a check with nothing asked
for: one chord played, what was heard kept and shown until the player says what it was
(`relabel`), with its clip to hear again (`listen_wav`: the mixed inputs, louder, 16-bit mono, as a
browser plays it).
The clip (every kept channel, from just before the attack) and what was asked, heard and scored are
kept in the config's takes_dir, for study on the desktop (`tools/pull-checks`). A label is the same
from the Listen view: the last `LABEL_S` kept with the chord the player says it was.

The Listen view also follows the playing with no chart: a steady pulse (`livechords.Pulse`: the
tempo, once the strikes or strums keep time) and a loop of chords (`livechords.LoopFinder`: the
shortest cycle the last two rounds of changes repeat), with where the playing is in it.

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
SUFFIX = {"maj": "", "min": "m", "7": "7", "dim": "dim", "dim7": "dim7"}
QUALITY_WORDS = {"maj": "major", "min": "minor", "7": "seven", "dim": "diminished", "dim7": "diminished seven"}
NAME_RE = re.compile(r"^([A-G])([#b]?)(dim7|dim|m|7)?(?:/([A-G])([#b]?))?$")
VOCAB = L.Vocabulary(("maj", "min", "7", "dim", "dim7"))
CHORDS = VOCAB.chords
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}(-\d+)?$")
NOTE_RE = re.compile(r"^([A-G])([#b]?)(-?\d)$")
PAIR_RE = re.compile(r"^([A-G])([#b]?)\+([A-G])([#b]?)$")
INTERVALS = ("octave", "minor second", "major second", "minor third", "major third", "fourth", "tritone",
             "fifth", "minor sixth", "major sixth", "minor seventh", "major seventh")


def chord_name(c: tuple[int, str], bass: int | None = None) -> str:
    """(pitch class, quality) and the bass's pitch class -> "C", "F#m", "Bbdim7", "C/E" (the bass only
    when it is not the root)."""
    return NAMES[c[0]] + SUFFIX[c[1]] + (f"/{NAMES[bass]}" if bass is not None and bass != c[0] else "")


def _semis(letter: str, accidental: str) -> int:
    """Semitones above C of a written note (Cb is -1, B# 12)."""
    return {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[letter] + {"#": 1, "b": -1, "": 0}[accidental]


def _pc(letter: str, accidental: str) -> int:
    return _semis(letter, accidental) % 12


def parse_chord(name: str) -> tuple[tuple[int, str], int | None]:
    """"D", "F#m", "Bbdim7", "C/E" -> ((pitch class, quality), the bass's pitch class or None for
    the root). Raises ValueError."""
    m = NAME_RE.match(name or "")
    if not m:
        raise ValueError(f"not a chord: {name!r}")
    q = {"m": "min", "7": "7", "dim": "dim", "dim7": "dim7", None: "maj"}[m.group(3)]
    root = _pc(m.group(1), m.group(2))
    bass = _pc(m.group(4), m.group(5) or "") if m.group(4) else None
    return (root, q), (None if bass == root else bass)


def note_name(m: int) -> str:
    """MIDI -> "E3" (C4 is middle C, 60)."""
    return f"{NAMES[m % 12]}{m // 12 - 1}"


@dataclass(frozen=True)
class Target:
    """What a check asks for, or a label says it was: a chord (and its bass), a note, or two notes."""
    kind: str  # "chord", "note", "pair"
    chord: tuple[int, str] | None = None
    bass: int | None = None  # a chord's bass when not its root
    midi: int | None = None  # a note, its octave too
    pcs: tuple[int, int] | None = None  # two notes, low then high, any octave

    @property
    def name(self) -> str:
        if self.kind == "note":
            return note_name(self.midi)
        if self.kind == "pair":
            return f"{NAMES[self.pcs[0]]}+{NAMES[self.pcs[1]]}"
        return chord_name(self.chord, self.bass)


def parse_target(name: str) -> Target:
    """"C/E", "Bbdim7" (chords), "E4" (a note), "C+G" (two notes). A chord's name wins: "C7" is C
    seven, never the note in octave 7. Raises ValueError."""
    if NAME_RE.match(name or ""):
        chord, bass = parse_chord(name)
        return Target("chord", chord=chord, bass=bass)
    m = NOTE_RE.match(name or "")
    if m:
        return Target("note", midi=12 * (int(m.group(3)) + 1) + _semis(m.group(1), m.group(2)))
    m = PAIR_RE.match(name or "")
    if m:
        a, b = _pc(m.group(1), m.group(2)), _pc(m.group(3), m.group(4))
        if a == b:
            raise ValueError(f"two notes, not one: {name!r}")
        return Target("pair", pcs=(a, b))
    chord, bass = parse_chord(name)
    return Target("chord", chord=chord, bass=bass)


@dataclass(frozen=True)
class Heard:
    """What a window holds: its kind (by how many pitch classes sound), a name and a line in words."""
    kind: str | None  # "note", "pair", "chord"; None: nothing
    name: str | None
    detail: str
    notes: tuple[int, ...] = ()  # MIDI, low to high
    chord: tuple[int, str] | None = None  # the chord scores' best (always, for a chord check)
    bass: int | None = None  # the bass's pitch class


def hear(V: np.ndarray, s: np.ndarray, best: int | None = None) -> Heard:
    """What `V` (semitone saliences) and `s` (VOCAB's chord scores) say: `best` is the chord to name
    (the smoothed one, in the Listen view), else the scores' best."""
    bass_midi, _ = L.bass_note(V)
    bass = bass_midi % 12 if bass_midi is not None else None
    notes = tuple(m for m, _ in L.notes_heard(V))
    pcs = []
    for m in notes:
        if m % 12 not in pcs:
            pcs.append(m % 12)
    chord = named(CHORDS[int(np.argmax(s)) if best is None else best], bass)
    if len(pcs) == 1:
        octs = [m for m in notes if m % 12 == pcs[0]]
        detail = f"{note_name(octs[0])}, a single note" if len(octs) == 1 else \
            f"{' and '.join(note_name(m) for m in octs)}, in octaves"
        return Heard("note", note_name(octs[0]), detail, notes, chord, bass)
    if len(pcs) == 2:
        lo = min(m for m in notes if m % 12 == pcs[0])
        hi = min(m for m in notes if m % 12 == pcs[1])
        word = INTERVALS[(hi - lo) % 12]
        return Heard("pair", f"{NAMES[pcs[0]]}+{NAMES[pcs[1]]}",
                     f"{note_name(lo)} and {note_name(hi)}, a {word}", notes, chord, bass)
    if not notes:
        return Heard(None, None, "", notes, chord, bass)
    words = f"{NAMES[chord[0]]} {QUALITY_WORDS[chord[1]]}"
    return Heard("chord", chord_name(chord, bass), words + (f", {NAMES[bass]} in the bass" if bass is not None
                                                            and bass != chord[0] else ""), notes, chord, bass)


def matches(said: Target | None, heard: str | None) -> bool:
    """Whether what was heard (a name as `hear` gives it) is what the player says it was."""
    if said is None or heard is None:
        return said is None and heard is None
    try:
        h = parse_target(heard)
    except ValueError:
        return False
    if said.kind != h.kind:
        return False
    if said.kind == "note":
        return said.midi == h.midi
    if said.kind == "pair":
        return set(said.pcs) == set(h.pcs)
    return same_chord(said.chord, h.chord) and (said.bass if said.bass is not None else said.chord[0]) == (
        h.bass if h.bass is not None else h.chord[0])


def tones(c: tuple[int, str]) -> set[int]:
    return {(c[0] + d) % 12 for d in L.ALL_QUALITIES[c[1]]}


def same_chord(a: tuple[int, str], b: tuple[int, str]) -> bool:
    """The same chord: a diminished seven on any of its four roots is the same four notes."""
    return a == b or (a[1] == b[1] == "dim7" and tones(a) == tones(b))


def named(c: tuple[int, str], bass: int | None) -> tuple[int, str]:
    """A diminished seven named by its bass, when the bass is one of its notes."""
    return (bass, "dim7") if c[1] == "dim7" and bass is not None and bass in tones(c) else c


def detector() -> str:
    """Which listener judged: the dobro-transcription revision the copy came from."""
    head = (Path(L.__file__).read_text().splitlines() or [""])[0]
    m = re.search(r"\(([0-9a-f]{6,})\)", head)
    return f"livechords@{m.group(1)}" if m else "livechords"


@dataclass
class Check:
    target: Target | None  # None: a strike, what it was said afterwards
    state: str = "waiting"  # waiting (for the attack), hearing, done
    attack: int | None = None  # the attack's position in the stream (samples)
    heard: Heard | None = None
    chord_right: bool | None = None  # a chord check: the chord (whatever the bass)
    bass_right: bool | None = None  # ...and the bass (the root, unless an inversion was asked for)
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
        self.post = np.full(len(CHORDS), 1.0 / len(CHORDS))
        self.bass_seen: list[int | None] = []  # the last frames' bass pitch classes
        self.bass_shown: int | None = None
        self.pulse = L.Pulse(self.rate)
        self.loops = L.LoopFinder()
        self.kind_seen: list[str | None] = []  # the last frames' kinds (note, pair, chord)
        self.recent_bass: collections.deque = collections.deque(maxlen=5)  # for each reading's sureness
        self.recent_kind: collections.deque = collections.deque(maxlen=5)
        self.kind_shown: str | None = None
        self.last_heard: Heard | None = None
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
            self.pulse, self.loops = L.Pulse(self.rate), L.LoopFinder()  # a new listen starts the form afresh
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
        V = L.window_salience(win, self.rate, 0.0, len(win) / self.rate, min_fft=MIN_FFT)
        tr, bs = L.fold(V)
        s = L.chord_scores(tr, bs, 0.5, inversions=True, vocab=VOCAB)
        bass_midi, _ = L.bass_note(V)
        frame_kind = hear(V, s).kind
        self.pulse.hear(mono)
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
            pc = bass_midi % 12 if (loud and bass_midi is not None) else None
            self.bass_seen = (self.bass_seen + [pc])[-2:]
            self.recent_bass.append(pc)
            self.recent_kind.append(frame_kind if loud else None)
            if len(self.bass_seen) == 2 and self.bass_seen[0] == self.bass_seen[1]:
                self.bass_shown = pc
            if not loud:
                self.bass_shown = None
            self.kind_seen = (self.kind_seen + [frame_kind if loud else None])[-2:]
            if len(self.kind_seen) == 2 and self.kind_seen[0] == self.kind_seen[1]:
                self.kind_shown = self.kind_seen[0]
            kind = self.kind_shown if loud else None
            h = hear(V, s, best)
            if kind == "chord":  # the chord as smoothed, with the bass shown
                c = named(CHORDS[best], self.bass_shown)
                h = Heard("chord", chord_name(c, self.bass_shown), f"{NAMES[c[0]]} {QUALITY_WORDS[c[1]]}" + (
                    f", {NAMES[self.bass_shown]} in the bass" if self.bass_shown not in (None, c[0]) else ""),
                    h.notes, c, self.bass_shown)
            elif kind is not None and h.kind != kind:  # a frame of another kind: keep the last of this one
                h = self.last_heard if self.last_heard is not None and self.last_heard.kind == kind else h
                kind = h.kind
            self.last_heard = h if kind else None
            name = h.name if kind else None
            levels = dict(L.notes_heard(V)) if kind else {}
            self.now = {"chord": name, "kind": kind, "detail": h.detail if kind else None,
                        "notes": [note_name(m) for m in h.notes] if kind else [],
                        "note_levels": [round(float(levels.get(m, 0.0)), 2) for m in h.notes] if kind else [],
                        "kind_confidence": round(sum(k == kind for k in self.recent_kind) / 5, 2) if kind else 0.0,
                        "bass_confidence": round(sum(b == self.bass_shown for b in self.recent_bass) / 5, 2)
                        if kind and self.bass_shown is not None else 0.0,
                        "bass": NAMES[self.bass_shown] if loud and self.bass_shown is not None else None,
                        "confidence": round(float(self.post[best]), 3) if kind == "chord" else (1.0 if kind else 0.0),
                        "top": [[chord_name(CHORDS[k]), round(float(s[k]), 3)] for k in order] if kind == "chord" else [],
                        "level_dbfs": round(block_level, 1), "quiet_dbfs": round(quiet, 1)}
            if name and self.now["confidence"] >= 0.5 and (not self.history or self.history[-1][1] != name):
                self.history.append((datetime.now().isoformat(timespec="seconds"), name))
            self.loops.hear(pos / self.rate, name if self.now["confidence"] >= 0.5 else None)
            tempo, loop = self.pulse.estimate(), self.loops.state()
            if loop and tempo:  # each chord's length in beats, at the pulse's tempo
                beat = 60.0 / tempo["bpm"]
                loop["beats"] = [round(x / beat) if x else None for x in loop["lengths_s"]]
            if loop:  # sure with the rounds it has held, less after a chord it let pass
                loop["confidence"] = round(min(1.0, (loop["rounds"] - 1) / 3) * (0.5 if self.loops.missed else 1.0), 2)
            self.now["tempo"], self.now["loop"] = tempo, loop
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
        sid = self._save("check" if ck.target is not None else "strike", ck.attack - int(CLIP_S[0] * self.rate),
                         ck.attack + int(CLIP_S[1] * self.rate), ck.meta)
        with self.lock:
            ck.id = sid

    def _judge(self, ck: Check) -> None:
        a, b = (ck.attack + int(t * self.rate) for t in CHECK_SPAN_S)
        seg = self._between(a, b)
        V = L.window_salience(seg, self.rate, 0.0, len(seg) / self.rate, min_fft=MIN_FFT)
        tr, bs = L.fold(V)
        s = L.chord_scores(tr, bs, 0.5, inversions=True, vocab=VOCAB)
        order = np.argsort(s)[::-1]
        bass_midi, bass_share = L.bass_note(V)
        h = hear(V, s)
        t = ck.target
        chord_right = bass_right = right = None
        heard_name = h.name
        if t is None:  # a strike: what it was comes later
            pass
        elif t.kind == "chord":  # judged as a chord whatever the count of notes
            chord_right = same_chord(h.chord, t.chord)
            bass_right = h.bass == (t.chord[0] if t.bass is None else t.bass)
            right = chord_right and bass_right
            heard_name = chord_name(h.chord, h.bass)
        elif t.kind == "note":
            right = h.kind == "note" and h.notes[0] == t.midi
            heard_name = h.name
        else:
            right = h.kind == "pair" and {m % 12 for m in h.notes} == set(t.pcs)
            heard_name = h.name
        scores = [[chord_name(CHORDS[k]), round(float(s[k]), 3)] for k in order[:6]]
        with self.lock:
            ck.heard, ck.scores, ck.state = h, scores, "done"
            ck.chord_right, ck.bass_right, ck.right = chord_right, bass_right, right
            ck.meta = {"target": t.name if t else None, "target_kind": t.kind if t else None, "heard": heard_name,
                       "right": right,
                       "chord_right": chord_right, "bass_right": bass_right, "heard_kind": h.kind,
                       "heard_detail": h.detail, "notes_heard": [note_name(m) for m in h.notes],
                       "bass": NAMES[h.bass] if h.bass is not None else None, "bass_midi": bass_midi,
                       "bass_share": bass_share, "scores": scores, "attack_s": CLIP_S[0],
                       "judged_s": list(CHECK_SPAN_S)}

    # --- checks and labels

    def start_check(self, target: Target | None = None) -> None:
        with self.lock:
            self.check = Check(target=target)

    def cancel_check(self) -> None:
        with self.lock:
            self.check = None

    def label(self, said: Target | None) -> str:
        """Keep the last LABEL_S with what the player says it was (a chord, a note or two; None:
        nothing), and what was shown."""
        with self.lock:
            shown, pos = self.now.get("chord"), self.pos
        if pos < int(0.5 * self.rate):
            raise ValueError("nothing heard yet")
        kind = said.kind if said else None
        said = said.name if said else None
        return self._save("label", pos - int(LABEL_S * self.rate), pos,
                          {"target": said, "target_kind": kind, "heard": shown, "heard_kind": self.now.get("kind"),
                           "right": said == shown, "scores": self.now.get("top", []),
                           "notes_heard": self.now.get("notes", [])})

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

    def relabel(self, sid: str, said: Target | None) -> dict:
        """What a kept strike (or any kept clip) was, as the player says: kept in its sidecar, with
        whether that is what was heard (None: nothing was playing)."""
        self.path(sid)
        side = self.dir / f"{sid}.json"
        meta = json.loads(side.read_text())
        meta.update({"target": said.name if said else None, "target_kind": said.kind if said else None,
                     "right": matches(said, meta.get("heard")),
                     "labelled": datetime.now().isoformat(timespec="seconds")})
        side.write_text(json.dumps(meta, indent=1))
        return meta

    def listen_wav(self, sid: str) -> bytes:
        """A kept clip as a browser plays it: the inputs it mixed, as one channel, peaking at -3 dBFS,
        16-bit."""
        path = self.path(sid)
        meta = json.loads((self.dir / f"{sid}.json").read_text())
        with wave.open(str(path)) as w:
            rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            x = np.frombuffer(w.readframes(w.getnframes()), dtype={2: "<i2", 4: "<i4"}[width]).reshape(-1, ch)
        chans = [c["channel"] for c in meta.get("channels", []) if c["input"] in meta.get("mixed", [])] or list(range(ch))
        y = x[:, chans].astype(np.float64).mean(axis=1)
        y *= 10 ** (-3 / 20) / max(float(np.abs(y).max()), 1e-12)
        out = __import__("io").BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(np.round(y * 32767).astype("<i2").tobytes())
        return out.getvalue()

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
                **(self.now if on else {"chord": None, "confidence": 0.0, "top": [], "level_dbfs": None,
                                        "tempo": None, "loop": None}),
                "history": [{"time": t, "chord": c} for t, c in self.history],
                "check": ck and {"target": ck.target.name if ck.target else None,
                                 "kind": ck.target.kind if ck.target else None, "state": ck.state,
                                 "heard_kind": ck.heard.kind if ck.heard else None,
                                 "notes": [note_name(m) for m in ck.heard.notes] if ck.heard else [],
                                 "heard": ck.meta["heard"] if ck.meta else None,
                                 "heard_detail": ck.heard.detail if ck.heard else None, "right": ck.right,
                                 "chord_right": ck.chord_right, "bass_right": ck.bass_right,
                                 "bass": NAMES[ck.heard.bass] if ck.heard and ck.heard.bass is not None else None,
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
        self.chords = [None if c == "-" else parse_target(c) for c in chords]  # "-": a rest
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
            t = self.chords[int(idx) % len(self.chords)]
            if t is None:
                continue
            if t.kind == "note":
                notes = [t.midi]
            elif t.kind == "pair":
                notes = [48 + t.pcs[0], 48 + t.pcs[0] + (t.pcs[1] - t.pcs[0]) % 12]
            else:
                root, q = t.chord
                notes = [48 + root + d for d in L.ALL_QUALITIES[q]] + [36 + (root if t.bass is None else t.bass)]
            env = np.where(since[m] < self.every - self.gap, np.exp(-self.decay * since[m]), 0.0)
            for tone in notes:
                f0 = 440 * 2 ** ((tone - 69) / 12)
                for h in range(1, 6):
                    x[m] += 0.05 * env * np.sin(2 * math.pi * f0 * h * since[m]) / h ** 1.5
        self.n += frames
        y = dsp.from_float(np.clip(x, -1, 1).astype(np.float32), self.dtype)
        return np.repeat(y[:, None], self.channels, axis=1)
