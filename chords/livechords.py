# Copied from dobro-transcription pipeline/src/transcribe/livechords.py (3be3b04) by tools/sync-livechords.
# Change it there and copy it again; do not edit this copy.
"""Chords in the moment, for a band that follows: per beat, what the audio heard so far says the chord
is, where in a chart the player is, and the key. Causal: every answer at a time t uses only audio
before t. The pieces and settings are the ones `tools/realtime_chords_study.py` found best
(2026-10-15, `docs/ecosystem/2026-10-15-realtime-chords.md`), taken into the package so the band
(`band.py`) can use them:

- The front end: each beat's audio (a window from the beat), its spectrum's peaks each at its
  interpolated frequency, credited to the nearest semitone (HPCP's way), cube-root compressed; an
  instrument's fixed resonances, learned once from its enrollment take, notched out first (the
  Martin D-1's body near 185 Hz: whitening against the take's own past cost 0.30 on the guitar, the
  notches gained 0.01-0.03).
- Two chromas: the bass (E1-D3) and the treble (C3-C6). Each chord scores the treble chroma's cosine
  with its tones, plus half the bass chroma's share on its root (1), fifth (0.4), third or seventh
  (0.2); the bass counted on beats 1 and 3 only once the bar is known (it walks to the fifth on 2 and
  4). Splitting them added 0.14-0.23 at 150 ms. For inversions (the chord app, 2026-10-16:
  `chord_scores(..., inversions=True)`) any chord tone in the bass counts nearly as the root, and
  `bass_note` names what is in the bass: the lowest strong peak.
- Reacting (`BeatChords`): a forward HMM over the chords, a change likelier on the bar's 1 and 3 than
  on 2 and 4 when the bar is known; 0.90-0.94 at 150 ms after the beat on the development mixes, the
  guitar take (held out) 0.90.
- Following a chart (`ChartFollower`): a forward HMM over the chart's beats, moving on one beat at a
  time, now and then holding a beat or jumping to a bar's first beat; it predicts the next beat's
  chord before the beat, 0.99-1.00 on takes played as written.
- The key (`KeyGuess`): the pitch classes heard so far against the Krumhansl-Kessler profiles; with
  the key, its bluegrass chords (I IV V V7 ii vi iii II II7 VI7 III7 I7 bVII) are favoured.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

LO, HI = 24, 96  # the pitch axis, C1 to C7 (MIDI)
N_SEMI = HI - LO + 1
BASS_RANGE = (28, 50)  # E1 to D3: the bass, and the guitar's bass notes
TREBLE_RANGE = (48, 84)  # C3 to C6

QUALITIES = {"maj": (0, 4, 7), "min": (0, 3, 7), "7": (0, 4, 7, 10)}  # the band's vocabulary
CHORDS = [(r, q) for q in ("maj", "min", "7") for r in range(12)]
# more chords, for a wider vocabulary (`Vocabulary`; the chord app's)
MORE_QUALITIES = {"dim": (0, 3, 6), "dim7": (0, 3, 6, 9)}
ALL_QUALITIES = {**QUALITIES, **MORE_QUALITIES}
# a bluegrass vocabulary in a key, as (semitones above the tonic, quality)
BLUEGRASS = ((0, "maj"), (5, "maj"), (7, "maj"), (7, "7"), (2, "min"), (9, "min"), (4, "min"), (2, "maj"),
             (2, "7"), (9, "7"), (4, "7"), (0, "7"), (10, "maj"))
# per beat of the bar, the chance the chord changes there: bluegrass changes on 1, sometimes on 3
CHANGE_BY_BEAT = {4: (0.35, 0.02, 0.2, 0.02), 3: (0.35, 0.02, 0.02), 2: (0.35, 0.02)}
CHANGE_UNKNOWN = 0.1  # ...and on any beat while the bar is not known
# Krumhansl-Kessler key profiles (C major, C minor)
KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def majmin(q: str) -> str:
    """A chart's quality as the band uses it: minor, a dominant seventh, or major."""
    if q.startswith("m") and not q.startswith("maj"):
        return "min"
    return "7" if q == "7" else "maj"


def tones(chord: tuple[int, str]) -> set[int]:
    """A chord's pitch classes."""
    r, q = chord
    return {(r + d) % 12 for d in ALL_QUALITIES[q]}


# --- the front end ---------------------------------------------------------------------------------


def peak_salience(S: np.ndarray, sr: int, n_fft: int, floor_db: float = 40.0) -> np.ndarray:
    """A magnitude spectrum's peaks (local maxima within `floor_db` of its loudest), each at the
    frequency and level a parabola through its log magnitude and its neighbours' gives, credited to the
    nearest semitone with weight cos^2(pi * distance); a semitone keeps its loudest peak. (N_SEMI,)"""
    L = np.log(S + 1e-12)
    a, b, c = L[:-2], L[1:-1], L[2:]
    k = np.nonzero((b > a) & (b >= c) & (b > L.max() - floor_db / 20 * np.log(10)))[0]
    den = a[k] - 2 * b[k] + c[k]
    p = np.where(np.abs(den) > 1e-12, 0.5 * (a[k] - c[k]) / den, 0.0)
    mag = np.exp(b[k] - 0.25 * (a[k] - c[k]) * p)
    midi = 69 + 12 * np.log2(np.maximum((k + 1 + p) * sr / n_fft, 1e-3) / 440.0)
    s = np.round(midi).astype(int)
    ok = (s >= LO) & (s <= HI)
    V = np.zeros(N_SEMI)
    np.maximum.at(V, s[ok] - LO, (mag * np.cos(np.pi * (midi - s)) ** 2)[ok])
    return V


def window_salience(y: np.ndarray, sr: int, a: float, b: float, notch=(), min_fft: int = 8192) -> np.ndarray:
    """Each semitone's salience (N_SEMI, from LO) in y between a and b seconds: one Hann-windowed
    segment, its peaks per semitone, the notches applied (pairs of semitone index and gain), cube-root
    compressed."""
    i, j = int(round(a * sr)), int(round(b * sr))
    seg = y[max(0, i): max(i + 16, j)]
    n = max(min_fft, 1 << int(np.ceil(np.log2(max(len(seg), 2)))))
    V = peak_salience(np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n)), sr, n)
    for idx, g in notch:
        V[idx] *= g
    return V ** (1 / 3)


def window_chroma(y: np.ndarray, sr: int, a: float, b: float, notch=(), min_fft: int = 8192):
    """The treble and bass chroma (12 each) of y between a and b seconds (`window_salience`, folded
    over TREBLE_RANGE and BASS_RANGE)."""
    return fold(window_salience(y, sr, a, b, notch, min_fft))


# where a chord's lowest note is looked for, E1 to E4, and how loud it must be against the loudest
# there (cube-root salience: 0.5 is about 18 dB under)
BASS_NOTE_RANGE = (28, 64)
BASS_NOTE_SHARE = 0.5


def bass_note(V: np.ndarray, share: float = BASS_NOTE_SHARE, rng: tuple[int, int] = BASS_NOTE_RANGE):
    """What is in the bass: the lowest semitone in `rng` that is a peak of V (no louder neighbour) and
    at least `share` of the loudest there. (MIDI note, its salience over that loudest), or (None, 0.0)."""
    lo, hi = rng[0] - LO, rng[1] - LO
    W = V[lo: hi + 1]
    top = float(W.max()) if len(W) else 0.0
    if top <= 0:
        return None, 0.0
    for k in range(len(W)):
        left = V[lo + k - 1] if lo + k > 0 else 0.0
        right = V[lo + k + 1] if lo + k + 1 < len(V) else 0.0
        if W[k] >= share * top and W[k] >= left and W[k] >= right:
            return rng[0] + k, round(float(W[k] / top), 3)
    return None, 0.0


# a note's partials, as semitones over its fundamental (h = 1..8), for `notes_heard`
HARMONIC_SEMIS = tuple(int(round(12 * np.log2(h))) for h in range(1, 9))
NOTE_SHARE = 0.2  # a note counts when its own fundamental, after the others' partials, is this share of the loudest


def notes_heard(V: np.ndarray, share: float = NOTE_SHARE, max_notes: int = 6,
                rng: tuple[int, int] = (28, 96)) -> list[tuple[int, float]]:
    """The notes sounding, low to high, as (MIDI, level against the loudest found), from `V` (semitone
    saliences as `window_salience` gives them; the cube root is undone here). Found one at a time: the
    pitch whose harmonic series (8 partials, the h-th expected at 1/h of the fundamental) holds the most
    of what is left, its partials then taken out; a pitch counts only while its own fundamental, after
    that, is `share` of the loudest found or more. So a single note's partials (its fifth and third
    above) are not notes, and an octave over a note counts only when louder than its second partial."""
    R = np.maximum(np.asarray(V, dtype=float), 0.0) ** 3
    found: list[tuple[int, float]] = []
    loudest = 0.0
    for _ in range(max_notes):
        best, best_sum = None, 0.0
        for m in range(rng[0], rng[1] + 1):
            i = m - LO
            if i >= len(R) or R[i] <= 0:
                continue
            total = sum(R[i + o] / h for h, o in enumerate(HARMONIC_SEMIS, start=1) if i + o < len(R))
            if total > best_sum:
                best, best_sum = m, total
        if best is None:
            break
        f = R[best - LO]
        if f < share * loudest:
            break
        loudest = max(loudest, f)
        found.append((best, f))
        for h, o in enumerate(HARMONIC_SEMIS, start=1):
            j = best - LO + o
            if j < len(R):
                R[j] = max(0.0, R[j] - f / h)
    return sorted((m, round(f / loudest, 3)) for m, f in found)


