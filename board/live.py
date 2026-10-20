"""Live cards: what the other apps on the unit want from the player right now, shown on the board and
never stored. Read from their own APIs; an app that does not answer has no cards (quietly).

- The review app (dobro-transcription's review/, port 8005): a card for each review with questions not
  yet answered (work "listen"). A card on the board that links to the review (`#/r/<id>`, the review app's own
  links) and is not done carries it instead (`live_summary`), so the review is not on the board twice. (`GET /api/reviews`: its title, how many questions, how many answered; demos left out),
  linking to it (`#/r/<id>`, the review app's links). Each says who made the review (`origin`: "pipeline", the
  project's scripts, or "agent", written by an AI agent; shown as a badge). A review the player archived (done
  with it, answered or not: `archived`) has no card of its own; a card that links to it says "Archived" and how
  many were answered, in place of a live count (`live_archived`).
- The exercise app (port 8002): one card (work "play"), how many exercises each instrument still wants a take of
  (`GET /api/exercises`, counted as its board counts them: wanted again or no take yet, and not recorded
  here since), linking to its board (and `#<instrument>` for each one's list).

Read at most every `ttl` seconds; /api/status uses what was read last and asks for a fresh read in the
background, so the home page's tile never waits on the other apps.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# the exercise app's names for its kinds (exercises/static/index.html: ORDER, NAMES)
KIND_ORDER = ["dobro", "bass", "guitar", "banjo", "piano", "voice", "generic"]
KIND_NAMES = {"bass": "Bass", "dobro": "Dobro", "guitar": "Guitar", "banjo": "Banjo", "piano": "Piano",
              "voice": "Voice", "generic": "Any instrument"}


def _get(url: str, timeout: float):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def _port(url: str) -> int:
    return urllib.parse.urlsplit(url).port or 80


def _card(**kw) -> dict:
    base = {"importance": 2, "who": ["player"], "work": "", "effort_player": "", "effort_claude": "",
            "waiting_on": "", "blocked_by": [], "links": [], "details": "", "source": "", "created": "",
            "updated": "", "done": None, "notes": [], "pending": 0, "pending_changes": []}
    return base | kw


ORIGINS = ("pipeline", "agent")  # the review app's `origin` (its review/model.py)


def review_cards(reviews: list[dict], port: int, area: str) -> list[dict]:
    """A card for each review with questions left; an archived one's card only to fold onto a card that links to
    it (`fold_reviews` keeps none of its own)."""
    out = []
    for r in reviews:
        if not isinstance(r, dict) or r.get("demo") or not r.get("id"):
            continue
        q, done = int(r.get("questions") or 0), int(r.get("answered") or 0)
        left = q - done
        archived = bool(r.get("archived"))
        if left <= 0 and not archived:
            continue
        rid = str(r["id"])
        answered = f"{done} of {q} answered"
        out.append(_card(
            id=f"live:review:{rid}", live="review", review_id=rid, work="listen", area=area, title=str(r.get("title") or rid),
            status="done" if archived else "doing" if done else "next",
            summary=f"Archived: {answered}" if archived else
            f"{left} question{'s' if left != 1 else ''} to answer" + (f" ({answered})" if done else ""),
            open_questions=0 if archived else left, archived=archived, answered_text=answered,
            origin=r.get("origin") if r.get("origin") in ORIGINS else "", origin_note=str(r.get("origin_note") or ""),
            links=[{"label": "Open the review", "port": port, "path": f"/#/r/{urllib.parse.quote(rid)}", "repo": ""}],
            details=str(r.get("subtitle") or ""), source="The review app", created=str(r.get("created") or ""),
            updated=str(r.get("last_answer") or r.get("created") or "")[:10]))
    return out


REVIEW_LINK = re.compile(r"^https?://[^/]+/#/r/([A-Za-z0-9._%-]+)$")  # the review app's links to a review


def fold_reviews(cards: list[dict], live: list[dict]) -> list[dict]:
    """A live review that a card links to (and is not done) goes onto that card as its live_summary (an archived
    one as "Archived", `live_archived`); the live cards left are returned, archived reviews' never. The cards are
    changed in place (the request's own copies)."""
    link = REVIEW_LINK
    holders: dict[str, list[dict]] = {}
    for c in cards:
        if c["status"] != "done":
            for l in c["links"]:
                m = link.search(str(l.get("href", "")))
                if m:
                    holders.setdefault(urllib.parse.unquote(m.group(1)), []).append(c)
    kept = []
    for lc in live:
        mine = holders.get(lc.get("review_id", "")) if lc.get("live") == "review" else None
        if not mine:
            if not lc.get("archived"):
                kept.append(lc)
            continue
        for c in mine:
            c["live_summary"], c["live_link"] = lc["summary"], lc["links"][0]
            c["live_archived"], c["live_detail"] = bool(lc.get("archived")), lc.get("answered_text", "")
            c["live_origin"] = lc.get("origin", "")
    return kept


def exercise_counts(exercises: list[dict]) -> list[dict]:
    """How many each instrument wants recorded, as the exercise app's board counts them."""
    listed = any(isinstance(e, dict) and e.get("status") for e in exercises)
    want: dict[str, int] = {}
    for e in exercises:
        if not isinstance(e, dict):
            continue
        k = str(e.get("instrument") or "")
        wanted = e.get("status") in ("wanted", "needed") if listed else not e.get("takes")
        if k and wanted and not e.get("to_review"):
            want[k] = want.get(k, 0) + 1
    rank = lambda k: (KIND_ORDER.index(k) if k in KIND_ORDER else 99, k)
    return [{"kind": k, "name": KIND_NAMES.get(k, k.capitalize()), "n": want[k]} for k in sorted(want, key=rank)]


def exercise_card(exercises: list[dict], port: int, area: str) -> dict | None:
    counts = exercise_counts(exercises)
    total = sum(c["n"] for c in counts)
    if not total:
        return None
    return _card(
        id="live:exercises", live="exercises", work="play", area=area, status="next",
        title=f"Exercises to record: {total}",
        summary=" · ".join(f"{c['name']} {c['n']}" for c in counts), counts=counts,
        links=[{"label": "The exercise board", "port": port, "path": "/", "repo": ""}]
        + [{"label": f"{c['name']}: {c['n']} to record", "port": port, "path": f"/#{c['kind']}", "repo": ""} for c in counts],
        details="What the project wants a take of, by instrument: wanted again, or no take yet.",
        source="The exercise app")


class Live:
    def __init__(self, cfg: dict):
        self.review = cfg.get("review", "")
        self.exercises = cfg.get("exercises", "")
        self.review_area = cfg.get("review_area", "dobro")
        self.exercises_area = cfg.get("exercises_area", "dobro")
        self.timeout = float(cfg.get("timeout", 1.5))
        self.ttl = float(cfg.get("ttl", 10))
        self._pool = ThreadPoolExecutor(max_workers=3)
        self._lock = threading.Lock()
        self._cards: list[dict] = []
        self._up: dict[str, bool] = {}
        self._at = 0.0
        self._refreshing = False

    def _read(self) -> tuple[list[dict], dict]:
        def reviews():
            return review_cards(_get(self.review.rstrip("/") + "/api/reviews", self.timeout).get("reviews", []),
                                _port(self.review), self.review_area)

        def exercises():
            c = exercise_card(_get(self.exercises.rstrip("/") + "/api/exercises", self.timeout),
                              _port(self.exercises), self.exercises_area)
            return [c] if c else []

        jobs = {name: self._pool.submit(fn) for name, fn, url in
                (("review", reviews, self.review), ("exercises", exercises, self.exercises)) if url}
        cards, up = [], {}
        for name, job in jobs.items():
            try:
                cards += job.result()
                up[name] = True
            except Exception:  # down, slow or saying something else: its cards are left out
                up[name] = False
        return cards, up

    def _store(self, cards, up) -> None:
        with self._lock:
            self._cards, self._up, self._at = cards, up, time.monotonic()

    def cards(self, fresh: bool = True) -> tuple[list[dict], dict]:
        """The live cards and which apps answered. fresh: read again if older than ttl (waits for it, at most
        the timeout); otherwise what was read last, and a read started in the background when it is old."""
        with self._lock:
            stale = time.monotonic() - self._at > self.ttl
            cards, up = self._cards, self._up
        if stale and fresh:
            cards, up = self._read()
            self._store(cards, up)
        elif stale:
            self._refresh_soon()
        return cards, up

    def _refresh_soon(self) -> None:
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True

        def run():
            try:
                self._store(*self._read())
            finally:
                with self._lock:
                    self._refreshing = False

        threading.Thread(target=run, daemon=True).start()
