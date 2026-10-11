"""Automated hardware checks for the audio devices in the config.

The tests are T1-T4, T7 and T9 in docs/components.md. Run on the control
unit, through tools/hwcheck. Prints one line per test, then a row for the
swap log.
"""

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from . import audio, dsp
from .config import Profile, load_config


@dataclass
class Result:
    test: str
    name: str
    ok: bool | None  # None: measured, no pass/fail
    detail: str


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout
    except FileNotFoundError:
        return ""


def t1_present(p: Profile) -> Result:
    if p.backend == "fake":
        return Result("T1", "present", True, f"{p.name}: fake device")
    usb = (p.usb_id is None) or (p.usb_id in _run("lsusb"))
    card = audio.present(p)
    detail = f"{p.name}: USB {p.usb_id or '-'} {'found' if usb else 'MISSING'}, card {p.card} {'found' if card else 'MISSING'}"
    return Result("T1", "present", usb and card, detail)


def _stream0_sections(card: str) -> dict[str, dict[str, set]]:
    """Formats, channels and rates offered, per direction, from stream0."""
    text = Path(f"/proc/asound/{card}/stream0").read_text()
    out: dict[str, dict[str, set]] = {}
    current = None
    for line in text.splitlines():
        if line.strip() in ("Playback:", "Capture:"):
            current = out.setdefault(line.strip()[:-1].lower(),
                                     {"format": set(), "channels": set(), "rates": set()})
        elif current is not None:
            m = re.match(r"\s+(Format|Channels|Rates):\s*(.*)", line)
            if m:
                key = m.group(1).lower()
                values = [v.strip() for v in m.group(2).split(",")]
                current[key].update(values)
    return out


def t2_format(p: Profile) -> Result:
    if p.backend == "fake":
        return Result("T2", "format", True, f"{p.name}: fake device")
    try:
        sections = _stream0_sections(p.card)
    except OSError as e:
        return Result("T2", "format", False, f"{p.name}: {e}")
    problems, seen = [], []
    for direction, s in (("capture", p.capture), ("playback", p.playback)):
        if s is None:
            continue
        offered = sections.get(direction)
        if offered is None:
            problems.append(f"no {direction} stream")
            continue
        want = {"format": s.format, "channels": str(s.channels), "rates": str(s.rate)}
        missing = [f"{k} {v}" for k, v in want.items() if v not in offered[k]]
        if missing:
            problems.append(f"{direction}: {', '.join(missing)} not offered")
        seen.append(f"{direction} {s.format} {s.rate} Hz {s.channels} ch")
    ok = not problems
    return Result("T2", "format", ok, f"{p.name}: " + ("; ".join(problems) if problems else ", ".join(seen)))


def t3_controls(p: Profile) -> Result:
    if p.backend == "fake":
        return Result("T3", "controls", True, f"{p.name}: fake device")
    text = _run("amixer", "-c", p.card, "contents")
    found = {}
    for m in re.finditer(r"name='([^']+)'\n\s+; ([^\n]*)", text):
        attrs = dict(kv.split("=", 1) for kv in m.group(2).split(",") if "=" in kv)
        found[m.group(1)] = attrs
    problems, names = [], []
    for s in (p.capture, p.playback):
        if s is None:
            continue
        for c in s.controls.values():
            names.append(c.name)
            if c.name not in found:
                problems.append(f"{c.name!r} missing")
                continue
            a = found[c.name]
            if c.min is not None and a.get("min") != str(c.min):
                problems.append(f"{c.name!r} min {a.get('min')} (profile {c.min})")
            if c.max is not None and a.get("max") != str(c.max):
                problems.append(f"{c.name!r} max {a.get('max')} (profile {c.max})")
    ok = not problems
    return Result("T3", "controls", ok, f"{p.name}: " + ("; ".join(problems) if problems else f"{len(names)} controls as in the profile"))


def _capture_seconds(p: Profile, seconds: float, raw: bool = True) -> tuple[np.ndarray, audio.Capture]:
    blocks = []
    with audio.Capture(p, raw=raw) as cap:
        need = int(seconds * cap.rate)
        got = 0
        while got < need:
            b = cap.read()
            if b is None:
                break
            blocks.append(b)
            got += len(b)
    x = np.concatenate(blocks) if blocks else np.zeros((0, cap.channels), dtype=cap.dtype)
    return x, cap


def t4_floor(p: Profile, seconds: float = 10.0) -> Result:
    x, cap = _capture_seconds(p, seconds)
    if len(x) < seconds * cap.rate * 0.99:
        return Result("T4", "floor", False, f"{p.name}: only {len(x) / cap.rate:.1f} s captured")
    parts = []
    for c in range(x.shape[1]):
        ch = x[:, c]
        dc = float(ch.mean())
        f = dsp.to_float(ch) - dc / dsp.full_scale(ch.dtype)
        parts.append(f"ch{c}: {dsp.rms_dbfs(f):.1f} dBFS rms, peak {dsp.peak_dbfs(f):.1f}, DC {dc:.1f} LSB")
    if x.shape[1] > 1:
        parts.append("channels identical" if all(np.array_equal(x[:, 0], x[:, c]) for c in range(1, x.shape[1])) else "channels differ")
    xr = cap.xruns()
    if xr:
        parts.append(f"{len(xr)} xruns")
    return Result("T4", "floor", not xr, f"{p.name}, {seconds:.0f} s: " + "; ".join(parts))


def _tone_level(x: np.ndarray, rate: int, freq: float = 1000.0) -> float:
    t = np.arange(len(x)) / rate
    amp = 2 * abs(np.dot(x, np.exp(-2j * np.pi * freq * t))) / max(1, len(x))
    return dsp.db(amp)


