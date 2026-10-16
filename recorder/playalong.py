"""After a play-along take: where the backing sits in it, and the take with
the backing taken out.

On a device with loopback (the BMG-04F, T7) the capture holds the backing
electrically, through a short, fixed path: the mixer's channel 3/4, its main
mix and its converter. The first bass takes (2026-10-12) showed it only
at about -41 dBFS before the bass came in, partly through the air (the speakers
into the bass body, under the Nadine pickup). Either way the backing
as played (after the loudness gain and the level offset) is found in the take
by cross-correlation, to the sample, and a short filter fitted to its path
predicts it, to be subtracted. Through the air the path is long and changes
as the player moves, so little of it comes out. And on the Pi 3 the USB can
drop pieces of the playback during a take (29 in the second bass take: "FIQ
reported NYET"): the backing then slides in the take, and neither lining up
nor subtraction holds past the first loss.

A click (an exercise take's) is lined up differently: a click track repeats every bar,
so a search over the whole take finds it almost as well a bar or two off (`find_click`).
It is not taken out of the take.

Numpy only (the control unit's venv has no scipy).
"""

from dataclasses import dataclass

import numpy as np

from . import dsp

# A correlation peak this many times the median of the others is the backing. Noise alone
# reaches about 6 in a 1 s search and 19 in the 12 s one (-2 to +10 s); the first bass takes
# (2026-10-12) peaked at 277 and 59 at their true offsets.
FOUND_RATIO = 30.0

# A click is searched for only this far into the take, or one beat if that is shorter: the
# engine starts the playback once the capture is running, and the click's first beat arrives
# 160-190 ms in on the Pi with the BMG-04F (six exercise takes, 2026-10-13), 300-420 ms through
# the 18i8 (through the air to the room microphone; 2026-10-14/15). A search over the whole take
# (-2 to +10 s, as for a backing) put four of those six 8-12 beats late: a click track correlates
# with itself shifted by a bar (or a beat) almost as well as unshifted, and the noise decides. A
# window under one beat wide holds only one of those candidates.
CLICK_SEARCH_S = 0.6
# ...over the click's first seconds only: a count-in bar and a few beats are plenty, and the
# Pi 3 took 23-27 s to line up and clean a whole take (35 ms on the desktop for this).
CLICK_EXCERPT_S = 8.0
# The click is found by its onsets in its own two tones' bands (1.5 and 2 kHz), every millisecond,
# in all of the take's channels, then placed to the sample by the phase-transform correlation
# within `CLICK_REFINE_S` of that. The phase transform alone weighs every frequency alike, and the
# click is two narrow tones in a take that is mostly bass: through the 18i8, where only the air
# carries the click, it put 13 of 14 takes at 57-200 ms for 300-420, eight of them as found.
CLICK_BANDS = ((1350.0, 1650.0), (1850.0, 2150.0))
CLICK_HOP_S = 0.001
CLICK_REFINE_S = 0.002
# Found: the take has an onset within `CLICK_TOLERANCE_S` of where the lag puts the click's beats
# (each beat's strongest onset within 40 ms, against their median) for at least `CLICK_ON_TIME` of
# them, and the onsets' correlation peak is `CLICK_RATIO` times its median. 15 takes through the
# 18i8: 0.73-1.00 on time, ratio 3.6-6.9; 11 through the BMG-04F: 1.00, 26-32; a click track at
# the wrong tempo (26 cases): at most 0.75 on time, the only one over 0.7 at ratio 2.4; noise 0.27.
CLICK_TOLERANCE_S = 0.015
CLICK_ON_TIME = 0.7
CLICK_RATIO = 3.0


@dataclass
class Alignment:
    lag: int  # the backing's sample 0 is at this capture sample
    rate: int
    peak_ratio: float  # how far the correlation peak stands above its surroundings
    backing_dbfs: float | None  # the backing as it sits in the take (rms over the backing)
    left_dbfs: float | None  # what the fit still finds of it after subtraction
    taps: int | None  # None for a click: not fitted or subtracted
    on_time: float | None = None  # a click: the share of its beats the take has an onset at
    found_: bool | None = None  # a click: decided by `find_click` (else by `peak_ratio`)

    @property
    def lag_ms(self) -> float:
        return 1000.0 * self.lag / self.rate

    @property
    def found(self) -> bool:
        return self.found_ if self.found_ is not None else self.peak_ratio >= FOUND_RATIO

    def as_dict(self) -> dict:
        d = {"lag_samples": self.lag, "lag_ms": round(self.lag_ms, 2), "rate": self.rate,
             "found": self.found, "peak_ratio": round(self.peak_ratio, 1),
             "backing_dbfs": None if self.backing_dbfs is None else round(self.backing_dbfs, 1),
             "left_dbfs": None if self.left_dbfs is None else round(self.left_dbfs, 1),
             "taps": self.taps}
        if self.on_time is not None:
            d["clicks_on_time"] = round(self.on_time, 2)
        return d


