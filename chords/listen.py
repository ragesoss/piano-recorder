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
next attack (a rise of `ATTACK_DB` within 0.3 s, over the quiet) starts it, and it is judged `CHECK_S`
after the attack, over the chord as it sounds (`judged_span`): from just after its onset until it
falls `SOUNDS_DB` under its peak or to `OVER_ROOM_DB` over the room, so a chord damped at once is
judged on itself, not on the quiet after it. Its bass is looked for over the whole pitch axis
(`CHECK_BASS`), so a chord with nothing under E4 has one; when that is over the bass chroma's range
(D3), the chord is scored without the bass chroma, which then holds only the strike's thump and the
room. For the piano, the span is judged by the piano detector instead (`livechords.hear_piano`, in
`heard_by_piano`: the notes A0-C8 that best explain its peaks, the chord they make, the lowest note
its bass), when the listener has one. A strike is a check with nothing asked for: one chord played, what
was heard kept and shown until the player says what it was (`relabel`), with its clip to hear again
(`listen_wav`: the mixed inputs, louder, 16-bit mono, as a browser plays it).
The clip (every kept channel, from `ROOM_S` before the attack) and what was asked, heard and scored
(with where the chord began and was judged) are kept in the config's takes_dir, for study on the
desktop (`tools/pull-checks`; `tools/check_study.py` judges them again). A label is the same from
the Listen view: the last `LABEL_S` kept with the chord the player says it was.

The Listen view follows the playing on three time scales (the player: "this instant, this bar, and
overall orientation"): the notes this moment (each frame's); the chord this bar (`livechords.ChordPool`:
the notes pooled by pitch over about half a bar, so an arpeggio reads as its chord) with the tempo
(`livechords.Pulse`, once the playing keeps time, and where its beats fall); and where the playing is
in a loop of those bar chords (`livechords.LoopFinder`), the next chord predicted. Each comes with how
sure it is, and says nothing until it holds.

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
CHECK_S = 1.1  # a check is judged once this much after its attack is heard, over the chord as it sounds:
ONSET_SEARCH_S = 0.1  # from its onset (the steepest rise of the 10 ms levels within this of the attack's block,
ONSET_DB = 6.0  # if it rises this much over the 50 ms before)
SKIP_S = 0.05  # ...and this long after it (the hammers' knock)...
SOUNDS_DB = 30.0  # ...until its level (over 50 ms) falls this far under its peak...
OVER_ROOM_DB = 10.0  # ...or to this far over the room's (before the onset)...
MIN_JUDGED_S = 0.2  # ...but this long at least (a shorter window smears the bass: E2 and F2 are 5 Hz apart)
ROOM_S = 1.0  # the room: what was heard up to this long before the attack
CLIP_S = (ROOM_S, 1.6)  # a check's clip: this long before the attack, and after it
CHECK_BASS = (L.BASS_NOTE_RANGE[0], L.HI)  # a check's bass: looked for from E1 to the top of the pitch axis
LABEL_S = 2.0
ROOM_LEARN_S = 1.0  # the Listen view learns the room from this long of quiet (the piano detector's background):
ROOM_DB = 4.0  # within this of the quietest of the last 10 s (itself under QUIET_CEIL_DBFS), not a chord's tail
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


def hear(V: np.ndarray, s: np.ndarray, best: int | None = None, bass_range: tuple[int, int] | None = None) -> Heard:
    """What `V` (semitone saliences) and `s` (VOCAB's chord scores) say: `best` is the chord to name
    (the smoothed one, in the Listen view), else the scores' best; `bass_range`: where to look for the
    bass (MIDI; the listener's own range if None)."""
    bass_midi, _ = L.bass_note(V) if bass_range is None else L.bass_note(V, rng=bass_range)
    bass = bass_midi % 12 if bass_midi is not None else None
    notes = tuple(m for m, _ in L.notes_heard(V))
    return heard_of(notes, named(CHORDS[int(np.argmax(s)) if best is None else best], bass), bass)


def heard_of(notes: tuple[int, ...], chord: tuple[int, str] | None, bass: int | None) -> Heard:
    """What the notes sounding (MIDI, low to high), the chord named and the bass (a pitch class) make:
    a note, two, or a chord, by how many pitch classes sound."""
    pcs = []
    for m in notes:
        if m % 12 not in pcs:
            pcs.append(m % 12)
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
    if not notes or chord is None:
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


