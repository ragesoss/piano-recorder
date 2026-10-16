#!/usr/bin/env python3
"""The chord app's kept checks and labels, judged again offline as the app judges a check, and scored
as it scores one: right, the chord right, the bass right (the root, unless the target names another).

Each clip (`tools/pull-checks` brings them from the control unit: <id>.wav and <id>.json) is mixed as
the app mixed it (the sidecar's `mixed` inputs), put in a `Listener`'s buffer as if just heard, and
judged by `Listener._judge`: the app's own code, with whatever listener it imports. A check's attack
is where the app found it (the sidecar's `attack_s`); a label's is found as the app finds one (a rise of
`ATTACK_DB` over the three blocks before); a label with none in it (the chord struck before its two
seconds) is judged from its start ("held").

Each is put in a register and a ring, measured here from the clip and the target alone (not from the
listener judged), so every listener is broken down the same way:
- register: the octave of the chord's lowest and highest notes as played: of the target's pitch
  classes, the notes whose fundamentals stand over the room (`register`). `--played` gives them
  instead (a CSV of id, lowest, highest: "F5", "C6"), where they were heard by ear.
- ring: how long the chord sounds after its onset, until its level (50 ms) falls 20 dB under its
  peak, or to 10 dB over the room. damped: under 0.4 s; short: under 1.0 s; rings: longer; held: a
  label with no attack; faint: none of the target's notes stands over the room.

    tools/check_study.py DIR [--livechords PATH | git:REV] [--csv OUT] [--compare OLD.csv]
                             [--old-span] [--played CSV] [--only ID ...]

`--livechords`: judge with another copy of the listener (a file, or git:REV for dobro-transcription's
pipeline/src/transcribe/livechords.py at that revision) in place of chords/livechords.py.
`--compare`: what changed from an earlier run's CSV. `--old-span`: judge over the span the app judged
until 2026-10-16 (0.1-1.1 s after the attack, whatever the chord did), to tell a listener's own
gain from the span's.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import subprocess
import sys
import tempfile
import types
import wave
from dataclasses import replace
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
DOBRO = REPO.parent / "dobro-transcription"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
RINGS = ((0.4, "damped"), (1.0, "short"), (float("inf"), "rings"))
OLD_SPAN_S = (0.1, 1.1)  # the span a check was judged over, after its attack, until 2026-10-16


def use_livechords(spec: str) -> str:
    """Load the listener from `spec` as chords.livechords, before the app imports it. Its name."""
    if spec.startswith("git:"):
        rev = spec[4:]
        root = DOBRO if DOBRO.exists() else Path.home() / "play" / "dobro-transcription"
        src = subprocess.run(["git", "-C", str(root), "show", f"{rev}:pipeline/src/transcribe/livechords.py"],
                             capture_output=True, text=True, check=True).stdout
        name, origin = f"livechords@{rev}", f"{root} at {rev}"
    else:
        path = Path(spec).resolve()
        src = path.read_text()
        head = src.splitlines()[0] if src else ""
        rev = head.split("(")[1].split(")")[0] if head.startswith("# Copied from") and "(" in head else None
        if rev is None:  # a working copy: its last commit, and whether it has changed since
            git = ["git", "-C", str(path.parent)]
            last = subprocess.run(git + ["log", "-1", "--format=%h", "--", path.name], capture_output=True, text=True)
            changed = subprocess.run(git + ["diff", "--quiet", "HEAD", "--", path.name], capture_output=True)
            rev = (last.stdout.strip() + ("+changes" if changed.returncode == 1 else "")) \
                if last.returncode == 0 else None
        name, origin = f"{path}" + (f" ({rev})" if rev else ""), str(path)
    import chords

    mod = types.ModuleType("chords.livechords")
    mod.__file__ = origin
    sys.modules["chords.livechords"] = mod  # dataclasses look their module up here
    exec(compile(src, origin, "exec"), mod.__dict__)
    chords.livechords = mod
    return name


def read_clip(wav: Path, meta: dict) -> tuple[int, np.ndarray]:
    """The clip's mixed inputs as the app mixed them: their mean, over full scale (float32)."""
    with wave.open(str(wav)) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype={2: "<i2", 4: "<i4"}[width]).reshape(-1, ch)
    chans = [c["channel"] for c in meta.get("channels", []) if c["input"] in meta.get("mixed", [])] or list(range(ch))
    fs = float(2 ** (8 * width - 1))
    return rate, (x[:, chans].astype(np.float32) / fs).mean(axis=1).astype(np.float32)