# --- a piano's notes, A0 to C8, and the chord they make --------------------------------------------
# For the chord app (2026-10-16; `exercises/observations/piano-triads-2026-10-16`, `tools/piano_study.py`).
# The notes are the set that best explains the window's spectral peaks, each note's partials where a
# piano's are (its tuning stretched, its partials sharp: n f0 sqrt(1 + B n^2)); the chord is named from
# those notes and its bass is the lowest of them. On the triads take, judged as the app judges a check:
# notes 142 of 144 with 11 extra (`notes_heard`: 99, 85 extra), chords 47 of 48 (the raw spectrum's
# scores: 22). numpy only; causal (a window's own audio, and the room as heard before it).

PIANO_RANGE = (21, 108)  # A0 to C8

# what `window_peaks` works out once for a window's length (the Listen view's is always the same; a few
# kept, read only)
_HANN: dict = {}  # the segment's length: its Hann window, and the window's sum
_BANDS: dict = {}  # (sr, FFT length, segment length, min_band_s): the floor's band centres and bins


def _kept(cache: dict, key, value):
    """value, kept in cache under key (a few at most: past 16 the cache starts again)."""
    if len(cache) >= 16:
        cache.clear()
    cache[key] = value
    return value


def _readonly(*arrays):
    """Arrays kept and shared between calls: not to be written."""
    for x in arrays:
        x.flags.writeable = False


def _hann(m: int):
    w = _HANN.get(m)
    if w is None:
        win = np.hanning(m)
        _readonly(win)
        w = _kept(_HANN, m, (win, max(win.sum(), 1e-12)))
    return w


def _floor_bands(sr: int, n: int, m: int, min_band_s: float):
    """`window_peaks`'s floor bands: a sixth of an octave each from 20 Hz, at least `min_band_s` / T wide
    (T = m / sr), those of 3 bins or more: (their centres' logs, [(first bin, end bin)])."""
    key = (sr, n, m, min_band_s)
    b = _BANDS.get(key)
    if b is None:
        edges = [20.0]
        while edges[-1] < sr / 2:
            edges.append(max(edges[-1] * 2 ** (1 / 6), edges[-1] + min_band_s / max(m / sr, 1e-3)))
        cen, bands = [], []
        for e0, e1 in zip(edges[:-1], edges[1:]):
            b0, b1 = int(e0 * n / sr), int(e1 * n / sr)
            if b1 - b0 >= 3:
                cen.append(np.log(np.sqrt(e0 * e1)))
                bands.append((b0, b1))
        cen = np.array(cen)
        _readonly(cen)
        b = _kept(_BANDS, key, (cen, tuple(bands)))
    return b


def _median(x: np.ndarray) -> float:
    """np.median of a 1-d array, from the same order statistics (one partition: the lower middle one of
    an even count is the largest under the upper), without its overhead. x is partitioned in place."""
    h = len(x) // 2
    x.partition(h)
    return float(x[h]) if len(x) % 2 else float((x[:h].max() + x[h]) / 2)


def window_peaks(y: np.ndarray, sr: int, a: float, b: float, min_fft: int = 16384, floor_db: float = 50.0,
                 snr_db: float = 10.0, background=None, near_hz: float = 1.5, near_rel: float = 0.004,
                 rise_db: float = 6.0, ringing=None, ringing_rise_db: float = 3.0,
                 min_band_s: float = 16.0) -> tuple[np.ndarray, np.ndarray]:
    """The spectrum's peaks in y between a and b seconds (one Hann-windowed segment), as (frequencies
    in Hz, amplitudes): each where a parabola through its log magnitude and its neighbours' puts it, its
    amplitude a sinusoid's (so a steady tone reads the same in any window). Kept: peaks `snr_db` over
    the median of their sixth of an octave, and within `floor_db` of the loudest kept. `background`:
    the room's peaks (`quiet_peaks`); a peak within `near_hz` (or `near_rel` of its frequency) of one of
    them and not `rise_db` louder is the room's, not the playing's (a click's level moves 3 dB with
    where it falls in the window). `ringing`: what rang just before the attack (`quiet_peaks` of the
    0.28 s before it), whose peaks go on decaying: likewise, not `ringing_rise_db` louder."""
    i, j = int(round(a * sr)), int(round(b * sr))
    seg = np.asarray(y[max(0, i): max(i + 16, j)], dtype=float)
    n = max(min_fft, 1 << int(np.ceil(np.log2(max(len(seg), 2)))))
    win, win_sum = _hann(len(seg))
    L = np.abs(np.fft.rfft(seg * win, n))  # the log magnitude, as a sinusoid's amplitude
    L *= 2
    L /= win_sum
    L += 1e-15
    np.log(L, out=L)
    l, c, r = L[:-2], L[1:-1], L[2:]
    k = ((c > l) & (c >= r)).nonzero()[0]
    lk, ck, rk = l[k], c[k], r[k]
    den = lk - 2 * ck + rk
    p = np.where(np.abs(den) > 1e-12, 0.5 * (lk - rk) / den, 0.0)
    lmag = ck - 0.25 * (lk - rk) * p
    f = (k + 1 + p) * sr / n
    # the floor: the median log magnitude of each sixth of an octave from 20 Hz, and at least 16/T Hz
    # wide (T the window's length; a peak's main lobe is 4/T wide, so a narrower band's median is the
    # peak's own level: a low fundamental in a short window)
    cen, bands = _floor_bands(sr, n, len(seg), min_band_s)
    keep = f > 20.0
    if len(cen):
        med = [_median(L[b0:b1]) for b0, b1 in bands]  # (L is not needed after: its bands partitioned in place)
        keep &= lmag > np.interp(np.log(np.maximum(f, 1.0)), cen, med) + snr_db / 20 * np.log(10)
    kept = keep.nonzero()[0]  # the rest are tested against the room only over the floor
    f, lmag, keep = f[kept], lmag[kept], keep[kept]
    for bg, rise in ((background, rise_db), (ringing, ringing_rise_db)):
        if bg is None or not len(bg[0]):
            continue
        fb = np.asarray(bg[0])
        o = fb.argsort()
        fb, lb = fb[o], np.log(np.asarray(bg[1])[o] + 1e-15)
        q = np.clip(fb.searchsorted(f), 1, max(1, len(fb) - 1))
        for qq in (q - 1, np.minimum(q, len(fb) - 1)):
            near = np.abs(f - fb[qq]) < np.maximum(near_hz, near_rel * f)
            keep &= ~(near & (lmag < lb[qq] + rise / 20 * np.log(10)))
    if not keep.any():
        return f[:0], f[:0]
    keep &= lmag > lmag[keep].max() - floor_db / 20 * np.log(10)
    return f[keep], np.exp(lmag[keep])


def quiet_peaks(y: np.ndarray, sr: int, a: float, b: float, min_fft: int = 16384) -> tuple[np.ndarray, np.ndarray]:
    """The room's peaks, for `window_peaks(..., background=)`: those of a quiet stretch of y (or of the
    second before an attack), down to 60 dB under its loudest and 6 dB over the floor. Several stretches'
    peaks can be put together (concatenated), as a click or a hum is heard in each."""
    return window_peaks(y, sr, a, b, min_fft, floor_db=60.0, snr_db=6.0)


@dataclass(frozen=True)
class PianoModel:
    """A piano's tuning (cents from equal temperament at A440, by MIDI note; a line between the points)
    and its strings' inharmonicity B (partial n at n f0 sqrt(1 + B n^2); a line in log B between the
    points). The defaults: the player's piano, measured on the triads take (2026-10-16): A4 at 443 Hz,
    the middle 6-16 cents sharp, the treble stretched to +45 at C8, the bottom octave flat (A0 about
    -40, D1 -20); B from about 1.2e-4 (C2-C3) to 6.5e-3 (C7)."""
    tuning: tuple = ((21, -35.0), (24, -25.0), (27, -15.0), (31, 0.0), (36, 2.0), (48, 11.0), (84, 11.0), (108, 45.0))
    inharmonicity: tuple = ((21, 3e-4), (36, 1.2e-4), (48, 1.2e-4), (60, 3e-4), (72, 8e-4), (84, 2.7e-3),
                            (96, 6.5e-3), (108, 1.5e-2))

    def cents(self, m):
        x, v = zip(*self.tuning)
        return np.interp(m, x, v)

    def B(self, m):
        x, v = zip(*self.inharmonicity)
        return np.exp(np.interp(m, x, np.log(v)))


PIANO = PianoModel()