def judged_span(x: np.ndarray, rate: int, attack: int) -> dict:
    """Where in `x` (the mix around a check's attack, which is at sample `attack`) its chord is judged:
    from `SKIP_S` after its onset, until its level over 50 ms falls `SOUNDS_DB` under its peak or to
    `OVER_ROOM_DB` over the room (the 10 ms levels' median before the onset), but `MIN_JUDGED_S` at
    least. The onset is the steepest rise of the 10 ms levels within `ONSET_SEARCH_S` of the attack (an
    attack is found a block at a time: the onset is up to a block after it, or just before), if that
    rises `ONSET_DB` over the 50 ms before it; else the attack, with no room known. {"onset", "a", "b"}
    (samples into x), "peak_dbfs", "room_dbfs" (None with no room known) and "sounds_s" (from the
    onset)."""
    hop = max(1, rate // 100)
    n = len(x) // hop
    if n < 2:
        return {"onset": attack, "a": 0, "b": len(x), "peak_dbfs": None, "room_dbfs": None, "sounds_s": None}
    pw = np.mean(x[: n * hop].astype(np.float64).reshape(n, hop) ** 2, axis=1) + 1e-20
    lv = 10 * np.log10(pw)
    k = min(n - 1, attack // hop)
    lo, hi = max(1, k - int(ONSET_SEARCH_S * 100)), min(n, k + int(ONSET_SEARCH_S * 100) + 1)
    on = lo + int(np.argmax(lv[lo:hi] - lv[lo - 1:hi - 1])) if hi > lo else k
    if on < 3 or lv[on] - float(np.median(lv[max(0, on - 6): on - 1])) < ONSET_DB:
        on = k  # no rise: a chord already sounding (a label's, or a replay's)
        room = None
    else:
        room = float(np.median(lv[: on - 2]))
    pk = on + int(np.argmax(lv[on: on + 10]))
    peak = float(lv[pk])
    smooth = 10 * np.log10(np.convolve(pw, np.ones(5) / 5, mode="same"))
    stop = peak - SOUNDS_DB if room is None else max(peak - SOUNDS_DB, room + OVER_ROOM_DB)
    below = np.nonzero(smooth[pk:] < stop)[0]
    end = (pk + int(below[0])) * hop if len(below) else len(x)
    a = min(len(x), on * hop + int(SKIP_S * rate))
    b = min(len(x), max(end, a + int(MIN_JUDGED_S * rate)))
    return {"onset": on * hop, "a": a, "b": b, "peak_dbfs": round(peak, 1),
            "room_dbfs": None if room is None else round(room, 1), "sounds_s": round((end - on * hop) / rate, 2)}


def heard_in(seg: np.ndarray, rate: int) -> tuple[Heard, list, int | None, float | None]:
    """A check's span heard: its semitone saliences (`window_salience`), the bass over `CHECK_BASS`, the
    chords scored (with the bass chroma only when the bass is in its range: else it holds only the
    strike's thump and the room). (Heard, the six best [name, score], the bass's MIDI note, its share)."""
    V = L.window_salience(seg, rate, 0.0, len(seg) / rate, min_fft=MIN_FFT)
    bass_midi, bass_share = L.bass_note(V, rng=CHECK_BASS)
    tr, bs = L.fold(V)
    if bass_midi is None or bass_midi > L.BASS_RANGE[1]:
        bs = None
    s = L.chord_scores(tr, bs, 0.5, inversions=True, vocab=VOCAB)
    order = np.argsort(s)[::-1]
    return hear(V, s, bass_range=CHECK_BASS), [[chord_name(CHORDS[k]), round(float(s[k]), 3)] for k in order[:6]], \
        bass_midi, bass_share


RANG_S = 0.3  # what rang before a check's onset, for the piano detector: this long before it (as its study took it)


def heard_by_piano(x: np.ndarray, rate: int, span: dict) -> tuple[Heard, list, int | None, None]:
    """The same by the listener's piano detector (`hear_piano`: the notes that best explain the span's
    peaks; the chord they make, the lowest note its bass). What rang in the `RANG_S` before the onset is
    taken out only when the chord rose over it (an onset was found: before it, the room or an earlier
    chord); a chord already sounding with no onset (a label's, held) would take itself out."""
    a, b, on = span["a"] / rate, span["b"] / rate, span["onset"] / rate
    rang = L.quiet_peaks(x, rate, max(0.0, on - RANG_S), on - 0.02) if span["room_dbfs"] is not None and on >= 0.1 \
        else None
    p = L.hear_piano(x, rate, a, b, vocab=VOCAB, rang=rang)
    bass = p.bass % 12 if p.bass is not None else None
    chord = named(tuple(p.chord), bass) if p.chord is not None else None
    scores = [] if p.scores is None else [[chord_name(CHORDS[k]), round(float(p.scores[k]), 3)]
                                          for k in np.argsort(p.scores)[::-1][:6]]
    return heard_of(tuple(m for m, _ in p.notes), chord, bass), scores, p.bass, None


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
        self.pool = L.ChordPool(VOCAB)
        self.kind_seen: list[str | None] = []  # the last frames' kinds (note, pair, chord)
        self.recent_bass: collections.deque = collections.deque(maxlen=5)  # for each reading's sureness
        self.recent_kind: collections.deque = collections.deque(maxlen=5)
        self.kind_shown: str | None = None
        self.last_heard: Heard | None = None
        self.room = None  # the room's peaks (`livechords.quiet_peaks`), learned from a quiet second, for the piano
        self.quiet_run = 0  # frames of the room alone in a row
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
            self.pulse, self.loops, self.pool = L.Pulse(self.rate), L.LoopFinder(), L.ChordPool(VOCAB)  # afresh
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
        t_frame = time.perf_counter()
        V = L.window_salience(win, self.rate, 0.0, len(win) / self.rate, min_fft=MIN_FFT)
        tr, bs = L.fold(V)
        s = L.chord_scores(tr, bs, 0.5, inversions=True, vocab=VOCAB)
        piano = self.instrument == "piano"
        if piano:  # the piano detector: the notes that best explain the window's peaks, less the room's
            pn = L.piano_notes(L.window_peaks(win, self.rate, 0.0, len(win) / self.rate, MIN_FFT, background=self.room))
            pc_chord, pc_bass, ps_scores = L.chord_of_notes(pn, VOCAB)
            ps_scores = np.zeros(len(CHORDS)) if ps_scores is None else ps_scores
            ph = heard_of(tuple(m for m, _ in pn), named(tuple(pc_chord), pc_bass) if pc_chord else None, pc_bass)
            bass_midi, frame_kind = (pn[0][0] if pn else None), ph.kind
        else:
            bass_midi, _ = L.bass_note(V)
            frame_kind = hear(V, s).kind
        self.pulse.hear(mono)
        with self.lock:
            before = list(self.levels)[-3:]
            self.levels.append(block_level)
            quiet = min(min(self.levels), QUIET_CEIL_DBFS)
            loud = level > quiet + QUIET_DB
            floor = min(self.levels)
            room = not loud and floor <= QUIET_CEIL_DBFS and level <= floor + ROOM_DB  # the room alone, near its floor
            self.quiet_run = self.quiet_run + 1 if room else 0
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
            h = ph if piano else hear(V, s, best)
            if kind == "chord" and not piano:  # the chord as smoothed, with the bass shown
                c = named(CHORDS[best], self.bass_shown)
                h = Heard("chord", chord_name(c, self.bass_shown), f"{NAMES[c[0]]} {QUALITY_WORDS[c[1]]}" + (
                    f", {NAMES[self.bass_shown]} in the bass" if self.bass_shown not in (None, c[0]) else ""),
                    h.notes, c, self.bass_shown)
            elif kind is not None and h.kind != kind:  # a frame of another kind: keep the last of this one
                h = self.last_heard if self.last_heard is not None and self.last_heard.kind == kind else h
                kind = h.kind
            self.last_heard = h if kind else None
            name = h.name if kind else None
            levels = (dict(pn) if piano else dict(L.notes_heard(V))) if kind else {}
            self.now = {"chord": name, "kind": kind, "detail": h.detail if kind else None,
                        "notes": [note_name(m) for m in h.notes] if kind else [],
                        "note_levels": [round(float(levels.get(m, 0.0)), 2) for m in h.notes] if kind else [],
                        "kind_confidence": round(sum(k == kind for k in self.recent_kind) / 5, 2) if kind else 0.0,
                        "bass_confidence": round(sum(b == self.bass_shown for b in self.recent_bass) / 5, 2)
                        if kind and self.bass_shown is not None else 0.0,
                        "bass": NAMES[self.bass_shown] if loud and self.bass_shown is not None else None,
                        "confidence": (min(1.0, round(float(ps_scores.max()), 3)) if piano else
                                       round(float(self.post[best]), 3)) if kind == "chord" else (1.0 if kind else 0.0),
                        "top": ([[chord_name(CHORDS[k]), round(float(ps_scores[k]), 3)]
                                 for k in np.argsort(ps_scores)[::-1][:4]] if piano else
                                [[chord_name(CHORDS[k]), round(float(s[k]), 3)] for k in order]) if kind == "chord" else [],
                        "level_dbfs": round(block_level, 1), "quiet_dbfs": round(quiet, 1),
                        "frame_ms": round(1000 * (time.perf_counter() - t_frame), 1)}
            if name and self.now["confidence"] >= 0.5 and (not self.history or self.history[-1][1] != name):
                self.history.append((datetime.now().isoformat(timespec="seconds"), name))
            # this bar: the notes pooled over about half a bar (once the tempo is known), and the loop of those
            tempo = self.pulse.estimate()
            self.pool.set_tau(2 * tempo["beat_s"] if tempo else 0.8)
            heard = (pn if piano else L.notes_heard(V)) if loud else []
            self.pool.hear(pos / self.rate, heard, n / self.rate)
            ps = self.pool.state()
            bar_name = chord_name(named(ps["chord"], ps["bass"]), ps["bass"]) if ps else None
            self.loops.hear(pos / self.rate, bar_name if ps and ps["confidence"] >= 0.4 else None)
            loop = self.loops.state()
            if loop and tempo:  # each chord's length in beats, at the pulse's tempo
                beat = 60.0 / tempo["bpm"]
                loop["beats"] = [round(x / beat) if x else None for x in loop["lengths_s"]]
            if loop:  # sure with the rounds it has held, less after a chord it let pass; the next one predicted
                loop["confidence"] = round(min(1.0, (loop["rounds"] - 1) / 3) * (0.5 if self.loops.missed else 1.0), 2)
                loop["next"] = loop["chords"][(loop["at"] + 1) % len(loop["chords"])]
            beat = tempo and {"period_s": tempo["beat_s"],
                              "since_s": round((self.pulse.now_s() - tempo["last_strike_s"]) % tempo["beat_s"], 3)}
            self.now["tempo"], self.now["loop"] = tempo, loop
            self.now["bar"] = {"chord": bar_name, "bass": NAMES[ps["bass"]] if ps and ps["bass"] is not None else None,
                               "detail": (f"{NAMES[ps['chord'][0]]} {QUALITY_WORDS[ps['chord'][1]]}" if ps else None),
                               "confidence": ps["confidence"] if ps else 0.0, "beat": beat}
            ck = self.check
            if ck is not None and ck.state == "waiting" and before and loud and \
                    block_level - min(before) >= ATTACK_DB:
                ck.state, ck.attack = "hearing", pos - n
            judge = ck is not None and ck.state == "hearing" and pos >= ck.attack + int(CHECK_S * self.rate)
        if piano and self.quiet_run and self.quiet_run % int(ROOM_LEARN_S / BLOCK_S) == 0:
            # each quiet second: the room's hum and noise, to take out of the notes
            self.room = L.quiet_peaks(self._between(pos - int(ROOM_LEARN_S * self.rate), pos), self.rate, 0.0,
                                      ROOM_LEARN_S, MIN_FFT)
        if judge:
            self._judge(ck)
        if ck is not None and ck.state == "done" and ck.id is None and pos >= ck.attack + int(CLIP_S[1] * self.rate):
            self._keep(ck)

    def _keep(self, ck: Check) -> None:
        """The check's clip, from just before its attack, kept with what was asked and heard (and where
        in it the attack is: less than CLIP_S[0] in when the listening began just before it)."""
        p0 = ck.attack - int(CLIP_S[0] * self.rate)
        with self.lock:
            first = self.raw[0][0] if self.raw else p0
        meta = {**(ck.meta or {}), "attack_s": round((ck.attack - max(p0, first)) / self.rate, 3)}
        sid = self._save("check" if ck.target is not None else "strike", p0,
                         ck.attack + int(CLIP_S[1] * self.rate), meta)
        with self.lock:
            ck.id = sid

    def _judge(self, ck: Check) -> None:
        start = max(ck.attack - int(ROOM_S * self.rate), self.pos - self.K, 0)
        x = self._between(start, ck.attack + int(CHECK_S * self.rate))
        span = judged_span(x, self.rate, ck.attack - start)
        if self.instrument == "piano" and hasattr(L, "hear_piano"):
            h, scores, bass_midi, bass_share = heard_by_piano(x, self.rate, span)
        else:
            h, scores, bass_midi, bass_share = heard_in(x[span["a"]: span["b"]], self.rate)
        t = ck.target
        chord_right = bass_right = right = None
        heard_name = h.name
        if t is None:  # a strike: what it was comes later
            pass
        elif t.kind == "chord":  # judged as a chord whatever the count of notes
            chord_right = h.chord is not None and same_chord(h.chord, t.chord)
            bass_right = h.bass == (t.chord[0] if t.bass is None else t.bass)
            right = chord_right and bass_right
            heard_name = chord_name(h.chord, h.bass) if h.chord is not None else None
        elif t.kind == "note":
            right = h.kind == "note" and h.notes[0] == t.midi
            heard_name = h.name
        else:
            right = h.kind == "pair" and {m % 12 for m in h.notes} == set(t.pcs)
            heard_name = h.name
        with self.lock:
            ck.heard, ck.scores, ck.state = h, scores, "done"
            ck.chord_right, ck.bass_right, ck.right = chord_right, bass_right, right
            ck.meta = {"target": t.name if t else None, "target_kind": t.kind if t else None, "heard": heard_name,
                       "right": right,
                       "chord_right": chord_right, "bass_right": bass_right, "heard_kind": h.kind,
                       "heard_detail": h.detail, "notes_heard": [note_name(m) for m in h.notes],
                       "bass": NAMES[h.bass] if h.bass is not None else None, "bass_midi": bass_midi,
                       "bass_share": bass_share, "scores": scores, "attack_s": CLIP_S[0],
                       # where the chord began and was judged, in seconds from the attack; its levels
                       "onset_s": round((span["onset"] + start - ck.attack) / self.rate, 3),
                       "judged_s": [round((span[k] + start - ck.attack) / self.rate, 3) for k in ("a", "b")],
                       "sounds_s": span["sounds_s"], "peak_dbfs": span["peak_dbfs"],
                       "room_dbfs": span["room_dbfs"]}

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
                                        "tempo": None, "loop": None, "bar": None}),
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


