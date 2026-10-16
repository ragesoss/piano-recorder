# Copied from dobro-transcription pipeline/src/transcribe/livechords.py (d0054ee) by tools/sync-livechords.
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
    r, q = chord
    return {(r + d) % 12 for d in QUALITIES[q]}


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
