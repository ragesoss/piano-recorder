"""The chart: an exercise's notes as played, rendered to audio on the desktop when it is pushed
(tools/push-exercise), so the exercise app can play it over the speakers before a take, with the
take's own count-in and click (Engine.play_chart). The control unit only plays the file.

- `read_notes`: the notes of the score's tab staff (else its first), in play order (repeats played
  out as the click counts them: `play_order`), in beats from beat 0 (the music's first note: the
  pickup's first beat, or bar 1's downbeat). Each note's pitch (as it sounds), its length (ties
  joined), its string, whether it is picked (a legato slide, hammer-on, pull-off or slurred bow
  change is not), a slide from the note before on its string, a scoop from below, and vibrato
  (words over the note: "vib.", "slow, wide vib."; "no vib." none). Words "soft" and "loud" set
  the velocity from there on, "arco" and "pizz." the bass's sound.
- `midi_file`: a MIDI file of them: two channels per string, by turns, so the pitch bend of one
  note (a slide, a scoop, vibrato; bend range 12 semitones) never bends the next.
- `render`: the MIDI file through FluidSynth (a program, used at push time) with the instrument's
  SoundFont (`SOUNDS`), mono, 48 kHz, 16-bit, its peak at -1 dBFS, beat 0 at `LEAD_S`.

    python -m exercises.chart SCORE.musicxml OUT.wav [--instrument dobro]
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import tempfile
import wave
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
RATE = 48000
LEAD_S = 0.5  # silence before beat 0 in the file (room for a slide or grace before it)
PEAK_DBFS = -1.0
VELOCITY = 92  # a picked note; "soft" and "loud" words set it from there on
SOFT, LOUD = 58, 116
LEGATO_VELOCITY = 0.6  # a hammer-on, pull-off or slurred bow change: a new note, this much softer
SCOOP_ST, SCOOP_S = 2.0, 0.12  # a scoop: picked this far below, slid up over this long
VIB_DELAY_S, VIB_RAMP_S = 0.15, 0.25  # vibrato starts this long into the note, and widens over this
BEND_RANGE = 12  # semitones either way (RPN 0 on every channel)
BEND_STEP_S = 0.008  # a moving bend is written this often
PPQ = 9600  # ticks per quarter at 60 bpm: a tick is about 0.1 ms
DRUMS = 9  # MIDI channel 10, left alone
TAIL_S = 0.4  # kept after the sound falls 60 dB under its peak


@dataclass(frozen=True)
class Sound:
    name: str  # what the page and exercise.json say it is
    font: str  # the SoundFont file, looked for in `sound_dirs()`
    program: int
    bank: int = 0
    gain: float = 0.5  # FluidSynth's -g: low enough not to clip before the peak is set


# The closest to each instrument we have. The dobro and the bass are the player's own instruments
# (prototype SoundFonts made from the exercise takes, private: not for publication), the rest
# GeneralUser GS v2.0.3 (S. Christian Collins: free to use, private or commercial).
SOUNDS = {
    "dobro": Sound("the Beard dobro (the player's own samples)", "dobro.sf2", 25),
    "bass": Sound("the Cleveland upright bass, plucked (the player's own samples)", "upright.sf2", 32),
    "bass-arco": Sound("a bowed double bass (GeneralUser GS)", "GeneralUser-GS.sf2", 43),
    "guitar": Sound("a steel-string guitar (GeneralUser GS)", "generaluser.sf2", 25),
    "banjo": Sound("a banjo (GeneralUser GS)", "generaluser.sf2", 105),
}


def sound_dirs() -> list[Path]:
    """Where the SoundFonts are looked for: $EXERCISE_SOUNDS (folders, colon-separated), then the
    ambient synth's (~/play/ambient-room/sounds: dobro, upright, generaluser), then the usual."""
    env = [Path(p).expanduser() for p in os.environ.get("EXERCISE_SOUNDS", "").split(":") if p]
    return env + [Path.home() / "play" / "ambient-room" / "sounds", Path.home() / ".local" / "share" / "sounds" / "sf2",
                  Path("/usr/share/sounds/sf2")]


def find_font(name: str) -> Path | None:
    return next((d / name for d in sound_dirs() if (d / name).is_file()), None)


@dataclass
class Note:
    start: float  # beats from beat 0
    length: float  # beats, tied notes joined
    midi: int  # as it sounds
    string: int  # 1 = the highest; without a tab, its place in its chord (1 = the first written)
    picked: bool = True  # False: reached by a legato slide, hammer-on, pull-off or slurred bow change
    slide: bool = False  # slid into from the note before on its string (picked again: a shift slide)
    scoop: float = 0.0  # slid into from this many semitones below, as it is picked
    vibrato: tuple | None = None  # (from beat, rate Hz, depth semitones either side)
    velocity: int = VELOCITY
    arco: bool = False

    @property
    def end(self) -> float:
        return self.start + self.length


