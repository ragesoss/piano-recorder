""""Review it": the transcription project's review app checks a take, and the player's answers turn the
draft (transcription.musicxml) into the take's answer key. The generator is dobro-transcription's
(`review/make_review.py`, its `take-check` kind), run on the unit where the takes are:

    cd <root> && PYTHONPATH=.:pipeline/src <its venv>/python -m review.make_review take-check FOLDER --install local

It prints the review's id first ("ID: N questions, ..."); the page links to the review app
(`http://<host>:8005/#/r/ID`, its own links: `review/static/index.html`). Until the review app has that
kind (looked for in make_review.py's choices each time it is asked), the page says so.
"""

import os
import re
import subprocess
import threading
import time
from datetime import datetime

from recorder.engine import Busy

from .settings import Settings, which
from .store import Folders

KIND = "take-check"
ID_LINE = re.compile(r"^([a-z0-9][a-z0-9-]*[a-z0-9]):\s+\d+\s+questions?\b")
BARE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,120}$")


def review_id(stdout: str) -> str | None:
    """The review's id from make_review's output: its "ID: N questions" line (else a line that is an id alone)."""
    lines = [l.strip() for l in stdout.splitlines() if l.strip()]
    for l in lines:
        m = ID_LINE.match(l)
        if m:
            return m.group(1)
    return next((l for l in lines if BARE_ID.match(l)), None)


class Reviews:
    def __init__(self, settings: Settings, folders: Folders):
        self.settings, self.folders = settings, folders
        self.lock = threading.Lock()
        self.current: dict | None = None  # {"take", "started"}
        self._seen: tuple | None = None  # (mtime, available, why)

    def available(self) -> tuple[bool, str]:
        """Whether the review app can check a take yet, and if not, why."""
        path = self.settings.review.make_review
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return False, f"the review app's generator was not found ({path})"
        if self._seen and self._seen[0] == mtime:
            return self._seen[1], self._seen[2]
        text = path.read_text(errors="replace")
        m = re.search(r'add_argument\(\s*["\']kind["\']\s*,\s*choices\s*=\s*[\(\[]([^\)\]]*)[\)\]]', text)
        quoted = re.compile(r'["\']' + re.escape(KIND) + r'["\']')
        ok = bool(quoted.search(m.group(1))) if m else bool(quoted.search(text))
        why = "" if ok else "the review app doesn't check takes yet (no take-check in its make_review.py)"
        if ok and not which(self.settings.review.python):
            ok, why = False, f"the review app's python was not found ({self.settings.review.python})"
        self._seen = (mtime, ok, why)
        return ok, why

    def command(self, take_id: str) -> list[str]:
        return [self.settings.review.python, "-m", "review.make_review", KIND, str(self.folders.path(take_id)),
                "--install", "local"]

    def environment(self) -> dict:
        r = self.settings.review
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        env["PYTHONPATH"] = f"{r.root}:{r.root / 'pipeline' / 'src'}"
        if r.data is not None:
            env["REVIEW_DATA"] = str(r.data)
        return env

    def status(self) -> dict | None:
        with self.lock:
            cur = self.current
            return cur and {"take": cur["take"], "elapsed_s": round(time.monotonic() - cur["started"], 1)}

    def start(self, take_id: str) -> None:
        ok, why = self.available()
        if not ok:
            raise Busy(why)
        self.folders.path(take_id)
        with self.lock:
            if self.current:
                raise Busy("a review is being made: wait for it")
            self.current = {"take": take_id, "started": time.monotonic()}
        threading.Thread(target=self._run, args=(take_id,), daemon=True).start()

    def _run(self, take_id: str) -> None:
        made = datetime.now().isoformat(timespec="seconds")
        try:
            r = subprocess.run(self.command(take_id), cwd=str(self.settings.review.root), env=self.environment(),
                               capture_output=True, text=True, timeout=self.settings.review.timeout_s)
            rid = review_id(r.stdout) if r.returncode == 0 else None
            if rid:
                info = {"id": rid, "made": made, "kind": KIND}
            else:
                said = [l for l in (r.stderr or r.stdout or "").strip().splitlines() if l.strip()]
                info = {"error": (said[-1] if said else f"exit {r.returncode}")[:400], "made": made}
        except subprocess.TimeoutExpired:
            info = {"error": f"no review after {self.settings.review.timeout_s:g} s", "made": made}
        except OSError as e:
            info = {"error": str(e)[:400], "made": made}
        try:
            old = self.folders.meta(take_id).get("review") or {}
            if "id" not in info and old.get("id"):  # a failed remake keeps the review there was
                info = {**old, "error": info["error"]}
            self.folders.update_meta(take_id, review=info)
        except Exception:
            pass
        finally:
            with self.lock:
                self.current = None
            self.folders.touched()
