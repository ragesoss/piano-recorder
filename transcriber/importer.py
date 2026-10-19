"""Bring a recording in as a take of the transcribe app, and have the app transcribe it: a take made
elsewhere (a phone, the exercise app, the corpus), or one to try the app with.

    tools/import-take FILE --instrument dobro|bass|piano --bpm 100 [--beats-per-bar 4] [--click]
                      [--downbeat S] [--channel N] [--title T]

Runs where the app runs, with its config ($TAKE_TRANSCRIBER_CONFIG, else /etc/take-transcriber.toml):
the take's folder is made in its takes_dir as a recorded take's is (store.py), `take-01.flac` from
FILE's channel N (default: all channels mixed to one) at FILE's own rate, 24-bit; then the app is
asked to transcribe it (POST /api/takes/ID/transcribe). `--click`: it was played to a click at
--bpm; `--downbeat`: bar 1's first beat in FILE (s), as a click's line-up would give it. A piano exercise take
(dobro-transcription `exercises/piano/*/take-01.flac`, the room mic alone; its click's beat 0, bar 1's downbeat, is
takes.csv's beat0_s): `--instrument piano --bpm 60 --click --downbeat 4.366`.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from recorder.config import load_config
from recorder.takes import ID_FORMAT

from . import VERSION
from .jobs import FLAC24, JobError, _ffmpeg
from .server import METERS, SYSTEM_CONFIG
from .settings import load_settings
from .store import TAKE, Folders


def probe(path: Path) -> tuple[float, int, int]:
    """The file's length (s), rate and channels (ffprobe)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                        "stream=sample_rate,channels:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"{path}: not a sound file ffprobe can read ({r.stderr.strip()})")
    info = json.loads(r.stdout)
    st = info["streams"][0]
    return float(info["format"]["duration"]), int(st["sample_rate"]), int(st["channels"])


def new_id(folders: Folders, when: datetime) -> str:
    t = when.timestamp()
    while (folders.root / datetime.fromtimestamp(t).strftime(ID_FORMAT)).exists():
        t += 1
    return datetime.fromtimestamp(t).strftime(ID_FORMAT)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", type=Path)
    ap.add_argument("--instrument", required=True)
    ap.add_argument("--bpm", type=float, required=True)
    ap.add_argument("--beats-per-bar", type=int, default=4, choices=METERS)
    ap.add_argument("--click", action="store_true", help="played to a click at --bpm")
    ap.add_argument("--downbeat", type=float, metavar="S", help="bar 1's first beat in the file (s)")
    ap.add_argument("--channel", type=int, help="the file's channel with the instrument (default: all, mixed)")
    ap.add_argument("--title", default="")
    ap.add_argument("--no-transcribe", action="store_true", help="make the folder only")
    a = ap.parse_args(argv)
    cfg = load_config(Path(os.environ.get("TAKE_TRANSCRIBER_CONFIG", SYSTEM_CONFIG)))
    settings = load_settings(cfg)
    inst = settings.instrument(a.instrument)
    if inst is None:
        sys.exit(f"--instrument: one of {', '.join(i.kind for i in settings.instruments)}")
    seconds, rate, channels = probe(a.file)
    if a.channel is not None and not 0 <= a.channel < channels:
        sys.exit(f"--channel: the file has channels 0-{channels - 1}")
    folders = Folders(cfg.takes_dir)
    take_id = new_id(folders, datetime.now())
    d = folders.create(take_id)
    mix = ["-af", f"pan=mono|c0=c{a.channel}"] if a.channel is not None else ["-ac", "1"]
    try:
        _ffmpeg([settings.ffmpeg], ["-i", str(a.file), *mix, *FLAC24], d / TAKE)
    except JobError as e:
        folders.delete(take_id)
        sys.exit(str(e))
    meta = {"instrument": inst.kind, "instrument_name": inst.name, "input": None, "input_name": "",
            "channel": a.channel, "bpm": a.bpm, "beats_per_bar": a.beats_per_bar,
            "count_in_bars": 0, "click": a.click, "title": " ".join(a.title.split())[:120],
            "recorded_at": datetime.fromtimestamp(a.file.stat().st_mtime).isoformat(timespec="seconds"),
            "seconds": round(seconds, 2), "rate": rate, "peak_dbfs": None, "downbeat_s": a.downbeat,
            "click_found": None, "imported_from": a.file.name, "app": "piano-recorder transcribe app",
            "app_version": VERSION, "transcription": {"state": "queued" if not a.no_transcribe else "none"}}
    folders.write_meta(take_id, meta)
    folders.write_takes_csv(take_id, beat0_s=a.downbeat or 0.0, bpm=a.bpm, metronome=a.click, pickup_beats=0.0,
                            beats_per_bar=a.beats_per_bar)
    print(f"{take_id}: {d}")
    if a.no_transcribe:
        return
    url = f"http://127.0.0.1:{cfg.web_port}/api/takes/{take_id}/transcribe"
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="POST", data=b""), timeout=10).read()
        print(f"transcribing: the app at port {cfg.web_port} has it")
    except (urllib.error.URLError, OSError) as e:
        print(f"the app did not answer at {url} ({e}): its page has Transcribe again", file=sys.stderr)


if __name__ == "__main__":
    main()