def play_order(measures: list) -> list:
    """The measures as played: a backward repeat returns to the last forward repeat (or the
    bar after the previous backward repeat, or the start), once; a volta ending only on its
    pass. (As dobro-transcription's score reader; tools/push-exercise counts the click by it.)"""
    endings, active = [], None
    for m in measures:
        for e in m.findall("barline/ending"):
            if e.get("type") == "start":
                active = {int(x) for x in e.get("number").replace(" ", "").split(",")}
        endings.append(active)
        if any(e.get("type") in ("stop", "discontinue") for e in m.findall("barline/ending")):
            active = None
    order, i, start, pass_no, done = [], 0, 0, 1, set()
    while i < len(measures):
        m = measures[i]
        if m.find("barline/repeat[@direction='forward']") is not None and i != start:
            start, pass_no = i, 1
        if endings[i] is not None and pass_no not in endings[i]:
            i += 1
            continue
        order.append(m)
        if m.find("barline/repeat[@direction='backward']") is not None:
            if i not in done:
                done.add(i)
                i, pass_no = start, pass_no + 1
                continue
            start, pass_no = i + 1, 1
        i += 1
    return order


def measure_lengths(measures: list) -> list[int]:
    """Each measure's length in divisions: as far as anything in it reaches."""
    out = []
    for m in measures:
        cursor = end = 0
        for el in m:
            if el.tag == "backup":
                cursor -= int(el.findtext("duration"))
            elif el.tag == "forward":
                cursor += int(el.findtext("duration"))
            elif el.tag == "note" and el.find("chord") is None and el.find("grace") is None:
                cursor += int(el.findtext("duration") or 0)
            end = max(end, cursor)
        out.append(end)
    return out


def _midi(pitch: ET.Element, transpose: int) -> int:
    alter = float(pitch.findtext("alter") or 0)
    return 12 * (int(pitch.findtext("octave")) + 1) + STEPS[pitch.findtext("step")] + int(round(alter)) + transpose


def _vibrato(text: str) -> tuple | None:
    """A vibrato's (rate Hz, depth semitones) from its words; None for none ("no vib.")."""
    t = text.lower()
    if "vib" not in t or t.startswith("no ") or "no vib" in t:
        return None
    rate = 4.0 if "slow" in t else 7.0 if "fast" in t else 5.5  # exercise 026: slow about 4 a second, fast 7
    depth = 0.12 if "narrow" in t else 0.45 if "wide" in t else 0.25
    return rate, depth


def _play_staff(part: ET.Element) -> str | None:
    """The staff to read: the tab staff (it says which string each note is on), else the first."""
    for clef in part.iter("clef"):
        if clef.findtext("sign") == "TAB":
            return clef.get("number", "1")
    return None