@dataclass(frozen=True)
class NoteSearch:
    """The settings of `piano_notes` (`docs/ledger.md`, "Piano notes")."""
    partials: int = 16  # a note's partials looked for, at most...
    explain_hz: float = 9000.0  # ...and none over this
    expect_hz: float = 5000.0  # a note's partials up to here are expected to be there...
    expect_always: int = 2  # ...and its first two wherever they fall
    low_hz: float = 60.0  # a partial under this is expected at half weight (the room mic's faint low end)
    offsets: tuple = (-30, -20, -10, 0, 10, 20, 30)  # cents around the piano's tuning tried for each note
    tol_cents: float = 15.0  # a partial is the loudest peak within this many cents of its place...
    stretch_tol: float = 0.15  # ...widened by this share of its stretch
    gamma: float = 1 / 3  # a peak's weight: its amplitude over the window's loudest, to this power
    per_note: float = 0.3  # each note costs this...
    miss: float = 0.3  # ...and this times its level for each expected partial not there...
    chance: float = 1.0  # ...and what its partials would catch by chance, at the local density of peaks
    fund_hz: float = 100.0  # a note over this needs its fundamental at least `fund_share` of its loudest partial
    fund_share: float = 0.5
    fund_low_hz: float = 60.0  # a note over this needs its fundamental there at all
    beam: int = 4  # sets of notes kept at each step of the search
    max_notes: int = 8
    claim_cap: float = 1.0  # a note claims of a peak above its fundamental no more than this times its
    # neighbouring partials' amplitude (0: the whole peak)
    stack_ratio: float = 2.0  # a note on another's partial: its fundamental's peak this many times the found
    # notes' predicted share of it (0: no such notes looked for; `_stacked`)...
    stack_left: float = 0.15  # ...and a partial of it counts where this share of its peak is left over
    stack_coprime: bool = True  # a found note's own level read from its partials prime to 6
    stack_shared_miss: float = 0.0  # a partial of it on a shared peak with too little left counts this much missing
    own_partials: int = 32  # a found note's partials to here are its own unless a note stands over them
    stack_partials: int = 6  # notes are looked for on found notes' partials 2 to this (an octave, a twelfth,
    # two octaves, a seventeenth, two octaves and a fifth over them)


_PARTIAL_TABLES: dict = {}


def _partial_table(search: NoteSearch, piano: PianoModel):
    """Every candidate note's partials (notes, tuning offsets, partials): their frequencies, tolerances
    (as ratios), whether each may explain a peak and how much each is expected."""
    key = (search, piano)
    if key not in _PARTIAL_TABLES:
        ms = np.arange(PIANO_RANGE[0], PIANO_RANGE[1] + 1)
        h = np.arange(1, search.partials + 1)
        B = piano.B(ms)[:, None, None]
        cents = piano.cents(ms)[:, None, None] + np.asarray(search.offsets, dtype=float)[None, :, None]
        f = 440.0 * 2 ** ((ms[:, None, None] - 69) / 12 + cents / 1200) * h * np.sqrt(1 + B * h ** 2)
        stretch = 1200 * np.log2(np.sqrt(1 + B * h ** 2))
        tol = np.broadcast_to(2 ** ((search.tol_cents + search.stretch_tol * stretch) / 1200), f.shape)
        explain = f <= search.explain_hz
        expect = (((f <= search.expect_hz) | (h <= search.expect_always)) & explain) * np.where(
            f < search.low_hz, 0.5, 1.0)
        f0 = 440.0 * 2 ** ((ms - 69) / 12)
        fund = np.where(f0 >= search.fund_hz, search.fund_share, np.where(f0 >= search.fund_low_hz, 1e-9, 0.0))
        _PARTIAL_TABLES[key] = (ms, f, tol, explain, expect, fund)
    return _PARTIAL_TABLES[key]


@dataclass(frozen=True)
class _Bounds:
    """Fixed frequencies (x), sorted (`ordered`; `rank`: each one's place there), to count the peaks under
    each by merging (`below`)."""
    x: np.ndarray
    ordered: np.ndarray
    rank: np.ndarray

    @staticmethod
    def of(x: np.ndarray) -> _Bounds:
        o = np.argsort(x, kind="stable")
        rank = np.empty(len(x), int)
        rank[o] = np.arange(len(x))
        return _Bounds(x, x[o], rank)

    def below(self, fs: np.ndarray) -> np.ndarray:
        """np.searchsorted(fs, self.x): the peaks (sorted fs) under each x."""
        return np.bincount(self.ordered.searchsorted(fs, "right"), minlength=len(self.x) + 1).cumsum()[self.rank]


@dataclass(frozen=True)
class _Tables:
    """What `piano_notes` looks up for every window, worked out once per search and piano."""
    base: tuple  # `_partial_table`'s
    flat: np.ndarray  # the partials (flat indices) that may explain a peak...
    lo: _Bounds  # ...and the bounds a peak is looked for between (f / tol, f * tol)
    hi: _Bounds
    near_lo: np.ndarray  # each partial's sixth of an octave either side (for the chance of a peak)
    near_hi: np.ndarray
    cents_wide: np.ndarray  # its tolerance's width in cents, both sides
    x_lo: np.ndarray  # the partials past the counted ones, to `own_partials`: their bounds, by offset...
    x_hi: np.ndarray
    x_explain: np.ndarray  # ...and whether each may explain a peak


_NOTE_TABLES: dict = {}
_LAST_TABLES: tuple = (None, None, None)  # the last search and piano asked for, and their tables


def _note_tables(search: NoteSearch, piano: PianoModel) -> _Tables:
    global _LAST_TABLES
    last = _LAST_TABLES
    if last[0] is search and last[1] is piano:  # (most calls pass the same two: no hashing them)
        return last[2]
    key = (search, piano)
    t = _NOTE_TABLES.get(key)
    if t is None:
        ms, f, tol, explain, expect, fund = base = _partial_table(search, piano)
        flat = np.flatnonzero(explain)
        hx = np.arange(search.partials + 1, max(search.partials, search.own_partials) + 1)
        cents = piano.cents(ms)[:, None] + np.asarray(search.offsets, dtype=float)[None, :]
        Bm = piano.B(ms)[:, None]
        fx = 440.0 * 2 ** ((ms[:, None, None] - 69) / 12 + cents[:, :, None] / 1200) * hx * np.sqrt(
            1 + Bm[:, None] * hx ** 2)
        tx = 2 ** ((search.tol_cents + search.stretch_tol * 1200 * np.log2(np.sqrt(1 + Bm * hx ** 2))) / 1200)
        lo, hi = _Bounds.of((f / tol).ravel()[flat]), _Bounds.of((f * tol).ravel()[flat])
        t = _Tables(base, flat, lo, hi, f / 2 ** (1 / 6), f * 2 ** (1 / 6), 2 * 1200 * np.log2(tol), fx / tx[:, None],
                    fx * tx[:, None], fx <= search.explain_hz)
        _readonly(flat, lo.x, lo.ordered, lo.rank, hi.x, hi.ordered, hi.rank, t.near_lo, t.near_hi, t.cents_wide,
                  t.x_lo, t.x_hi, t.x_explain)
        _NOTE_TABLES[key] = t
    _LAST_TABLES = (search, piano, t)
    return t


_NO_PEAK = np.zeros(1)  # a peak's weight where a partial has none


def _top3(x: np.ndarray) -> np.ndarray:
    """Each row's three largest, their mean (as np.sort(x, 1)[:, -3:].mean(1))."""
    s = x.copy()
    s.sort(1)
    return s[:, -3:].sum(1) / 3


def _loudest_within(fs: np.ndarray, ws: np.ndarray, f: np.ndarray, tol: np.ndarray):
    """For each frequency in f: the loudest of (at most three) peaks within its tolerance, as an index
    into the sorted peaks fs (-1: none), and its weight."""
    return _loudest_in(fs, ws, np.searchsorted(fs, f / tol), np.searchsorted(fs, f * tol))


def _loudest_in(fs: np.ndarray, ws: np.ndarray, lo: np.ndarray, hi: np.ndarray):
    """`_loudest_within`, from the peaks under each window's bounds (lo, hi: np.searchsorted(fs, bound)),
    worked out only where a peak falls between."""
    best, bw = np.full(lo.shape, -1), np.zeros(lo.shape)
    k, b, bk = _loudest_at(fs, ws, lo, hi)
    best[k], bw[k] = b, bk
    return best, bw


def _loudest_at(fs: np.ndarray, ws: np.ndarray, lo: np.ndarray, hi: np.ndarray):
    """`_loudest_in` where a peak falls in the window: (where, as np.nonzero gives it; the peak; its
    weight)."""
    k = (hi > lo).nonzero()
    lk = lo[k]
    near = lk[:, None] + _THREE  # the (at most three) peaks from the first in the window
    w = np.where(near < hi[k][:, None], ws[np.minimum(near, len(fs) - 1)], 0.0)
    first = w.argmax(1)  # the loudest, the first of equals
    bk = w[np.arange(len(lk)), first]
    return k, np.where(bk > 0, lk + first, -1), np.maximum(bk, 0.0)


_THREE = np.arange(3)
_readonly(_NO_PEAK, _THREE)


def piano_notes(peaks: tuple[np.ndarray, np.ndarray], search: NoteSearch = NoteSearch(),
                piano: PianoModel = PIANO) -> list[tuple[int, float]]:
    """The piano notes sounding (A0 to C8), low to high, as (MIDI, level against the loudest found),
    from a window's peaks (`window_peaks`). Each peak weighs its amplitude over the loudest's, to the
    power `gamma`. Each candidate note claims, per partial, the loudest peak near where the piano puts
    it (`piano`'s tuning and stretch, tried at a few offsets), and costs `per_note`, `miss` times its
    level for each partial it should have but lacks, and what its partials would catch by chance where
    peaks are dense (the bottom octave's partials lie a few hertz apart). The notes are the set whose
    claimed peaks, each counted once, weigh most over their costs: a beam search adding a note at a time,
    then dropping any note the set is better without. So a note's own partials are not notes, a note
    an octave or a twelfth below a chord (whose partials the chord's notes are) lacks the partials in
    between, and a low note is judged by its partials, its fundamental faint at a room mic."""
    levels = _note_levels(peaks, search, piano)
    top = max(levels.values(), default=0.0)
    return sorted((m, round(v / top, 3)) for m, v in levels.items())