def t7_loopback(cp: Profile, pp: Profile) -> Result:
    """A 1 kHz tone at -20 dBFS for 2 s, captured; reports how much returns."""
    import threading
    rate = pp.playback.rate
    n = 2 * rate
    t = np.arange(n) / rate
    fade = np.minimum(1, np.minimum(np.arange(n), n - 1 - np.arange(n)) / (0.05 * rate))
    tone = (10 ** (-20 / 20) * fade * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)

    def play():
        time.sleep(1.0)
        with audio.Playback(pp) as pb:
            pb.write(tone)

    th = threading.Thread(target=play)
    th.start()
    x, cap = _capture_seconds(cp, 4.0, raw=False)
    th.join()
    f = dsp.to_float(x[:, 0])
    r = cap.rate
    before = _tone_level(f[: int(0.9 * r)], r)
    during = _tone_level(f[int(1.3 * r): int(2.7 * r)], r)
    return Result("T7", "loopback", None,
                  f"{pp.name} -> {cp.name}: 1 kHz at -20 dBFS returns at {during:.1f} dBFS (before: {before:.1f})")


def _throttled() -> str | None:
    out = _run("vcgencmd", "get_throttled").strip()
    return out.split("=", 1)[1] if "=" in out else None


def _kernel_log() -> list[str]:
    """The kernel log, if readable (dmesg needs root on Debian)."""
    out = _run("sudo", "-n", "dmesg") or _run("dmesg")
    return out.splitlines()


KERNEL_LOSS = re.compile(r"data may have been lost|usb .*(reset|disconnect)|xhci.*error", re.I)


def t9_soak(cp: Profile, pp: Profile, minutes: float) -> Result:
    """Capture for `minutes`; play silence during the middle third."""
    import threading
    seconds = minutes * 60
    th_before = _throttled()
    log_before = len(_kernel_log())
    stop = threading.Event()

    def play_silence():
        if stop.wait(seconds / 3):
            return
        with audio.Playback(pp) as pb:
            block = np.zeros(pb.rate // 10, dtype=np.float32)
            end = time.monotonic() + seconds / 3
            while time.monotonic() < end and not stop.is_set():
                if not pb.write(block):
                    break

    th = threading.Thread(target=play_silence, daemon=True)
    t0 = time.monotonic()
    th.start()
    frames = 0
    with audio.Capture(cp, raw=True) as cap:
        while time.monotonic() - t0 < seconds:
            b = cap.read()
            if b is None:
                break
            frames += len(b)
        xr = cap.xruns()
    wall = time.monotonic() - t0
    stop.set()
    th.join(timeout=5)
    th_after = _throttled()
    kernel = [l for l in _kernel_log()[log_before:] if KERNEL_LOSS.search(l)]
    audio_s = frames / cap.rate
    lost = wall - audio_s
    # arecord's start-up and one buffer in flight are not losses.
    ok = not xr and not kernel and lost < cp.capture.buffer_ms / 1000 + 1.0 and th_after in (None, "0x0")
    detail = (f"{cp.name}, {minutes:g} min: {audio_s:.1f} s of audio in {wall:.1f} s "
              f"({lost:+.2f} s), {len(xr)} xruns, {len(kernel)} kernel USB errors, "
              f"throttled {th_before} -> {th_after}")
    for line in xr[:5] + kernel[:5]:
        detail += f"\n      {line.strip()}"
    return Result("T9", "soak", ok, detail)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hwcheck", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, help="config file (default: $PRACTICE_RECORDER_CONFIG or /etc/practice-recorder.toml)")
    ap.add_argument("--sound", action="store_true",
                    help="also T7: plays a 1 kHz tone at -20 dBFS for 2 s (audible if playback reaches the speakers)")
    ap.add_argument("--soak", type=float, metavar="MINUTES", help="also T9 for this many minutes")
    ap.add_argument("--only", help="comma-separated test ids, e.g. T1,T2")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    cp, pp = cfg.capture, cfg.playback
    profiles = [cp] if cp.name == pp.name else [cp, pp]
    only = {t.strip().upper() for t in args.only.split(",")} if args.only else None

    def want(tid: str) -> bool:
        return only is None or tid in only

    results: list[Result] = []
    for p in profiles:
        for tid, fn in (("T1", t1_present), ("T2", t2_format), ("T3", t3_controls)):
            if want(tid):
                results.append(fn(p))
                print(_line(results[-1]), flush=True)
    present = all(r.ok for r in results if r.test == "T1")
    if not present:
        print("T1 failed: skipping the tests that need the device.")
    else:
        if want("T4"):
            results.append(t4_floor(cp))
            print(_line(results[-1]), flush=True)
        if args.sound and want("T7"):
            results.append(t7_loopback(cp, pp))
            print(_line(results[-1]), flush=True)
        if args.soak and want("T9"):
            print(f"T9 soak: {args.soak:g} min, started {time.strftime('%H:%M:%S')}", flush=True)
            results.append(t9_soak(cp, pp, args.soak))
            print(_line(results[-1]), flush=True)

    failed = [r for r in results if r.ok is False]
    tests = ", ".join(sorted({r.test for r in results}))
    verdict = "fail: " + ", ".join(r.test for r in failed) if failed else "pass"
    print()
    print("For the swap log (docs/components.md):")
    print(f"| {date.today()} | | | {cp.name} / {pp.name} | {tests} (hwcheck) | {verdict} |")
    return 1 if failed else 0


def _line(r: Result) -> str:
    mark = {True: "PASS", False: "FAIL", None: "INFO"}[r.ok]
    return f"{r.test} {r.name:9s} {mark}  {r.detail}"


if __name__ == "__main__":
    sys.exit(main())