def read_notes(path: Path) -> tuple[list[Note], dict]:
    """The score's notes as played (see the module's docstring), and {"bpm", "beats"} (beats: from
    beat 0 to the end of the last bar, repeats played out, as the click counts them)."""
    root = ET.parse(path).getroot()
    part = root.find("part")
    measures = part.findall("measure")
    staff = _play_staff(part)
    lengths = dict(zip(map(id, measures), measure_lengths(measures)))
    divisions, bpm, transpose = None, None, 0
    notes: list[Note] = []
    segments: list[tuple[float, Note]] = []  # every written note's onset, tied continuations too
    marks: list[tuple[float, tuple]] = []  # vibrato words: (where, (rate, depth))
    settings: list[tuple[float, str, object]] = []  # words that hold from there on: (where, "velocity" or "arco", value)
    sounding: dict[int, Note] = {}  # the last note on each string
    slurs: dict[str, int] = {}  # open slurs on the staff read: number -> string
    scoop_next: dict[int, float] = {}  # a grace note slid from, before the next note on its string
    bar_start = 0.0
    for m in play_order(measures):
        cursor, chord_place, last_onset = 0.0, 0, None
        for el in m:
            if el.tag == "attributes":
                if el.findtext("divisions"):
                    divisions = int(el.findtext("divisions"))
                tr = el.find("transpose")
                if tr is not None:
                    transpose = int(tr.findtext("chromatic") or 0) + 12 * int(tr.findtext("octave-change") or 0)
            elif el.tag == "direction":
                s = el.find("sound")
                if s is not None and s.get("tempo") and bpm is None:
                    bpm = float(s.get("tempo"))
                at = bar_start + cursor / divisions
                for w in el.iter("words"):
                    text = (w.text or "").strip().lower()
                    vib = _vibrato(text)
                    if vib:
                        marks.append((at, vib))
                    if text in ("soft", "loud"):
                        settings.append((at, "velocity", SOFT if text == "soft" else LOUD))
                    elif text.startswith(("arco", "pizz")):
                        settings.append((at, "arco", text.startswith("arco")))
            elif el.tag == "backup":
                cursor -= int(el.findtext("duration"))
            elif el.tag == "forward":
                cursor += int(el.findtext("duration"))
            elif el.tag == "note":
                grace = el.find("grace") is not None
                dur = 0 if grace else int(el.findtext("duration") or 0)
                is_chord = el.find("chord") is not None
                onset = (last_onset if is_chord and last_onset is not None else cursor)
                if not is_chord and not grace:
                    cursor += dur
                    last_onset = onset
                if staff is not None and el.findtext("staff", "1") != staff:
                    continue
                chord_place = chord_place + 1 if is_chord else 1
                if el.find("rest") is not None or el.find("pitch") is None:
                    continue
                string = int(el.findtext("notations/technical/string") or 0) or chord_place
                midi = _midi(el.find("pitch"), transpose)
                if grace:  # an ornament: slid from into the next note on its string, else left out
                    if any(sl.get("type") == "start" for sl in el.findall("notations/slide")):
                        scoop_next[string] = midi
                    continue
                start, length = bar_start + onset / divisions, dur / divisions
                if any(t.get("type") == "stop" for t in el.findall("tie")) and string in sounding \
                        and sounding[string].midi == midi and abs(sounding[string].end - start) < 1e-6:
                    sounding[string].length += length  # a tied continuation: the same sound, longer
                    segments.append((start, sounding[string]))
                    continue
                slurred = [(sl.get("type"), sl.get("number", "1")) for sl in el.findall("notations/slur")]
                legato = any(kind == "stop" and n in slurs for kind, n in slurred)
                for kind, n in slurred:
                    if kind == "stop":
                        slurs.pop(n, None)
                for kind, n in slurred:
                    if kind == "start":
                        slurs[n] = string
                note = Note(start, length, midi, string, picked=not legato,
                            slide=any(sl.get("type") == "stop" for sl in el.findall("notations/slide")))
                if string in scoop_next:
                    note.scoop = max(0.5, note.midi - scoop_next.pop(string))
                elif el.find("notations/articulations/scoop") is not None:
                    note.scoop = SCOOP_ST
                prev = sounding.get(string)
                if note.slide and (prev is None or prev.end < start - 1e-6):
                    note.slide = False  # nothing sounding on the string to slide from
                notes.append(note)
                segments.append((start, note))
                sounding[string] = note
        bar_start += lengths[id(m)] / divisions
    if not divisions or not bpm:
        raise ValueError(f"{path}: no divisions or no tempo marking (<sound tempo=...>)")
    for at, key, value in sorted(settings, key=lambda s: s[0]):  # in time order (staves are written one after another)
        for n in notes:
            if n.start >= at - 1e-6:
                setattr(n, key, value)
    for at, vib in marks:  # vibrato: on the notes that start where its words are (or next)
        onsets = sorted({s for s, _ in segments if s >= at - 1e-6})
        if onsets:
            for s, n in segments:
                if abs(s - onsets[0]) < 1e-6 and (n.vibrato is None or n.vibrato[0] > s):
                    n.vibrato = (s, *vib)
    notes.sort(key=lambda n: (n.start, -n.midi))
    return notes, {"bpm": bpm, "beats": bar_start}


# --- the MIDI file

@dataclass
class _Voice:
    """One MIDI note: a pick, and the notes slid to from it without a pick."""
    channel: int
    key: int
    on: float  # seconds from beat 0
    off: float
    velocity: int
    start_bend: float = 0.0  # semitones, at the pick (a scoop starts below)
    moves: list = field(default_factory=list)  # (from s, to s, from st, to st): the bend moving
    vibratos: list = field(default_factory=list)  # (from s, to s, rate, depth)
    arco: bool = False

    def bend_at(self, t: float) -> float:
        b = self.start_bend
        for t0, t1, a, z in self.moves:
            if t >= t1:
                b = z
            elif t > t0:
                x = (t - t0) / (t1 - t0)
                b = a + (z - a) * x * x * (3 - 2 * x)  # smooth, like a hand moving the bar
        for t0, t1, rate, depth in self.vibratos:
            if t0 < t < t1:
                b += depth * min(1.0, (t - t0) / VIB_RAMP_S) * math.sin(2 * math.pi * rate * (t - t0))
        return b