def _note_levels(peaks: tuple[np.ndarray, np.ndarray], search: NoteSearch, piano: PianoModel) -> dict:
    """`piano_notes`'s notes, each with its level before it is put against the loudest's and rounded (the
    weight of its loudest partial): {MIDI: level}. Its cost, per window (2026-10-16, `docs/ledger.md`,
    "The piano detector's cost"): the tables a search and piano look up are worked out once; a peak is
    looked for only near partials it could be; each step of the beam scores its sets together, and only
    the notes the first step gained by; `_stacked` works in Python floats. The same numbers as before."""
    f, amp = peaks
    if len(f) == 0:
        return {}
    f, amp = np.asarray(f, dtype=float), np.asarray(amp, dtype=float)
    o = f.argsort()
    fs, w = f[o], (amp[o] / amp.max()) ** search.gamma
    T = _note_tables(search, piano)
    ms, fp, tol, explain, expect, fund = T.base
    best, bw = np.full(fp.shape, -1), np.zeros(fp.shape)  # the loudest peak near each partial, and its weight
    hit, b, bk = _loudest_at(fs, w, T.lo.below(fs), T.hi.below(fs))
    best.reshape(-1)[T.flat[hit]], bw.reshape(-1)[T.flat[hit]] = b, bk
    rows = np.arange(len(ms))
    j = bw.sum(-1).argmax(1)  # each note's best offset
    match = best[rows, j]
    # chance: at the local density of peaks (a sixth of an octave either side), what a window this wide catches
    cs = np.zeros(len(w) + 1)
    w.cumsum(out=cs[1:])
    lo, hi = fs.searchsorted(T.near_lo[rows, j]), fs.searchsorted(T.near_hi[rows, j])
    mean_w = np.where(hi > lo, (cs[hi] - cs[lo]) / np.maximum(hi - lo, 1), 0.0)
    expected_hits = np.maximum(hi - lo - 1, 0) * T.cents_wide[rows, j] / 400.0
    chance = np.where(explain[rows, j], (1 - np.exp(-expected_hits)) * mean_w, 0.0).sum(1)
    present = match >= 0
    idx = np.where(present, match, len(w))  # len(w): no peak
    wpad = np.concatenate((w, _NO_PEAK))
    pw = wpad[idx]
    level = _top3(pw)
    expect_j = expect[rows, j]
    cost = search.per_note + search.miss * level * (expect_j * ~present).sum(1) + search.chance * chance
    cost = np.where(pw[:, 0] < fund * pw.max(1), 1e9, cost)
    # what each note claims of its peaks (linear amplitude): above its fundamental, no more than
    # `claim_cap` times its neighbouring partials' (the rest, a louder peak, is another note's too)
    apad = np.concatenate((w ** (1 / search.gamma), _NO_PEAK))
    A = apad[idx]
    claim = A
    if search.claim_cap:
        nbr = np.zeros_like(A)  # the louder of the partials either side (none past the ends)
        nbr[:, :-1] = A[:, 1:]
        np.maximum(nbr[:, 1:], A[:, :-1], out=nbr[:, 1:])
        claim = np.minimum(A, search.claim_cap * nbr)
        claim[:, 0] = A[:, 0]
    g = search.gamma

    def explained(sets):
        """What each set of notes explains: their claims on each peak, added in the set's own order, no
        more than the peak (the last entry: no peak)."""
        orders = [list(ns) for ns in sets]
        sel = [i for o in orders for i in o]
        E = np.zeros((len(sets), len(apad)))
        np.add.at(E, (np.repeat(np.arange(len(sets)), [len(o) for o in orders])[:, None], idx[sel]), claim[sel])
        E[:, -1] = 0.0
        return np.minimum(E, apad, out=E)

    # the beam search over sets of notes: a set's score is what its notes explain (each peak's explained
    # amplitude to the power gamma) less their costs. A note's gain only falls as the set explains more
    # (x^gamma is concave), so after the first step only the notes that gained then are looked at. The
    # sets of a step are scored together (E: what each explains), and a grown set's E worked out once kept.
    beams = [(0.0, frozenset())]
    E = np.zeros((1, len(apad)))
    best_set = beams[0]
    seen = set()
    live, A_l, claim_l, idx_l, cost_l = rows, A, claim, idx, cost
    for step in range(search.max_notes):
        t = E[:, idx_l]  # each set, each note's gain: (min(A, e + claim)^g - e^g) over its partials, less its cost
        t += claim_l
        np.minimum(A_l, t, out=t)
        t **= g
        t -= (E ** g)[:, idx_l]
        gain = t.sum(2)
        gain -= cost_l
        if step == 0:
            live = np.nonzero(gain[0] > 0)[0]
            A_l, claim_l, idx_l, cost_l = A[live], claim[live], idx[live], cost[live]
            gain = gain[:, live]
        grown = []
        top = np.argsort(gain, 1)[:, ::-1][:, : 2 * search.beam]  # each set's likeliest notes to add
        for k, ((score, notes), gk, ik) in enumerate(zip(beams, gain[np.arange(len(beams))[:, None], top].tolist(),
                                                         live[top].tolist())):
            for gi, i in zip(gk, ik):
                if gi <= 0:
                    break
                grow = notes | {i}
                if i in notes or grow in seen:
                    continue
                seen.add(grow)
                grown.append((score + gi, grow, k, i))
        if not grown:
            break
        grown.sort(key=lambda s: -s[0])
        kept = grown[: search.beam]
        added = [i for _, _, _, i in kept]
        E = E[[k for _, _, k, _ in kept]]
        np.add.at(E, (np.arange(len(kept))[:, None], idx[added]), claim[added])
        np.minimum(E, apad, out=E)
        E[:, -1] = 0.0
        beams = [(s, ns) for s, ns, _, _ in kept]
        if beams[0][0] > best_set[0]:
            best_set = beams[0]

    def totals(sets):
        return [float(v - sum(cost[i] for i in ns)) for v, ns in zip((explained(sets) ** g).sum(1), sets)]

    notes = set(best_set[1])
    while notes:
        order = sorted(notes)
        base, *without = totals([notes] + [notes - {i} for i in order])
        drop = next((i for i, v in zip(order, without) if v > base), None)
        if drop is None:
            break
        notes.discard(drop)
    if not notes:
        return {}
    level_of = {i: float(pw[i].max()) for i in notes}
    if search.stack_ratio:
        # each note's partials past the counted ones, up to `own_partials`, at its offset: to tell its high
        # partials from notes struck on them
        # (only for the notes `_stacked` reads: those found, and those with a fundamental, its candidates)
        reads = idx[:, 0] != len(w)
        reads[list(notes)] = True
        bx = np.full(T.x_explain.shape[::2], len(w))
        ex = r, c = np.nonzero(T.x_explain[rows, j] & reads[:, None])
        b, _ = _loudest_in(fs, w, np.searchsorted(fs, T.x_lo[r, j[r], c]), np.searchsorted(fs, T.x_hi[r, j[r], c]))
        bx[ex] = np.where(b >= 0, b, len(w))
        idx_all = np.concatenate([idx, bx], axis=1)
        level_of = _stacked(notes, level_of, idx_all, apad[idx_all], expect_j, chance, fund, len(w), search)
    return {int(ms[i]): v for i, v in level_of.items()}


def _envelope(a: np.ndarray, clean: np.ndarray) -> np.ndarray:
    """A note's partial amplitudes (a, linear, by partial number) where they are its own (`clean`), and
    elsewhere the most its own partials either side allow: a line through them in log amplitude over
    log partial number or over partial number, or falling as 1/h from the one above (a bass's
    fundamental is faint at a room mic); with one side only, falling as 1/h from it. With none of its
    own, its observed ones. The larger reading, so that a note's own partial is not taken for another
    note's."""
    own = clean & (a > 0)
    if not own.any():
        return a.copy()
    h = np.arange(1, len(a) + 1, dtype=float)
    return np.array(_fill(a.tolist(), own.tolist(), h.tolist(), np.log(h).tolist(), (~clean).tolist()))


def _fill(a: list, own: list, h: list, lh: list, fill: list | None = None) -> list:
    """`_envelope` on lists, as `_stacked` reads it: `a` at the positions `fill` (by default, those not
    `own`) from the own ones (each above 0) either side; h the partial numbers, lh their logs."""
    anchors = [k for k, o in enumerate(own) if o]
    out = list(a)
    if not anchors:
        return out
    skip = own if fill is None else [not x for x in fill]
    # under the first own partial and over the last: falling as 1/h from it
    for s, ks in ((anchors[0], range(anchors[0])), (anchors[-1], range(anchors[-1] + 1, len(a)))):
        ash = a[s] * h[s]
        for k in ks:
            if not skip[k]:
                out[k] = ash / h[k]
    # between two: the larger of the lines through them (log amplitude over log partial number and over
    # partial number) and 1/h from the upper
    ln, ex = math.log, math.exp
    for p, q in zip(anchors, anchors[1:]):
        if q > p + 1:
            lp = ln(a[p])
            dl, dlh, dh, aqh = ln(a[q]) - lp, lh[q] - lh[p], h[q] - h[p], a[q] * h[q]
            for k in range(p + 1, q):
                if not skip[k]:
                    out[k] = max(ex(lp + dl * ((lh[k] - lh[p]) / dlh)), ex(lp + dl * ((h[k] - h[p]) / dh)), aqh / h[k])
    return out