def _next_pow2(n: int) -> int:
    return 1 << (int(n) - 1).bit_length()


def find_lag(take: np.ndarray, played: np.ndarray, max_lag: int, min_lag: int = 0) -> tuple[int, float]:
    """The capture sample where `played` starts, by phase-transform cross-correlation
    (sharp peaks whatever the backing's spectrum), searched from min_lag (it may be negative:
    the backing can start before the capture's first sample) to max_lag. Returns the lag and
    how far its peak stands above the median of the others."""
    n = _next_pow2(len(take) + len(played) + abs(min(min_lag, 0)))
    X = np.fft.rfft(take.astype(np.float64), n)
    P = np.fft.rfft(played.astype(np.float64), n)
    cross = X * np.conj(P)
    cross /= np.abs(cross) + 1e-12
    cc = np.abs(np.fft.irfft(cross, n))
    lags = np.arange(min_lag, max_lag + 1)
    window = cc[lags % n]
    k = int(np.argmax(window))
    floor = float(np.median(window)) + 1e-12
    return int(lags[k]), float(window[k] / floor)


def _placed(played: np.ndarray, start: int, n: int) -> np.ndarray:
    """`played` laid into n samples starting at `start` (which may be negative)."""
    out = np.zeros(n, dtype=np.float64)
    src0, dst0 = max(0, -start), max(0, start)
    m = min(len(played) - src0, n - dst0)
    if m > 0:
        out[dst0:dst0 + m] = played[src0:src0 + m]
    return out


def fit_path(take: np.ndarray, played: np.ndarray, lag: int, taps: int = 64, pre: int = 8) -> np.ndarray:
    """Least-squares filter h (`taps` long, starting `pre` samples before the lag) with
    take[n] ~ sum_k h[k] played[n - lag + pre - k]. From the auto- and cross-correlations
    (Wiener-Hopf), so it does not need the samples in memory twice. Short on purpose: a
    longer path fitted to the first bass takes (2026-10-12) took out no more of the backing
    (5-7 dB even fitted to the seconds before the bass came in) and some of the bass."""
    n = len(take)
    shifted = _placed(played, lag - pre, n)
    N = _next_pow2(2 * n)
    S = np.fft.rfft(shifted, N)
    auto = np.fft.irfft(S * np.conj(S), N)[:taps]
    cross = np.fft.irfft(np.fft.rfft(take.astype(np.float64), N) * np.conj(S), N)[:taps]
    R = auto[np.abs(np.subtract.outer(np.arange(taps), np.arange(taps)))]
    R[np.diag_indices(taps)] *= 1.0 + 1e-6  # a little regularization
    return np.linalg.solve(R, cross)


def predict(played: np.ndarray, h: np.ndarray, lag: int, n: int, pre: int = 8) -> np.ndarray:
    """The backing as the take holds it: `played` through h, placed at the lag."""
    y = np.convolve(played.astype(np.float64), h)
    return _placed(y, lag - pre, n)


def align_and_clean(take: np.ndarray, played: np.ndarray, rate: int, max_lag_s: float = 10.0,
                    taps: int = 64, pre: int = 8) -> tuple[Alignment, np.ndarray]:
    """Find the backing in a take and subtract it. Returns the alignment and the cleaned
    take (float32). A take with no backing found comes back unchanged. The search runs from
    2 s before the capture's first sample (the second bass take's backing began 73 ms before
    it, 2026-10-12) to 10 s after."""
    take = take.astype(np.float64)
    lag, ratio = find_lag(take, played, int(max_lag_s * rate), -int(min(2.0, max_lag_s) * rate))
    if ratio < FOUND_RATIO:
        return Alignment(lag, rate, ratio, -240.0, -240.0, taps), take.astype(np.float32)
    h = fit_path(take, played, lag, taps, pre)
    echo = predict(played, h, lag, len(take), pre)
    clean = take - echo
    span = slice(max(lag, 0), min(lag + len(played), len(take)))
    h2 = fit_path(clean, played, lag, taps, pre)  # what a second fit still finds
    left = predict(played, h2, lag, len(take), pre)
    al = Alignment(lag, rate, ratio, dsp.rms_dbfs(echo[span]), dsp.rms_dbfs(left[span]), taps)
    return al, clean.astype(np.float32)


