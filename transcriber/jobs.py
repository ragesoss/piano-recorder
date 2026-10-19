"""After a take, in the background, one take at a time:

1. **The folder** (store.py's layout): the engine's take (all kept inputs, its sidecar, where the click
   was found) becomes `take-01.flac` (the instrument's input), `raw/`, `meta.json` and `takes.csv`;
   the engine's own copy is deleted.
2. **The sound for the page:** `play/full.mp3` and `play/half.mp3` (ffmpeg's atempo=0.5, the pitch
   kept: the review app's way, `review/render.py` in dobro-transcription), so the take plays while
   it is transcribed.
3. **The transcription:** job/run_pipeline.py in the pipeline's venv, at `nice` 19 with a few threads,
   stopped (SIGSTOP) while this app records and let go on after (SIGCONT); its stages shown on the
   page. Then `takes.csv` gets the score's beat 0 and pickup, and `meta.json` how it went.

A take waiting to start does not start while this app records.
"""

import collections
import json
import os
import shutil
import signal
import statistics
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from recorder.config import Config
from recorder.takes import TakeNotFound, Takes

from . import VERSION
from .settings import Settings, which
from .store import TAKE, Folders

SCRIPT = Path(__file__).resolve().parent / "job" / "run_pipeline.py"
STAGES = {  # what the page says each stage is doing
    "queued": "Waiting its turn",
    "building": "Saving the take",
    "sound": "Making it playable",
    "starting": "Starting the pipeline",
    "loading": "Reading the take",
    "beats": "Finding the beat",
    "notes": "Finding the notes",
    "writing": "Writing the score",
}
DEFAULT_RATIO = 0.4  # transcribing time per second of take, until this unit has done some (Red Wing on the
# desktop: 0.05 over a click, 0.19 without; the unit's CPU is slower)
DEFAULT_RATIOS = {"piano": 0.1}  # the piano's path is faster (exercise takes of 28-40 s on the desktop: 0.5 s over
# the click, 2.4 s without; the unit about 3.5 times as long)
LOG_MAX = 200_000  # transcribe.log is cut back to its end past this


class JobError(Exception):
    pass