def _stacked(notes: set, level_of: dict, idx: np.ndarray, A: np.ndarray, expect: np.ndarray, chance: np.ndarray,
             fund: np.ndarray, none: int, search: NoteSearch) -> dict:
    """Notes struck on another note's partials, which the set search cannot tell from those partials, since
    their peaks are claimed: a left-hand root under a right-hand triad (C2 under C3-E3-G3: C3 is C2's 2nd
    partial, G3 its 3rd), octaves, tenths over a bass. A found note's share of a peak it shares is what
    its own partials predict there (`_envelope`). The notes are read low to high, and a note's own
    amplitude at a peak it shares with lower notes is the peak less their shares there. What it reads
    as its own: not its partials that a note an octave, a twelfth or two octaves over it would share
    (numbers not prime to 6, `stack_coprime`), nor those shared with a higher found note or the note
    tested. What a peak holds beyond the found notes' shares is the tested note's.
    A found note whose fundamental is a lower found note's partial (to `own_partials`) and not
    `stack_ratio` times the shares there is that note's partial, and goes. Notes are then added, the
    best at a time: a note whose fundamental is a found note's partial and `stack_ratio` times the
    shares there, and whose leftovers (each its amplitude to the power gamma; less than `stack_left` of
    a peak left is none) pay its costs as a note's do. A partial of it on a shared peak with none left
    is not missing (`stack_shared_miss`: partials at one frequency add with their phases, and may
    cancel). After each addition the found notes are checked again. Returns {note index: level}.
    (Worked in Python floats over each note's partials with a peak, the shares kept per set of found
    notes and the tested note's peaks that change them: the same numbers as numpy's, a few times faster.)"""
    g, P = search.gamma, search.partials
    H = idx.shape[1]
    hh = np.arange(1, H + 1)
    stackable = ((hh % 2 == 0) | (hh % 3 == 0)) if search.stack_coprime else np.zeros(H, bool)
    prime, h_of, lh_of = (~stackable).tolist(), hh.astype(float).tolist(), np.log(hh.astype(float)).tolist()
    first = idx[:, 0].tolist()
    by_fundamental: dict = {}
    for m, q in enumerate(first):
        if q != none:
            by_fundamental.setdefault(q, []).append(m)
    rows: dict = {}

    def row(n):
        """Note n's peaks by partial (`none`: no peak); over its partials with a peak, their peaks,
        amplitudes, numbers and those numbers' logs, and whether each is prime to 6; its peaks over its
        fundamental."""
        if n not in rows:
            q, a = idx[n].tolist(), A[n].tolist()
            ks = [k for k in range(H) if q[k] != none]
            rows[n] = (q, [q[k] for k in ks], [a[k] for k in ks], [h_of[k] for k in ks], [lh_of[k] for k in ks],
                       [prime[k] for k in ks], set(q[1:]))
        return rows[n]

    tops: dict = {}  # found notes (low to high): the highest of them on each peak, and the peaks where
    # the note tested having a peak changes what the found notes read as their own
    preds: dict = {}  # (found notes, the note tested's peaks among those): the shares at their peaks

    def shares(found, m):
        """{peak: the found notes' (a tuple, low to high) predicted share} at their peaks, note m tested (m
        not among them). Read low to high: a note's own amplitude at a peak it shares with lower notes is
        the peak less their predicted shares there; its partials shared with higher found notes or with
        m, or that an octave, a twelfth or two octaves over it would share, are not read."""
        if found not in tops:
            top = {q: n for n in found for q in row(n)[1]}
            tops[found] = (top, {q for n in found for q, p in zip(row(n)[1], row(n)[5]) if p and top[q] == n})
        top, matter = tops[found]
        key = (found, frozenset(matter.intersection(row(m)[1])))
        if key not in preds:
            mine = key[1]
            pred: dict = {}
            for n in found:
                _, qs, As, hs, lhs, pr, _ = row(n)
                xs = [Av - pred.get(q, 0.0) for q, Av in zip(qs, As)] if pred else As
                own = [p and x > 0.0 and top[q] == n and q not in mine for q, x, p in zip(qs, xs, pr)]
                a = [x if o else Av for x, Av, o in zip(xs, As, own)]
                for q, v in zip(qs, _fill(a, own, hs, lhs)):
                    pred[q] = pred.get(q, 0.0) + v
            preds[key] = pred
        return preds[key]

    def check(found):
        """Drop each found note whose fundamental is a lower found note's partial (to `own_partials`) and
        not `stack_ratio` times the predicted share there (low to high). Returns whether any went."""
        went = False
        for m in sorted(found):
            q0 = first[m]
            if q0 == none or not any(n < m and q0 in row(n)[6] for n in found):
                continue
            if row(m)[2][0] < search.stack_ratio * shares(tuple(sorted(found - {m})), m).get(q0, 0.0):
                found.discard(m)
                level_of.pop(m, None)
                went = True
        return went

    found = set(notes)
    check(found)
    for _ in range(2 * search.max_notes):
        if len(found) >= search.max_notes:
            break
        cands = {m for n in found for q in row(n)[0][1:search.stack_partials] if q != none
                 for m in by_fundamental.get(q, ()) if m not in found}
        F = tuple(sorted(found))
        tested = [(m, pred) for m in cands for pred in (shares(F, m),)
                  if not row(m)[2][0] < search.stack_ratio * pred.get(first[m], 0.0)]
        best = None
        if tested:
            ms = [m for m, _ in tested]
            left, gone = [], []  # each one's leftover at each partial (where enough is left), and its missing
            for (_, pred), qs, As in zip(tested, idx[ms, :P].tolist(), A[ms, :P].tolist()):
                lr, gr = [], []
                for q, Av in zip(qs, As):
                    x = Av - pred.get(q, 0.0) if q != none else 0.0
                    has = (x if x > 0.0 else 0.0) > search.stack_left * Av
                    lr.append(x if has else 0.0)
                    # a partial on a shared peak is not missing, whatever is left of it: notes' partials at
                    # one frequency add with their phases, and may cancel
                    gr.append(0.0 if has else (search.stack_shared_miss if q != none and q in pred else 1.0))
                left.append(lr)
                gone.append(gr)
            lw = np.array(left) ** g
            top = lw.max(1)
            level = _top3(lw)
            cost = (search.per_note + search.miss * level * (expect[ms] * np.array(gone)).sum(1)
                    + search.chance * chance[ms])
            for m, l0, t, gc, fm in zip(ms, lw[:, 0].tolist(), top.tolist(), (lw.sum(1) - cost).tolist(),
                                        fund[ms].tolist()):
                if l0 < fm * t:
                    continue
                if gc > 0 and (best is None or gc > best[0]):
                    best = (gc, m, t)
        if best is None:
            break
        found.add(best[1])
        level_of[best[1]] = best[2]
        check(found)
    return {n: v for n, v in level_of.items() if n in found}


def chord_of_notes(notes: list[tuple[int, float]], vocab: Vocabulary | None = None, root_bass: float = 0.05,
                   outside_bass: float = 0.1):
    """The chord the notes make (as `piano_notes` gives them): of the vocabulary's chords (`vocab`, or the
    band's CHORDS), the one whose tones the notes' pitch classes match best (the cosine), `root_bass`
    more when the lowest note is its root, `outside_bass` less when the lowest note is none of its tones.
    (chord, the bass's pitch class, the scores over the vocabulary); (None, None, None) with no notes."""
    if not notes:
        return None, None, None
    chords, T = (CHORDS, TEMPLATES) if vocab is None else (vocab.chords, vocab.templates)
    pcs = np.zeros(12)
    for m, _ in notes:
        pcs[m % 12] = 1.0
    bass = min(m for m, _ in notes) % 12
    s = T @ pcs / np.linalg.norm(pcs)
    s = s + root_bass * (np.array([r for r, _ in chords]) == bass) - outside_bass * (T[:, bass] <= 0)
    return chords[int(np.argmax(s))], bass, s


@dataclass(frozen=True)
class PianoHeard:
    """What a window of piano holds (`hear_piano`)."""
    notes: list  # (MIDI, level), low to high
    chord: tuple | None  # (root, quality) of the vocabulary, None with no notes
    bass: int | None  # the lowest note (MIDI)
    scores: np.ndarray | None  # each of the vocabulary's chords, from the notes


def hear_piano(y: np.ndarray, sr: int, a: float, b: float, vocab: Vocabulary | None = None, rang=None, room=None,
               search: NoteSearch = NoteSearch(), piano: PianoModel = PIANO, min_fft: int = 16384) -> PianoHeard:
    """The notes in y between a and b seconds, the chord they make and its bass: `window_peaks` (less what
    `rang` just before the attack, `quiet_peaks` of the 0.28 s before it, and the `room`'s peaks, if
    learned from a quiet stretch), `piano_notes`, `chord_of_notes`."""
    notes = piano_notes(window_peaks(y, sr, a, b, min_fft, background=room, ringing=rang), search, piano)
    chord, _, s = chord_of_notes(notes, vocab)
    return PianoHeard(notes, chord, notes[0][0] if notes else None, s)


