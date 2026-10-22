"""The board's data on the control unit: the cards pushed from the desktop, and the player's edits.

    data_dir/cards.json             the base: dobro-transcription's `board/board.py json`, put here by
                                    tools/push-board (written as cards.json.new, then renamed)
    data_dir/changes.jsonl          the player's edits not yet on the desktop, one JSON object a line
    data_dir/changes.applied.jsonl  the edits the desktop has applied to cards.toml (moved here by
                                    tools/pull-board once the new base is pushed)
    data_dir/changes.lock           taken (flock) for every append and every archive

The app shows the base with the pending edits applied in order, so an edit shows at once: the cards' and the
goals' (a goal's stage moved, a note on a goal).

Each card carries its number from the base (what the player says, "card 42"; none on a card made here until the
desktop gives it one) and the player's mark "ready to go" (`ready`), which only a Claude-ready card may take (Claude
among who, in Backlog or Next, every card it is blocked by done: `claude_ready`, the rule of dobro-transcription's
board.py, kept the same here) and which a move out of Backlog or Next, or Claude out of who, clears. A base pushed
by a board.py that does not know `ready` offers it on no card, so no mark is logged that board.py would refuse.

    python -m board.store datadir             print the data folder (what the tools write to)
    python -m board.store pending             print the pending changes (whole lines, read under the lock)
    python -m board.store archive < IDS       move the changes with these ids (one a line) to
                                              changes.applied.jsonl
Both read $ROOM_BOARD_CONFIG, else /etc/room-board.toml (--config another).
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import re
import secrets
import sys
import tomllib
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SYSTEM_CONFIG = Path("/etc/room-board.toml")

# The data contract (dobro-transcription board/board.py)
STATUSES = ("backlog", "next", "doing", "waiting", "done")
WHO = ("player", "claude", "others")
# the kind of work it is for the player (2026-10-19): listening and evaluating; playing an instrument and/or
# singing; managing an AI agent (decisions, priorities, reviewing what it built, and all of Claude's software work);
# communicating with other people; working with the hardware
WORK = ("listen", "play", "agent", "people", "hardware")
EFFORT_PLAYER = ("", "5m", "15m", "30m", "1h", "2h", "half-day", "day+")
EFFORT_CLAUDE = ("", "S", "M", "L", "XL")
IMPORTANCE = (1, 2, 3)
SETTABLE = ("status", "importance", "who", "work", "effort_player", "effort_claude", "waiting_on", "area", "goals",
            "ready")
# Claude-ready (2026-10-21): Claude among who, in one of these columns, every card it is blocked by done (board.py's
# claude_ready). Only such a card may be marked ready; the mark goes when the card leaves these columns or Claude
# leaves who.
READY_STATUSES = ("backlog", "next")
# the large-scale goals (2026-10-20): each at a stage of ten, from an idea (1) to released (10: put out into the
# world, the repo cleaned up, open source, an app); a card serves any of them
STAGE_NAMES = {1: "Idea", 2: "Idea with a plan", 3: "Testable prototype", 4: "Tested prototype", 5: "Rough but usable",
               6: "Decent", 7: "Getting pretty good", 8: "Almost release-ready", 9: "Release-ready", 10: "Released"}
STAGES = tuple(STAGE_NAMES)
NOTE_BY = ("player", "claude")
GOAL_COLORS = ("teal", "orange", "pink", "green", "gold", "blue", "red", "violet", "lime", "sky")
TITLE_MAX = 200
TEXT_MAX = 300  # waiting_on
NOTE_MAX = 4000


class Invalid(ValueError):
    """An edit the contract does not allow (the server answers 422)."""


class NotFound(KeyError):
    pass


def load_config(path: Path | str | None = None) -> dict:
    """The board's config: port, data_dir, and the apps it reads live cards from ([live])."""
    path = Path(path or os.environ.get("ROOM_BOARD_CONFIG") or SYSTEM_CONFIG)
    raw = tomllib.loads(path.read_text())
    d = Path(str(raw.get("data_dir", "~/board-data"))).expanduser()
    raw["data_dir"] = d if d.is_absolute() else REPO / d
    raw.setdefault("port", 8007)
    raw.setdefault("host", "0.0.0.0")
    raw.setdefault("live", {})
    return raw


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def slug(title: str) -> str:
    """A card's id from its title: lower case, words joined by hyphens, a short random suffix."""
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if len(s) > 40:  # at a word's end
        s = s[:41].rsplit("-", 1)[0] if "-" in s[:41] else s[:40]
    s = s.strip("-") or "card"
    return f"{s}-{secrets.token_hex(2)}"


