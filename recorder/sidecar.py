"""A take's channels by name: what its sidecar says each channel carried, and one channel picked out by it.

Every app's take has a sidecar (`<take>.json` beside its WAV; a transcribe app take's `raw/<id>.json` and its
`meta.json`) whose `channels` lists, for each channel of the recording, the interface's input it came from and that
input's name as the room had it (recorder/inputs.py): `{"channel", "input", "input_name", "input_details",
"input_for"}`, and in an exercise take what it carried (`carries`: an instrument's id, "room" or "none"). The tools
that bring a take in (tools/pull-take, transcriber/importer.py; dobro-transcription's exercises/bring_in.py has its
twin, `transcribe.sidecar`) pick a channel by its input's name or number, not by its place, so a take whose inputs
were in another order, or another number of them, reads the same.

Sidecars of before 2026-10-20 still read: the exercise app's had `channels` (without `input_details` and
`input_for`); the chord app's named each channel `name`; the practice recorder's had none (`channels` is then
empty, and a channel can only be asked for by its place).

    python -m recorder.sidecar TAKE.wav                         what each channel is
    python -m recorder.sidecar TAKE.wav --input "Room mic" --out room.wav
                                                                that input's channel alone (also --input 4)
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path


def _same(a: str, b: str) -> bool:
    return " ".join(str(a).split()).casefold() == " ".join(str(b).split()).casefold()


def channels(meta: dict) -> list[dict]:
    """The sidecar's channels, each with `channel`, `input` and `input_name` ("" when not said)."""
    out = []
    for c in meta.get("channels") or []:
        if not isinstance(c, dict) or "channel" not in c:
            continue
        out.append({**c, "channel": int(c["channel"]), "input": c.get("input"),
                    "input_name": c.get("input_name") or c.get("name") or ""})
    return out


def find_channel(meta: dict, name: str | int | None = None, carries: str | None = None) -> int:
    """The channel of the input called `name` (any case; or its number: 4, "4", "In 4", "input 4"), else the first
    that carried `carries`. Raises LookupError saying what the take has."""
    chans = channels(meta)
    if not chans:
        raise LookupError("the sidecar does not say what each channel is (a take of before 2026-10-20): "
                          "give the channel by its place")
    if name is not None and str(name).strip():
        text = str(name).strip()
        hit = [c for c in chans if _same(c["input_name"], text)]
        if not hit:
            low = text.lower()
            for prefix in ("input ", "in "):
                if low.startswith(prefix):
                    low = low[len(prefix):].strip()
            if low.isdigit():
                hit = [c for c in chans if c["input"] == int(low)]
        if not hit:
            raise LookupError(f"no input {text!r} in this take: it has {described(chans)}")
        return hit[0]["channel"]
    if carries:
        hit = [c for c in chans if c.get("carries") == carries]
        if hit:
            return hit[0]["channel"]
        raise LookupError(f"no channel carried {carries!r}: it has {described(chans)}")
    raise LookupError("say which input")


def described(chans: list[dict]) -> str:
    return ", ".join(f"channel {c['channel']} input {c['input']} ({c['input_name'] or 'not named'})" for c in chans) \
        or "no channels described"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav", type=Path, help="the take's WAV, its sidecar (.json) beside it")
    ap.add_argument("--input", help="an input's name as the sidecar has it, or its number")
    ap.add_argument("--out", type=Path, help="write that input's channel alone here (a WAV, the take's format)")
    a = ap.parse_args(argv)
    side = a.wav.with_suffix(".json")
    meta = json.loads(side.read_text()) if side.exists() else {}
    chans = channels(meta)
    if a.input is None:
        for c in chans:
            print(f"channel {c['channel']}: input {c['input']}, {c['input_name'] or 'not named'}"
                  + (f" ({c['input_details']})" if c.get("input_details") else "")
                  + (f", carried {c.get('carries_name') or c['carries']}" if c.get("carries") else ""))
        if not chans:
            print(f"{side.name}: no channels described", file=sys.stderr)
        return
    try:
        ch = find_channel(meta, a.input)
    except LookupError as e:
        sys.exit(f"{a.wav.name}: {e}")
    if a.out is None:
        print(ch)
        return
    with wave.open(str(a.wav)) as w:
        n, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.readframes(w.getnframes())
    if ch >= n:
        sys.exit(f"{a.wav.name}: the sidecar names channel {ch}, the WAV has {n}")
    if width not in (2, 4):
        sys.exit(f"{a.wav.name}: {8 * width}-bit samples (the apps write 16 or 32)")
    import numpy as np
    mono = np.frombuffer(frames, dtype=f"<i{width}").reshape(-1, n)[:, ch].copy().tobytes()
    with wave.open(str(a.out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(mono)
    print(f"{a.out}: channel {ch} of {a.wav.name} ({a.input})")


if __name__ == "__main__":
    main()