class Jobs:
    def __init__(self, cfg: Config, settings: Settings, folders: Folders, work: Takes, busy=lambda: False):
        self.cfg, self.settings, self.folders, self.work = cfg, settings, folders, work
        self.busy = busy  # this app is recording: do not start (and keep stopped) a run
        self.lock = threading.Lock()
        self.queue: collections.deque = collections.deque()  # (take_id, build)
        self.current: dict | None = None  # {"take", "stage", "started", "paused_s", "estimate_s"}
        self.proc: subprocess.Popen | None = None
        self._paused_at: float | None = None
        self._cancel: set[str] = set()
        self._wake = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    # What the page sees

    def status(self) -> dict | None:
        with self.lock:
            cur = self.current
            if cur is None:
                return None
            now = time.monotonic()
            paused = cur["paused_s"] + (now - self._paused_at if self._paused_at else 0.0)
            return {"take": cur["take"], "stage": cur["stage"], "doing": STAGES.get(cur["stage"], cur["stage"]),
                    "elapsed_s": round(now - cur["started"] - paused, 1), "estimate_s": cur.get("estimate_s"),
                    "paused": self._paused_at is not None}

    def queued(self) -> list[str]:
        with self.lock:
            return [t for t, _ in self.queue]

    def state_of(self, take_id: str) -> str | None:
        """"running", "queued" or None."""
        with self.lock:
            if self.current and self.current["take"] == take_id:
                return "running"
            return "queued" if any(t == take_id for t, _ in self.queue) else None

    # Asking

    def submit(self, take_id: str, build: bool = False) -> None:
        """Transcribe a take (`build`: make its folder from the engine's take first)."""
        with self.lock:
            if any(t == take_id for t, _ in self.queue) or (self.current and self.current["take"] == take_id):
                return
            self.queue.append((take_id, build))
            self._cancel.discard(take_id)
        if not build:
            self.folders.update_meta(take_id, transcription={"state": "queued"})
        self._wake.set()

    def cancel(self, take_id: str) -> None:
        """Forget a take (it is being deleted): out of the queue, or its run stopped."""
        with self.lock:
            self.queue = collections.deque((t, b) for t, b in self.queue if t != take_id)
            self._cancel.add(take_id)
            proc = self.proc if self.current and self.current["take"] == take_id else None
        if proc is not None and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGCONT)
                proc.kill()
            except OSError:
                pass

    def pause(self, paused: bool) -> None:
        """Hold the pipeline still while this app records (SIGSTOP), and let it go on after."""
        with self.lock:
            proc = self.proc
            if paused and self._paused_at is None:
                self._paused_at = time.monotonic()
                sig = signal.SIGSTOP
            elif not paused and self._paused_at is not None:
                if self.current:
                    self.current["paused_s"] += time.monotonic() - self._paused_at
                self._paused_at = None
                sig = signal.SIGCONT
            else:
                return
        if proc is not None and proc.poll() is None:
            try:
                proc.send_signal(sig)
            except OSError:
                pass

    # The work

    def _loop(self) -> None:
        while True:
            self._wake.wait(1.0)
            self._wake.clear()
            while True:
                if self.busy():
                    break  # not while recording: the next take's capture comes first
                with self.lock:
                    if not self.queue:
                        break
                    take_id, build = self.queue.popleft()
                    self.current = {"take": take_id, "stage": "building" if build else "sound",
                                    "started": time.monotonic(), "paused_s": 0.0, "estimate_s": None}
                try:
                    self._run(take_id, build)
                except TakeNotFound:
                    pass  # deleted meanwhile
                except Exception as e:  # noqa: BLE001 - the page says what went wrong
                    try:
                        self.folders.update_meta(take_id, transcription={"state": "failed", "error": str(e)[:500],
                                                                         "finished": _now()})
                    except TakeNotFound:
                        pass
                finally:
                    with self.lock:
                        self.current, self.proc = None, None
                    self.folders.touched()

    def _stage(self, stage: str) -> None:
        with self.lock:
            if self.current:
                self.current["stage"] = stage

    def _cancelled(self, take_id: str) -> bool:
        with self.lock:
            return take_id in self._cancel

    def _run(self, take_id: str, build: bool) -> None:
        if build:
            self.build(take_id)
            if self._cancelled(take_id):
                return
            self.folders.update_meta(take_id, transcription={"state": "queued"})
        self._stage("sound")
        self.sound(take_id)
        if self._cancelled(take_id):
            return
        self.transcribe(take_id)

    def build(self, take_id: str) -> Path:
        """The take's folder, from the engine's take (`work`): see store.py."""
        side = self.work.meta(take_id)  # raises TakeNotFound
        wav = self.work.path(take_id)
        info = dict(side.get("transcribe") or {})
        d = self.folders.create(take_id)
        channel = int(info.get("channel", 0))
        self._ffmpeg(["-i", str(wav), "-af", f"pan=mono|c0=c{channel}", *FLAC24], d / TAKE)
        if self.settings.keep_raw:
            (d / "raw").mkdir(exist_ok=True)
            self._ffmpeg(["-i", str(wav), *FLAC24], d / "raw" / f"{take_id}.flac")
            (d / "raw" / f"{take_id}.json").write_text(json.dumps(side, indent=1))
        t = self.work.get(take_id)
        guided = side.get("guide") == "click"
        found = bool(guided and (side.get("playalong") or {}).get("found"))
        downbeat = side.get("beat0_s") if found else None  # a click with no pickup: its beat 0 is bar 1's downbeat
        meta = {
            **info,
            "seconds": t.seconds,
            "rate": self.cfg.capture.capture.rate,
            "peak_dbfs": side.get("peak_dbfs"),
            "downbeat_s": downbeat,
            "click_found": found if guided else None,
            "app": "piano-recorder transcribe app",
            "app_version": VERSION,
            "transcription": {"state": "building"},
        }
        tmp = d / "meta.json.tmp"
        tmp.write_text(json.dumps(meta, indent=1, ensure_ascii=False))
        tmp.replace(d / "meta.json")
        self.folders.write_takes_csv(take_id, beat0_s=downbeat if downbeat is not None else 0.0,
                                     bpm=float(info.get("bpm", 0.0)), metronome=bool(info.get("click")),
                                     pickup_beats=0.0, beats_per_bar=int(info.get("beats_per_bar", 4)))
        self.work.delete(take_id)
        self.folders.touched()
        return d

    def _ffmpeg(self, args: list[str], out: Path) -> None:
        _ffmpeg(_niced(self.settings.pipeline.nice) + [self.settings.ffmpeg], args, out)

    def sound(self, take_id: str) -> None:
        """The take for the page, at full and half speed (made once)."""
        d = self.folders.path(take_id)
        (d / "play").mkdir(exist_ok=True)
        for name, af in (("full.mp3", []), ("half.mp3", ["-af", "atempo=0.5"])):
            if not (d / "play" / name).is_file():
                self._ffmpeg(["-i", str(d / TAKE), *af, "-ac", "1", "-b:a", "128k"], d / "play" / name)
        self.folders.touched()

    def estimate(self, seconds: float | None, instrument: str, click: bool) -> float | None:
        """How long a take this long should take here: the median rate of the last takes like it."""
        if not seconds:
            return None
        rates = []
        for i in self.folders.ids()[:40]:
            m = self.folders.meta(i)
            tr = m.get("transcription") or {}
            if tr.get("state") == "done" and tr.get("seconds") and m.get("seconds") and \
                    m.get("instrument") == instrument and bool(m.get("click")) == click:
                rates.append(tr["seconds"] / m["seconds"])
            if len(rates) >= 8:
                break
        default = DEFAULT_RATIOS.get(instrument, DEFAULT_RATIO)
        return round(seconds * (statistics.median(rates) if rates else default) + 2.0, 1)

    def command(self, take_id: str, meta: dict) -> list[str]:
        p = self.settings.pipeline
        args = [p.python, str(SCRIPT), str(self.folders.path(take_id)), "--take", TAKE,
                "--instrument", meta.get("instrument", "dobro"), "--bpm", f"{float(meta['bpm']):g}",
                "--beats-per-bar", str(int(meta.get("beats_per_bar", 4)))]
        if meta.get("click"):
            args.append("--metronome")
        else:
            args.append("--track-beats")
        if meta.get("downbeat_s") is not None:
            args += ["--downbeat", f"{float(meta['downbeat_s']):.4f}"]
        if meta.get("title"):
            args += ["--title", str(meta["title"])]
        return _niced(p.nice) + args

    def environment(self) -> dict:
        p = self.settings.pipeline
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        env["PYTHONPATH"] = str(p.src)
        n = str(max(1, p.threads))
        for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS"):
            env[k] = n
        return env

    def transcribe(self, take_id: str) -> dict:
        d = self.folders.path(take_id)
        meta = self.folders.meta(take_id)
        if not (d / TAKE).is_file():
            raise JobError(f"the take has no {TAKE}")
        p = self.settings.pipeline
        if not which(p.python):
            raise JobError(f"the pipeline's python was not found ({p.python})")
        if not (p.src / "transcribe" / "transcribe.py").is_file():
            raise JobError(f"the transcription pipeline was not found in {p.src}")
        est = self.estimate(meta.get("seconds"), meta.get("instrument", ""), bool(meta.get("click")))
        with self.lock:
            if self.current:  # the time shown is the pipeline's from here
                self.current.update(stage="starting", estimate_s=est, started=time.monotonic(), paused_s=0.0)
            if self._paused_at is not None:
                self._paused_at = time.monotonic()
        started_at = _now()
        self.folders.update_meta(take_id, transcription={"state": "running", "started": started_at})
        for name in ("transcription.json", "transcription.musicxml"):
            (d / name).unlink(missing_ok=True)
        log = d / "transcribe.log"
        _trim(log)
        done, failed = None, None
        with open(log, "a") as err:
            err.write(f"--- {started_at}: {' '.join(self.command(take_id, meta))}\n")
            err.flush()
            proc = subprocess.Popen(self.command(take_id, meta), cwd=str(d), env=self.environment(),
                                    stdout=subprocess.PIPE, stderr=err, text=True)
            with self.lock:
                self.proc = proc
                if self._paused_at is not None:  # recording already: wait for it
                    proc.send_signal(signal.SIGSTOP)
            killer = threading.Timer(p.timeout_s, lambda: proc.poll() is None and proc.kill())
            killer.start()
            try:
                for line in proc.stdout:
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("stage") == "done":
                        done = msg
                    elif msg.get("stage") == "failed":
                        failed = msg.get("error")
                    elif msg.get("stage"):
                        self._stage(msg["stage"])
                code = proc.wait()
            finally:
                killer.cancel()
        st = self.status() or {}
        if self._cancelled(take_id):
            return {}
        if code != 0 or done is None:
            why = failed or _last_line(log) or f"the pipeline stopped (exit {code})"
            if code == -signal.SIGKILL:
                why = f"stopped after {p.timeout_s:g} s"
            raise JobError(why)
        tr = {"state": "done", "seconds": st.get("elapsed_s"), "pipeline_seconds": done.get("seconds"),
              "notes": done.get("notes"), "bars": done.get("bars"), "bpm": done.get("bpm"),
              "pickup_beats": done.get("pickup_beats"), "beat0_s": done.get("beat0_s"), "bar1_s": done.get("bar1_s"),
              "grid": done.get("grid"),
              "started": started_at, "finished": _now()}
        self.folders.update_meta(take_id, transcription=tr)
        # takes.csv's beat0_s, while the take has no answer key: bar 1's downbeat, as the transcription has it (the
        # one it was told, from the click or count-in); the review side's key moves it to the key's beat 0
        self.folders.write_takes_csv(take_id, beat0_s=float(done.get("bar1_s", done.get("beat0_s")) or 0.0),
                                     bpm=float(done.get("bpm") or meta.get("bpm")), metronome=bool(meta.get("click")),
                                     pickup_beats=float(done.get("pickup_beats") or 0.0),
                                     beats_per_bar=int(meta.get("beats_per_bar", 4)))
        return tr


FLAC24 = ["-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"]


def _niced(level: int) -> list[str]:
    """A prefix that runs a program at niceness `level` (an absolute level: the service itself runs at -5)."""
    nice = shutil.which("nice")
    step = level - os.nice(0)
    return [nice, "-n", str(step)] if nice and level and step > 0 else []


def _ffmpeg(ffmpeg: list[str], args: list[str], out: Path) -> None:
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    r = subprocess.run([*ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args, str(tmp)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        said = (r.stderr or "").strip().splitlines()
        raise JobError(f"ffmpeg could not make {out.name}: {said[-1] if said else r.returncode}")
    tmp.replace(out)


def _last_line(path: Path) -> str:
    try:
        lines = [l.strip() for l in path.read_text(errors="replace").splitlines() if l.strip() and not l.startswith("---")]
    except OSError:
        return ""
    return lines[-1][:300] if lines else ""


def _trim(path: Path) -> None:
    try:
        if path.stat().st_size > LOG_MAX:
            path.write_bytes(path.read_bytes()[-LOG_MAX // 2:])
    except OSError:
        pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