def _str_list(v) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else []


def _unique(xs: list[str]) -> list[str]:
    return list(dict.fromkeys(xs))


def normalize(card: dict) -> dict:
    """A card with every field of the contract (defaults where the base leaves one out)."""
    c = dict(card)
    c["id"] = str(c.get("id", ""))
    n = c.get("number")
    c["number"] = n if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else None  # none until the desktop's
    c["ready"] = c.get("ready") is True
    c["title"] = str(c.get("title", "")) or c["id"]
    c["area"] = str(c.get("area", ""))
    c["status"] = c.get("status") if c.get("status") in STATUSES else "backlog"
    c["importance"] = c.get("importance") if c.get("importance") in IMPORTANCE else 2
    who = _str_list(c.get("who", ["player"]))
    c["who"] = [w for w in WHO if w in who]  # the contract's order
    c["work"] = c.get("work") if c.get("work") in WORK else ""
    c["effort_player"] = c.get("effort_player") if c.get("effort_player") in EFFORT_PLAYER else ""
    c["effort_claude"] = c.get("effort_claude") if c.get("effort_claude") in EFFORT_CLAUDE else ""
    c["waiting_on"] = str(c.get("waiting_on") or "")
    c["blocked_by"] = _str_list(c.get("blocked_by"))
    c["links"] = [l for l in c.get("links", []) if isinstance(l, dict) and l.get("href")] if isinstance(c.get("links"), list) else []
    c["details"] = str(c.get("details") or "")
    c["source"] = str(c.get("source") or "")
    for k in ("created", "updated"):
        c[k] = str(c.get(k) or "")
    c["done"] = c.get("done") or None
    c["notes"] = [n for n in c.get("notes", []) if isinstance(n, dict)] if isinstance(c.get("notes"), list) else []
    c["goals"] = _unique(_str_list(c.get("goals")))  # the goals it serves, most relevant first
    c["pending"] = 0
    c["pending_changes"] = []
    return c