def _glide_s(semitones: float, before_s: float) -> float:
    """How long a slide takes, arriving on its note: longer for a longer slide, and at most half
    the note it leaves."""
    return max(0.02, min(0.06 + 0.035 * abs(semitones), 0.3, 0.5 * before_s))


def voices(notes: list[Note], bpm: float) -> list[_Voice]:
    """The notes as MIDI notes: a legato slide bends the note before; a shift slide bends it and
    then picks again; each string's notes take its two channels by turns."""
    spb = 60.0 / bpm
    chans = [c for c in range(16) if c != DRUMS]
    turn: dict[int, int] = {}
    last: dict[int, tuple[_Voice, Note]] = {}  # each string's last voice, and the note it sounds now
    out: list[_Voice] = []
    for n in notes:
        t0, t1 = n.start * spb, n.end * spb
        prev = last.get(n.string)
        if prev is not None and prev[0].off > t0 + 1e-6:
            prev[0].off = t0  # a new note on the string stops the one before
        if n.slide and prev is not None:
            v, was = prev
            a = was.midi - v.key
            g = _glide_s(n.midi - was.midi, t0 - was.start * spb)
            v.moves.append((t0 - g, t0, a, n.midi - v.key))
            if not n.picked:  # legato: the same sound goes on, at the new pitch
                v.off = t1
                if n.vibrato:
                    v.vibratos.append((n.vibrato[0] * spb + VIB_DELAY_S, t1, *n.vibrato[1:]))
                last[n.string] = (v, n)
                continue
        k = turn.get(n.string, 0)
        turn[n.string] = k + 1
        vel = n.velocity if n.picked else int(n.velocity * LEGATO_VELOCITY)
        v = _Voice(channel=chans[(2 * (n.string - 1) + k % 2) % len(chans)], key=n.midi, on=t0, off=t1, velocity=vel,
                   arco=n.arco)
        if n.scoop:
            v.start_bend = -n.scoop
            v.moves.append((t0, t0 + SCOOP_S, -n.scoop, 0.0))
        if n.vibrato:
            v.vibratos.append((n.vibrato[0] * spb + VIB_DELAY_S, t1, *n.vibrato[1:]))
        out.append(v)
        last[n.string] = (v, n)
    return out


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _bend_value(st: float) -> int:
    return max(0, min(16383, 8192 + int(round(st / BEND_RANGE * 8191))))


def midi_file(notes: list[Note], bpm: float, sound: Sound, arco: Sound | None = None) -> bytes:
    """A type-0 MIDI file of the notes: beat 0 at `LEAD_S`, 60 bpm with `PPQ` ticks to the beat
    (so a tick is a fixed time), the sound's program (an arco note: `arco`'s) on every channel."""
    vs = voices(notes, bpm)
    events: list[tuple[int, int, bytes]] = []  # (tick, order, message): offs, then controls, then ons

    def at(t: float) -> int:
        return max(0, int(round((LEAD_S + t) * PPQ)))

    used = sorted({v.channel for v in vs})
    for c in used:
        events.append((0, 1, bytes([0xB0 | c, 0, sound.bank])))
        events.append((0, 1, bytes([0xC0 | c, sound.program])))
        for cc, val in ((101, 0), (100, 0), (6, BEND_RANGE), (38, 0), (101, 127), (100, 127)):
            events.append((0, 1, bytes([0xB0 | c, cc, val])))
    program = {c: sound for c in used}
    for v in vs:
        c = v.channel
        want = arco if (v.arco and arco is not None) else sound
        if program[c] is not want:
            events.append((at(v.on), 1, bytes([0xB0 | c, 0, want.bank])))
            events.append((at(v.on), 1, bytes([0xC0 | c, want.program])))
            program[c] = want
        last = _bend_value(v.bend_at(v.on))
        events.append((at(v.on), 1, bytes([0xE0 | c, last & 0x7F, last >> 7])))
        events.append((at(v.on), 2, bytes([0x90 | c, v.key, max(1, min(127, v.velocity))])))
        t = v.on + BEND_STEP_S
        while t < v.off:
            b = _bend_value(v.bend_at(t))
            if b != last:
                events.append((at(t), 1, bytes([0xE0 | c, b & 0x7F, b >> 7])))
                last = b
            t += BEND_STEP_S
        events.append((at(v.off), 0, bytes([0x80 | c, v.key, 0])))
    end = max((v.off for v in vs), default=0.0) + 3.0  # room for the last notes to ring out
    events.sort(key=lambda e: (e[0], e[1]))
    track, now = bytearray(), 0
    track += _vlq(0) + bytes([0xFF, 0x51, 3]) + (1_000_000).to_bytes(3, "big")  # 60 bpm
    for tick, _, msg in events:
        track += _vlq(tick - now) + msg
        now = tick
    track += _vlq(max(0, at(end) - now)) + bytes([0xFF, 0x2F, 0])
    return b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") + (1).to_bytes(2, "big") + PPQ.to_bytes(2, "big") \
        + b"MTrk" + len(track).to_bytes(4, "big") + bytes(track)