def fold(V: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Semitone saliences folded to the treble and bass chroma (TREBLE_RANGE, BASS_RANGE)."""
    out = []
    for lo, hi in (TREBLE_RANGE, BASS_RANGE):
        C = np.zeros(12)
        for m in range(lo, hi + 1):
            C[m % 12] += V[m - LO]
        out.append(C)
    return out[0], out[1]


INVERSION_TONE = 0.85  # with inversions, a chord's other tones in the bass count this much against its root


def _templates(chords=CHORDS):
    T = np.zeros((len(chords), 12))
    B = np.zeros((len(chords), 12))
    BI = np.zeros((len(chords), 12))
    for i, (r, q) in enumerate(chords):
        iv = ALL_QUALITIES[q]
        for d in iv:
            T[i, (r + d) % 12] = 1.0
            BI[i, (r + d) % 12] = INVERSION_TONE
        B[i, r] = BI[i, r] = 1.0
        B[i, (r + iv[2]) % 12] = max(B[i, (r + iv[2]) % 12], 0.4)
        for d in iv[1:2] + iv[3:]:
            B[i, (r + d) % 12] = max(B[i, (r + d) % 12], 0.2)
    return T / np.linalg.norm(T, axis=1, keepdims=True), B, BI


TEMPLATES, BASS_WEIGHTS, INVERSION_BASS_WEIGHTS = _templates()


class Vocabulary:
    """A set of chord qualities (of `ALL_QUALITIES`) on all 12 roots, with its templates, for
    `chord_scores(..., vocab=)`: the band keeps `CHORDS`; the chord app hears more."""

    def __init__(self, qualities=("maj", "min", "7", "dim", "dim7")):
        self.qualities = tuple(qualities)
        self.chords = [(r, q) for q in self.qualities for r in range(12)]
        self.templates, self.bass_weights, self.inversion_bass_weights = _templates(self.chords)


def chord_scores(treble: np.ndarray, bass: np.ndarray | None, w_bass: float = 0.5,
                 inversions: bool = False, vocab: Vocabulary | None = None) -> np.ndarray:
    """Each of CHORDS: the treble chroma's cosine with its tones, plus `w_bass` times the bass chroma's
    share on its root, fifth, third and seventh (root position expected: 1, 0.4, 0.2, 0.2). With
    `inversions`, any of its tones in the bass counts nearly as much as the root (`INVERSION_TONE`), so
    the bass says only that it is the chord's, and the treble says which chord (C/E against Em).
    `vocab`: score its chords (`vocab.chords`) in place of `CHORDS`."""
    T, B, BI = (TEMPLATES, BASS_WEIGHTS, INVERSION_BASS_WEIGHTS) if vocab is None else (
        vocab.templates, vocab.bass_weights, vocab.inversion_bass_weights)
    s = T @ (treble / (np.linalg.norm(treble) + 1e-12))
    if bass is not None and w_bass:
        s = s + w_bass * ((BI if inversions else B) @ (bass / (bass.sum() + 1e-12)))
    return s


# --- the key ---------------------------------------------------------------------------------------


class KeyGuess:
    """The key from the pitch classes heard so far: their running sum (each beat's chromas, normalised)
    against the 24 Krumhansl-Kessler profiles. `confidence` is the margin of the best key's correlation
    over the next one's not sharing its scale (a relative major or minor shares it: the band plays the
    same chords in both), over `margin`, capped at 1; nothing before `min_beats`."""

    def __init__(self, min_beats: int = 8, margin: float = 0.15):
        self.sum = np.zeros(12)
        self.n = 0
        self.min_beats, self.margin = min_beats, margin

    def hear(self, treble: np.ndarray, bass: np.ndarray) -> None:
        for c in (treble, bass):
            self.sum += c / (c.sum() + 1e-12)
        self.n += 1

    def guess(self) -> tuple[tuple[int, str] | None, float]:
        if self.n < self.min_beats or self.sum.sum() == 0:
            return None, 0.0
        x = self.sum - self.sum.mean()
        rs = []
        for t in range(12):
            for mode, prof in (("major", KK_MAJOR), ("minor", KK_MINOR)):
                p = np.roll(prof, t) - prof.mean()
                rs.append((float(x @ p / (np.linalg.norm(x) * np.linalg.norm(p) + 1e-12)), t, mode))
        rs.sort(reverse=True)
        r1, t1, m1 = rs[0]
        scale = (t1 if m1 == "major" else (t1 + 3) % 12)  # the relative major's tonic
        other = next(r for r, t, m in rs[1:] if (t if m == "major" else (t + 3) % 12) != scale)
        return (t1, m1), float(min(1.0, max(0.0, (r1 - other) / self.margin)))


class ChordKey:
    """The key from the chords heard so far, as bluegrass spends its time: the tonic is the root heard
    most, among beats whose chord was heard surely (`sure`), its mode that root's commonest third. The
    guess holds until another root has led for `turn` beats in a row (a tune can sit on V for bars:
    Down Yonder's D bars pulled the pitch-class profile toward D major). `confidence`: the leading root's
    share over the next one's, over `margin`, capped at 1; nothing before `min_beats` sure beats."""

    def __init__(self, min_beats: int = 8, sure: float = 0.6, margin: float = 0.25, turn: int = 8):
        self.counts = np.zeros((12, 2))  # per root: major (and seventh), minor
        self.n = 0
        self.min_beats, self.sure, self.margin, self.turn = min_beats, sure, margin, turn
        self.held: tuple[int, str] | None = None
        self.led = 0

    def hear(self, chord: tuple[int, str] | None, conf: float) -> None:
        if chord is None or conf < self.sure:
            return
        self.counts[chord[0], 1 if chord[1] == "min" else 0] += 1
        self.n += 1
        top = int(np.argmax(self.counts.sum(axis=1)))
        if self.held is None or top == self.held[0]:
            self.led = 0
        else:
            self.led += 1
        if self.n >= self.min_beats and (self.held is None or self.led >= self.turn):
            self.held = (top, "minor" if self.counts[top, 1] > self.counts[top, 0] else "major")
            self.led = 0

    def guess(self) -> tuple[tuple[int, str] | None, float]:
        if self.held is None:
            return None, 0.0
        tot = self.counts.sum(axis=1)
        mine = tot[self.held[0]]
        other = np.max(np.delete(tot, self.held[0]))
        return self.held, float(min(1.0, max(0.0, (mine - other) / (self.n * self.margin + 1e-12))))


def chart_key(chart: list[tuple[int, str]]) -> tuple[int, str]:
    """A chart's key: its commonest root, major unless that root is mostly minor."""
    roots = np.zeros(12)
    minor = np.zeros(12)
    for r, q in chart:
        roots[r] += 1
        minor[r] += q == "min"
    t = int(np.argmax(roots))
    return t, "minor" if minor[t] > roots[t] / 2 else "major"


def key_chords(key: tuple[int, str]) -> set[tuple[int, str]]:
    """The bluegrass chords of a key (a minor key: those of its relative major)."""
    tonic, mode = key
    major = tonic if mode == "major" else (tonic + 3) % 12
    return {((major + d) % 12, q) for d, q in BLUEGRASS}


# --- reacting: the chord of each beat --------------------------------------------------------------


class BeatChords:
    """A forward HMM over CHORDS, one step a beat: the chord each beat's audio says, given the beats
    before. A change is likelier on the bar's 1 and 3 when the bar is known. With a key, its bluegrass
    chords get `key_bonus` on their scores. `beta` sharpens the scores into likelihoods."""

    def __init__(self, beats_per_bar: int = 4, beta: float = 15.0, key_bonus: float = 0.05):
        self.post = np.full(len(CHORDS), 1 / len(CHORDS))
        self.bpb, self.beta, self.key_bonus = beats_per_bar, beta, key_bonus

    def hear(self, scores: np.ndarray, beat_in_bar: int | None, key: tuple[int, str] | None = None):
        """One beat heard; returns (chord, probability)."""
        p = CHANGE_BY_BEAT.get(self.bpb, ())[beat_in_bar - 1] if beat_in_bar else CHANGE_UNKNOWN
        n = len(CHORDS)
        prior = (1 - p) * self.post + p * (1 - self.post) / (n - 1)
        s = scores.copy()
        if key is not None:
            s += self.key_bonus * np.array([c in key_chords(key) for c in CHORDS])
        e = np.exp(self.beta * (s - s.max()))
        post = prior * e
        self.post = post / post.sum()
        k = int(np.argmax(self.post))
        return CHORDS[k], float(self.post[k])


# --- following a chart -----------------------------------------------------------------------------


class ChartFollower:
    """Where in a chart (one chord per beat, a pass of the tune) the player is: a forward HMM over the
    chart's beats. Each beat heard weighs every place by its chord's likelihood; then the place moves on
    a beat (looping at the end), holds (`stay`), or jumps to any bar's first beat (`jump`). `predict`
    gives the chord at the likeliest next place and its probability (the share of the belief on places
    holding that chord). `start`: the chart beat the playing starts on (None: any bar's first beat).
    The study's settings: beta 10, jump and stay 0.01 (dev rows 0.91-0.95 over beta 5-25, jump
    0.002-0.05, stay 0-0.01)."""

    def __init__(self, chart: list[tuple[int, str]], beats_per_bar: int = 4, start: int | None = 0,
                 beta: float = 10.0, jump: float = 0.01, stay: float = 0.01):
        self.chart = [(r, majmin(q) if q not in QUALITIES else q) for r, q in chart]
        self.bpb, self.beta, self.jump, self.stay = beats_per_bar, beta, jump, stay
        M = len(self.chart)
        self.bar_starts = np.arange(0, M, beats_per_bar)
        self.post = np.zeros(M)
        if start is None:
            self.post[self.bar_starts] = 1 / len(self.bar_starts)
        else:
            self.post[start % M] = 1.0
        self.idx = [CHORDS.index(c) if c in CHORDS else None for c in self.chart]

    def predict(self) -> tuple[tuple[int, str], float, int]:
        """The chord to play on the coming beat, its probability, and the likeliest place."""
        j = int(np.argmax(self.post))
        c = self.chart[j]
        return c, float(sum(self.post[k] for k in range(len(self.chart)) if self.chart[k] == c)), j

    def hear(self, scores: np.ndarray) -> None:
        """The beat just played heard (its scores over CHORDS); the belief moves on to the next beat."""
        e = np.exp(self.beta * (scores - scores.max()))
        e = e / e.sum()
        like = np.array([e[k] if k is not None else 1e-3 for k in self.idx]) + 1e-4
        post = self.post * like
        post /= post.sum()
        nxt = (1 - self.jump - self.stay) * np.roll(post, 1) + self.stay * post
        nxt[self.bar_starts] += self.jump / len(self.bar_starts)
        self.post = nxt / nxt.sum()


# --- the pulse and the loop, as heard (no chart) ---------------------------------------------------


class Pulse:
    """A steady pulse in the playing, as it arrives (`hear` each block of mono samples). An onset
    envelope: each 10 ms frame's log energy over the mean of the three before it, the rises only. Every
    `every_s`, its autocorrelation over the last `span_s` (the mean taken out, normalised), and the
    strongest peak between `min_s` and `max_s`: the period, or half of it when the half is nearly as
    strong (a faster pulse heard in it). Steady once the peak reaches `sure` and the last `hold`
    estimates agree within 4%. The beat (`estimate`) is the period, doubled or halved into 70-140 bpm:
    a chord struck every 2.6 s is 92 bpm, four beats a chord."""

    def __init__(self, rate: int, span_s: float = 12.0, every_s: float = 0.5, min_s: float = 0.25,
                 max_s: float = 4.0, sure: float = 0.3, hold: int = 3):
        self.rate, self.hop = rate, max(1, rate // 100)
        self.span = int(span_s * 100)
        self.every, self.min_k, self.max_k = int(every_s * 100), int(min_s * 100), int(max_s * 100)
        self.sure, self.hold = sure, hold
        self.left = np.zeros(0)
        self.loud: list[float] = []  # each frame's log energy (the last few)
        self.env: list[float] = []
        self.since = 0
        self.periods: list[tuple[float, float]] = []  # (period s, peak), each estimate
        self.steady: dict | None = None
        self.frames = 0  # frames heard, all told: the envelope's clock

    def hear(self, x: np.ndarray) -> None:
        x = np.concatenate([self.left, np.asarray(x, dtype=float)])
        n = len(x) // self.hop
        self.left = x[n * self.hop:]
        for f in x[: n * self.hop].reshape(n, self.hop) if n else []:
            e = float(np.log(np.mean(f ** 2) + 1e-10))
            prev = self.loud[-3:]
            self.env.append(max(0.0, e - (sum(prev) / len(prev) if prev else e)))
            self.loud = (self.loud + [e])[-3:]
            self.frames += 1
            self.since += 1
            if self.since >= self.every:
                self.since = 0
                self._estimate()
        self.env = self.env[-self.span:]

    def _estimate(self) -> None:
        e = np.array(self.env[-self.span:])
        if len(e) < 3 * self.min_k or not e.any():
            return
        e = e - e.mean()
        den = float(np.dot(e, e)) or 1.0
        hi = min(self.max_k, len(e) // 2)
        if hi <= self.min_k:
            return
        r = np.array([float(np.dot(e[:-k], e[k:])) / den for k in range(1, hi + 1)])  # r[k-1]: lag k frames
        lags = [k for k in range(self.min_k, hi) if r[k - 1] >= r[k - 2] and r[k - 1] >= r[k]]
        if not lags:
            return
        k = max(lags, key=lambda q: r[q - 1])
        half = k // 2
        if half >= self.min_k and r[half - 1] >= 0.85 * r[k - 1]:
            k = half
        self.periods = (self.periods + [(k / 100.0, float(r[k - 1]))])[-self.hold:]
        p = [q for q, _ in self.periods]
        ok = len(p) == self.hold and all(h >= self.sure for _, h in self.periods) and max(p) <= 1.04 * min(p)
        if ok:
            period = float(np.mean(p))
            bpm, per = 60.0 / period, 1.0
            while bpm < 70:
                bpm, per = bpm * 2, per * 2
            while bpm >= 140:
                bpm, per = bpm / 2, per / 2
            # where the pulse's strikes fall: the offset whose every period back holds the most onset
            kp = int(round(period * 100))
            n = len(self.env)
            env = np.array(self.env)
            best = max(range(kp), key=lambda o: float(env[n - 1 - o::-kp].sum()))
            last_s = (self.frames - 1 - best) / 100.0  # the last strike, on the envelope's clock
            beat = period / per
            self.steady = {"bpm": round(bpm, 1), "period_s": round(period, 3), "beats_per_pulse": per,
                           "strength": round(float(np.mean([h for _, h in self.periods])), 3),
                           "beat_s": round(beat, 4), "last_strike_s": round(last_s, 2)}
        elif self.periods and self.periods[-1][1] < self.sure:
            self.steady = None

    def estimate(self) -> dict | None:
        """{"bpm", "period_s", "beats_per_pulse", "strength", "beat_s", "last_strike_s"} while the pulse
        is steady, else None. `last_strike_s` is on the clock of the audio heard (its first sample 0)."""
        return self.steady

    def now_s(self) -> float:
        """The time of the audio heard so far, on the same clock."""
        return self.frames / 100.0


class LoopFinder:
    """A loop of chords from the chord heard over time (`hear(t, chord)` a few times a second, a name or
    None). A chord counts as a change once it has held `min_s`, so a passing misreading is not one; the
    loop is the shortest cycle of 2 to `max_len` changes that the last two rounds repeat exactly. It
    holds while the next changes match it (`misses` wrong in its last two rounds are let pass), till
    another loop is found the same way, and goes after `quiet_s` without a chord. `state()`: the loop, the chord the playing is on, the rounds heard and
    each chord's mean length."""

    def __init__(self, min_s: float = 0.5, max_len: int = 8, misses: int = 1, quiet_s: float = 4.0):
        self.min_s, self.max_len, self.misses_ok, self.quiet_s = min_s, max_len, misses, quiet_s
        self.cur: list | None = None  # [chord, start, last seen]
        self.changes: list[tuple[str, float]] = []  # (chord, start)
        self.loop: list[str] | None = None
        self.pos = 0  # the loop's chord the playing is on
        self.rounds = 0
        self.missed = 0
        self.recent: list[bool] = []  # whether each change lately was the one the loop had next
        self.last_chord_t: float | None = None

    def hear(self, t: float, chord: str | None) -> None:
        if chord is None:
            if self.last_chord_t is not None and t - self.last_chord_t > self.quiet_s:
                self.changes, self.loop, self.cur = [], None, None
            return
        self.last_chord_t = t
        if self.cur is None or self.cur[0] != chord:
            self.cur = [chord, t, t]
        else:
            self.cur[2] = t
        c, start, seen = self.cur
        if seen - start >= self.min_s and (not self.changes or self.changes[-1][0] != c or self.changes[-1][1] != start):
            if self.changes and self.changes[-1][0] == c:
                return  # the same chord again after a passing misreading: not a change
            self.changes = (self.changes + [(c, start)])[-4 * self.max_len:]
            self._next(c)

    def _next(self, c: str) -> None:
        if self.loop is not None:
            want = self.loop[(self.pos + 1) % len(self.loop)]
            self.recent = (self.recent + [c == want])[-2 * len(self.loop):]
            if c == want:
                self.pos = (self.pos + 1) % len(self.loop)
                self.missed = 0
                if self.pos == 0:
                    self.rounds += 1
            else:
                self.missed += 1
            if self.missed <= self.misses_ok and self.recent.count(False) <= self.misses_ok:
                found = self._find()
                if found is None or self._same(found[0]):
                    return
            self.loop = None  # it has gone (another chord too often in two rounds), or another has come
        found = self._find()
        if found is not None:
            self.loop, self.pos, self.rounds = found
            self.missed, self.recent = 0, []

    def _find(self) -> tuple[list[str], int, int] | None:
        """The shortest loop the last two rounds of changes repeat exactly: (it, the chord on, rounds)."""
        names = [n for n, _ in self.changes]
        for p in range(2, self.max_len + 1):
            if len(names) >= 2 * p and names[-p:] == names[-2 * p:-p] and len(set(names[-p:])) > 1:
                r = 2
                while len(names) >= (r + 1) * p and names[-(r + 1) * p:-r * p] == names[-p:]:
                    r += 1
                return names[-p:], p - 1, r
        return None

    def _same(self, other: list[str]) -> bool:
        p = len(self.loop)
        return len(other) == p and any(other == self.loop[i:] + self.loop[:i] for i in range(p))

    def state(self) -> dict | None:
        if self.loop is None:
            return None
        p = len(self.loop)
        lengths = [[] for _ in range(p)]
        starts = [s for _, s in self.changes]
        for i in range(len(self.changes) - 1):
            k = (self.pos - (len(self.changes) - 1 - i)) % p  # the loop slot of change i
            if self.changes[i][0] == self.loop[k]:
                lengths[k].append(starts[i + 1] - starts[i])
        return {"chords": list(self.loop), "at": self.pos, "rounds": self.rounds,
                "lengths_s": [round(float(np.mean(v)), 2) if v else None for v in lengths]}


def chord_from_weights(w: np.ndarray, bass, vocab: Vocabulary, allowed=None) -> tuple[tuple[int, str], float]:
    """The vocabulary's chord that best fits pooled pitch-class weights `w` (12): the share of the weight on
    its tones, less half the share off them and a quarter for each of its tones hardly there; a little
    for its root in the bass (`bass`: a pitch class, None, or a function of the chord giving its bass).
    Only chords `allowed` (a function of the chord) if given. (chord, that fit, 0-1)."""
    total = float(w.sum())
    if total <= 0:
        return vocab.chords[0], 0.0
    top = float(w.max())
    best, best_s = vocab.chords[0], -9.0
    for c in vocab.chords:
        if allowed is not None and not allowed(c):
            continue
        t = {(c[0] + d) % 12 for d in ALL_QUALITIES[c[1]]}
        on = float(sum(w[k] for k in t))
        missing = sum(1 for k in t if w[k] < 0.15 * top)
        b = bass(c) if callable(bass) else bass
        sc = on / total - 0.5 * (total - on) / total - 0.25 * missing / len(t) + (0.05 if b == c[0] else 0.0)
        if sc > best_s:
            best, best_s = c, sc
    return best, max(0.0, min(1.0, best_s))


class ChordPool:
    """The chord over the last while (about a bar), from the notes heard moment by moment (`hear` each
    frame with `notes_heard`'s (MIDI, level)): each note's level times the frame's length, pooled by pitch,
    fading with time constant `tau_s` (`set_tau`: half a bar once the tempo is known).
    A note outside the chord shown, as it starts (`fresh_level` or louder, and not heard the frame
    before, or then faint and since risen half again: an attack), starts a new chord. From then until a
    chord has been named, or such a note last started, `settle_s` before:
    - what was pooled before it and is no longer heard fades fast (`fade_s`; and drops to `fresh` as the
      note starts), so the chord before does not linger in the new one (F's residue making G a G7),
      while what still rings (the pedal down) stays, and what has started since (an arpeggio's notes,
      each starting anew) is pooled;
    - a chord other than the one shown is taken only with two of its notes started since, or what it
      adds to the one shown started and the rest of it sounding (Am to F, A and C held; C to C7), so an
      arpeggio's first note, over what is left of the chord before, does not name a third chord (G3
      over Am's last as A7, or C/G).
    The chord is the vocabulary's that best fits the pitch classes (`chord_from_weights`). It names a
    new chord only once that has led for `hold_s`. Its bass: the lowest of its notes started since it
    began, in the `bass_s` up to the last of them started (so a chord held keeps its bass), where it
    began being where the run of notes started that all belong to it goes back to as it is named, but
    not before the note that started the change: an arpeggio's first, lowest note, though it has faded,
    and not the chord before's (its bass, a moment late, belonging to the new chord as well).
    `state()`: the chord, its bass, and how sure (its fit, times how much has been heard, `enough`
    level-seconds for all of it, times the share of its tones there: one note of a triad, a third);
    None before anything is heard."""

    def __init__(self, vocab: Vocabulary, tau_s: float = 0.8, hold_s: float = 0.4, enough: float = 0.6,
                 fresh: float = 0.3, fresh_level: float = 0.3, fade_s: float = 0.25, settle_s: float = 1.0,
                 bass_s: float = 4.0):
        self.vocab, self.tau, self.hold, self.enough = vocab, tau_s, hold_s, enough
        self.fresh, self.fresh_level, self.fade, self.settle, self.bass_s = fresh, fresh_level, fade_s, settle_s, bass_s
        self.wm: dict[int, float] = {}  # MIDI -> pooled weight
        self.starts: list[tuple[float, int]] = []  # (time, MIDI): the notes started, lately
        self.last: dict[int, float] = {}  # the notes of the frame before, and their levels
        self.t = 0.0
        self.shown: tuple[int, str] | None = None
        self.since = 0.0  # when the chord shown began
        self.named = 0.0  # when it was named
        self.mine: list[tuple[float, int]] = []  # (time, MIDI): its notes started since
        self.first_fresh: float | None = None  # when a note first started a new chord, till one settles
        self.last_fresh = 0.0  # and when one last did
        self.lead: tuple[tuple[int, str], float] | None = None  # a chord leading, since when
        self.fit = 0.0

    def set_tau(self, tau_s: float) -> None:
        self.tau = max(0.3, float(tau_s))

    def weights(self) -> np.ndarray:
        w = np.zeros(12)
        for m, x in self.wm.items():
            w[m % 12] += x
        return w

    def hear(self, t: float, notes: list[tuple[int, float]], frame_s: float = 0.1) -> None:
        k = np.exp(-frame_s / self.tau)
        level = dict(notes)
        sounding = {m for m, x in notes if x >= self.fresh_level}
        started = sorted(m for m in sounding if m not in self.last or (
            self.last[m] < self.fresh_level and level[m] >= 1.5 * self.last[m]))
        fresh = any(self.shown is None or m % 12 not in tones(self.shown) for m in started)
        if self.first_fresh is not None and t - max(self.last_fresh, self.named) >= self.settle:
            self.first_fresh = None  # a chord has settled
        if fresh:
            if self.first_fresh is None or self.first_fresh < self.named:
                self.first_fresh = t  # since the chord shown was named: a new change
            self.last_fresh = t
        if self.first_fresh is not None:  # a new chord: what was before it, no longer heard, fades fast
            since = {m for a, m in self.starts if a >= self.first_fresh} | set(started)
            gone = {m for m in self.wm if m not in level and m not in since}
            drop = np.exp(-frame_s / self.fade)
            self.wm = {m: x * (drop * (self.fresh if fresh else 1.0) if m in gone else 1.0) for m, x in self.wm.items()}
        self.wm = {m: x * k for m, x in self.wm.items() if x * k > 1e-4}
        for m, x in notes:
            self.wm[m] = self.wm.get(m, 0.0) + x * frame_s
        self.t, self.last = t, level
        self.starts = [(a, m) for a, m in self.starts if t - a <= self.bass_s] + [(t, m) for m in started]
        w = self.weights()
        if w.sum() < 0.05 * self.enough:  # hardly anything heard lately: no chord, and if quiet, none before
            self.shown, self.lead, self.since, self.mine, self.first_fresh = None, None, t, [], None
            if not notes:
                self.wm = {}
            return
        bound = self.since if self.first_fresh is None else self.first_fresh

        def low(c):
            if c == self.shown:
                return self.bass()
            since = self.began(c, bound)
            ms = [m for a, m in self.starts if a >= since and m % 12 in tones(c)]
            return min(ms) % 12 if ms else None

        allowed = None
        if self.first_fresh is not None and self.shown is not None:
            # a new chord only with two of its notes started since one began, or what it adds to the chord
            # shown started and the rest sounding
            new = {m % 12 for a, m in self.starts if a >= self.first_fresh}
            was, now = tones(self.shown), {m % 12 for m in sounding}

            def allowed(c):
                adds = tones(c) - was
                return (c == self.shown or len(tones(c) & new) >= 2
                        or (bool(adds) and adds <= new and tones(c) - adds <= new | now))

        c, self.fit = chord_from_weights(w, low, self.vocab, allowed)
        if c == self.shown:
            self.lead = None
            if not fresh:
                self.mine = (self.mine + [(t, m) for m in started if m % 12 in tones(c)])[-64:]
        elif self.shown is None or (self.lead is not None and self.lead[0] == c and t - self.lead[1] >= self.hold):
            self.shown, self.lead, self.named = c, None, t
            self.since = self.began(c, bound)
            self.mine = [(a, m) for a, m in self.starts if a >= self.since and m % 12 in tones(c)]
            if not self.mine:  # none of its notes started lately: the lowest of them sounding
                self.mine = [(t, m) for m in sorted(sounding) if m % 12 in tones(c)][:1]
        elif self.lead is None or self.lead[0] != c:
            self.lead = (c, t)

    def began(self, chord: tuple[int, str], bound: float) -> float:
        """Where the run of notes started lately that all belong to `chord` goes back to, not before `bound`."""
        inside, since = tones(chord), self.t
        for a, m in reversed(self.starts):
            if a < bound or m % 12 not in inside:
                break
            since = a
        return since

    def bass(self) -> int | None:
        if not self.mine:
            return None
        latest = self.mine[-1][0]  # held, it stays; played on, the notes of the last `bass_s`
        return min(m for a, m in self.mine if latest - a <= self.bass_s) % 12

    def state(self) -> dict | None:
        if self.shown is None:
            return None
        heard = min(1.0, float(sum(self.wm.values())) / self.enough)
        w = self.weights()
        t = tones(self.shown)
        there = sum(1 for k in t if w[k] >= 0.15 * w.max()) / len(t) if w.max() > 0 else 0.0
        return {"chord": self.shown, "bass": self.bass(), "confidence": round(self.fit * heard * there, 3)}
