"""Transcribe a take folder with the transcription project's pipeline (dobro-transcription's
`transcribe.transcribe`), and say what it found for the transcribe app's page.

    PYTHONPATH=<pipeline/src> <its venv>/python transcriber/job/run_pipeline.py FOLDER \
        --instrument dobro|bass|piano --bpm 72 [--beats-per-bar 4] [--metronome] [--downbeat S] \
        [--track-beats] [--title T]

Run by the app (transcriber/jobs.py) in the pipeline's venv, which has numpy, scipy, librosa and
soundfile; this file imports nothing of the app's, and sits alone in its folder (the folder of the
script run is first on the path: nothing here may stand in for a module of the pipeline's).

Writes into FOLDER, each by a rename once it is whole:
- `transcription.musicxml`: the pipeline's output as its CLI writes it (`write_transcription`):
  standard notation and tab; for the piano a grand staff (the right hand on the treble staff, the left on
  the bass staff), no tab;
- `transcription.json`: the notes (time, beat, bar, pitch, string, fret, technique, length; the piano's
  pitch, time, beat, bar, length and staff, its hand, with no string or fret) and the score's beats in the
  take's time, for the page's cursor and the review side.

Prints a JSON line at each stage on stdout (`{"stage": "notes"}` ...), the last `{"stage": "done", ...}`.

The beat: over a click (`--metronome --downbeat S`) the score's beats are the click's. Otherwise the
beat tracker (`transcribe.chords.beat_grid`: librosa's, from the tempo given) gives the tempo the
pipeline is told (`--track-beats`; held to the tempo given when it strays more than `TRACK_SPAN`), and
the score's beats are placed in the take by its notes (`fit_times`).

The piano (`--instrument piano`, the pipeline's `pianoscore`) reads the take at its own rate (48 kHz: its
partials to 9 kHz); the beat tracker reads it at the pipeline's 22.05 kHz. Over a click its count-in bar
teaches it the click, so it is told the downbeat as the others are.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

TRACK_SPAN = 0.15  # the tracker's tempo is used when within 15% of the tempo given
NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")


def say(stage: str, **kw) -> None:
    print(json.dumps({"stage": stage, **kw}), flush=True)


def pitch_name(midi: int) -> str:
    return f"{NAMES[midi % 12]}{midi // 12 - 1}"


def fit_times(beats, times, query, spb: float, half_window: float = 4.0) -> np.ndarray:
    """The take's time (s) at each score beat in `query`, from where the notes are (`beats` in the
    score, `times` in the take): a line through the notes within `half_window` beats, nearer ones
    weighing more (widened where there are fewer than three onsets), its slope held within half and
    twice `spb`; one onset alone, or none near, goes by `spb`. Increasing, so the page can invert it."""
    b, t = np.asarray(beats, float), np.asarray(times, float)
    q = np.asarray(query, float)
    if not len(b):
        return np.zeros(len(q))
    keys, inv = np.unique(np.round(b, 6), return_inverse=True)  # a chord's notes share their onset
    ut = np.array([np.median(t[inv == i]) for i in range(len(keys))])
    out = np.empty(len(q))
    for k, x in enumerate(q):
        w = half_window
        sel = np.abs(keys - x) <= w
        while sel.sum() < 3 and w < 256:
            w *= 2
            sel = np.abs(keys - x) <= w
        if sel.sum() < 2 or np.ptp(keys[sel]) < 1e-6:
            i = int(np.argmin(np.abs(keys - x)))
            out[k] = ut[i] + (x - keys[i]) * spb
            continue
        wt = np.clip(1.0 - np.abs(keys[sel] - x) / (w + 1e-9), 0.05, None)
        xb, yt = keys[sel], ut[sel]
        mx, my = np.average(xb, weights=wt), np.average(yt, weights=wt)
        slope = np.sum(wt * (xb - mx) * (yt - my)) / max(np.sum(wt * (xb - mx) ** 2), 1e-12)
        slope = float(np.clip(slope, 0.5 * spb, 2.0 * spb))
        out[k] = my + (x - mx) * slope
    for k in range(1, len(out)):  # increasing: at least a fifth of a beat's time a beat
        out[k] = max(out[k], out[k - 1] + 0.2 * spb * max(q[k] - q[k - 1], 0.0))
    return out


def in_order(t, instrument: str) -> list:
    """Each event's note as found, in the events' order: what gives a note its time in the take. The piano's
    path writes its events strike by strike from the top key down, its notes found (`written`) in the order
    each strike's keys were read: sorted alike, they pair (a strike's notes share its time)."""
    w = list(t.written or [])
    if instrument == "piano" and len(w) == len(t.events):
        w = sorted(w, key=lambda n: (n.onset_s, -n.midi))
    return w


def score_beats(pickup: float, bpb: int, total: float) -> list[tuple[float, int, int]]:
    """The score's beats on the bar's grid, from its start to its end: (beat from the score's start,
    bar (0: the pickup bar), beat in the bar)."""
    out = []
    j = -int(np.floor(pickup + 1e-9))
    while pickup + j <= total + 1e-9:
        b = pickup + j
        if b >= -1e-9:
            bar = (j // bpb) + 1 if j >= 0 else 0
            in_bar = (j % bpb) + 1
            out.append((round(b, 6), bar, in_bar))
        j += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--take", default="take-01.flac")
    ap.add_argument("--instrument", choices=("dobro", "bass", "piano"), default="dobro")
    ap.add_argument("--bpm", type=float, required=True, help="the tempo given (the click's, if there was one)")
    ap.add_argument("--beats-per-bar", type=int, default=4)
    ap.add_argument("--metronome", action="store_true", help="played to a click at --bpm")
    ap.add_argument("--downbeat", type=float, metavar="S", help="bar 1's first beat in the take (from the click)")
    ap.add_argument("--track-beats", action="store_true", help="no click: the beat tracker from --bpm")
    ap.add_argument("--title", default="")
    a = ap.parse_args()
    started = time.monotonic()

    say("loading")
    from transcribe.detect import SR, load
    from transcribe.transcribe import to_bars, transcribe, write_transcription

    piano = a.instrument == "piano"
    if piano:  # at the take's own rate
        from transcribe.pianoscore import load as load_piano

        y, sr = load_piano(a.folder / a.take)
    else:
        y, sr = load(a.folder / a.take), SR
    take_s = len(y) / sr
    bpm, tracked = a.bpm, None
    if a.track_beats and not a.metronome:
        say("beats")
        from transcribe.chords import beat_grid

        tracked = [float(x) for x in beat_grid(y if sr == SR else load(a.folder / a.take), a.bpm)]
        if len(tracked) > 4:
            found = 60.0 / float(np.median(np.diff(tracked)))
            if abs(found / a.bpm - 1.0) <= TRACK_SPAN:
                bpm = round(found, 1)

    say("notes")
    t = transcribe(y, bpm, a.beats_per_bar, metronome=a.metronome, instrument=a.instrument,
                   downbeat_s=a.downbeat, sr=sr)

    say("writing")
    bpb, pickup = t.beats_per_bar, float(t.pickup_beats)
    xml = a.folder / "transcription.musicxml"
    tmp = a.folder / "transcription.musicxml.tmp"
    if t.events:
        write_transcription(t, tmp, a.title or f"Transcription: {a.folder.name}")
        if piano:
            from transcribe.pianoscore import to_piano_bars

            n_bars = len(to_piano_bars(t.events, bpb))
        else:
            n_bars = len(to_bars(t.events, bpb, pickup))
    else:
        write_empty(tmp, a.title or f"Transcription: {a.folder.name}", t, a.instrument)
        n_bars = 1
    total = (pickup + (n_bars - 1) * bpb) if pickup > 0 else n_bars * bpb
    spb = 60.0 / bpm
    written = in_order(t, a.instrument)
    note_beats = [e.onset_beats for e in t.events]
    note_times = [w.onset_s for w in written]
    grid = "click" if a.metronome and a.downbeat is not None else "notes" if t.events else "tempo"
    grid_beats = score_beats(pickup, bpb, total)
    query = [b for b, _, _ in grid_beats]
    if grid == "click":
        spb = 60.0 / a.bpm
        # the score's bar 1 this many beats from the downbeat: the piano's path writes notes found before the
        # downbeat in a bar of their own before it (the others, as a pickup)
        lead = 0.0
        if piano and note_times:
            off = float(np.median([(s - a.downbeat) / spb - b for b, s in zip(note_beats, note_times)]))
            lead = round(off / bpb) * bpb
        times = [a.downbeat + (b - pickup + lead) * spb for b in query]
    elif grid == "notes":
        times = list(fit_times(note_beats, note_times, query, spb))
    else:
        start = a.downbeat if a.downbeat is not None else 0.0
        times = [start + (b - pickup) * spb for b in query]
    at = lambda b: float(np.interp(b, query, times)) if len(query) > 1 else float(times[0]) + (b - query[0]) * spb
    notes = []
    for e, w in zip(t.events, written):
        n = {"t": round(w.onset_s, 4), "beat": round(e.onset_beats, 4), "bar": e.bar,
             "length_beats": round(e.duration_beats, 4),
             "length_s": round(max(0.0, at(e.onset_beats + e.duration_beats) - at(e.onset_beats)), 4),
             "end_s": None if w.offset_s is None else round(w.offset_s, 4),
             "midi": e.midi, "pitch": pitch_name(e.midi)}
        if piano:  # the staff is the hand (the pipeline's `string`): 1 the treble staff, 2 the bass staff
            n.update(staff=int(e.string), hand="right" if int(e.string) == 1 else "left")
        else:
            n.update(string=e.string, fret=e.fret, attack=bool(e.attack), technique=e.technique)
        notes.append(n)
    beat0, bar1 = at(0.0), at(pickup)
    out = {
        "format": 1,
        "instrument": a.instrument,
        "bpm_given": a.bpm,
        "bpm": bpm,  # the tempo the pipeline was given
        "metronome": a.metronome,
        "beats_per_bar": bpb,
        "pickup_beats": pickup,
        "downbeat_s": a.downbeat,  # bar 1's first beat as the click put it (None: not known)
        "beat0_s": round(beat0, 4),  # the score's beat 0 (its first bar's start: a pickup's, if any) in the take
        "bar1_s": round(bar1, 4),  # bar 1's first beat in the take: the downbeat told, else the transcription's
        "bars": n_bars,
        "beats_total": total,
        "tuning": None if piano else {str(s): int(m) for s, m in sorted(t.tuning.items())},  # the piano has no strings
        "grid": grid,  # where the beats' times come from: the click, the notes, or the tempo alone
        "beats": [{"beat": b, "bar": bar, "in_bar": k, "t": round(float(x), 4)}
                  for (b, bar, k), x in zip(grid_beats, times)],
        "tracked_beats": None if tracked is None else [round(x, 4) for x in tracked],
        "notes": notes,
        "take_seconds": round(take_s, 3),
        "seconds": round(time.monotonic() - started, 2),
    }
    tmp.replace(xml)
    jtmp = a.folder / "transcription.json.tmp"
    jtmp.write_text(json.dumps(out, indent=1))
    jtmp.replace(a.folder / "transcription.json")
    say("done", notes=len(notes), bars=n_bars, bpm=bpm, pickup_beats=pickup, beat0_s=out["beat0_s"],
        bar1_s=out["bar1_s"], seconds=out["seconds"], grid=grid)


def write_empty(path: Path, title: str, t, instrument: str) -> None:
    """No notes found: a bar's rest, so the page still draws a staff and tab (the piano: a grand staff)."""
    if instrument == "piano":
        from transcribe.notation import Rest, write_piano_score

        bar = 4 * t.beats_per_bar
        write_piano_score(path, title, [([Rest(bar)], [Rest(bar)])], tempo_bpm=round(t.bpm),
                          beats_per_bar=t.beats_per_bar)
        return
    from transcribe.notation import DOBRO, UPRIGHT_BASS, Rest, write_score

    write_score(path, title, [[Rest(4 * t.beats_per_bar)]], tempo_bpm=round(t.bpm), tuning=t.tuning,
                beats_per_bar=t.beats_per_bar, instrument=UPRIGHT_BASS if instrument == "bass" else DOBRO)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # the app shows this line
        say("failed", error=f"{type(e).__name__}: {e}")
        raise