# --- rendering

def sound_for(instrument: str) -> Sound:
    return SOUNDS.get(instrument) or SOUNDS["guitar"]


def render(score: Path, out: Path, instrument: str, fluidsynth: str | None = None) -> dict:
    """The score's chart as a WAV (see the module's docstring). Returns what exercise.json says of it:
    {"file", "beat0_s", "seconds", "sound", "notes"}. Raises RuntimeError if it cannot be made."""
    import numpy as np  # the desktop's; the control unit never renders

    notes, info = read_notes(score)
    if not notes:
        raise RuntimeError(f"{score}: no notes to play")
    sound = sound_for(instrument)
    arco = SOUNDS["bass-arco"] if instrument == "bass" and any(n.arco for n in notes) else None
    fonts = []
    for s in [sound] + ([arco] if arco else []):
        f = find_font(s.font)
        if f is None:
            raise RuntimeError(f"no SoundFont {s.font} in {', '.join(map(str, sound_dirs()))} ($EXERCISE_SOUNDS)")
        if f not in fonts:
            fonts.append(f)
    synth = fluidsynth or shutil.which("fluidsynth")
    if not synth:
        raise RuntimeError("FluidSynth not found (Debian: apt install fluidsynth)")
    gain = sound.gain
    with tempfile.TemporaryDirectory() as tmp:
        mid, wav = Path(tmp) / "chart.mid", Path(tmp) / "chart.wav"
        mid.write_bytes(midi_file(notes, info["bpm"], sound, arco))
        for _ in range(4):  # quieter again if it clipped
            # the arco font first, so the instrument's own program wins where both have it
            cmd = [synth, "-ni", "-q", "-R", "0", "-C", "0", "-g", f"{gain:g}", "-r", str(RATE), "-O", "s32",
                   "-T", "wav", "-F", str(wav)] + [str(f) for f in reversed(fonts)] + [str(mid)]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if r.returncode or not wav.exists():
                raise RuntimeError(f"FluidSynth failed ({r.returncode}): {r.stderr[-400:]}")
            with wave.open(str(wav)) as w:
                ch, width, frames = w.getnchannels(), w.getsampwidth(), w.readframes(w.getnframes())
            x = np.frombuffer(frames, dtype={2: "<i2", 4: "<i4"}[width]).astype(np.float64) / (2 ** (8 * width - 1))
            x = x.reshape(-1, ch).mean(axis=1)
            peak = float(np.max(np.abs(x))) if len(x) else 0.0
            if peak < 0.9:
                break
            gain /= 2
    if peak <= 0:
        raise RuntimeError("FluidSynth rendered silence")
    x *= 10 ** (PEAK_DBFS / 20) / peak
    loud = np.flatnonzero(np.abs(x) > 10 ** ((PEAK_DBFS - 60) / 20))
    x = x[: min(len(x), int(loud[-1] + TAIL_S * RATE))]
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.round(x * 32767).astype("<i2").tobytes())
    return {"file": out.name, "beat0_s": LEAD_S, "seconds": round(len(x) / RATE, 2),
            "sound": (sound.name + (f"; arco: {arco.name}" if arco else "")), "notes": len(notes),
            "beats": info["beats"], "bpm": info["bpm"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("score", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--instrument", default="dobro", choices=sorted(k for k in SOUNDS if "-" not in k))
    ap.add_argument("--list", action="store_true", help="print the notes as read, too")
    a = ap.parse_args()
    if a.list:
        notes, info = read_notes(a.score)
        print(f"{info['bpm']:g} bpm, {info['beats']:g} beats")
        for n in notes:
            print(f"{n.start:7.3f} {n.length:6.3f} midi {n.midi:3d} string {n.string} "
                  f"{'picked' if n.picked else 'legato'}{' slide' if n.slide else ''}"
                  f"{f' scoop {n.scoop:g}' if n.scoop else ''}{f' vib {n.vibrato}' if n.vibrato else ''} vel {n.velocity}"
                  f"{' arco' if n.arco else ''}")
    print(render(a.score, a.out, a.instrument))


if __name__ == "__main__":
    main()