def is_stage(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v in STAGES


def can_be_ready(card: dict) -> bool:
    """A card's own fields allow the mark "ready": Claude among who, in Backlog or Next."""
    return "claude" in card.get("who", []) and card.get("status") in READY_STATUSES


def claude_ready(card: dict, cards: dict[str, dict]) -> bool:
    """Claude can take the card up now: can_be_ready, and every card in blocked_by done (one not on the board counts
    as open, as the page's "Blocked by" does)."""
    return can_be_ready(card) and all((cards.get(b) or {}).get("status") == "done" for b in card.get("blocked_by", []))


def stage_labels(base: dict) -> list[str] | None:
    """The base's own labels for the ten stages ("stages", in order), when it has all ten; else None."""
    raw = base.get("stages")
    if isinstance(raw, list) and len(raw) == len(STAGES) and all(isinstance(x, str) and x.strip() for x in raw):
        return [x.strip() for x in raw]
    return None


def normalize_goal(goal: dict) -> dict:
    """A goal with every field of the contract: its history (oldest first, only the contract's stages, 1 to 10) and
    notes (each by the player or Claude); "stage" is the last entry's (1 before any). Its color is checked in merge
    (one not taken, if not given)."""
    g = dict(goal)
    g["id"] = str(g.get("id", ""))
    g["name"] = str(g.get("name") or "") or g["id"]
    for k in ("about", "release", "shipped"):  # release: what release-ready (9) means; shipped: released (10)
        g[k] = str(g.get(k) or "")
    raw = g.get("history") if isinstance(g.get("history"), list) else []
    g["history"] = [dict(h) | {"date": str(h.get("date") or "")[:10], "note": str(h.get("note") or "")}
                    for h in raw if isinstance(h, dict) and is_stage(h.get("stage"))]
    raw = g.get("notes") if isinstance(g.get("notes"), list) else []
    g["notes"] = [dict(n) | {"date": str(n.get("date") or "")[:10], "text": str(n.get("text") or ""),
                             "by": n.get("by") if n.get("by") in NOTE_BY else ""}
                  for n in raw if isinstance(n, dict) and n.get("text")]
    g["stage"] = g["history"][-1]["stage"] if g["history"] else 1
    g["pending"] = 0
    g["pending_changes"] = []
    return g


def check_set(fields: dict, areas: set[str], goals: set[str] = frozenset()) -> dict:
    """The fields of a "set" change, checked against the contract (raises Invalid)."""
    if not isinstance(fields, dict) or not fields:
        raise Invalid("nothing to set")
    out = {}
    for k, v in fields.items():
        if k not in SETTABLE:
            raise Invalid(f"{k} cannot be set here (only {', '.join(SETTABLE)})")
        if k == "status" and v not in STATUSES:
            raise Invalid(f"status must be one of {', '.join(STATUSES)}")
        if k == "importance" and (isinstance(v, bool) or v not in IMPORTANCE):
            raise Invalid("importance must be 1, 2 or 3")
        if k == "who":
            if not isinstance(v, list) or not v or any(w not in WHO for w in v) or len(set(v)) != len(v):
                raise Invalid(f"who must be one or more of {', '.join(WHO)}")
            v = [w for w in WHO if w in v]  # the contract's order
        if k == "work" and v not in WORK:
            raise Invalid(f"work must be one of {', '.join(WORK)}")
        if k == "effort_player" and v not in EFFORT_PLAYER:
            raise Invalid(f"effort_player must be one of {', '.join(repr(e) for e in EFFORT_PLAYER)}")
        if k == "effort_claude" and v not in EFFORT_CLAUDE:
            raise Invalid(f"effort_claude must be one of {', '.join(repr(e) for e in EFFORT_CLAUDE)}")
        if k == "waiting_on":
            if not isinstance(v, str) or len(v) > TEXT_MAX:
                raise Invalid(f"waiting_on: text of {TEXT_MAX} characters at most")
            v = v.strip()
        if k == "area" and v not in areas:
            raise Invalid("no such area")
        if k == "goals":  # in the order given: the most relevant first
            if not isinstance(v, list) or any(not isinstance(g, str) for g in v) or len(set(v)) != len(v):
                raise Invalid("goals must be a list of goal ids, each once")
            if any(g not in goals for g in v):
                raise Invalid("no such goal")
            v = list(v)
        if k == "ready" and not isinstance(v, bool):
            raise Invalid("ready must be true or false")
        out[k] = v
    return out


def _date(at: str) -> str:
    return str(at)[:10]


def _apply_set(card: dict, fields: dict, at: str) -> None:
    for k, v in fields.items():
        if k in SETTABLE:
            card[k] = copy.copy(v)
    if "status" in fields:
        # the contract's "done" date: shown here at once (board.py keeps its own when it applies the change)
        if fields["status"] == "done" and not card.get("done"):
            card["done"] = _date(at)
        elif fields["status"] != "done":
            card["done"] = None
    if card.get("ready") and not can_be_ready(card):
        card["ready"] = False  # taken up (Doing), waiting, done, or not Claude's: board.py clears the mark the same way
    card["updated"] = _date(at)


def goal_change(ch: dict) -> str:
    """What a change in the log does to a goal: "stage", "note", or "" when it is not a goal's."""
    if "goal" not in ch:
        return ""
    return "stage" if "stage" in ch else "note" if "note" in ch else ""


def _goals(base: dict) -> dict[str, dict]:
    goals: dict[str, dict] = {}
    for raw in base.get("goals", []) if isinstance(base.get("goals"), list) else []:
        if isinstance(raw, dict) and raw.get("id") and str(raw["id"]) not in goals:
            g = normalize_goal(raw)
            goals[g["id"]] = g
    taken = {g["color"] for g in goals.values() if g.get("color") in GOAL_COLORS}
    for g in goals.values():  # a goal without one of the ten colors: the first not taken
        if g.get("color") not in GOAL_COLORS:
            free = [c for c in GOAL_COLORS if c not in taken] or list(GOAL_COLORS)
            g["color"] = free[0]
            taken.add(free[0])
    return goals


def merge(base: dict, changes: list[dict]) -> dict:
    """The base with the pending changes applied in order: those whose ids are not in the base's "applied" (the
    changes board.py has folded into these cards; the archive after a sync may lag). Each card and each goal says
    how many are pending on it ("pending", "pending_changes"); a change for a card or goal not there is left out
    (not lost: still in the log). A base with no "goals" (pushed before them) has none, and its cards none."""
    folded = set(base.get("applied") or []) if isinstance(base.get("applied"), list) else set()
    goals = _goals(base)
    cards: dict[str, dict] = {}
    for raw in base.get("cards", []) if isinstance(base.get("cards"), list) else []:
        if isinstance(raw, dict) and raw.get("id"):
            c = normalize(raw)
            c["goals"] = [g for g in c["goals"] if g in goals]
            cards[c["id"]] = c
    in_base = set(cards)
    areas = {a["id"] for a in base.get("areas", []) if isinstance(a, dict) and a.get("id")}
    for ch in changes:
        if ch.get("id") in folded:
            continue
        at = str(ch.get("at", ""))
        kind = goal_change(ch)
        if "goal" in ch:
            g = goals.get(ch.get("goal"))
            if not g or not kind:
                continue
            day, by = _date(at), ch.get("by", "player")
            if kind == "stage":
                if not is_stage(ch["stage"]):
                    continue
                note = str(ch.get("note") or "")
                if any(h["date"] == day and h["stage"] == ch["stage"] and h["note"] == note for h in g["history"]):
                    continue  # the base has it already
                g["history"].append({"date": day, "stage": ch["stage"], "note": note, "at": at, "by": by, "pending": True})
                g["stage"] = ch["stage"]
            else:
                text = str(ch["note"])
                if not text or any(n["date"] == day and n["text"] == text for n in g["notes"]):
                    continue
                g["notes"].append({"date": day, "text": text, "by": by if by in NOTE_BY else "player", "at": at, "pending": True})
            g["pending"] += 1
            g["pending_changes"].append(ch.get("id"))
            continue
        if isinstance(ch.get("new"), dict):
            c = normalize(ch["new"])
            if not c["id"] or c["id"] in in_base:  # already brought into the base (applied, not yet archived)
                continue
            c["goals"] = [g for g in c["goals"] if g in goals]
            c["new"] = True
            cards[c["id"]] = c
        elif "set" in ch and ch.get("card") in cards:
            c = cards[ch["card"]]
            try:
                fields = check_set(ch["set"], areas | {c["area"]}, set(goals))
            except Invalid:
                continue
            _apply_set(c, fields, at)
        elif "note" in ch and ch.get("card") in cards:
            c = cards[ch["card"]]
            text = str(ch["note"])
            if any(n.get("text") == text and n.get("at") == at for n in c["notes"]):
                continue  # the base has it already
            c["notes"].append({"at": at, "by": ch.get("by", "player"), "text": text, "pending": True})
        else:
            continue
        c["pending"] += 1
        c["pending_changes"].append(ch.get("id"))
    # which cards Claude could take up now, as the cards stand with the edits: those offer "Ready" (none when the base
    # came from a board.py that does not know it)
    knows = knows_ready(base)
    for c in cards.values():
        c["claude_ready"] = knows and claude_ready(c, cards)
    return {**base, "cards": list(cards.values()), "goals": list(goals.values()), "stages": stage_labels(base),
            "ready_known": knows}


def knows_ready(base: dict) -> bool:
    """The base came from a board.py that knows the mark "ready" (its cards carry the field)."""
    raw = base.get("cards") if isinstance(base.get("cards"), list) else []
    return any(isinstance(c, dict) and "ready" in c for c in raw)


class Store:
    def __init__(self, data_dir: Path | str):
        self.dir = Path(data_dir)
        self.base_path = self.dir / "cards.json"
        self.changes_path = self.dir / "changes.jsonl"
        self.applied_path = self.dir / "changes.applied.jsonl"
        self.lock_path = self.dir / "changes.lock"
        self._base_key = None
        self._base = None

    # --- reading
    def base(self) -> dict | None:
        """The pushed cards.json (read again only when it changes), or None before the first push."""
        try:
            st = self.base_path.stat()
        except FileNotFoundError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        if key != self._base_key:
            try:
                raw = json.loads(self.base_path.read_text())
            except (OSError, ValueError):
                return self._base  # being replaced: the last one read
            self._base, self._base_key = raw if isinstance(raw, dict) else None, key
        return self._base

    def changes(self) -> list[dict]:
        """The pending changes, in order; a line that is not a JSON object is skipped."""
        out = []
        try:
            text = self.changes_path.read_text()
        except FileNotFoundError:
            return out
        for line in text.splitlines():
            try:
                ch = json.loads(line)
            except ValueError:
                continue
            if isinstance(ch, dict):
                out.append(ch)
        return out

    def pending(self) -> list[dict]:
        """The changes not yet folded into the base (not in its "applied")."""
        folded = set((self.base() or {}).get("applied") or [])
        return [ch for ch in self.changes() if ch.get("id") not in folded]

    def board(self) -> dict:
        base = self.base() or {"version": 1, "generated": None, "areas": [], "cards": []}
        out = merge(base, self.changes())
        out["has_base"] = self.base() is not None
        out["pending"] = sum(c["pending"] for c in out["cards"]) + sum(g["pending"] for g in out["goals"])
        return out

    def card(self, card_id: str) -> dict:
        for c in self.board()["cards"]:
            if c["id"] == card_id:
                return c
        raise NotFound(card_id)

    def areas(self) -> set[str]:
        return {a["id"] for a in (self.base() or {}).get("areas", []) if isinstance(a, dict) and a.get("id")}

    def goal(self, goal_id: str) -> dict:
        for g in self.board()["goals"]:
            if g["id"] == goal_id:
                return g
        raise NotFound(goal_id)

    def goal_ids(self) -> set[str]:
        return {g["id"] for g in _goals(self.base() or {}).values()}

    # --- writing
    @contextmanager
    def locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _append(self, change: dict) -> None:
        """One line on the end of changes.jsonl, in one write, synced (the lock held)."""
        line = (json.dumps(change, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        fd = os.open(self.changes_path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                line = b"\n" + line  # a torn last line stays on its own line, and this one whole
            while line:
                line = line[os.write(fd, line):]
            os.fsync(fd)
        finally:
            os.close(fd)

    def _change(self, **body) -> dict:
        return {"id": str(uuid.uuid4()), "at": now_iso(), "by": "player", **body}

    def _set(self, card: dict, fields: dict) -> tuple[dict, dict | None]:
        fields = check_set(fields, self.areas() | {card["area"]}, self.goal_ids())
        fields = {k: v for k, v in fields.items() if card.get(k) != v}
        if not fields:
            return card, None
        if fields.get("ready") is True and not card.get("claude_ready"):
            if not knows_ready(self.base() or {}):
                raise Invalid("the cards on the unit are from a board.py without Ready: push them again from the desktop")
            raise Invalid("only a card Claude can take up now is marked ready: Claude among who, in Backlog or Next, "
                          "nothing open blocking it")
        change = self._change(card=card["id"], set=fields)
        self._append(change)
        return self.card(card["id"]), change

    def set(self, card_id: str, fields: dict) -> tuple[dict, dict | None]:
        """Set fields on a card. Returns the card as now shown and the change (None when nothing changed)."""
        with self.locked():
            return self._set(self.card(card_id), fields)

    def claude_next(self, card_id: str) -> tuple[dict, dict | None]:
        """"Claude: do this next": status next, Claude among who, importance 1 (one change)."""
        with self.locked():
            card = self.card(card_id)
            who = [w for w in WHO if w in card["who"] or w == "claude"]
            return self._set(card, {"status": "next", "who": who, "importance": 1})

    def note(self, card_id: str, text: str) -> tuple[dict, dict]:
        text = str(text or "").strip()
        if not text:
            raise Invalid("an empty note")
        if len(text) > NOTE_MAX:
            raise Invalid(f"a note of {NOTE_MAX} characters at most")
        with self.locked():
            self.card(card_id)
            change = self._change(card=card_id, note=text)
            self._append(change)
            return self.card(card_id), change

    def move_goal(self, goal_id: str, stage: int, note: str = "") -> tuple[dict, dict | None]:
        """A goal to another stage (1 to 10): a history entry, with a note (it may be empty). Returns the goal as now
        shown and the change (None when it is at that stage already)."""
        if not is_stage(stage):
            raise Invalid("stage must be a whole number from 1 to 10")
        note = str(note or "").strip()
        if len(note) > NOTE_MAX:
            raise Invalid(f"a note of {NOTE_MAX} characters at most")
        with self.locked():
            if self.goal(goal_id)["stage"] == stage:
                return self.goal(goal_id), None
            change = self._change(goal=goal_id, stage=stage, note=note)
            self._append(change)
            return self.goal(goal_id), change

    def goal_note(self, goal_id: str, text: str) -> tuple[dict, dict]:
        text = str(text or "").strip()
        if not text:
            raise Invalid("an empty note")
        if len(text) > NOTE_MAX:
            raise Invalid(f"a note of {NOTE_MAX} characters at most")
        with self.locked():
            self.goal(goal_id)
            change = self._change(goal=goal_id, note=text)
            self._append(change)
            return self.goal(goal_id), change

    def new(self, body: dict) -> tuple[dict, dict]:
        """A new card from the page: title, area, who and work; the rest defaults."""
        title = " ".join(str(body.get("title") or "").split())
        if not title:
            raise Invalid("a card needs a title")
        if len(title) > TITLE_MAX:
            raise Invalid(f"a title of {TITLE_MAX} characters at most")
        with self.locked():
            areas = self.areas()
            fields = {"status": body.get("status", "backlog"), "importance": body.get("importance", 2),
                      "who": body.get("who") or ["player"], "area": body.get("area", "")}
            if not body.get("work"):
                raise Invalid(f"a card needs its work: {', '.join(WORK)}")
            for k in ("work", "effort_player", "effort_claude", "waiting_on"):
                if body.get(k):
                    fields[k] = body[k]
            fields = check_set(fields, areas)
            details = str(body.get("details") or "")
            if len(details) > NOTE_MAX:
                raise Invalid(f"details of {NOTE_MAX} characters at most")
            taken = {c["id"] for c in self.board()["cards"]}
            card_id = slug(title)
            while card_id in taken:
                card_id = slug(title)
            today = now_iso()[:10]
            card = {"id": card_id, "title": title, "area": fields["area"], "status": fields["status"],
                    "importance": fields["importance"], "who": fields["who"], "work": fields["work"],
                    "effort_player": fields.get("effort_player", ""), "effort_claude": fields.get("effort_claude", ""),
                    "waiting_on": fields.get("waiting_on", ""), "blocked_by": [], "links": [], "details": details,
                    "source": "", "created": today, "updated": today,  # board.py names the source
                    "done": today if fields["status"] == "done" else None}
            change = self._change(new=card)
            self._append(change)
            return self.card(card_id), change

    def pending_text(self) -> str:
        """changes.jsonl as it is, whole lines only (what tools/pull-board takes to the desktop)."""
        with self.locked():
            try:
                text = self.changes_path.read_text()
            except FileNotFoundError:
                return ""
        return "".join(l for l in text.splitlines(keepends=True) if l.endswith("\n") and l.strip())

    def archive(self, ids: set[str]) -> tuple[int, int]:
        """Move the changes with these ids from changes.jsonl to changes.applied.jsonl (after a sync).
        The applied file is written (and synced) first, so a crash between leaves a change in both, never in
        neither. Returns (moved, left)."""
        ids = {str(i) for i in ids}
        with self.locked():
            try:
                lines = self.changes_path.read_text().splitlines(keepends=True)
            except FileNotFoundError:
                return 0, 0
            moved, kept = [], []
            for line in lines:
                try:
                    cid = json.loads(line).get("id")
                except (ValueError, AttributeError):
                    cid = None
                (moved if cid in ids else kept).append(line if line.endswith("\n") else line + "\n")
            if not moved:
                return 0, len(kept)
            fd = os.open(self.applied_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                data = "".join(moved).encode()
                while data:
                    data = data[os.write(fd, data):]
                os.fsync(fd)
            finally:
                os.close(fd)
            tmp = self.changes_path.with_name("changes.jsonl.new")
            with open(tmp, "w") as f:
                f.write("".join(kept))
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(self.changes_path)
            return len(moved), len(kept)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=["datadir", "pending", "archive"])
    ap.add_argument("--config", type=Path, help="default: $ROOM_BOARD_CONFIG or /etc/room-board.toml")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    if a.what == "datadir":
        print(cfg["data_dir"])
        return
    if a.what == "pending":
        sys.stdout.write(Store(cfg["data_dir"]).pending_text())
        return
    ids = {line.strip() for line in sys.stdin if line.strip()}
    moved, left = Store(cfg["data_dir"]).archive(ids)
    print(f"archived {moved} change{'s' if moved != 1 else ''}; {left} still pending")


if __name__ == "__main__":
    main()
