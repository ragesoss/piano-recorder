# Copied from dobro-transcription pipeline/src/transcribe/livechords.py (616e8d5) by tools/sync-livechords.
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