def click_onsets(x: np.ndarray, rate: int) -> tuple[np.ndarray, int]:
    """Onset strength in the click's bands (`CLICK_BANDS`), every `CLICK_HOP_S`: the rise of each
    band's log energy over a ~11 ms window (bands x frames), and the hop in samples."""
    hop = max(1, int(round(CLICK_HOP_S * rate)))
    n_fft = _next_pow2(int(0.0107 * rate))
    x = np.concatenate([np.asarray(x, dtype=np.float32), np.zeros(n_fft, dtype=np.float32)])
    frames = np.lib.stride_tricks.sliding_window_view(x, n_fft)[: len(x) - n_fft: hop] * np.hanning(n_fft).astype(np.float32)
    S = np.abs(np.fft.rfft(frames, axis=1)) ** 2
    f = np.fft.rfftfreq(n_fft, 1.0 / rate)
    out = []
    for lo, hi in CLICK_BANDS:
        e = np.log(S[:, (f >= lo) & (f < hi)].sum(axis=1) + 1e-12)
        out.append(np.maximum(0.0, np.diff(e, prepend=e[:1])))
    return np.array(out), hop


def find_click(take: np.ndarray, click: np.ndarray, rate: int, beat_s: float) -> Alignment:
    """Where a click track (`click_track`) starts in a take (one channel, or several: frames x
    channels), searched from the take's first sample to `CLICK_SEARCH_S` in, or one beat if that
    is shorter, so the click's beats either side of the true one fall outside the search; over
    the click's first `CLICK_EXCERPT_S`. Found by its onsets in its own bands, placed to the sample
    by the phase-transform correlation near there (`CLICK_*`)."""
    take = take[:, None] if take.ndim == 1 else take
    max_lag = max(1, int(min(CLICK_SEARCH_S, beat_s) * rate) - 1)
    m = min(len(click), int(CLICK_EXCERPT_S * rate))
    seg = take[: m + max_lag + 1]
    fc, hop = click_onsets(click[:m], rate)
    ft = sum(click_onsets(seg[:, c], rate)[0] for c in range(seg.shape[1]))
    n = fc.shape[1]
    scores = np.array([np.sum(ft[:, k:k + n] * fc[:, : max(0, min(n, ft.shape[1] - k))])
                       for k in range(max(1, max_lag // hop))])
    k = int(np.argmax(scores))
    ratio = float(scores[k] / (np.median(scores) + 1e-12))
    # the click's beats (its onsets' peaks), and whether the take has its onsets there
    c = fc.sum(axis=0)
    beats = [i for i in range(1, len(c) - 1) if c[i] > 0.5 * c.max() and c[i] >= c[i - 1] and c[i] >= c[i + 1]]
    beats = [i for j, i in enumerate(beats) if j == 0 or (i - beats[j - 1]) * hop > 0.02 * rate]
    t = ft.sum(axis=0)
    w = int(0.04 * rate / hop)
    offs = np.array([int(np.argmax(t[i + k - w: i + k + w + 1])) - w for i in beats
                     if i + k - w >= 0 and i + k + w + 1 <= len(t)]) * hop / rate
    on_time = float(np.mean(np.abs(offs - np.median(offs)) <= CLICK_TOLERANCE_S)) if len(offs) else 0.0
    # to the sample, near the onsets' lag
    near = int(CLICK_REFINE_S * rate)
    lag, _ = find_lag(seg.mean(axis=1).astype(np.float64), click[:m], k * hop + near, max(0, k * hop - near))
    return Alignment(lag, rate, ratio, None, None, None, on_time=on_time,
                     found_=on_time >= CLICK_ON_TIME and ratio >= CLICK_RATIO)


def count_in(tempo: dict, rate: int, level_dbfs: float = -14.0) -> tuple[np.ndarray, float, list[float]]:
    """Clicks to count a player in, in the backing's tempo: a full bar, then the beats of the
    pickup bar before the first note, downbeats accented. Returns the clicks (float32, from
    `lead` seconds before the backing to the backing's first note), `lead` (how long the backing
    waits for them), and the click times in the backing's own time (negative before it starts).
    Down Yonder take 1 (120 bpm, first note at 0.5 s, pickup 1.5 beats): clicks at -2.75 ...
    0.25 s, the pickup coming in on the "and" of 3."""
    spb = 60.0 / float(tempo["bpm"])
    bpb = int(tempo.get("beats_per_bar", 4))
    first = float(tempo["first_note_s"])
    downbeat = first + float(tempo.get("pickup_beats", 0.0)) * spb  # bar 1, in the backing's time
    times, accents = [], []
    for k in range(2 * bpb, 0, -1):
        t = downbeat - k * spb
        if t < first - 0.05:
            times.append(t)
            accents.append(k % bpb == 0)
    lead = max(0.0, -min(times)) + 0.2 if times else 0.0
    n = int(round((lead + max(first, 0.0)) * rate))
    out = np.zeros(n, dtype=np.float32)
    dur = int(0.03 * rate)
    tt = np.arange(dur) / rate
    for t, acc in zip(times, accents):
        freq, lv = (2000.0, level_dbfs) if acc else (1500.0, level_dbfs - 4.0)
        click = (10 ** (lv / 20) * np.sin(2 * np.pi * freq * tt) * np.exp(-tt / 0.006)).astype(np.float32)
        i = int(round((lead + t) * rate))
        m = min(dur, n - i)
        if i >= 0 and m > 0:
            out[i:i + m] += click[:m]
    return out, lead, times


def click_level(backing: np.ndarray, rate: int) -> float:
    """A level for the count-in that stands out like the backing's louder attacks: the 90th
    percentile of its 50 ms peaks, kept between -24 and -6 dBFS. Down Yonder take 1 as the
    control unit plays it: -8.7 dBFS (its median attack -13.8)."""
    w = int(0.05 * rate)
    peaks = np.abs(backing[: len(backing) // w * w]).reshape(-1, w).max(axis=1) if len(backing) >= w else []
    peaks = [p for p in peaks if p > 1e-3]
    if not peaks:
        return -14.0
    return float(np.clip(20 * np.log10(np.percentile(peaks, 90)), -24.0, -6.0))


def with_count_in(backing: np.ndarray, rate: int, tempo: dict) -> tuple[np.ndarray, float]:
    """The backing preceded (and overlaid, up to its first note) by its count-in, and the lead."""
    clicks, lead, _ = count_in(tempo, rate, click_level(backing, rate))
    k = int(round(lead * rate))
    out = np.concatenate([np.zeros(k, dtype=np.float32), backing.astype(np.float32)])
    m = min(len(clicks), len(out))
    out[:m] += clicks[:m]
    return out, lead


def click_track(tempo: dict, rate: int, beats: float, count_in_bars: int = 1, through: bool = True,
                level_dbfs: float = -12.0) -> tuple[np.ndarray, float]:
    """A click to play an exercise to: `count_in_bars` full bars before the bar the music starts
    in (its pickup bar, or bar 1), the pickup bar's beats before the first note, then (with
    `through`) every beat through `beats` beats from the first note; downbeats accented
    (2000 Hz against 1500 Hz, 4 dB louder). `tempo`: {"bpm", "pickup_beats", "beats_per_bar"}.
    Returns the clicks (float32) and where beat 0, the music's first note, falls in them (s)."""
    spb = 60.0 / float(tempo["bpm"])
    bpb = int(tempo.get("beats_per_bar", 4))
    pickup = float(tempo.get("pickup_beats", 0.0))
    # beats counted from bar 1's downbeat; the music's first note is at -pickup
    first_bar_start = -bpb * np.ceil(pickup / bpb - 1e-9) if pickup > 0 else 0.0
    k0 = int(round(first_bar_start)) - count_in_bars * bpb
    k1 = int(np.floor(beats - pickup + 1e-9)) if through else int(np.ceil(-pickup - 1e-9)) - 1
    ks = [k for k in range(k0, k1 + 1) if through or k < -pickup - 0.1]
    lead = 0.2 - k0 * spb if ks else 0.2  # bar 1's downbeat, from the start
    n = int(round((lead + (max(ks) + 1) * spb if ks else lead) * rate)) + int(0.3 * rate)
    out = np.zeros(n, dtype=np.float32)
    dur = int(0.03 * rate)
    tt = np.arange(dur) / rate
    for k in ks:
        acc = k % bpb == 0
        freq, lv = (2000.0, level_dbfs) if acc else (1500.0, level_dbfs - 4.0)
        click = (10 ** (lv / 20) * np.sin(2 * np.pi * freq * tt) * np.exp(-tt / 0.006)).astype(np.float32)
        i = int(round((lead + k * spb) * rate))
        m = min(dur, n - i)
        if i >= 0 and m > 0:
            out[i:i + m] += click[:m]
    return out, lead - pickup * spb