def piano_tone(note: int, s: np.ndarray, amp: float, decay: float) -> np.ndarray:
    """A piano's note `s` seconds after it is struck: its partials up to 5 kHz, at 1/h, partial h dying
    at `decay` (1 + 0.3 (h - 1)) per second, tuned and stretched as `livechords.PIANO` has the player's
    piano."""
    f0 = 440 * 2 ** ((note - 69) / 12 + float(L.PIANO.cents(note)) / 1200)
    B = float(L.PIANO.B(note))
    out = np.zeros(len(s))
    for h in range(1, 40):
        f = f0 * h * math.sqrt(1 + B * h * h)
        if f > 5000 and h > 2:
            break
        out += amp * np.exp(-decay * (1 + 0.3 * (h - 1)) * s) * np.sin(2 * math.pi * f * s) / h
    return out


class DemoSource:
    """A stand-in for the capture (the page tried on the desktop, and the tests): piano-like chords, a
    new one every `every_s`, each tone its partials up to 5 kHz (1/h, the higher dying faster), sharp as
    the player's piano stretches them (`livechords.PIANO`), so the piano detector hears them as notes,
    on every channel, in real time."""

    def __init__(self, chords: list[str], channels: int, rate: int = 48000, every_s: float = 2.5,
                 gap_s: float = 0.3, realtime: bool = True, decay: float = 1.2):
        # "-": a rest; "~Am": its notes one at a time (an arpeggio, the octave on top)
        self.chords = [None if c == "-" else ("arp", parse_target(c[1:])) if c.startswith("~") else parse_target(c)
                       for c in chords]
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
            if isinstance(t, tuple):  # an arpeggio: root, third, fifth, octave, each a quarter of the time
                root, q = t[1].chord
                arp = [48 + root + d for d in L.ALL_QUALITIES[q][:3]] + [60 + root]
                quarter = (self.every - self.gap) / 4
                for j, tone in enumerate(arp):
                    mm = m & (since >= j * quarter) & (since < self.every - self.gap)
                    s0 = since[mm] - j * quarter
                    x[mm] += piano_tone(tone, s0, 0.08, 1.5)
                continue
            if t.kind == "note":
                notes = [t.midi]
            elif t.kind == "pair":
                notes = [48 + t.pcs[0], 48 + t.pcs[0] + (t.pcs[1] - t.pcs[0]) % 12]
            else:
                root, q = t.chord
                notes = [48 + root + d for d in L.ALL_QUALITIES[q]] + [36 + (root if t.bass is None else t.bass)]
            on = since[m] < self.every - self.gap
            for tone in notes:
                x[m] += on * piano_tone(tone, since[m], 0.05, self.decay)
        self.n += frames
        y = dsp.from_float(np.clip(x, -1, 1).astype(np.float32), self.dtype)
        return np.repeat(y[:, None], self.channels, axis=1)
