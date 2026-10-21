""""Draft a chord chart": the transcription project's drafter reads a chord chart from a take's recording, and the
review app's chart check (`chart-check`) asks the player to correct and confirm it. Both are dobro-transcription's,
run on the unit where the takes are, in the background, one chart at a time:

    cd <root>/pipeline && PYTHONPATH=src <venv>/python -m transcribe.chart_draft RECORDING OUTDIR
        [--instrument auto|bass|guitar|dobro|piano|voice|band] [--bpm N] [--meter N] [--title T]
    cd <root> && PYTHONPATH=.:pipeline/src <venv>/python -m review.make_review chart-check OUTDIR --install local
        --id ID

- RECORDING: the take's take-01.flac (the instrument's input alone; a recording brought in, its channel or all of
  them mixed to one).
- OUTDIR: the take folder's `chart-draft/` (the name make_review's chart-check gives a draft beside a recording),
  where the drafter writes draft.json and the rest. The chart there was waits in `chart-draft.old/` while it drafts
  again, and is put back if the draft fails.
- --instrument: as chosen on the page; the page offers what the take was played on where the drafter knows it
  (`settings.chart_instrument`), else auto. --bpm: the tempo the take was transcribed at, else its own; --meter: its
  beats in a bar. --title: as typed on the page (none typed: none passed, and the drafter reads the take's own from its
  meta.json). make_review is not given the title: the chart check takes it from the draft ("Check the chart: T").
- ID: `chart-check-<the take's id>`, the same each time (as the take check's are), so a chart drafted again keeps
  the review's answers: what the chart check makes of a draft folder with a generic name, told it all the same
  (make_review takes `--id` for every kind).
- Each step runs at the pipeline's nice and threads in a process group of its own, held still (SIGSTOP) while this
  app records, as a transcription is. What both say goes to the take's `chart.log`.

Until the project on the unit has the drafter (`pipeline/src/transcribe/chart_draft.py`) and its review app the kind
(`chart-check` among make_review.py's choices), the page says which is missing (looked for each time it asks).
"""

import os
import re
import shutil
import signal
import statistics
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from recorder.engine import Busy
from recorder.takes import TakeNotFound

from .jobs import _niced, _trim, pipeline_environment
from .review import environment as review_environment
from .review import has_kind, review_id
from .settings import CHART_INSTRUMENTS, Settings, which
from .store import TAKE, TITLE_MAX, Folders

KIND = "chart-check"
DRAFTER = "transcribe.chart_draft"
OUT = "chart-draft"  # the take folder's draft: the drafter's OUTDIR
LOG = "chart.log"
DOING = {"drafting": "Drafting the chart", "review": "Making the review"}
POLL_S = 2.0  # how often a step's time is looked at


class ChartError(Exception):
    pass


def chart_id(take_id: str) -> str:
    """The review's id for a take: the same each time it is made."""
    return "chart-check-" + re.sub(r"[^a-z0-9]+", "-", take_id.lower()).strip("-")


def tempo(meta: dict) -> float | None:
    """The take's tempo: as transcribed, else as given."""
    tr = meta.get("transcription") or {}
    bpm = tr.get("bpm") if tr.get("state") == "done" else None
    bpm = bpm or meta.get("bpm")
    return round(float(bpm), 2) if bpm else None