def find_attack(LS, y: np.ndarray, rate: int) -> int | None:
    """Where the app would start a check in this clip: the first block whose level rises ATTACK_DB
    over the lowest of the three before it, while the last WINDOW_S is QUIET_DB over the quiet."""
    from recorder import dsp

    n = int(LS.BLOCK_S * rate)
    levels: list[float] = []
    for k in range(len(y) // n):
        block = y[k * n:(k + 1) * n]
        win = y[max(0, (k + 1) * n - int(LS.WINDOW_S * rate)):(k + 1) * n]
        level = dsp.db(float(np.sqrt(np.mean(win ** 2))))
        bl = dsp.db(float(np.sqrt(np.mean(block ** 2))))
        before = levels[-3:]
        quiet = min(min(levels + [bl]), LS.QUIET_CEIL_DBFS)
        if before and level > quiet + LS.QUIET_DB and bl - min(before) >= LS.ATTACK_DB:
            return k * n
        levels.append(bl)
    return None


def use_old_span(LS) -> None:
    """Judge over OLD_SPAN_S after the attack, as the app did until 2026-10-16."""
    def fixed(x, rate, attack):
        a, b = (min(len(x), attack + int(t * rate)) for t in OLD_SPAN_S)
        return {"onset": attack, "a": a, "b": b, "peak_dbfs": None, "room_dbfs": None, "sounds_s": None}

    LS.judged_span = fixed


def judge(LS, lis, y: np.ndarray, rate: int, attack: int, target):
    """The clip judged by the app's `Listener._judge`, its buffer holding what the app had heard when
    it judged (CHECK_S after the attack, to the end of that block)."""
    n = int(LS.BLOCK_S * rate)
    due = attack + int((LS.CHECK_S if hasattr(LS, "CHECK_S") else LS.CHECK_SPAN_S[1]) * rate)
    end = min(len(y), attack + -(-(due - attack) // n) * n)
    lis.buf[:] = 0.0
    lis.buf[:end] = y[:end]
    lis.pos = end
    ck = LS.Check(target=target, state="hearing", attack=attack)
    lis._judge(ck)
    return ck


# --- what was played: its ring and its register, from the clip and the target ----------------------


def envelope(y: np.ndarray, rate: int, hop_s: float = 0.01) -> np.ndarray:
    hop = int(hop_s * rate)
    n = len(y) // hop
    return np.mean(y[: n * hop].astype(np.float64).reshape(n, hop) ** 2, axis=1) + 1e-20


def ring(y: np.ndarray, rate: int, attack: int | None) -> dict:
    """The onset (the steepest 10 ms rise near the attack), the peak after it, the room before it, and
    how long the chord sounds: until its level over 50 ms falls 20 dB under the peak, or to 10 dB over
    the room."""
    p = envelope(y, rate)
    db = 10 * np.log10(p)
    sm = 10 * np.log10(np.convolve(p, np.ones(5) / 5, mode="same"))
    if attack is None:
        on = 0
        room = float(np.percentile(db, 5))
    else:
        a = attack * 100 // rate
        lo, hi = max(1, a - 5), min(len(db) - 1, a + 15)
        on = lo + int(np.argmax(db[lo:hi] - db[lo - 1:hi - 1])) if hi > lo else a
        room = float(np.median(db[: max(1, on - 2)])) if on >= 5 else float(np.percentile(db, 5))
    pk = on + int(np.argmax(db[on: on + 10]))
    peak = float(db[pk])
    stop = max(peak - 20.0, room + 10.0)
    below = np.nonzero(sm[pk:] < stop)[0]
    sounds = (pk - on + below[0]) / 100 if len(below) else None
    return {"onset_s": on / 100, "peak_dbfs": round(peak, 1), "room_dbfs": round(room, 1),
            "ring_s": round(sounds, 2) if sounds is not None else round((len(db) - on) / 100, 2),
            "ring_open": sounds is None}


def ring_class(r: dict, attack: int | None) -> str:
    if attack is None:
        return "held"
    return next(name for top, name in RINGS if r["ring_s"] < top)


def _spectrum(y: np.ndarray, rate: int, a: float, b: float, nfft: int = 1 << 17):
    seg = y[max(0, int(a * rate)): max(int(a * rate) + 64, int(b * rate))].astype(np.float64)
    w = np.hanning(len(seg))
    S = np.abs(np.fft.rfft(seg * w, nfft)) * 2 / w.sum()
    return np.fft.rfftfreq(nfft, 1 / rate), 20 * np.log10(S + 1e-12)


def _at(f, L, hz, cents):
    m = (f >= hz * 2 ** (-cents / 1200)) & (f <= hz * 2 ** (cents / 1200))
    return float(L[m].max()) if m.any() else -240.0


def register(y: np.ndarray, rate: int, onset_s: float, ring_s: float, pcs: set[int],
             held: bool = False) -> tuple[int | None, int | None, list[int]]:
    """The lowest and highest notes played (MIDI), of the target's pitch classes, as the spectrum of
    the sounding part (from the onset, for its ring but at least 0.25 s and at most 1 s; a held chord's
    first second) shows them. A note is there when the peak at its fundamental is 12 dB over the
    spectrum's local floor (its median over a third of an octave) and over the room's (the clip before
    the onset), and within 25 dB of the loudest (the played bass notes here were 9-17 dB under it; a
    string ringing in sympathy, 25-35). The lowest is the lowest there; the highest, the highest there
    that is not where a lower one's partial falls (its octave, twelfth, two octaves and so on), unless
    10 dB louder than that one's fundamental (over 100 Hz: under it a room mic hears a partial louder
    than its fundamental). A doubled octave is not told from a partial. (lowest, highest, all there)"""
    a, b = (0.0, 1.0) if held else (onset_s + 0.03, onset_s + min(1.0, max(0.25, ring_s)))
    f, L = _spectrum(y, rate, a, b)
    fr, Lr = _spectrum(y, rate, 0.0, onset_s - 0.02) if onset_s >= 0.2 and not held else (None, None)
    band = (f > 25) & (f < 5000)
    top = float(L[band].max())

    def hz(m):
        return 440 * 2 ** ((m - 69) / 12)

    def level(m):
        return _at(f, L, hz(m), 40)

    def there(m) -> bool:
        near = (f > hz(m) * 2 ** (-1 / 6)) & (f < hz(m) * 2 ** (1 / 6))
        floor_ = float(np.median(L[near])) if near.any() else -240.0
        room = _at(fr, Lr, hz(m), 40) if fr is not None else -240.0
        return level(m) >= max(floor_, room) + 12 and level(m) >= top - 25

    present = [m for m in range(21, 109) if m % 12 in pcs and hz(m) < 4500 and there(m)]
    if not present:
        return None, None, []
    partial = {12, 19, 24, 28, 31, 34, 36}  # semitones from a fundamental to its partials 2-8
    alone = [m for m in present if not any(m - n in partial and (hz(n) < 100 or level(m) <= level(n) + 10)
                                           for n in present if n < m)]
    return present[0], alone[-1], present


def note_name(m: int | None) -> str:
    return "" if m is None else f"{NAMES[m % 12]}{m // 12 - 1}"


def parse_note(s: str) -> int | None:
    import re

    m = re.match(r"^([A-G])([#b]?)(-?\d)$", s.strip())
    if not m:
        return None
    return 12 * (int(m.group(3)) + 1) + {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[m.group(1)] + {
        "#": 1, "b": -1, "": 0}[m.group(2)]


# --- the study ------------------------------------------------------------------------------------


def study(directory: Path, played: dict, only: list[str] | None = None) -> list[dict]:
    from chords import listen as LS
    from recorder.config import load_config

    tmp = Path(tempfile.mkdtemp(prefix="check-study-"))
    cfg = replace(load_config(REPO / "config" / "chords-dev.toml"), takes_dir=tmp)
    rows = []
    for side in sorted(directory.glob("*.json")):
        meta = json.loads(side.read_text())
        sid = meta.get("id", side.stem)
        if only and not any(sid.endswith(o) for o in only):
            continue
        if not meta.get("target") or not (directory / f"{sid}.wav").exists():
            continue  # a strike nobody has said, or no clip
        target = LS.parse_target(meta["target"])
        rate, y = read_clip(directory / f"{sid}.wav", meta)
        lis = LS.Listener(cfg, tmp)
        if lis.rate != rate:
            lis.rate = rate
            lis._reset()
        if meta.get("kind") in ("check", "strike") and meta.get("attack_s") is not None:
            attack = int(round(meta["attack_s"] * rate))
        else:
            attack = find_attack(LS, y, rate)
        ck = judge(LS, lis, y, rate, attack if attack is not None else 0, target)
        r = ring(y, rate, attack)
        pcs = LS.tones(target.chord) if target.kind == "chord" else (
            {target.midi % 12} if target.kind == "note" else set(target.pcs))
        lo, hi, there = register(y, rate, r["onset_s"], r["ring_s"], pcs, held=attack is None)
        faint = lo is None
        if target.kind == "note":  # its octave is the target's
            faint = target.midi not in there
            lo = hi = target.midi
        if sid in played:
            lo, hi = played[sid]
        m = ck.meta or {}
        rows.append({
            "id": sid, "kind": meta.get("kind"), "target": target.name, "target_kind": target.kind,
            "heard": m.get("heard"), "right": m.get("right"), "chord_right": m.get("chord_right"),
            "bass_right": m.get("bass_right"), "notes_heard": " ".join(m.get("notes_heard", [])),
            "bass": m.get("bass"), "judged_s": " ".join(str(v) for v in m.get("judged_s", [])),
            "then_heard": meta.get("heard"), "then_right": meta.get("right"),
            "lowest": note_name(lo), "highest": note_name(hi),
            "low_octave": "" if lo is None else lo // 12 - 1, "high_octave": "" if hi is None else hi // 12 - 1,
            "ring": "faint" if faint else ring_class(r, attack), **r,
            "attack_s": None if attack is None else round(attack / rate, 3),
        })
    return rows


def rate_of(rows, key):
    xs = [r[key] for r in rows if r[key] is not None]
    return f"{sum(bool(x) for x in xs)}/{len(xs)}" if xs else "-"


def breakdown(rows: list[dict], by: str, title: str) -> list[str]:
    groups = collections.OrderedDict()
    for r in sorted(rows, key=lambda r: (str(r[by]) == "", str(r[by]))):
        groups.setdefault(r[by] if r[by] != "" else "?", []).append(r)
    out = [f"{title:<14} {'n':>3} {'right':>7} {'chord':>7} {'bass':>7}"]
    for k, rs in groups.items():
        out.append(f"{str(k):<14} {len(rs):>3} {rate_of(rs, 'right'):>7} {rate_of(rs, 'chord_right'):>7} "
                   f"{rate_of(rs, 'bass_right'):>7}")
    return out


def report(rows: list[dict], name: str) -> str:
    def yn(v):
        return "-" if v is None else ("yes" if v else "no")

    lines = [f"Judged with {name}", ""]
    kept = [r for r in rows if r["kind"] == "check" and r["then_heard"] is not None]
    if kept:
        same = sum(r["heard"] == r["then_heard"] for r in kept)
        lines += [f"Checks heard as when kept (by the listener then): {same} of {len(kept)}", ""]
    lines.append(f"{'id':<18} {'target':<6} {'heard':<9} {'right':<5} {'chord':<5} {'bass':<5} {'played':<9} "
                 f"{'ring':<7} {'ring_s':>6}  notes heard")
    for r in rows:
        lines.append(f"{r['id']:<18} {r['target']:<6} {str(r['heard']):<9} {yn(r['right']):<5} "
                     f"{yn(r['chord_right']):<5} {yn(r['bass_right']):<5} "
                     f"{(r['lowest'] + '-' + r['highest']) if r['lowest'] else '?':<9} {r['ring']:<7} "
                     f"{r['ring_s']:>6.2f}  {r['notes_heard']}")
    sets = [("all", rows)]
    checks = [r for r in rows if r["kind"] == "check"]
    if checks and len(checks) != len(rows):
        sets.append(("checks", checks))
    for label, rs in sets:
        lines += ["", f"{label}: {len(rs)} clips, right {rate_of(rs, 'right')}, chord right "
                  f"{rate_of(rs, 'chord_right')}, bass right {rate_of(rs, 'bass_right')}", ""]
        lines += breakdown(rs, "low_octave", "lowest octave") + [""]
        lines += breakdown(rs, "high_octave", "highest octave") + [""]
        lines += breakdown(rs, "ring", "ring")
    return "\n".join(lines)


def compare(rows: list[dict], old_csv: Path) -> str:
    """The clips whose verdict changed from an earlier run (its CSV)."""
    with open(old_csv, newline="") as fh:
        old = {r["id"]: r for r in csv.DictReader(fh)}

    def v(x):
        return "" if x is None else str(x)

    lines = [f"Changed from {old_csv}:"]
    keys = ("right", "chord_right", "bass_right")
    gained = lost = 0
    for r in rows:
        o = old.get(r["id"])
        if o is None:
            continue
        if any(v(r[k]) != o[k] for k in keys) or v(r["heard"]) != o["heard"]:
            lines.append(f"  {r['id']} {r['target']:<6} {o['heard'] or '-':<9} -> {v(r['heard']) or '-':<9} "
                         f"right {o['right'] or '-'} -> {v(r['right']) or '-'}  "
                         f"({r['lowest']}-{r['highest']}, {r['ring']})")
            gained += o["right"] != "True" and r["right"] is True
            lost += o["right"] == "True" and r["right"] is not True
    lines.append(f"  right: {gained} gained, {lost} lost")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dir", type=Path, help="the pulled checks: <id>.wav and <id>.json")
    ap.add_argument("--livechords", help="the listener to judge with: a file, or git:REV (dobro-transcription)")
    ap.add_argument("--csv", type=Path, help="write each clip's row here")
    ap.add_argument("--played", type=Path, help="a CSV of id, lowest, highest (as heard by ear)")
    ap.add_argument("--only", nargs="*", help="only these ids (or their ends)")
    ap.add_argument("--compare", type=Path, help="an earlier run's CSV: show what changed")
    ap.add_argument("--old-span", action="store_true",
                    help="judge over 0.1-1.1 s after the attack, as until 2026-10-16")
    a = ap.parse_args(argv)
    name = use_livechords(a.livechords) if a.livechords else None
    if a.old_span:
        from chords import listen as LS

        use_old_span(LS)
    played = {}
    if a.played:
        with open(a.played, newline="") as fh:
            for row in csv.reader(fh):
                if row and not row[0].startswith("#") and row[0] != "id":
                    played[row[0]] = (parse_note(row[1]), parse_note(row[2]))
    rows = study(a.dir, played, a.only)
    if name is None:
        from chords import listen as LS

        name = LS.detector() + " (the app's copy)"
    print(report(rows, name + (", over the old span" if a.old_span else "")))
    if a.compare:
        print("\n" + compare(rows, a.compare))
    if a.csv:
        with open(a.csv, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            keys = list(rows[0]) if rows else []
            w.writerow(keys)
            for r in rows:
                w.writerow(["" if r[k] is None else r[k] for k in keys])
    return 0


if __name__ == "__main__":
    sys.exit(main())
