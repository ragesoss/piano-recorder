"""Levels, loudness and the playback limiter. Audio here is float32 in
[-1, 1), shaped (frames,) for mono or (frames, channels)."""

import numpy as np

# Sample formats the hardware layer can read and write: ALSA name -> dtype.
FORMATS = {"S16_LE": np.dtype("<i2"), "S32_LE": np.dtype("<i4")}


def dtype_for(fmt: str) -> np.dtype:
    if fmt not in FORMATS:
        raise NotImplementedError(
            f"sample format {fmt} is not supported yet (have: {', '.join(FORMATS)})"
        )
    return FORMATS[fmt]


def full_scale(dtype: np.dtype) -> float:
    return float(2 ** (8 * dtype.itemsize - 1))


def to_float(samples: np.ndarray) -> np.ndarray:
    return samples.astype(np.float32) / full_scale(samples.dtype)


def from_float(x: np.ndarray, dtype: np.dtype) -> np.ndarray:
    fs = full_scale(dtype)
    return np.clip(np.round(x * fs), -fs, fs - 1).astype(dtype)


def db(amplitude: float) -> float:
    return float(20 * np.log10(max(float(amplitude), 1e-12)))


def peak_dbfs(x: np.ndarray) -> float:
    return db(np.max(np.abs(x))) if len(x) else -240.0


def rms_dbfs(x: np.ndarray) -> float:
    return db(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else -240.0


def gated_rms_dbfs(x: np.ndarray, rate: int, block_s: float = 0.4,
                   gate_dbfs: float = -70.0) -> float | None:
    """Mean power over 400 ms blocks louder than the gate, as dBFS.

    The measure used to match takes to records (docs/components.md,
    "Shared mixer"). None when nothing is above the gate.
    """
    blk = int(rate * block_s)
    nb = len(x) // blk
    if nb == 0:
        return None
    sq = np.square(x, dtype=np.float64)
    if sq.ndim == 2:  # power summed over channels, as for loudness
        sq = sq.sum(axis=1)
    power = np.mean(sq[: nb * blk].reshape(nb, blk), axis=1)
    loud = power[power > 10 ** (gate_dbfs / 10)]
    return db(np.sqrt(np.mean(loud))) if len(loud) else None


class DCRemover:
    """Subtract a slowly tracked DC offset, block by block."""

    def __init__(self, rate: int, time_constant_s: float = 2.0):
        self.rate = rate
        self.tc = time_constant_s
        self.dc: np.ndarray | None = None

    def process(self, block: np.ndarray) -> np.ndarray:
        x = block.astype(np.float64)
        mean = x.mean(axis=0)
        if self.dc is None:
            self.dc = mean
        else:
            self.dc = self.dc + (mean - self.dc) * min(1.0, len(x) / (self.tc * self.rate))
        return x - self.dc


def limit(x: np.ndarray, rate: int, ceiling_dbfs: float = -1.0,
          lookahead_s: float = 0.005, release_s: float = 0.08) -> np.ndarray:
    """Peak limiter: no sample above the ceiling.

    Works on 1 ms blocks. Each block's gain is the lowest any block needs from
    `lookahead_s` before it to `release_s` after it, smoothed on release, then
    interpolated per sample.
    """
    n = len(x)
    if n == 0:
        return x
    ceiling = 10 ** (ceiling_dbfs / 20)
    blk = max(1, rate // 1000)
    nb = -(-n // blk)
    mag = np.zeros(nb * blk, dtype=np.float32)
    mag[:n] = np.abs(x) if x.ndim == 1 else np.abs(x).max(axis=1)
    need = np.minimum(1.0, ceiling / np.maximum(mag.reshape(nb, blk).max(axis=1), 1e-12))
    la = max(1, round(lookahead_s * 1000))
    rl = max(1, round(release_s * 1000))
    padded = np.concatenate([np.ones(rl), need, np.ones(la)])
    hold = np.lib.stride_tricks.sliding_window_view(padded, la + rl + 1).min(axis=1)
    smooth = np.convolve(hold, np.ones(la) / la, mode="same")
    gain_blocks = np.minimum(hold, smooth)
    centres = (np.arange(nb) + 0.5) * blk
    gain = np.interp(np.arange(n), centres, gain_blocks).astype(np.float32)
    if x.ndim == 2:
        gain = gain[:, None]
    return np.clip(x * gain, -ceiling, ceiling)


def prepare_for_playback(x: np.ndarray, rate: int, target_dbfs: float,
                         limit_dbfs: float, max_gain_db: float) -> tuple[np.ndarray, float]:
    """Bring a take to the target gated rms, then limit its peaks.

    Limiting takes some loudness away when the peaks carry much of the
    energy, so the result is measured again and the gain corrected (twice at
    most). Returns the processed audio and the gain applied, in dB.
    """
    level = gated_rms_dbfs(x, rate)
    if level is None:
        return limit(x, rate, limit_dbfs), 0.0
    gain_db = min(target_dbfs - level, max_gain_db)
    for _ in range(3):
        y = limit(x * np.float32(10 ** (gain_db / 20)), rate, limit_dbfs)
        short = target_dbfs - (gated_rms_dbfs(y, rate) or target_dbfs)
        if short < 0.25 or gain_db >= max_gain_db:
            break
        gain_db = min(gain_db + short, max_gain_db)
    return y, gain_db