class Charts:
    def __init__(self, settings: Settings, folders: Folders):
        self.settings, self.folders = settings, folders
        self.lock = threading.Lock()
        self.current: dict | None = None  # {"take", "stage", "started", "paused_s", "estimate_s"}
        self.proc: subprocess.Popen | None = None
        self._paused_at: float | None = None
        self._seen: tuple | None = None  # (mtimes, available, why)

    @property
    def drafter(self) -> Path:
        return self.settings.pipeline.src / "transcribe" / "chart_draft.py"

    def available(self) -> tuple[bool, str]:
        """Whether a take's chord chart can be drafted and reviewed yet, and if not, why."""
        p, r = self.settings.pipeline, self.settings.review
        try:
            drafted = self.drafter.stat().st_mtime
        except OSError:
            return False, "the transcription project here has no chord-chart drafter yet (no transcribe/chart_draft.py)"
        try:
            made = r.make_review.stat().st_mtime
        except OSError:
            return False, f"the review app's generator was not found ({r.make_review})"
        if self._seen and self._seen[0] == (drafted, made):
            return self._seen[1], self._seen[2]
        ok, why = True, ""
        if not has_kind(r.make_review.read_text(errors="replace"), KIND):
            ok, why = False, "the review app doesn't check chord charts yet (no chart-check in its make_review.py)"
        elif not which(p.python):
            ok, why = False, f"the pipeline's python was not found ({p.python})"
        elif not which(r.python):
            ok, why = False, f"the review app's python was not found ({r.python})"
        self._seen = ((drafted, made), ok, why)
        return ok, why

    # What the page sees

    def _elapsed(self, now: float) -> float:
        cur = self.current
        return now - cur["started"] - cur["paused_s"] - (now - self._paused_at if self._paused_at else 0.0)

    def status(self) -> dict | None:
        with self.lock:
            cur = self.current
            if cur is None:
                return None
            return {"take": cur["take"], "stage": cur["stage"], "doing": DOING[cur["stage"]],
                    "elapsed_s": round(self._elapsed(time.monotonic()), 1), "estimate_s": cur["estimate_s"],
                    "paused": self._paused_at is not None}

    def estimate(self, seconds: float | None, instrument: str) -> float | None:
        """How long a recording this long should take here: the median rate of the last charts drafted of the same
        instrument (none yet: no estimate)."""
        if not seconds:
            return None
        rates = []
        for i in self.folders.ids()[:60]:
            m = self.folders.meta(i)
            c = m.get("chart") or {}
            if c.get("id") and c.get("seconds") is not None and c.get("instrument") == instrument and m.get("seconds"):
                rates.append(c["seconds"] / m["seconds"])
            if len(rates) >= 8:
                break
        return round(seconds * statistics.median(rates) + 2.0, 1) if rates else None

    # Asking

    def start(self, take_id: str, instrument: str, title: str = "") -> None:
        d = self.folders.path(take_id)
        if not (d / TAKE).is_file():
            raise Busy("this take is still being saved")
        if instrument not in CHART_INSTRUMENTS:
            raise ValueError(f"the drafter reads {', '.join(CHART_INSTRUMENTS)}")
        ok, why = self.available()
        if not ok:
            raise Busy(why)
        title = " ".join(str(title or "").split())[:TITLE_MAX]
        est = self.estimate(self.folders.meta(take_id).get("seconds"), instrument)
        with self.lock:
            if self.current:
                raise Busy("a chord chart is being drafted: wait for it")
            now = time.monotonic()
            self.current = {"take": take_id, "stage": "drafting", "started": now, "paused_s": 0.0, "estimate_s": est}
            if self._paused_at is not None:  # recording already: the time counts from when it ends
                self._paused_at = now
        threading.Thread(target=self._run, args=(take_id, instrument, title), daemon=True).start()

    def pause(self, paused: bool) -> None:
        """Hold the drafting still while this app records (SIGSTOP), and let it go on after."""
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
        _signal(proc, sig)

    # The work

    def draft_command(self, take_id: str, out: Path, instrument: str, title: str) -> list[str]:
        meta = self.folders.meta(take_id)
        p = self.settings.pipeline
        args = [p.python, "-m", DRAFTER, str(self.folders.path(take_id) / TAKE), str(out), "--instrument", instrument]
        bpm = tempo(meta)
        if bpm:
            args += ["--bpm", f"{bpm:g}"]
        if meta.get("beats_per_bar"):
            args += ["--meter", str(int(meta["beats_per_bar"]))]
        if title:
            args += ["--title", title]
        return _niced(p.nice) + args

    def review_command(self, take_id: str) -> list[str]:
        args = [self.settings.review.python, "-m", "review.make_review", KIND,
                str(self.folders.path(take_id) / OUT), "--install", "local", "--id", chart_id(take_id)]
        return _niced(self.settings.pipeline.nice) + args

    def _stage(self, stage: str) -> None:
        with self.lock:
            if self.current:
                self.current["stage"] = stage

    def _step(self, cmd: list[str], cwd: Path, env: dict, limit: float, log: Path) -> tuple[int, str, str]:
        """Run one step, held while this app records: its exit code, stdout and stderr. Stopped past `limit` s of
        its own time (the time held not counted)."""
        with self.lock:
            begun = self._elapsed(time.monotonic())
        with open(log, "a") as f:
            f.write(f"--- {_now()}: {' '.join(cmd)}\n")
        proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
        with self.lock:
            self.proc = proc
            held = self._paused_at is not None
        if held:
            _signal(proc, signal.SIGSTOP)
        killed = False
        while True:
            try:
                out, err = proc.communicate(timeout=POLL_S)
                break
            except subprocess.TimeoutExpired:
                with self.lock:
                    over = self._elapsed(time.monotonic()) - begun > limit
                if over and not killed:
                    killed = True
                    _signal(proc, signal.SIGKILL)
        with self.lock:
            self.proc = None
        with open(log, "a") as f:
            f.write(out + err)
        if killed:
            raise ChartError(f"stopped after {limit:g} s")
        return proc.returncode, out, err

    def _run(self, take_id: str, instrument: str, title: str) -> None:
        made = _now()
        out = kept = None
        try:
            d = self.folders.path(take_id)
            out, kept = d / OUT, d / f"{OUT}.old"
            log = d / LOG
            _trim(log)
            shutil.rmtree(kept, ignore_errors=True)
            if out.exists():
                out.replace(kept)  # the chart there was, back in place if this draft fails
            out.mkdir()
            p, r = self.settings.pipeline, self.settings.review
            code, said, err = self._step(self.draft_command(take_id, out, instrument, title), p.src.parent,
                                         pipeline_environment(self.settings), p.timeout_s, log)
            if code != 0:
                raise ChartError(_last(err, said) or f"the drafter stopped (exit {code})")
            if not (out / "draft.json").is_file():
                raise ChartError("the drafter wrote no draft.json")
            shutil.rmtree(kept, ignore_errors=True)
            kept = None
            self._stage("review")
            code, said, err = self._step(self.review_command(take_id), r.root, review_environment(self.settings),
                                         r.timeout_s, log)
            rid = review_id(said) if code == 0 else None
            if not rid:
                raise ChartError("the chart was drafted, but its review was not made: "
                                 + (_last(err, said) or f"exit {code}"))
            with self.lock:
                took = self._elapsed(time.monotonic())
            info = {"id": rid, "made": made, "kind": KIND, "instrument": instrument, "title": title,
                    "seconds": round(took, 1)}
        except TakeNotFound:
            info = None  # deleted meanwhile
        except (ChartError, OSError) as e:
            info = {"error": str(e)[:400], "made": made, "instrument": instrument}
            if kept is not None:  # the draft failed: the chart there was (if any) stays
                shutil.rmtree(out, ignore_errors=True)
                if kept.exists():
                    kept.replace(out)
        try:
            if info is not None:
                old = self.folders.meta(take_id).get("chart") or {}
                if "id" not in info and old.get("id"):  # a failed redraft keeps the review there was
                    info = {**old, "error": info["error"], "tried": made}
                self.folders.update_meta(take_id, chart=info)
        except Exception:
            pass
        finally:
            with self.lock:
                self.current = None
            self.folders.touched()


def _signal(proc: subprocess.Popen | None, sig: int) -> None:
    """A signal to a step's whole process group (what the drafter starts too)."""
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, sig)
    except OSError:
        pass


def _last(*texts: str) -> str:
    """The last line said, of the first text with any."""
    for t in texts:
        lines = [l.strip() for l in (t or "").splitlines() if l.strip()]
        if lines:
            return lines[-1][:300]
    return ""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
